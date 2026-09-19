import {
  createContext, useCallback, useContext, useEffect, useMemo, useRef, useState,
} from 'react'
import { api, getPlayerToken } from './lib/api'

const GameContext = createContext(null)

/** 재연결 간격. 학교 와이파이가 끊겼다 붙는 상황을 가정한다. */
const RETRY_MS = [500, 1000, 2000, 4000, 6000]

export function GameProvider({ children }) {
  const [snapshot, setSnapshot] = useState(null)
  const [connected, setConnected] = useState(false)
  const [lastFills, setLastFills] = useState(null)
  const socketRef = useRef(null)
  const retryRef = useRef(0)
  const aliveRef = useRef(true)

  const applyTick = useCallback((msg) => {
    setSnapshot((prev) => {
      if (!prev) return prev
      const stocks = prev.stocks.map((s) => {
        const price = msg.p[s.symbol] ?? s.price
        const spark = [...s.spark, price].slice(-60)
        return { ...s, price, chg_pct: msg.c[s.symbol] ?? s.chg_pct, spark }
      })
      // 총자산은 서버가 매 틱 보내지 않는다. 보유 수량과 새 가격으로 직접 계산한다.
      let me = prev.me
      if (me) {
        const holdings = me.holdings.map((h) => {
          const price = msg.p[h.symbol] ?? h.price
          return {
            ...h,
            price,
            value: h.quantity * price,
            pnl_pct: h.avg_cost ? price / h.avg_cost - 1 : 0,
          }
        })
        const held = holdings.reduce((sum, h) => sum + h.value, 0)
        const total = me.cash + me.reserved_cash + held
        const mine = msg.rank?.find((r) => r.player_id === me.player_id)
        me = {
          ...me,
          holdings: holdings.map((h) => ({
            ...h,
            weight: total ? h.value / total : 0,
          })),
          total_asset: total,
          profit: total - me.starting_cash,
          return_pct: total / me.starting_cash - 1,
          rank: mine ? mine.rank_return : me.rank,
        }
      }
      const news = msg.news?.length ? [...msg.news, ...prev.news].slice(0, 30) : prev.news
      return {
        ...prev,
        stocks,
        me,
        news,
        badges: msg.badges || {},
        flow: msg.flow || prev.flow,
        rank: msg.rank?.length ? msg.rank : prev.rank,
        players_count: msg.players_count ?? prev.players_count,
        indices: {
          fear: { value: msg.fear, display_delta: msg.disp.fear_delta },
          fx: { value: msg.fx, display_delta: msg.disp.fx_delta },
        },
        session: {
          ...prev.session,
          current_tick: msg.t,
          remaining_seconds: msg.remaining_seconds,
        },
      }
    })
  }, [])

  const connect = useCallback(() => {
    const token = getPlayerToken()
    const proto = location.protocol === 'https:' ? 'wss' : 'ws'
    const url = `${proto}://${location.host}/ws/ticks?token=${encodeURIComponent(token)}`
    const socket = new WebSocket(url)
    socketRef.current = socket

    socket.onopen = () => {
      setConnected(true)
      retryRef.current = 0
    }
    socket.onmessage = (event) => {
      const msg = JSON.parse(event.data)
      if (msg.type === 'snapshot') {
        const { type, ...rest } = msg
        setSnapshot(rest)
      } else if (msg.type === 'tick') {
        applyTick(msg)
      } else if (msg.type === 'fills') {
        setLastFills(msg.orders)
        if (msg.me) setSnapshot((prev) => (prev ? { ...prev, me: msg.me } : prev))
      } else if (msg.type === 'ended') {
        setSnapshot((prev) =>
          prev ? { ...prev, session: { ...prev.session, status: 'ended' } } : prev,
        )
      }
    }
    socket.onclose = () => {
      setConnected(false)
      if (!aliveRef.current) return
      const wait = RETRY_MS[Math.min(retryRef.current, RETRY_MS.length - 1)]
      retryRef.current += 1
      setTimeout(() => aliveRef.current && connect(), wait)
    }
    socket.onerror = () => socket.close()
  }, [applyTick])

  useEffect(() => {
    aliveRef.current = true
    api.state().then(setSnapshot).catch(() => {})
    connect()
    return () => {
      aliveRef.current = false
      socketRef.current?.close()
    }
  }, [connect])

  const refresh = useCallback(async () => {
    try {
      setSnapshot(await api.state())
    } catch {
      /* 판이 아직 없을 수 있다 */
    }
  }, [])

  const value = useMemo(
    () => ({ snapshot, connected, lastFills, clearFills: () => setLastFills(null), refresh }),
    [snapshot, connected, lastFills, refresh],
  )
  return <GameContext.Provider value={value}>{children}</GameContext.Provider>
}

export function useGame() {
  const ctx = useContext(GameContext)
  if (!ctx) throw new Error('GameProvider 안에서만 쓸 수 있다')
  return ctx
}

export function useStock(symbol) {
  const { snapshot } = useGame()
  return snapshot?.stocks.find((s) => s.symbol === symbol) || null
}
