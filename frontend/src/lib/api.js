/** 백엔드 호출. 같은 오리진이라 baseURL 이 필요 없다(결정-12). */

const TOKEN_KEY = 'festival.playerToken'
const DEVICE_KEY = 'festival.deviceToken'
const ADMIN_KEY = 'festival.adminToken'

export function getPlayerToken() {
  return localStorage.getItem(TOKEN_KEY) || ''
}

export function setPlayerToken(token) {
  localStorage.setItem(TOKEN_KEY, token)
}

export function clearPlayerToken() {
  localStorage.removeItem(TOKEN_KEY)
}

/** 기기 토큰. 새로고침하거나 실수로 창을 닫아도 같은 참가자로 복구된다. */
export function getDeviceToken() {
  let token = localStorage.getItem(DEVICE_KEY)
  if (!token) {
    const bytes = new Uint8Array(16)
    crypto.getRandomValues(bytes)
    token = Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')
    localStorage.setItem(DEVICE_KEY, token)
  }
  return token
}

export function getAdminToken() {
  return sessionStorage.getItem(ADMIN_KEY) || ''
}

export function setAdminToken(token) {
  sessionStorage.setItem(ADMIN_KEY, token)
}

async function request(path, { method = 'GET', body, admin = false } = {}) {
  const headers = {}
  if (body) headers['Content-Type'] = 'application/json'
  const token = admin ? getAdminToken() : getPlayerToken()
  if (token) headers[admin ? 'X-Admin-Token' : 'X-Player-Token'] = token

  const res = await fetch(path, {
    method,
    headers,
    body: body ? JSON.stringify(body) : undefined,
  })
  const text = await res.text()
  let data = null
  try {
    data = text ? JSON.parse(text) : null
  } catch {
    data = { detail: text }
  }
  if (!res.ok) {
    const error = new Error(data?.detail || data?.reject_code || `오류 ${res.status}`)
    error.status = res.status
    error.rejectCode = data?.reject_code
    error.payload = data
    throw error
  }
  return data
}

export const api = {
  join: (nickname) =>
    request('/api/join', {
      method: 'POST',
      body: { nickname, device_token: getDeviceToken() },
    }),
  state: () => request('/api/state'),
  order: (payload) => request('/api/order', { method: 'POST', body: payload }),
  orders: () => request('/api/orders'),
  rank: (tab) => request(`/api/rank?tab=${tab}`),
  result: () => request('/api/result'),
  meta: () => request('/api/meta'),

  adminLogin: (password) =>
    request('/api/admin/login', { method: 'POST', body: { password } }),
  adminScenarios: () => request('/api/admin/scenarios', { admin: true }),
  adminStart: (payload) =>
    request('/api/admin/start', { method: 'POST', body: payload, admin: true }),
  adminPause: () => request('/api/admin/pause', { method: 'POST', admin: true }),
  adminResume: () => request('/api/admin/resume', { method: 'POST', admin: true }),
  adminEnd: () => request('/api/admin/end', { method: 'POST', admin: true }),
  adminKick: (playerId) =>
    request('/api/admin/kick', {
      method: 'POST',
      body: { player_id: playerId },
      admin: true,
    }),
  adminHealth: () => request('/api/admin/health', { admin: true }),
  adminExportUrl: (table) => `/api/admin/export?table=${table}`,
}
