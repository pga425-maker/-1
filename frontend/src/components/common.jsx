import { Link, useLocation, useNavigate } from 'react-router-dom'
import { COLOR, FALLBACK_AVATAR, signColor } from '../theme'
import { clock, deltaMark, pct, signedPct, won } from '../lib/format'
import {
  IconAlert, IconBack, IconChevron, IconClock, IconHome, IconMarket,
  IconNews, IconRank,
} from './Icons'

/* ------------------------------------------------------------------ 상단바 */

export function TopBar({ title, remaining }) {
  return (
    <div className="flex items-center justify-between pt-4 pb-3">
      <h1 className="font-display" style={{ fontSize: 19, letterSpacing: '-0.01em' }}>
        {title}
      </h1>
      {remaining != null && (
        <div
          className="flex items-center gap-1.5"
          style={{
            background: COLOR.ink,
            color: COLOR.bg,
            borderRadius: 20,
            padding: '5px 11px',
          }}
        >
          <IconClock size={14} color={COLOR.bg} />
          <span style={{ fontSize: 13, fontWeight: 700 }}>{clock(remaining)}</span>
        </div>
      )}
    </div>
  )
}

export function BackBar({ title, tag }) {
  const navigate = useNavigate()
  return (
    <div className="flex items-center gap-2.5 pt-4 pb-3">
      <button
        onClick={() => navigate(-1)}
        aria-label="뒤로 가기"
        className="flex items-center justify-center shrink-0"
        style={{ width: 34, height: 34, borderRadius: 10, background: COLOR.card }}
      >
        <IconBack />
      </button>
      <h1 style={{ fontSize: 18, fontWeight: 700 }}>{title}</h1>
      {tag && <Tag>{tag}</Tag>}
    </div>
  )
}

export function Tag({ children, tone = 'default' }) {
  const styles =
    tone === 'outline'
      ? { background: COLOR.bg, border: `1px solid rgba(33,26,46,0.1)` }
      : { background: COLOR.tag }
  return (
    <span
      style={{
        ...styles,
        borderRadius: 20,
        padding: '3px 9px',
        fontSize: 11,
        color: COLOR.muted,
        whiteSpace: 'nowrap',
      }}
    >
      {children}
    </span>
  )
}

/* ------------------------------------------------------------------ 지수 티커 */

export function IndexTicker({ indices }) {
  if (!indices) return null
  const items = [
    { label: '공포지수', ...indices.fear, digits: 0 },
    { label: '원/달러', ...indices.fx, digits: 0 },
  ]
  return (
    <div
      className="flex items-center gap-4 pb-2.5"
      style={{ fontSize: 12, color: COLOR.muted }}
    >
      {items.map((it) => (
        <div key={it.label} className="flex items-center gap-1.5">
          <span>{it.label}</span>
          <span style={{ color: COLOR.ink, fontWeight: 700 }}>
            {Math.round(it.value).toLocaleString('ko-KR')}
          </span>
          <span
            style={{ color: signColor(it.display_delta), fontWeight: 700 }}
          >
            {deltaMark(it.display_delta)}
          </span>
        </div>
      ))}
    </div>
  )
}

/* -------------------------------------------------------- 공매도 집중 경고 배너 */

export function ShortPressureBanner({ badges, stocks }) {
  const entries = Object.entries(badges || {})
  if (!entries.length) return null
  return (
    <div className="flex flex-col gap-1.5 pb-2.5">
      {entries.map(([symbol, badge]) => {
        const stock = stocks?.find((s) => s.symbol === symbol)
        return (
          <Link
            key={symbol}
            to={`/stock/${symbol}`}
            className="flex items-center gap-2 fade-up"
            style={{
              background: '#FDEEE8',
              border: '1px solid rgba(193,59,59,0.35)',
              borderRadius: 12,
              padding: '9px 12px',
            }}
          >
            <IconAlert />
            <div className="min-w-0">
              <div style={{ fontSize: 12, fontWeight: 700, color: COLOR.up }}>
                {stock?.name || symbol} · {badge.label}
              </div>
              <div style={{ fontSize: 11, color: COLOR.muted }}>
                공매도 집중으로 위험하지만, 일시적으로 오를 수 있습니다
              </div>
            </div>
          </Link>
        )
      })}
    </div>
  )
}

/* ------------------------------------------------------------------ 종목 행 */

