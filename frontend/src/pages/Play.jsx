import { Link } from 'react-router-dom'
import { useGame } from '../store'
import { COLOR, signColor } from '../theme'
import { pct, signedWon, won } from '../lib/format'
import Donut from '../components/Donut'
import {
  Empty, IndexTicker, PriceCell, SectionHeader, ShortPressureBanner, StockRow,
  TabBar, Tag, TopBar,
} from '../components/common'

/** 홈. 총자산과 보유 종목이 한눈에 들어오는 것이 목표다. */
export default function Play() {
  const { snapshot, connected } = useGame()
  if (!snapshot) return <Loading />

  const { me, stocks, indices, badges, session, players_count } = snapshot
  const stockBySymbol = Object.fromEntries(stocks.map((s) => [s.symbol, s]))

  const sorted = [...stocks].sort((a, b) => b.chg_pct - a.chg_pct)
  const bestSymbol = sorted[0]?.symbol
  const worstSymbol = sorted[sorted.length - 1]?.symbol

  const holdings = (me?.holdings || []).map((h) => ({
    ...h,
    name: stockBySymbol[h.symbol]?.name,
    sector: stockBySymbol[h.symbol]?.sector,
    avatar_color: stockBySymbol[h.symbol]?.avatar_color,
  }))

  return (
    <div className="screen">
      <TopBar title="모의투자 리그" remaining={session.remaining_seconds} />
      <IndexTicker indices={indices} />
      <ShortPressureBanner badges={badges} stocks={stocks} />
      {!connected && (
        <div
          style={{
            background: COLOR.tag, borderRadius: 12, padding: '8px 12px',
            fontSize: 11, color: COLOR.muted, marginBottom: 10,
          }}
        >
          연결이 끊겼습니다. 자동으로 다시 붙는 중입니다
        </div>
      )}

      {me ? (
        <>
          <div
            style={{
              background: COLOR.card, borderRadius: 20, padding: 22,
              boxShadow: '0 2px 14px rgba(33,26,46,0.07)',
            }}
          >
            <div style={{ fontSize: 13, color: COLOR.muted }}>내 총자산</div>
            <div
              className="font-display"
              style={{ fontSize: 32, lineHeight: 1.2, marginTop: 2 }}
            >
              {won(me.total_asset)}
            </div>
            <div
              style={{
                fontSize: 13, fontWeight: 700, color: signColor(me.profit),
                marginTop: 2,
              }}
            >
              {signedWon(me.profit)} ({pct(me.return_pct)})
            </div>
            <div style={{ fontSize: 12, color: COLOR.muted, marginTop: 8 }}>
              전체 {me.rank || '―'}위 · {players_count}명 참가
            </div>
            <div
              style={{
                marginTop: 12, paddingTop: 12, borderTop: `1px solid ${COLOR.line}`,
                fontSize: 12, color: COLOR.muted,
              }}
            >
              현금 보유액{' '}
              <span style={{ color: COLOR.ink, fontWeight: 700 }}>
                {won(me.cash)}원
              </span>
              {me.reserved_cash > 0 && (
                <span> · 주문 대기 {won(me.reserved_cash)}원</span>
              )}
            </div>
          </div>

          <div style={{ marginTop: 10 }}>
            <div style={{ background: COLOR.card, borderRadius: 16, padding: 15 }}>
              <Donut holdings={holdings} cash={me.cash + me.reserved_cash} />
            </div>
          </div>

          <SectionHeader title="보유 종목" right="평가금 · 등락률" />
          <div className="flex flex-col gap-2.5">
            {holdings.length === 0 && (
              <Empty>아직 보유한 종목이 없습니다. 시세 탭에서 골라 보세요</Empty>
            )}
            {holdings.map((h) => {
              const stock = stockBySymbol[h.symbol]
              const badge =
                h.symbol === bestSymbol ? (
                  <Tag>오늘 1위</Tag>
                ) : h.symbol === worstSymbol ? (
                  <Tag>오늘 꼴찌</Tag>
                ) : null
              return (
                <StockRow
                  key={h.symbol}
                  stock={stock}
                  sub={`${h.quantity}주 · ${stock.sector}`}
                  badge={badge}
                  right={<PriceCell value={h.value} changePct={h.pnl_pct} />}
                />
              )
            })}
          </div>
        </>
      ) : (
        <Empty>
          참가 정보가 없습니다.{' '}
          <Link to="/onboard" style={{ color: COLOR.ink, fontWeight: 700 }}>
            다시 참가하기
          </Link>
        </Empty>
      )}

      {session.status === 'ended' && (
        <Link
          to="/result"
          className="flex items-center justify-center"
          style={{
            marginTop: 16, background: COLOR.ink, color: COLOR.bg,
            borderRadius: 14, padding: '15px 0', fontSize: 15, fontWeight: 700,
          }}
        >
          결과 보기
        </Link>
      )}
      <TabBar />
    </div>
  )
}

export function Loading() {
  return (
    <div className="screen">
      <div style={{ paddingTop: 120, textAlign: 'center', color: COLOR.muted }}>
        불러오는 중
      </div>
    </div>
  )
}
