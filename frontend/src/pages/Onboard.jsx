import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, getPlayerToken, setPlayerToken } from '../lib/api'
import { COLOR } from '../theme'

/**
 * 닉네임 한 줄과 시작 버튼만 둔다.
 * 부스 회전율이 중요하므로 규칙 설명이나 튜토리얼은 넣지 않는다(스펙 15-1).
 */
export default function Onboard() {
  const [nickname, setNickname] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const navigate = useNavigate()
  const inputRef = useRef(null)

  useEffect(() => {
    // 기기 토큰으로 재접속이 확인되면 입력을 건너뛰고 바로 들어간다.
    if (!getPlayerToken()) {
      inputRef.current?.focus()
      return
    }
    api
      .state()
      .then((snap) => {
        if (snap?.me) navigate('/play', { replace: true })
      })
      .catch(() => {})
  }, [navigate])

  async function submit(event) {
    event.preventDefault()
    const name = nickname.trim()
    if (!name) {
      setError('닉네임을 입력해 주세요')
      return
    }
    setBusy(true)
    setError('')
    try {
      const res = await api.join(name)
      setPlayerToken(res.player_token)
      navigate('/play', { replace: true })
    } catch (err) {
      setError(err.message || '참가하지 못했습니다')
      inputRef.current?.select()
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="screen flex flex-col" style={{ paddingBottom: 24 }}>
      <div style={{ height: 96 }} />
      <h1 className="font-display" style={{ fontSize: 30, lineHeight: 1.25 }}>
        모의투자 리그
      </h1>
      <p style={{ fontSize: 13, color: COLOR.muted, marginTop: 8 }}>
        시작 자금 1,000만 원으로 30분 동안 겨룹니다
      </p>

      <form onSubmit={submit} style={{ marginTop: 28 }}>
        <label
          htmlFor="nickname"
          style={{ fontSize: 12, color: COLOR.muted, display: 'block' }}
        >
          닉네임
        </label>
        <input
          id="nickname"
          ref={inputRef}
          value={nickname}
          onChange={(e) => setNickname(e.target.value)}
          maxLength={12}
          autoComplete="off"
          placeholder="12자 이내"
          style={{
            width: '100%',
            marginTop: 8,
            background: COLOR.card,
            border: `1px solid ${error ? COLOR.up : COLOR.line}`,
            borderRadius: 14,
            padding: '15px 16px',
            fontSize: 16,
            outline: 'none',
          }}
        />
        {error && (
          <div style={{ color: COLOR.up, fontSize: 12, marginTop: 8 }}>{error}</div>
        )}
        <button
          type="submit"
          disabled={busy}
          style={{
            width: '100%',
            marginTop: 14,
            background: COLOR.ink,
            color: COLOR.bg,
            borderRadius: 14,
            padding: '15px 0',
            fontSize: 15,
            fontWeight: 700,
            opacity: busy ? 0.6 : 1,
          }}
        >
          {busy ? '참가하는 중' : '시작하기'}
        </button>
      </form>
    </div>
  )
}