export function Avatar({ name, color, size = 40, radius = 12, fontSize = 15 }) {
  return (
    <div
      className="flex items-center justify-center shrink-0"
      style={{
        width: size,
        height: size,
        borderRadius: radius,
        background: color || FALLBACK_AVATAR,
        color: '#FFFFFF',
        fontFamily: "'Black Han Sans', sans-serif",
        fontSize,
      }}
      aria-hidden="true"
    >
      {name?.[0] || '?'}
    </div>
  )
}

export function StockRow({ stock, right, sub, badge }) {
  return (
    <Link
      to={`/stock/${stock.symbol}`}
      className="flex items-center gap-3"
      style={{
        background: COLOR.card,
        borderRadius: 16,
        padding: '14px 15px',
        minHeight: 44,
      }}
    >
      <Avatar name={stock.name} color={stock.avatar_color} />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-1.5">
          <span style={{ fontSize: 14, fontWeight: 700 }}>{stock.name}</span>
          {badge}
        </div>
        <div style={{ fontSize: 11, color: COLOR.muted }}>{sub}</div>
      </div>
      <div className="text-right">
        {right}
      </div>
      <IconChevron />
    </Link>
  )
}

export function PriceCell({ value, changePct }) {
  return (
    <>
      <div style={{ fontSize: 14, fontWeight: 700 }}>{won(value)}</div>
      <div style={{ fontSize: 12, fontWeight: 700, color: signColor(changePct) }}>
        {pct(changePct)}
      </div>
    </>
  )
}

/* ------------------------------------------------------------------ 뉴스 카드 */

const STATE_LABEL = {
  pending: '반영 대기',
  applying: '반영중',
  done: '반영 완료',
}

export function ImpactBadge({ state }) {
  const pendingStyle = { background: COLOR.ink, color: COLOR.bg }
  const otherStyle = { background: COLOR.tag, color: COLOR.ink }
  const style = state === 'pending' ? pendingStyle : otherStyle
  return (
    <span
      style={{
        ...style,
        borderRadius: 20,
        padding: '3px 9px',
        fontSize: 11,
        fontWeight: 700,
        whiteSpace: 'nowrap',
      }}
    >
      {STATE_LABEL[state] || state}
    </span>
  )
}

/* ------------------------------------------------------------------ 하단 탭바 */

const TABS = [
  { to: '/play', label: '홈', Icon: IconHome },
  { to: '/market', label: '시세', Icon: IconMarket },
  { to: '/news', label: '뉴스', Icon: IconNews },
  { to: '/rank', label: '랭킹', Icon: IconRank },
]

export function TabBar() {
  const { pathname } = useLocation()
  return (
    <nav
      className="fixed bottom-0 left-0 right-0"
      style={{
        background: COLOR.card,
        borderTop: `1px solid ${COLOR.line}`,
        paddingBottom: 'env(safe-area-inset-bottom)',
      }}
    >
      <div className="mx-auto flex" style={{ maxWidth: 480 }}>
        {TABS.map(({ to, label, Icon }) => {
          const active = pathname.startsWith(to)
          const color = active ? COLOR.ink : COLOR.muted
          return (
            <Link
              key={to}
              to={to}
              className="flex flex-1 flex-col items-center justify-center gap-0.5"
              style={{ minHeight: 56, color }}
            >
              <Icon size={20} color={color} />
              <span style={{ fontSize: 11, fontWeight: active ? 700 : 500 }}>
                {label}
              </span>
            </Link>
          )
        })}
      </div>
    </nav>
  )
}

/* ------------------------------------------------------------------ 기타 */

export function Card({ children, style, className = '' }) {
  return (
    <div
      className={className}
      style={{ background: COLOR.card, borderRadius: 16, padding: 15, ...style }}
    >
      {children}
    </div>
  )
}

export function Empty({ children }) {
  return (
    <div
      className="text-center"
      style={{ color: COLOR.muted, fontSize: 12, padding: '28px 0' }}
    >
      {children}
    </div>
  )
}

export function SectionHeader({ title, right }) {
  return (
    <div className="flex items-end justify-between pb-2 pt-4">
      <span style={{ fontSize: 14, fontWeight: 700 }}>{title}</span>
      {right && <span style={{ fontSize: 11, color: COLOR.muted }}>{right}</span>}
    </div>
  )
}

export { signColor, signedPct }
