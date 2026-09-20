import { useEffect, useRef, useState } from 'react'
import { COLOR, signColor } from '../theme'
import { clock, pct, won } from '../lib/format'

/**
 * 프로젝터 화면. 3m 거리에서 읽히도록 모바일 규격 대신 대형 화면 규격을 쓴다.
 * 참가자 토큰 없이 /ws/board 로 붙는다.
 */
export default function Board() {
  const [snap, setSnap] = useState(null)
  const [flash, setFlash] = useState(null)
  const socketRef = useRef(null)

  useEffect(() => {
    let alive = true
    function connect() {
      const proto = location.protocol === 'https:' ? 'wss' : 'ws'
      const ws = new WebSocket(`${proto}://${location.host}/ws/board`)
      socketRef.current = ws
      ws.onmessage = (event) => {
        const msg = JSON.parse(event.data)
        if (msg.type === 'snapshot') {
          const { type, ...rest } = msg
          setSnap(rest)
        } else if (msg.type === 'tick') {
          setSnap((prev) =>
            prev
              ? {
                  ...prev,
                  stocks: prev.stocks.map((s) => ({
                    ...s,
                    price: msg.p[s.symbol] ?? s.price,
                    chg_pct: msg.c[s.symbol] ?? s.chg_pct,
                  })),
                  indices: {
                    fear: { value: msg.fear, display_delta: msg.disp.fear_delta },
                    fx: { value: msg.fx, display_delta: msg.disp.fx_delta },
                  },
                  rank: msg.rank?.length ? msg.rank : prev.rank,
                  news: msg.news?.length
                    ? [...msg.news, ...prev.news].slice(0, 10)
                    : prev.news,
                  badges: msg.badges || {},
                  players_count: msg.players_count ?? prev.players_count,
                  session: {
                    ...prev.session,
                    current_tick: msg.t,
                    remaining_seconds: msg.remaining_seconds,
                  },
                }
              : prev,
          )
          if (msg.news?.length) setFlash(msg.news[0])
        }
      }
      ws.onclose = () => alive && setTimeout(connect, 1000)
    }
    connect()
    return () => {
      alive = false
      socketRef.current?.close()
    }
  }, [])

  useEffect(() => {
    if (!flash) return undefined
    const timer = setTimeout(() => setFlash(null), 12000)
    return () => clearTimeout(timer)
  }, [flash])

  if (!snap) {
    return (
      <div
        className="flex items-center justify-center"
        style={{ minHeight: '100vh', fontSize: 40, color: COLOR.muted }}
      >
        대기 중
      </div>
    )
  }

  const badges = Object.entries(snap.badges || {})

  return (
    <div style={{ minHeight: '100vh', padding: '32px 44px' }}>
      <header className="flex items-center justify-between">
        <h1 className="font-display" style={{ fontSize: 52 }}>
          모의투자 리그
        </h1>
        <div className="flex items-center" style={{ gap: 36 }}>
          <BoardIndex label="공포지수" value={Math.round(snap.indices.fear.value)} />
          <BoardIndex
            label="원/달러"
            value={Math.round(snap.indices.fx.value).toLocaleString('ko-KR')}
          />
          <div
            style={{
              background: COLOR.ink, color: COLOR.bg, borderRadius: 18,
              padding: '10px 26px',
            }}
          >
            <span className="font-display" style={{ fontSize: 46 }}>
              {clock(snap.session.remaining_seconds)}
            </span>
          </div>
        </div>
      </header>

      {badges.length > 0 && (
        <div
          style={{
            marginTop: 20, background: '#FDEEE8',
            border: '3px solid rgba(193,59,59,0.5)', borderRadius: 18,
            padding: '16px 24px', fontSize: 34, fontWeight: 700, color: COLOR.up,
          }}
        >
          {badges
            .map(([symbol, b]) => {
              const name = snap.stocks.find((s) => s.symbol === symbol)?.name || symbol
              return `${name} ${b.label}`
            })
            .join('   ·   ')}
        </div>
      )}

      <div className="grid" style={{ gridTemplateColumns: '1.1fr 1fr', gap: 28, marginTop: 24 }}>
        <section>
          <h2 style={{ fontSize: 24, fontWeight: 700, marginBottom: 12 }}>
            실시간 순위 · {snap.players_count}명 참가
          </h2>
          <div className="flex flex-col" style={{ gap: 8 }}>
            {(snap.rank || []).slice(0, 10).map((r, i) => (
              <div
                key={r.player_id}
                className="flex items-center"
                style={{
                  background: COLOR.card, borderRadius: 14, padding: '12px 20px',
                  gap: 20,
                }}
              >
                <span className="font-display" style={{ fontSize: 34, width: 52 }}>
                  {i + 1}
                </span>
                <span style={{ fontSize: 28, fontWeight: 700, flex: 1 }}>
                  {r.nickname}
                </span>
                <span
                  style={{
                    fontSize: 30, fontWeight: 700, color: signColor(r.return_pct),
                  }}
                >
                  {pct(r.return_pct)}
                </span>
              </div>
            ))}
            {(!snap.rank || snap.rank.length === 0) && (
              <div style={{ fontSize: 26, color: COLOR.muted }}>참가자를 기다리는 중</div>
            )}
          </div>
        </section>

        <section>
          <h2 style={{ fontSize: 24, fontWeight: 700, marginBottom: 12 }}>종목 시세</h2>
          <div className="flex flex-col" style={{ gap: 8 }}>
            {snap.stocks.map((s) => (
              <div
                key={s.symbol}
                className="flex items-center"
                style={{
                  background: COLOR.card, borderRadius: 14, padding: '12px 20px',
                  gap: 16,
                }}
              >
                <span
                  className="flex items-center justify-center shrink-0"
                  style={{
                    width: 42, height: 42, borderRadius: 12,
                    background: s.avatar_color, color: '#fff',
                    fontFamily: "'Black Han Sans', sans-serif", fontSize: 20,
                  }}
                >
                  {s.name[0]}
                </span>
                <span style={{ fontSize: 24, fontWeight: 700, flex: 1 }}>{s.name}</span>
                <span style={{ fontSize: 22 }}>{won(s.price)}</span>
                <span
                  style={{
                    fontSize: 24, fontWeight: 700, color: signColor(s.chg_pct),
                    width: 108, textAlign: 'right',
                  }}
                >
                  {pct(s.chg_pct)}
                </span>
              </div>
            ))}
          </div>
        </section>
      </div>

      {flash && (
        <div
          className="fixed left-0 right-0 fade-up"
          style={{
            bottom: 0, background: COLOR.ink, color: COLOR.bg,
            padding: '22px 44px', fontSize: 32, fontWeight: 700,
          }}
        >
          속보 · {flash.headline}
        </div>
      )}
    </div>
  )
}

function BoardIndex({ label, value }) {
  return (
    <div className="text-right">
      <div style={{ fontSize: 18, color: COLOR.muted }}>{label}</div>
      <div className="font-display" style={{ fontSize: 38 }}>
        {value}
      </div>
    </div>
  )
}
