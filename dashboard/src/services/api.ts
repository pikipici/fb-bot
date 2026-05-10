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
      if (retry.status === 204) return null
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
  if (res.status === 204) return null
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

  // Sources (scan targets: home_feed | group | page)
  listSources: (enabledOnly = false) =>
    request(`/sources?enabled_only=${enabledOnly}`),

  createSource: (data: {
    type: string
    label: string
    url?: string | null
    fb_entity_id?: string | null
    keywords_include?: string[]
    keywords_exclude?: string[]
    enabled?: boolean
  }) =>
    request('/sources', {
      method: 'POST',
      body: JSON.stringify(data),
    }),

  updateSource: (
    id: number,
    data: {
      label?: string
      url?: string | null
      fb_entity_id?: string | null
      keywords_include?: string[]
      keywords_exclude?: string[]
      enabled?: boolean
    },
  ) =>
    request(`/sources/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    }),

  toggleSource: (id: number) =>
    request(`/sources/${id}/toggle`, { method: 'POST' }),

  deleteSource: (id: number) =>
    request(`/sources/${id}`, { method: 'DELETE' }),

  // Trending posts (read-only feed from the scanner)
  listTrending: (params: {
    status?: string
    source_id?: number
    sort?: 'score' | 'velocity' | 'recent'
    limit?: number
  } = {}) => {
    const q = new URLSearchParams()
    if (params.status) q.set('status', params.status)
    if (params.source_id != null) q.set('source_id', String(params.source_id))
    if (params.sort) q.set('sort', params.sort)
    if (params.limit != null) q.set('limit', String(params.limit))
    const qs = q.toString()
    return request(`/trending${qs ? `?${qs}` : ''}`)
  },

  // Comment template (single active row MVP)
  getTemplate: () => request('/template'),

  upsertTemplate: (templateText: string) =>
    request('/template', {
      method: 'PUT',
      body: JSON.stringify({ template_text: templateText }),
    }),

  generateDraft: (postId: number) =>
    request(`/trending/${postId}/draft`, { method: 'POST' }),

  generateAIDraft: (postId: number) =>
    request(`/trending/${postId}/ai-draft`, { method: 'POST' }),

  skipTrendingPost: (postId: number) =>
    request(`/trending/${postId}/skip`, { method: 'POST' }),

  sendComment: (postId: number, commentText: string) =>
    request(`/trending/${postId}/comment`, {
      method: 'POST',
      body: JSON.stringify({ comment_text: commentText }),
    }),

  getRateLimitStatus: () => request('/rate-limit/status'),

  // Comment history (Layer 2 audit trail)
  listHistory: (params: {
    status?: 'SENT' | 'FAILED' | 'PENDING'
    limit?: number
    offset?: number
  } = {}) => {
    const q = new URLSearchParams()
    if (params.status) q.set('status', params.status)
    if (params.limit != null) q.set('limit', String(params.limit))
    if (params.offset != null) q.set('offset', String(params.offset))
    const qs = q.toString()
    return request(`/history${qs ? `?${qs}` : ''}`)
  },

  // Scanner (audit trail + manual trigger)
  getScannerStatus: () => request('/scanner/status'),

  runScanNow: () =>
    request('/scanner/run-now', { method: 'POST' }),
}
