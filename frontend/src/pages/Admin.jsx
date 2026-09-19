import { useCallback, useEffect, useState } from 'react'
import { api, getAdminToken, setAdminToken } from '../lib/api'
import { COLOR } from '../theme'

/** 관리자 화면. 디자인보다 기능이 우선이다(스펙 15-9). */
export default function Admin() {
  const [authed, setAuthed] = useState(Boolean(getAdminToken()))
  const [password, setPassword] = useState('')
  const [scenarios, setScenarios] = useState([])
  const [picked, setPicked] = useState('festival_01.json')
  const [seed, setSeed] = useState('')
  const [speed, setSpeed] = useState('1')
  const [health, setHealth] = useState(null)
  const [log, setLog] = useState([])

  const say = useCallback((text) => {
    setLog((prev) => [`${new Date().toLocaleTimeString('ko-KR')} ${text}`, ...prev].slice(0, 12))
  }, [])

  const loadScenarios = useCallback(async () => {
    try {
      const res = await api.adminScenarios()
      setScenarios(res.scenarios)
    } catch (e) {
      say(`시나리오 목록 실패: ${e.message}`)
    }
  }, [say])

  useEffect(() => {
    if (!authed) return undefined
    loadScenarios()
    const timer = setInterval(
      () => api.adminHealth().then(setHealth).catch(() => {}),
      2000,
    )
    return () => clearInterval(timer)
  }, [authed, loadScenarios])

  async function login(e) {
    e.preventDefault()
    try {
      const res = await api.adminLogin(password)
      setAdminToken(res.admin_token)
      setAuthed(true)
      say('로그인 성공')
    } catch (err) {
      say(`로그인 실패: ${err.message}`)
    }
  }

  async function run(name, fn) {
    try {
      const res = await fn()
      say(`${name}: ${JSON.stringify(res)}`)
    } catch (e) {
      say(`${name} 실패: ${e.message}`)
    }
  }

  if (!authed) {
    return (
      <div className="screen" style={{ paddingTop: 80 }}>
        <h1 style={{ fontSize: 20, fontWeight: 700 }}>관리자</h1>
        <form onSubmit={login} style={{ marginTop: 16 }}>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="비밀번호"
            style={{
              width: '100%', background: COLOR.card, borderRadius: 12,
              border: `1px solid ${COLOR.line}`, padding: '13px 14px', fontSize: 15,
            }}
          />
          <button
            type="submit"
            style={{
              width: '100%', marginTop: 10, background: COLOR.ink, color: COLOR.bg,
              borderRadius: 12, padding: '13px 0', fontWeight: 700,
            }}
          >
            로그인
          </button>
        </form>
        {log.map((l) => (
          <div key={l} style={{ fontSize: 11, color: COLOR.muted, marginTop: 6 }}>
            {l}
          </div>
        ))}
      </div>
    )
  }

  const chosen = scenarios.find((s) => s.file === picked)

  return (
    <div style={{ maxWidth: 820, margin: '0 auto', padding: 20 }}>
      <h1 style={{ fontSize: 20, fontWeight: 700 }}>관리자</h1>

      <Panel title="시나리오">
        <div className="flex flex-col gap-1.5">
          {scenarios.map((s) => (
            <label
              key={s.file}
              className="flex items-start gap-2"
              style={{ fontSize: 13 }}
            >
              <input
                type="radio"
                name="scenario"
                checked={picked === s.file}
                onChange={() => setPicked(s.file)}
                disabled={!s.valid}
                style={{ marginTop: 3 }}
              />
              <span>
                <b>{s.name || s.file}</b>{' '}
                <span style={{ color: COLOR.muted }}>({s.file})</span>
                {!s.valid && (
                  <div style={{ color: COLOR.up, fontSize: 11 }}>{s.error}</div>
                )}
                {s.valid && (
                  <div style={{ color: COLOR.muted, fontSize: 11 }}>
                    {s.total_ticks}틱 · {s.tick_seconds}초 · 종목 {s.stocks} · 이벤트{' '}
                    {s.events}
                  </div>
                )}
                {s.warnings?.map((w) => (
                  <div key={w} style={{ color: '#9A6A18', fontSize: 11 }}>
                    경고: {w}
                  </div>
                ))}
              </span>
            </label>
          ))}
        </div>
        <button
          onClick={loadScenarios}
          style={{ fontSize: 12, color: COLOR.muted, marginTop: 8 }}
        >
          목록 새로고침
        </button>
      </Panel>

      <Panel title="진행">
        <div className="flex flex-wrap items-center gap-2">
          <input
            value={seed}
            onChange={(e) => setSeed(e.target.value)}
            placeholder="시드(비우면 임의)"
            style={inputStyle}
          />
          <input
            value={speed}
            onChange={(e) => setSpeed(e.target.value)}
            placeholder="배속"
            style={{ ...inputStyle, width: 90 }}
          />
          <Button
            tone="primary"
            onClick={() =>
              run('시작', () =>
                api.adminStart({
                  scenario_file: picked,
                  seed: seed ? Number(seed) : null,
                  speed: Number(speed) || 1,
                }),
              )
            }
            disabled={!chosen?.valid}
          >
            시작
          </Button>
          <Button onClick={() => run('일시정지', api.adminPause)}>일시정지</Button>
          <Button onClick={() => run('재개', api.adminResume)}>재개</Button>
          <Button tone="danger" onClick={() => run('종료', api.adminEnd)}>
            종료
          </Button>
        </div>
        <div style={{ fontSize: 11, color: COLOR.muted, marginTop: 8 }}>
          배속은 리허설 전용입니다. 본 라운드는 반드시 1로 둡니다
        </div>
      </Panel>

      <Panel title="상태">
        {health ? (
          <div style={{ fontSize: 12, lineHeight: 1.7 }}>
            <div>
              상태 <b>{health.status}</b> · 틱 {health.tick} · 배속 {health.speed}
            </div>
            <div>
              참가자 {health.players}명 · WebSocket {health.ws_connections}개 (끊김{' '}
              {health.ws_dropped})
            </div>
            <div>
              틱 처리 평균 {health.tick_duration_ms.mean.toFixed(1)}ms · 최대{' '}
              {health.tick_duration_ms.max.toFixed(1)}ms
            </div>
            <div>
              브로드캐스트 p95 {health.broadcast_latency_ms.p95.toFixed(1)}ms · p99{' '}
              {health.broadcast_latency_ms.p99.toFixed(1)}ms
            </div>
            {health.exceptions.length > 0 && (
              <div style={{ color: COLOR.up }}>
                예외 {health.exceptions.length}건: {health.exceptions[0]}
              </div>
            )}
          </div>
        ) : (
          <div style={{ fontSize: 12, color: COLOR.muted }}>불러오는 중</div>
        )}
      </Panel>

      <Panel title="참가자">
        <KickForm onKick={(id) => run('강제 퇴장', () => api.adminKick(id))} />
      </Panel>

      <Panel title="내보내기">
        <div className="flex flex-wrap gap-2">
          {['result', 'orders', 'tick_log', 'index_log', 'events_fired', 'players'].map(
            (t) => (
              <a
                key={t}
                href={api.adminExportUrl(t)}
                onClick={(e) => {
                  // 헤더 인증이 필요하므로 fetch 로 받아 내려받는다
                  e.preventDefault()
                  fetch(api.adminExportUrl(t), {
                    headers: { 'X-Admin-Token': getAdminToken() },
                  })
                    .then((r) => r.blob())
                    .then((blob) => {
                      const url = URL.createObjectURL(blob)
                      const a = document.createElement('a')
                      a.href = url
                      a.download = `${t}.csv`
                      a.click()
                      URL.revokeObjectURL(url)
                      say(`${t}.csv 내려받음`)
                    })
                    .catch((err) => say(`내보내기 실패: ${err.message}`))
                }}
                style={{
                  background: COLOR.card, border: `1px solid ${COLOR.line}`,
                  borderRadius: 10, padding: '9px 13px', fontSize: 12,
                }}
              >
                {t}.csv
              </a>
            ),
          )}
        </div>
      </Panel>

      <Panel title="기록">
        {log.map((l) => (
          <div key={l} style={{ fontSize: 11, color: COLOR.muted }}>
            {l}
          </div>
        ))}
      </Panel>
    </div>
  )
}

