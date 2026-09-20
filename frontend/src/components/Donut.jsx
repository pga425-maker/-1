import { COLOR } from '../theme'

/**
 * 자산배분 도넛. 한 종목 비중이 임계치를 넘으면 분산도 경고를 함께 낸다.
 * 외부 차트 라이브러리를 쓰지 않는다. 값이 단순해서 SVG 가 더 가볍다.
 */
const WARN_RATIO = 0.4

export default function Donut({ holdings, cash, size = 86 }) {
  const slices = [
    ...holdings.map((h) => ({
      key: h.symbol,
      label: h.name || h.symbol,
      value: h.value,
      color: h.avatar_color,
    })),
    { key: '__cash__', label: '현금', value: cash, color: COLOR.tag },
  ].filter((s) => s.value > 0)

  const total = slices.reduce((sum, s) => sum + s.value, 0)
  const radius = size / 2 - 7
  const circumference = 2 * Math.PI * radius
  let offset = 0

  const top = holdings.reduce(
    (best, h) => (h.value > (best?.value ?? 0) ? h : best),
    null,
  )
  const topRatio = total && top ? top.value / total : 0

  return (
    <div className="flex items-center gap-4">
      <svg width={size} height={size} style={{ transform: 'rotate(-90deg)' }}>
        {slices.map((s) => {
          const ratio = total ? s.value / total : 0
          const dash = ratio * circumference
          const el = (
            <circle
              key={s.key}
              cx={size / 2}
              cy={size / 2}
              r={radius}
              fill="none"
              stroke={s.color}
              strokeWidth={13}
              strokeDasharray={`${dash} ${circumference - dash}`}
              strokeDashoffset={-offset}
            />
          )
          offset += dash
          return el
        })}
      </svg>
      <div className="min-w-0 flex-1">
        <div className="flex flex-col gap-1">
          {slices.slice(0, 4).map((s) => (
            <div key={s.key} className="flex items-center gap-1.5">
              <span
                style={{
                  width: 8, height: 8, borderRadius: 3, background: s.color,
                  display: 'inline-block', flexShrink: 0,
                }}
              />
              <span style={{ fontSize: 11, color: COLOR.muted }} className="truncate">
                {s.label}
              </span>
              <span style={{ fontSize: 11, fontWeight: 700, marginLeft: 'auto' }}>
                {total ? Math.round((s.value / total) * 100) : 0}%
              </span>
            </div>
          ))}
        </div>
        {topRatio >= WARN_RATIO && (
          <div style={{ fontSize: 11, color: COLOR.up, marginTop: 6, fontWeight: 700 }}>
            {top.name || top.symbol} 비중이 {Math.round(topRatio * 100)}% 입니다
          </div>
        )}
      </div>
    </div>
  )
}
