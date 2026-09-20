import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, clearPlayerToken } from '../lib/api'
import { COLOR, signColor } from '../theme'
import { pct, signedWon, won } from '../lib/format'
import { Card, Empty } from '../components/common'
import { Loading } from './Play'

const FACTOR_LABEL = {
  base_drift: '종목 자체 흐름',
  event_delta: '뉴스 이벤트',
  fear_shock: '공포지수',
  fx_shock: '환율',
}

export default function Result() {
  const [data, setData] = useState(null)
  const [error, setError] = useState('')

  useEffect(() => {
    api.result().then(setData).catch((e) => setError(e.message))
  }, [])

  if (error) return <div className="screen"><Empty>{error}</Empty></div>
  if (!data) return <Loading />

  const me = data.me
  const contributions = Object.entries(data.decomposition.contributions)
  const maxAbs = Math.max(1, ...contributions.map(([, v]) => Math.abs(v)))

  return (
    <div className="screen" style={{ paddingBottom: 40 }}>
      <div style={{ paddingTop: 22 }}>
        <div style={{ fontSize: 13, color: COLOR.muted }}>{me?.nickname} 님의 결과</div>
        <div className="font-display" style={{ fontSize: 32, marginTop: 2 }}>
          {me ? `${me.rank_return}위` : '―'}
          <span style={{ fontSize: 15, color: COLOR.muted, marginLeft: 8 }}>
            / {me?.players_count}명
          </span>
        </div>
      </div>

      <Card style={{ marginTop: 14, borderRadius: 20, padding: 20 }}>
        <div className="flex items-baseline justify-between">
          <span style={{ fontSize: 12, color: COLOR.muted }}>최종 자산</span>
          <span className="font-display" style={{ fontSize: 24 }}>
            {won(me?.final_asset)}
          </span>
        </div>
        <div
          className="flex items-baseline justify-between"
          style={{ marginTop: 8 }}
        >
          <span style={{ fontSize: 12, color: COLOR.muted }}>수익률</span>
          <span
            style={{ fontSize: 16, fontWeight: 700, color: signColor(me?.return_pct) }}
          >
            {pct(me?.return_pct)}
          </span>
        </div>
        <div className="grid grid-cols-3 gap-2" style={{ marginTop: 14 }}>
          <Metric label="위험조정" value={me?.risk_adjusted?.toFixed(3) ?? '―'} />
          <Metric
            label="최대낙폭"
            value={me ? `${(me.mdd * 100).toFixed(1)}%` : '―'}
          />
          <Metric
            label="종합 순위"
            value={me?.rank_total ? `${me.rank_total}위` : '집계 제외'}
          />
        </div>
        {me && !me.eligible_for_risk && (
          <div style={{ fontSize: 11, color: COLOR.muted, marginTop: 10 }}>
            참여 시간이 짧아 위험조정과 종합 순위에서는 제외되었습니다
          </div>
        )}
      </Card>

      <Section title="수익이 어디서 왔는가">
        <Card>
          {contributions.map(([key, value]) => (
            <div key={key} style={{ padding: '7px 0' }}>
              <div className="flex items-center justify-between">
                <span style={{ fontSize: 12 }}>{FACTOR_LABEL[key] || key}</span>
                <span
                  style={{ fontSize: 12, fontWeight: 700, color: signColor(value) }}
                >
                  {signedWon(value)}
                </span>
              </div>
              <div
                style={{
                  height: 6, borderRadius: 3, background: COLOR.tag, marginTop: 5,
                  position: 'relative', overflow: 'hidden',
                }}
              >
                <div
                  style={{
                    position: 'absolute', left: '50%', top: 0, bottom: 0,
                    width: `${(Math.abs(value) / maxAbs) * 50}%`,
                    background: signColor(value),
                    transform: value < 0 ? 'translateX(-100%)' : 'none',
                  }}
                />
              </div>
            </div>
          ))}
          <div style={{ fontSize: 11, color: COLOR.muted, marginTop: 8 }}>
            {data.decomposition.note}
          </div>
        </Card>
      </Section>

      <Section title="매수 근거별 성적">
        <Card>
          {data.reasons.length === 0 && (
            <div style={{ fontSize: 12, color: COLOR.muted }}>매수 기록이 없습니다</div>
          )}
          {data.reasons.map((r) => (
            <div
              key={r.reason}
              className="flex items-center justify-between"
              style={{ padding: '8px 0', borderBottom: `1px solid ${COLOR.line}` }}
            >
              <div>
                <div style={{ fontSize: 13, fontWeight: 700 }}>{r.reason}</div>
                <div style={{ fontSize: 11, color: COLOR.muted }}>
                  {r.count}번 · {won(r.invested)}원 투입
                </div>
              </div>
              <span
                style={{ fontSize: 14, fontWeight: 700, color: signColor(r.avg_return) }}
              >
                {pct(r.avg_return)}
              </span>
            </div>
          ))}
        </Card>
      </Section>

      <Section title="투자 성향 진단">
        <Card>
          <div className="flex items-center justify-between">
            <span style={{ fontSize: 12, color: COLOR.muted }}>내가 고른 근거</span>
            <span style={{ fontSize: 13, fontWeight: 700 }}>
              {data.diagnosis.declared || '―'}
            </span>
          </div>
          <div
            className="flex items-center justify-between"
            style={{ marginTop: 8 }}
          >
            <span style={{ fontSize: 12, color: COLOR.muted }}>실제 매매 패턴</span>
            <span style={{ fontSize: 13, fontWeight: 700 }}>
              {data.diagnosis.behavior.label}
            </span>
          </div>
          {data.diagnosis.has_gap && (
            <div
              style={{
                marginTop: 12, background: COLOR.bg, borderRadius: 12,
                padding: '10px 12px', fontSize: 12, lineHeight: 1.5,
              }}
            >
              {data.diagnosis.gap_message}
            </div>
          )}
          <div style={{ marginTop: 14 }}>
            <div style={{ fontSize: 11, color: COLOR.muted, marginBottom: 6 }}>
              오늘 대회 유형별 평균 수익률
            </div>
            {data.type_averages.map((t) => (
              <div
                key={t.type}
                className="flex items-center justify-between"
                style={{ padding: '5px 0' }}
              >
                <span style={{ fontSize: 12 }}>
                  {t.label}
                  <span style={{ color: COLOR.muted }}> {t.count}명</span>
                </span>
                <span
                  style={{
                    fontSize: 12, fontWeight: 700, color: signColor(t.avg_return),
                  }}
                >
                  {pct(t.avg_return)}
                </span>
              </div>
            ))}
          </div>
        </Card>
      </Section>

      <Section title="군중을 따라갔을 때">
        <Card>
          {data.crowd.count === 0 && (
            <div style={{ fontSize: 12, color: COLOR.muted }}>
              이번 판에는 뚜렷한 매수 쏠림이 없었습니다
            </div>
          )}
          {data.crowd.moments.map((m) => (
            <div
              key={`${m.start_tick}-${m.symbol}`}
              style={{ padding: '8px 0', borderBottom: `1px solid ${COLOR.line}` }}
            >
              <div style={{ fontSize: 12, fontWeight: 700 }}>
                {m.symbol} 쏠림 ({m.follower_count}명 참여)
              </div>
              <div className="flex items-center justify-between" style={{ marginTop: 4 }}>
                <span style={{ fontSize: 11, color: COLOR.muted }}>따라간 사람</span>
                <span
                  style={{
                    fontSize: 12, fontWeight: 700,
                    color: signColor(m.follower_avg_return),
                  }}
                >
                  {pct(m.follower_avg_return)}
                </span>
              </div>
              <div className="flex items-center justify-between">
                <span style={{ fontSize: 11, color: COLOR.muted }}>안 따라간 사람</span>
                <span
                  style={{
                    fontSize: 12, fontWeight: 700,
                    color: signColor(m.other_avg_return),
                  }}
                >
                  {pct(m.other_avg_return)}
                </span>
              </div>
            </div>
          ))}
        </Card>
      </Section>

      <Section title="공매도 집중 구간">
        <Card>
          {data.short_pressure.map((s) => (
            <div
              key={s.event_key}
              style={{ padding: '8px 0', borderBottom: `1px solid ${COLOR.line}` }}
            >
              <div style={{ fontSize: 12, fontWeight: 700 }}>{s.headline}</div>
              <div style={{ fontSize: 11, color: COLOR.muted, marginTop: 3 }}>
                펌프 {pct(s.pump_realized_pct)} 뒤 덤프 {pct(s.dump_realized_pct)}
              </div>
              <div className="flex items-center justify-between" style={{ marginTop: 6 }}>
                <span style={{ fontSize: 11, color: COLOR.muted }}>
                  펌프 구간에 산 사람 {s.buyer_count}명
                </span>
                <span
                  style={{
                    fontSize: 12, fontWeight: 700,
                    color: signColor(s.buyer_avg_return),
                  }}
                >
                  {pct(s.buyer_avg_return)}
                </span>
              </div>
              <div className="flex items-center justify-between">
                <span style={{ fontSize: 11, color: COLOR.muted }}>사지 않은 사람</span>
                <span
                  style={{
                    fontSize: 12, fontWeight: 700,
                    color: signColor(s.other_avg_return),
                  }}
                >
                  {pct(s.other_avg_return)}
                </span>
              </div>
              <div style={{ fontSize: 11, color: COLOR.muted, marginTop: 4 }}>
                덤프 전에 빠져나온 비율 {(s.escape_ratio * 100).toFixed(0)}%
              </div>
            </div>
          ))}
        </Card>
      </Section>

      <Link
        to="/onboard"
        onClick={clearPlayerToken}
        className="flex items-center justify-center"
        style={{
          marginTop: 22, background: COLOR.ink, color: COLOR.bg, borderRadius: 14,
          padding: '15px 0', fontSize: 15, fontWeight: 700,
        }}
      >
        다시 하기
      </Link>
    </div>
  )
}

function Metric({ label, value }) {
  return (
    <div style={{ background: COLOR.bg, borderRadius: 12, padding: '10px 8px' }}>
      <div style={{ fontSize: 11, color: COLOR.muted }}>{label}</div>
      <div style={{ fontSize: 14, fontWeight: 700, marginTop: 2 }}>{value}</div>
    </div>
  )
}

function Section({ title, children }) {
  return (
    <div style={{ marginTop: 18 }}>
      <div style={{ fontSize: 14, fontWeight: 700, marginBottom: 8 }}>{title}</div>
      {children}
    </div>
  )
}
