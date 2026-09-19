import { useEffect, useState } from 'react'
import { useGame } from '../store'
import { api } from '../lib/api'
import { COLOR, signColor } from '../theme'
import { pct, won } from '../lib/format'
import { Empty, TabBar, TopBar } from '../components/common'

const TABS = [
  { key: 'return', label: '수익률' },
  { key: 'risk', label: '위험조정' },
  { key: 'total', label: '종합' },
]

/** 순위 3탭. 탭마다 집계 대상이 다르다는 점을 화면에 밝힌다. */
export default function Rank() {
  const { snapshot } = useGame()
  const [tab, setTab] = useState('return')
  const [rows, setRows] = useState([])
  const [minTicks, setMinTicks] = useState(60)

  useEffect(() => {
    let alive = true
    const load = () =>
      api
        .rank(tab)
        .then((res) => {
          if (!alive) return
          setRows(res.rows)
          setMinTicks(res.min_ticks_for_risk_rank)
        })
        .catch(() => {})
    load()
    const timer = setInterval(load, 5000)
    return () => {
      alive = false
      clearInterval(timer)
    }
  }, [tab])

  const myId = snapshot?.me?.player_id

  return (
    <div className="screen">
      <TopBar title="랭킹" remaining={snapshot?.session.remaining_seconds} />

      <div
        className="flex"
        style={{ background: COLOR.bg, borderRadius: 12, padding: 4 }}
      >
        {TABS.map((t) => {
          const active = tab === t.key
          return (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              style={{
                flex: 1, borderRadius: 10, padding: '9px 0', fontSize: 13,
                fontWeight: 700,
                background: active ? COLOR.card : 'transparent',
                color: active ? COLOR.ink : COLOR.muted,
              }}
            >
              {t.label}
            </button>
          )
        })}
      </div>

      {tab !== 'return' && (
        <div style={{ fontSize: 11, color: COLOR.muted, marginTop: 8 }}>
          참여 {minTicks}틱 미만 참가자는 표본이 모자라 이 탭에서 제외됩니다
        </div>
      )}

      <div className="flex flex-col gap-2" style={{ marginTop: 12 }}>
        {rows.length === 0 && <Empty>아직 집계된 참가자가 없습니다</Empty>}
        {rows.map((r, i) => {
          const rank =
            tab === 'return' ? r.rank_return : tab === 'risk' ? r.rank_risk : r.rank_total
          const mine = r.player_id === myId
          return (
            <div
              key={r.player_id}
              className="flex items-center gap-3"
              style={{
                background: mine ? COLOR.ink : COLOR.card,
                color: mine ? COLOR.bg : COLOR.ink,
                borderRadius: 16,
                padding: '13px 15px',
              }}
            >
              <span
                className="font-display"
                style={{ fontSize: 17, width: 26, textAlign: 'center' }}
              >
                {rank || i + 1}
              </span>
              <span style={{ fontSize: 14, fontWeight: 700 }} className="truncate flex-1">
                {r.nickname}
              </span>
              <div className="text-right">
                <div
                  style={{
                    fontSize: 14, fontWeight: 700,
                    color: mine ? COLOR.bg : signColor(r.return_pct),
                  }}
                >
                  {tab === 'risk'
                    ? r.risk_adjusted.toFixed(3)
                    : tab === 'total'
                      ? pct(r.return_pct)
                      : pct(r.return_pct)}
                </div>
                <div
                  style={{
                    fontSize: 11,
                    color: mine ? 'rgba(251,247,240,0.7)' : COLOR.muted,
                  }}
                >
                  {won(r.final_asset)}원 · MDD {(r.mdd * 100).toFixed(1)}%
                </div>
              </div>
            </div>
          )
        })}
      </div>
      <TabBar />
    </div>
  )
}
