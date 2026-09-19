/**
 * 스펙 14번 디자인 토큰의 단일 출처.
 * 검증을 마친 값이라 임의로 바꾸지 않는다. Tailwind 설정과 값이 같아야 한다.
 */
export const COLOR = {
  bg: '#FBF7F0',
  card: '#FFFFFF',
  ink: '#211A2E',
  muted: '#5C5548',
  point: '#F2A93B',
  up: '#C13B3B',
  down: '#3457B2',
  tag: '#EFEAE0',
  line: 'rgba(33,26,46,0.08)',
}

/** 상승은 빨강, 하락은 파랑. 국내 증권 관례를 따른다. */
export function signColor(value) {
  if (value > 0) return COLOR.up
  if (value < 0) return COLOR.down
  return COLOR.muted
}

export const FONT = {
  display: "'Black Han Sans', 'Noto Sans KR', sans-serif",
  body: "'Noto Sans KR', sans-serif",
}

/** 시나리오가 주지 않는 종목은 이 톤 안에서 고른다. */
export const FALLBACK_AVATAR = '#5C5548'
