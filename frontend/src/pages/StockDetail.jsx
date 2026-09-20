import { useEffect, useMemo, useRef, useState } from 'react'
import { useParams } from 'react-router-dom'
import { createChart } from 'lightweight-charts'
import { Link } from 'react-router-dom'
import { useGame } from '../store'
import { api } from '../lib/api'
import { COLOR, signColor } from '../theme'
import { pct, signedWon, won } from '../lib/format'
import { Loading } from './Play'
import {
  BackBar, Card, ImpactBadge, IndexTicker, Tag,
} from '../components/common'
import { IconAlert, IconCheck, IconMinus, IconPlus } from '../components/Icons'

const REASONS = ['실적성장', '저평가', '차트추세', '분산', '뉴스', '직감']

export default function StockDetail() {
  const { symbol } = useParams()
  const { snapshot, refresh } = useGame()
  const [side, setSide] = useState('buy')
  const [qty, setQty] = useState(1)
  const [reason, setReason] = useState(null)
  const [message, setMessage] = useState(null)
  const [busy, setBusy] = useState(false)
  const [asking, setAsking] = useState(false)

  const stock = snapshot?.stocks.find((s) => s.symbol === symbol)
  const holding = snapshot?.me?.holdings.find((h) => h.symbol === symbol)
  const badge = snapshot?.badges?.[symbol]

  const related = useMemo(
    () =>
      (snapshot?.news || [])
        .filter((n) => n.symbol === symbol || (stock && n.sector === stock.sector))
        .slice(0, 2),
    [snapshot, symbol, stock],
  )

  if (!snapshot || !stock) return <Loading />

  const price = stock.price
  const diff = price - stock.initial_price
  const amount = qty * price
  const fee = Math.round(amount * 0.00015)

  // 한 종목 총자산 50% 상한과 현금 중 더 빡빡한 쪽이 실제 한도다.
  const me = snapshot.me
  const byCash = me ? Math.floor(me.cash / (price * 1.00015)) : 0
  const heldValue = (holding?.quantity || 0) * price
  const roomValue = me ? Math.floor(me.total_asset * 0.5) - heldValue : 0
  const byRoom = Math.max(0, Math.floor(roomValue / price))
  const maxBuy = Math.max(0, Math.min(byCash, byRoom))
  const limitedBy = byRoom < byCash ? '비중 상한' : '현금'
  const overLimit = side === 'buy' && qty > maxBuy
  // 근거를 안 고른 상태에서 버튼을 죽여 두면, 눌러도 아무 일이 없어서 고장으로 보인다.
  // 버튼은 살려 두고 무엇이 빠졌는지 버튼 글자로 알린다.
  const needReason = side === 'buy' && !reason
  const canSubmit =
    qty > 0 && !overLimit && snapshot.session.status === 'running'

  async function submit() {
    if (needReason) {
      setMessage({ tone: 'error', text: '매수 근거를 하나 골라야 주문할 수 있습니다' })
      setAsking(true)
      setTimeout(() => setAsking(false), 900)
      return
    }
    setBusy(true)
    setMessage(null)
    try {
      const res = await api.order({
        symbol,
        side,
        qty: Number(qty),
        reason: side === 'buy' ? reason : null,
      })
      setMessage({ tone: 'ok', text: `${res.qty}주 주문 접수. ${res.message}` })
      setQty(1)
      setReason(null)
      refresh()
    } catch (err) {
      setMessage({ tone: 'error', text: err.message })
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="screen" style={{ paddingBottom: 360 }}>
      <BackBar title={stock.name} tag={stock.sector} />
      <IndexTicker indices={snapshot.indices} />

      {badge && (
        <div
          className="flex items-center gap-2"
          style={{
            background: '#FDEEE8', border: '1px solid rgba(193,59,59,0.35)',
            borderRadius: 12, padding: '9px 12px', marginBottom: 10,
          }}
        >
          <IconAlert />
          <div>
            <div style={{ fontSize: 12, fontWeight: 700, color: COLOR.up }}>
              {badge.label}
            </div>
            <div style={{ fontSize: 11, color: COLOR.muted }}>
              공매도 집중으로 위험하지만, 일시적으로 오를 수 있습니다
            </div>
          </div>
        </div>
      )}

      <div className="font-display" style={{ fontSize: 30, lineHeight: 1.2 }}>
        {won(price)}
      </div>
      <div style={{ fontSize: 14, fontWeight: 700, color: signColor(diff) }}>
        {signedWon(diff)} ({pct(stock.chg_pct)})
      </div>

      <div style={{ marginTop: 12 }}>
        <PriceChart data={stock.spark} rising={stock.chg_pct >= 0} />
      </div>

      <Card style={{ marginTop: 12 }}>
        <InfoRow label="보유 수량" value={`${holding?.quantity || 0}주`} />
        <InfoRow
          label="평균매입가"
          value={holding?.avg_cost ? `${won(holding.avg_cost)}원` : '―'}
        />
        <InfoRow
          label="평가손익"
          value={
            holding
              ? `${signedWon(holding.value - holding.avg_cost * holding.quantity)} (${pct(
                  holding.pnl_pct,
                )})`
              : '―'
          }
          color={holding ? signColor(holding.pnl_pct) : undefined}
          last
        />
      </Card>

      <div className="flex items-end justify-between pt-4 pb-2">
        <span style={{ fontSize: 14, fontWeight: 700 }}>관련 뉴스</span>
        <Link to="/news" style={{ fontSize: 11, color: COLOR.muted }}>
          전체보기 ›
        </Link>
      </div>
      <div className="flex flex-col gap-2.5">
        {related.length === 0 && (
          <div style={{ fontSize: 12, color: COLOR.muted }}>아직 관련 소식이 없습니다</div>
        )}
        {related.map((n) => (
          <Card key={n.event_key}>
            <div className="flex items-start justify-between gap-2">
              <span style={{ fontSize: 13, fontWeight: 700, lineHeight: 1.4 }}>
                {n.headline}
              </span>
              <ImpactBadge state={n.impact_state} />
            </div>
            {n.sector && (
              <div style={{ marginTop: 8 }}>
                <Tag tone="outline">{n.sector}</Tag>
              </div>
            )}
          </Card>
        ))}
      </div>

      <OrderPanel
        side={side}
        setSide={setSide}
        qty={qty}
        setQty={setQty}
        reason={reason}
        setReason={setReason}
        amount={amount}
        fee={fee}
        canSubmit={canSubmit}
        busy={busy}
        onSubmit={submit}
        message={message}
        maxSell={holding?.quantity || 0}
        maxBuy={maxBuy}
        limitedBy={limitedBy}
        overLimit={overLimit}
        needReason={needReason}
        asking={asking}
      />
    </div>
  )
}

function InfoRow({ label, value, color, last }) {
  return (
    <div
      className="flex items-center justify-between"
      style={{
        padding: '9px 0',
        borderBottom: last ? 'none' : `1px solid ${COLOR.line}`,
      }}
    >
      <span style={{ fontSize: 12, color: COLOR.muted }}>{label}</span>
      <span style={{ fontSize: 13, fontWeight: 700, color: color || COLOR.ink }}>
        {value}
      </span>
    </div>
  )
}

/** 라인차트. 상승은 빨강, 하락은 파랑. 같은 색 0.22 -> 0 그라데이션 면을 깐다. */
function PriceChart({ data, rising }) {
  const boxRef = useRef(null)
  const chartRef = useRef(null)
  const seriesRef = useRef(null)

  useEffect(() => {
    if (!boxRef.current) return undefined
    const chart = createChart(boxRef.current, {
      height: 168,
      layout: {
        background: { color: COLOR.card },
        textColor: COLOR.muted,
        fontFamily: "'Noto Sans KR', sans-serif",
        // 차트 라이브러리가 기본으로 넣는 제작사 로고를 끈다.
        // 실존 기업 상표를 화면에 쓰지 않는다는 규칙에 걸린다.
        attributionLogo: false,
      },
      localization: {
        priceFormatter: (v) => Math.round(v).toLocaleString('ko-KR'),
      },
      grid: { vertLines: { visible: false }, horzLines: { color: COLOR.line } },
      rightPriceScale: { borderVisible: false },
      timeScale: { visible: false, borderVisible: false },
      crosshair: { horzLine: { visible: false }, vertLine: { visible: false } },
      handleScroll: false,
      handleScale: false,
    })
    chartRef.current = chart
    const handle = () =>
      chart.applyOptions({ width: boxRef.current?.clientWidth || 300 })
    handle()
    window.addEventListener('resize', handle)
    return () => {
      window.removeEventListener('resize', handle)
      chart.remove()
      chartRef.current = null
      seriesRef.current = null
    }
  }, [])

  useEffect(() => {
    const chart = chartRef.current
    if (!chart) return
    const color = rising ? COLOR.up : COLOR.down
    if (!seriesRef.current) {
      seriesRef.current = chart.addAreaSeries({
        lineColor: color,
        lineWidth: 2.5,
        topColor: `${color}38`,
        bottomColor: `${color}00`,
        priceLineVisible: false,
        lastValueVisible: false,
        // 원 단위라 소수점이 필요 없다
        priceFormat: { type: 'price', precision: 0, minMove: 1 },
      })
    } else {
      seriesRef.current.applyOptions({
        lineColor: color,
        topColor: `${color}38`,
        bottomColor: `${color}00`,
      })
    }
    seriesRef.current.setData(
      data.map((value, i) => ({ time: i + 1, value })),
    )
    chart.timeScale().fitContent()
  }, [data, rising])

  return (
    <div
      ref={boxRef}
      style={{ background: COLOR.card, borderRadius: 16, padding: 8, overflow: 'hidden' }}
    />
  )
}

function OrderPanel({
  side, setSide, qty, setQty, reason, setReason, amount, fee,
  canSubmit, busy, onSubmit, message, maxSell, maxBuy, limitedBy, overLimit,
  needReason, asking,
}) {
  const buying = side === 'buy'
  return (
    <div
      className="fixed bottom-0 left-0 right-0"
      style={{
        background: COLOR.card,
        borderTop: `1px solid ${COLOR.line}`,
        padding: '14px 20px calc(22px + env(safe-area-inset-bottom))',
      }}
    >
      <div className="mx-auto" style={{ maxWidth: 480 }}>
        <div
          className="flex"
          style={{ background: COLOR.bg, borderRadius: 12, padding: 4 }}
        >
          {[
            { key: 'buy', label: '매수' },
            { key: 'sell', label: '매도' },
          ].map((t) => {
            const active = side === t.key
            const style = !active
              ? { background: 'transparent', color: COLOR.muted }
              : t.key === 'buy'
                ? { background: COLOR.point, color: COLOR.ink }
                : { background: COLOR.ink, color: COLOR.bg }
            return (
              <button
                key={t.key}
                onClick={() => setSide(t.key)}
                style={{
                  ...style, flex: 1, borderRadius: 10, padding: '9px 0',
                  fontSize: 13, fontWeight: 700,
                }}
              >
                {t.label}
              </button>
            )
          })}
        </div>

        <div
          className="flex items-center justify-between"
          style={{ marginTop: 12 }}
        >
          <span style={{ fontSize: 12, color: COLOR.muted }}>수량</span>
          <div className="flex items-center gap-3">
            <button
              aria-label="수량 1 줄이기"
              onClick={() => setQty((q) => Math.max(1, q - 1))}
              style={{
                width: 32, height: 32, borderRadius: 10,
                border: `1px solid ${COLOR.line}`,
              }}
              className="flex items-center justify-center"
            >
              <IconMinus />
            </button>
            <input
              aria-label="주문 수량"
              inputMode="numeric"
              value={qty}
              onChange={(e) => {
                const next = parseInt(e.target.value.replace(/\D/g, ''), 10)
                setQty(Number.isFinite(next) && next > 0 ? next : 1)
              }}
              style={{
                width: 64, textAlign: 'center', fontSize: 15, fontWeight: 700,
                border: 'none', outline: 'none', background: 'transparent',
              }}
            />
            <button
              aria-label="수량 1 늘리기"
              onClick={() => setQty((q) => q + 1)}
              style={{
                width: 32, height: 32, borderRadius: 10,
                border: `1px solid ${COLOR.line}`,
              }}
              className="flex items-center justify-center"
            >
              <IconPlus />
            </button>
          </div>
        </div>
        {!buying && maxSell > 0 && (
          <button
            onClick={() => setQty(maxSell)}
            style={{ fontSize: 11, color: COLOR.muted, marginTop: 4 }}
          >
            보유 {maxSell}주 전량
          </button>
        )}
        {buying && (
          <button
            onClick={() => setQty(Math.max(1, maxBuy))}
            disabled={maxBuy <= 0}
            style={{ fontSize: 11, color: COLOR.muted, marginTop: 4 }}
          >
            {maxBuy > 0
              ? `최대 ${maxBuy}주 (${limitedBy} 기준)`
              : `${limitedBy} 때문에 더 담을 수 없습니다`}
          </button>
        )}

        <div
          className="flex items-center justify-between"
          style={{ marginTop: 8, fontSize: 12 }}
        >
          <span style={{ color: COLOR.muted }}>예상 금액</span>
          <span style={{ fontWeight: 700 }}>
            {won(amount)}원{' '}
            <span style={{ color: COLOR.muted, fontWeight: 400 }}>
              (수수료 {won(fee)}원)
            </span>
          </span>
        </div>

        {buying && (
          <div style={{ marginTop: 10 }}>
            <div
              style={{
                fontSize: 11, marginBottom: 6,
                color: needReason ? COLOR.ink : COLOR.muted,
                fontWeight: needReason ? 700 : 400,
              }}
            >
              매수 근거를 하나 고르세요
            </div>
            <div className="flex flex-wrap gap-1.5">
              {REASONS.map((r) => {
                const active = reason === r
                return (
                  <button
                    key={r}
                    onClick={() => setReason(r)}
                    style={{
                      background: asking ? '#FDF3E2' : active ? COLOR.ink : COLOR.bg,
                      color: active ? COLOR.bg : COLOR.muted,
                      borderRadius: 20, padding: '7px 12px', fontSize: 12,
                      fontWeight: active ? 700 : 400,
                      display: 'flex', alignItems: 'center', gap: 4,
                      boxShadow: needReason
                        ? `inset 0 0 0 ${asking ? 2 : 1}px rgba(242,169,59,${asking ? 1 : 0.7})`
                        : 'none',
                      transition: 'box-shadow .15s, background .15s',
                    }}
                  >
                    {active && <IconCheck color={COLOR.bg} />}
                    {r}
                  </button>
                )
              })}
            </div>
          </div>
        )}

        {overLimit && (
          <div style={{ marginTop: 10, fontSize: 12, fontWeight: 700, color: COLOR.up }}>
            {limitedBy === '비중 상한'
              ? `한 종목에 총자산의 50% 까지만 담을 수 있습니다. 최대 ${maxBuy}주`
              : `현금이 모자랍니다. 최대 ${maxBuy}주`}
          </div>
        )}
        {message && (
          <div
            style={{
              marginTop: 10, fontSize: 12, fontWeight: 700,
              color: message.tone === 'ok' ? COLOR.ink : COLOR.up,
            }}
          >
            {message.text}
          </div>
        )}

        <button
          onClick={onSubmit}
          disabled={!canSubmit || busy}
          style={{
            width: '100%', marginTop: 12, borderRadius: 14, padding: '15px 0',
            fontSize: 15, fontWeight: 700,
            background: needReason ? COLOR.tag : buying ? COLOR.ink : COLOR.up,
            color: needReason ? COLOR.muted : COLOR.bg,
            opacity: !canSubmit || busy ? 0.45 : 1,
          }}
        >
          {needReason
            ? '매수 근거를 고르세요'
            : `${qty}주 ${buying ? '매수' : '매도'} 주문하기`}
        </button>
      </div>
    </div>
  )
}
