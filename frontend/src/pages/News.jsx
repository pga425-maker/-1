import { useGame } from '../store'
import { COLOR, signColor } from '../theme'
import { deltaMark, elapsedLabel } from '../lib/format'
import { Loading } from './Play'
import { BackBar, Empty, ImpactBadge, TabBar, Tag } from '../components/common'
import { IconAlert, IconSpeaker } from '../components/Icons'

/** 시장 속보. 섹터 태그와 반영 상태 배지만 보여 주고 방향은 미리 알려주지 않는다. */
export default function News() {
  const { snapshot } = useGame()
  if (!snapshot) return <Loading />

  const { news, indices, session, stocks } = snapshot
  const tickSeconds = session.tick_seconds
  const stockName = (symbol) =>
    stocks.find((s) => s.symbol === symbol)?.name || symbol

  return (
    <div className="screen">
      <BackBar title="시장 속보" />

      <div className="flex gap-2.5">
        <IndexCard
          label="공포지수"
          value={Math.round(indices.fear.value)}
          delta={indices.fear.display_delta}
        />
        <IndexCard
          label="원/달러"
          value={Math.round(indices.fx.value).toLocaleString('ko-KR')}
          delta={indices.fx.display_delta}
        />
      </div>

      <div className="flex flex-col gap-2.5" style={{ marginTop: 14 }}>
        {news.length === 0 && (
          <Empty>아직 소식이 없습니다. 시장을 지켜보세요</Empty>
        )}
        {news.map((n) => (
          <article
            key={`${n.event_key}-${n.tick}`}
            className="fade-up"
            style={{
              background: COLOR.card, borderRadius: 16, padding: 16,
              display: 'flex', flexDirection: 'column', gap: 9,
              border: n.is_short_pressure
                ? '1px solid rgba(193,59,59,0.35)'
                : 'none',
            }}
          >
            <div className="flex items-center gap-2">
              <span
                className="flex items-center justify-center shrink-0"
                style={{
                  width: 26, height: 26, borderRadius: 8,
                  background: n.is_short_pressure ? '#FDEEE8' : COLOR.point,
                }}
              >
                {n.is_short_pressure ? <IconAlert size={14} /> : <IconSpeaker />}
              </span>
              <span
                style={{
                  fontSize: 11, fontWeight: 700,
                  color: n.is_short_pressure ? COLOR.up : COLOR.ink,
                }}
              >
                {n.is_short_pressure ? '경고' : '속보'}
              </span>
              <span
                style={{ fontSize: 11, color: COLOR.muted, marginLeft: 'auto' }}
              >
                {elapsedLabel(n.tick, session.current_tick, tickSeconds)}
              </span>
            </div>

            <div style={{ fontSize: 15, fontWeight: 700, lineHeight: 1.4 }}>
              {n.headline}
            </div>
            {n.is_short_pressure && (
              <div style={{ fontSize: 12, color: COLOR.muted }}>
                공매도 집중으로 위험하지만, 일시적으로 오를 수 있습니다
              </div>
            )}
            {!n.is_short_pressure && n.body && (
              <div style={{ fontSize: 12, color: COLOR.muted }}>{n.body}</div>
            )}

            <div className="flex items-center gap-1.5 flex-wrap">
              {n.is_short_pressure ? (
                <span
                  style={{
                    background: '#FDEEE8', border: '1px solid rgba(193,59,59,0.35)',
                    color: COLOR.up, borderRadius: 20, padding: '3px 9px',
                    fontSize: 11, fontWeight: 700,
                  }}
                >
                  {n.badge || '공매도 집중 과열 · 급등 후 급락 위험'}
                </span>
              ) : (
                <Tag tone="outline">{n.sector || stockName(n.symbol)}</Tag>
              )}
              <ImpactBadge state={n.impact_state} />
            </div>
          </article>
        ))}
      </div>
      <TabBar />
    </div>
  )
}

function IndexCard({ label, value, delta }) {
  return (
    <div
      style={{
        flex: 1, background: COLOR.card, borderRadius: 14, padding: '12px 14px',
      }}
    >
      <div style={{ fontSize: 11, color: COLOR.muted }}>{label}</div>
      <div className="flex items-baseline gap-1.5" style={{ marginTop: 2 }}>
        <span className="font-display" style={{ fontSize: 19 }}>
          {value}
        </span>
        <span style={{ fontSize: 12, fontWeight: 700, color: signColor(delta) }}>
          {deltaMark(delta)}
        </span>
      </div>
    </div>
  )
}
