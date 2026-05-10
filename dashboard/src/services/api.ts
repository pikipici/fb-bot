const API_BASE = '/api/v1'

async function request(path: string, options: RequestInit = {}) {
  const token = localStorage.getItem('access_token')
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(options.headers as Record<string, string>),
  }
  if (token) {
    headers['Authorization'] = `Bearer ${token}`
  }

  const res = await fetch(`${API_BASE}${path}`, { ...options, headers })

  if (res.status === 401) {
    // Try refresh
    const refreshed = await tryRefresh()
    if (refreshed) {
      headers['Authorization'] = `Bearer ${localStorage.getItem('access_token')}`
      const retry = await fetch(`${API_BASE}${path}`, { ...options, headers })
      if (!retry.ok) throw new Error(`HTTP ${retry.status}`)
      return retry.json()
    }
    // Refresh failed, logout
    localStorage.clear()
    window.location.href = '/login'
    throw new Error('Unauthorized')
  }

  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || `HTTP ${res.status}`)
  }
  return res.json()
}

async function tryRefresh(): Promise<boolean> {
  const refreshToken = localStorage.getItem('refresh_token')
  if (!refreshToken) return false

  try {
    const res = await fetch(`${API_BASE}/auth/refresh`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: refreshToken }),
    })
    if (!res.ok) return false
    const data = await res.json()
    localStorage.setItem('access_token', data.access_token)
    localStorage.setItem('refresh_token', data.refresh_token)
    return true
  } catch {
    return false
  }
}

export const api = {
  login: (username: string, password: string) =>
    request('/auth/login', {
      method: 'POST',
      body: JSON.stringify({ username, password }),
    }),

  getMe: () => request('/auth/me'),

  getPendingDrafts: (limit = 20, offset = 0) =>
    request(`/drafts/pending?limit=${limit}&offset=${offset}`),

  approveDraft: (draftId: number, action: string, reason?: string, editedText?: string) =>
    request(`/approvals/${draftId}`, {
      method: 'POST',
      body: JSON.stringify({ action, reason, edited_text: editedText }),
    }),

  getStats: () => request('/stats/summary'),

  // FB Accounts (single-account system)
  getFBAccounts: (includeDisabled = false) =>
    request(`/fb-accounts?include_disabled=${includeDisabled}`),

  getCurrentFBAccount: () => request('/fb-accounts/current'),

  createFBAccount: (data: { label: string; email: string; password: string; purpose?: string; notes?: string }) =>
    request('/fb-accounts', {
      method: 'POST',
      body: JSON.stringify(data),
    }),

  updateFBAccount: (id: number, data: { label?: string; email?: string; password?: string; purpose?: string; notes?: string; status?: string }) =>
    request(`/fb-accounts/${id}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    }),

  deleteFBAccount: (id: number) =>
    request(`/fb-accounts/${id}`, { method: 'DELETE' }),

  reactivateFBAccount: (id: number) =>
    request(`/fb-accounts/${id}/reactivate`, { method: 'POST' }),

  previewFBCookie: (rawCookies: string) =>
    request('/fb-accounts/preview-cookie', {
      method: 'POST',
      body: JSON.stringify({ raw_cookies: rawCookies }),
    }),

  connectFBCookie: (data: { label: string; raw_cookies: string; notes?: string }) =>
    request('/fb-accounts/connect-cookie', {
      method: 'POST',
      body: JSON.stringify(data),
    }),
}
