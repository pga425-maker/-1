import { useState } from 'react'
import { useGame } from '../store'
import { COLOR, signColor } from '../theme'
import { pct, won } from '../lib/format'
import { Loading } from './Play'
import {
  Avatar, IndexTicker, PriceCell, ShortPressureBanner, StockRow, TabBar, TopBar,
} from '../components/common'

/** 시세. 상승 1위와 하락 1위를 크게 보여주고 아래에 전체 목록을 둔다. */
export default function Market() {
  const { snapshot } = useGame()
  const [sortBy, setSortBy] = useState('default')
  if (!snapshot) return <Loading />

  const { stocks, indices, badges, session, flow } = snapshot
  const byChange = [...stocks].sort((a, b) => b.chg_pct - a.chg_pct)
  const best = byChange[0]
  const worst = byChange[byChange.length - 1]
  const list = sortBy === 'change' ? byChange : stocks

  return (
    <div className="screen">
      <TopBar title="시세" remaining={session.remaining_seconds} />
      <IndexTicker indices={indices} />
      <ShortPressureBanner badges={badges} stocks={stocks} />

      <div className="grid grid-cols-2 gap-2.5">
        <HighlightCard stock={best} label="상승 1위" flow={flow?.[best?.symbol]} />
        <HighlightCard stock={worst} label="하락 1위" flow={flow?.[worst?.symbol]} />
      </div>

      <div className="flex items-center gap-1.5 pt-4 pb-2">
        <SortChip active={sortBy === 'default'} onClick={() => setSortBy('default')}>
          기본순
        </SortChip>
        <SortChip active={sortBy === 'change'} onClick={() => setSortBy('change')}>
          등락률순
        </SortChip>
      </div>

      <div className="flex flex-col gap-2.5">
        {list.map((s) => (
          <StockRow
            key={s.symbol}
            stock={s}
            sub={
              flow?.[s.symbol]?.buyers
                ? `${s.sector} · 순매수 ${flow[s.symbol].buyers}명`
                : `${s.sector} · ${won(s.price)}원`
            }
            right={<PriceCell value={s.price} changePct={s.chg_pct} />}
          />
        ))}
      </div>
      <TabBar />
    </div>
  )
}

function HighlightCard({ stock, label, flow }) {
  if (!stock) return null
  return (
    <div style={{ background: COLOR.card, borderRadius: 16, padding: 15 }}>
      <div style={{ fontSize: 11, color: COLOR.muted }}>{label}</div>
      <div className="flex items-center gap-2" style={{ marginTop: 8 }}>
        <Avatar
          name={stock.name}
          color={stock.avatar_color}
          size={30}
          radius={9}
          fontSize={12}
        />
        <span style={{ fontSize: 13, fontWeight: 700 }} className="truncate">
          {stock.name}
        </span>
      </div>
      <div
        className="font-display"
        style={{ fontSize: 22, marginTop: 8, color: signColor(stock.chg_pct) }}
      >
        {pct(stock.chg_pct)}
      </div>
      <div style={{ fontSize: 11, color: COLOR.muted }}>{won(stock.price)}원</div>
      <div style={{ fontSize: 11, color: COLOR.muted, marginTop: 6 }}>
        {flow && flow.buyers > 0
          ? `지금 순매수 ${flow.buyers}명`
          : flow && flow.sellers > 0
            ? `지금 순매도 ${flow.sellers}명`
            : '지금은 조용합니다'}
      </div>
    </div>
  )
}

function SortChip({ active, children, onClick }) {
  return (
    <button
      onClick={onClick}
      className="touch"
      style={{
        background: active ? COLOR.ink : COLOR.card,
        color: active ? COLOR.bg : COLOR.muted,
        borderRadius: 12,
        padding: '7px 14px',
        fontSize: 12,
        fontWeight: 700,
        minHeight: 34,
      }}
    >
      {children}
    </button>
  )
}