const inputStyle = {
  background: COLOR.card,
  border: `1px solid ${COLOR.line}`,
  borderRadius: 10,
  padding: '9px 12px',
  fontSize: 13,
  width: 150,
}

function Panel({ title, children }) {
  return (
    <section
      style={{
        background: COLOR.card, borderRadius: 14, padding: 16, marginTop: 12,
      }}
    >
      <h2 style={{ fontSize: 14, fontWeight: 700, marginBottom: 10 }}>{title}</h2>
      {children}
    </section>
  )
}

function Button({ children, onClick, tone, disabled }) {
  const styles = {
    primary: { background: COLOR.ink, color: COLOR.bg },
    danger: { background: COLOR.up, color: '#fff' },
    default: { background: COLOR.bg, color: COLOR.ink },
  }
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      style={{
        ...(styles[tone] || styles.default),
        borderRadius: 10, padding: '10px 16px', fontSize: 13, fontWeight: 700,
        opacity: disabled ? 0.4 : 1,
      }}
    >
      {children}
    </button>
  )
}

function KickForm({ onKick }) {
  const [id, setId] = useState('')
  return (
    <div className="flex items-center gap-2">
      <input
        value={id}
        onChange={(e) => setId(e.target.value)}
        placeholder="참가자 번호"
        style={inputStyle}
      />
      <Button tone="danger" onClick={() => id && onKick(Number(id))}>
        강제 퇴장
      </Button>
    </div>
  )
}
