import { create } from 'zustand'

interface AuthState {
  accessToken: string | null
  refreshToken: string | null
  username: string | null
  role: string | null
  setTokens: (access: string, refresh: string) => void
  setUser: (username: string, role: string) => void
  logout: () => void
}

export const useAuthStore = create<AuthState>((set) => ({
  accessToken: localStorage.getItem('access_token'),
  refreshToken: localStorage.getItem('refresh_token'),
  username: localStorage.getItem('username'),
  role: localStorage.getItem('role'),

  setTokens: (access, refresh) => {
    localStorage.setItem('access_token', access)
    localStorage.setItem('refresh_token', refresh)
    set({ accessToken: access, refreshToken: refresh })
  },

  setUser: (username, role) => {
    localStorage.setItem('username', username)
    localStorage.setItem('role', role)
    set({ username, role })
  },

  logout: () => {
    localStorage.removeItem('access_token')
    localStorage.removeItem('refresh_token')
    localStorage.removeItem('username')
    localStorage.removeItem('role')
    set({ accessToken: null, refreshToken: null, username: null, role: null })
  },
}))
