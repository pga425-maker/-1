/** 숫자 표시 규칙. 천 단위 콤마, 등락률은 소수 첫째 자리. */

export function won(value) {
  const n = Math.round(Number(value) || 0)
  return n.toLocaleString('ko-KR')
}

export function wonSuffix(value) {
  return `${won(value)}원`
}

export function signedWon(value) {
  const n = Math.round(Number(value) || 0)
  const mark = n > 0 ? '▲' : n < 0 ? '▼' : ''
  return `${mark} ${Math.abs(n).toLocaleString('ko-KR')}원`.trim()
}

export function pct(value, digits = 1) {
  const n = (Number(value) || 0) * 100
  const rounded = Number(n.toFixed(digits))
  if (rounded === 0) return `${(0).toFixed(digits)}%`
  const sign = rounded > 0 ? '+' : ''
  return `${sign}${rounded.toFixed(digits)}%`
}

export function signedPct(value, digits = 1) {
  const n = (Number(value) || 0) * 100
  const mark = n > 0 ? '▲' : n < 0 ? '▼' : ''
  return `${mark} ${Math.abs(n).toFixed(digits)}%`.trim()
}

export function clock(seconds) {
  const s = Math.max(0, Math.floor(Number(seconds) || 0))
  const m = Math.floor(s / 60)
  return `${String(m).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`
}

export function elapsedLabel(tick, currentTick, tickSeconds) {
  const seconds = Math.max(0, (currentTick - tick) * tickSeconds)
  if (seconds < 60) return '방금'
  return `${Math.floor(seconds / 60)}분 전`
}

export function deltaMark(value) {
  if (value > 0) return `▲${Math.abs(Math.round(value))}`
  if (value < 0) return `▼${Math.abs(Math.round(value))}`
  return '―'
}
