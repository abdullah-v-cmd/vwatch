import { create } from 'zustand'

interface User {
  id: number
  email: string
  username: string
  full_name: string
  role: 'admin' | 'traffic_police' | 'viewer'
  badge_number?: string
  is_active: boolean
}

interface AuthState {
  user: User | null
  accessToken: string | null
  isAuthenticated: boolean
  setAuth: (user: User, token: string, refreshToken: string) => void
  clearAuth: () => void
  updateUser: (updates: Partial<User>) => void
}

// Simple state without zustand if not available
let state: AuthState | null = null

const createStore = () => {
  try {
    const { create } = require('zustand')
    return create<AuthState>((set) => ({
      user: JSON.parse(localStorage.getItem('user') || 'null'),
      accessToken: localStorage.getItem('access_token'),
      isAuthenticated: !!localStorage.getItem('access_token'),

      setAuth: (user, token, refreshToken) => {
        localStorage.setItem('access_token', token)
        localStorage.setItem('refresh_token', refreshToken)
        localStorage.setItem('user', JSON.stringify(user))
        set({ user, accessToken: token, isAuthenticated: true })
      },

      clearAuth: () => {
        localStorage.removeItem('access_token')
        localStorage.removeItem('refresh_token')
        localStorage.removeItem('user')
        set({ user: null, accessToken: null, isAuthenticated: false })
      },

      updateUser: (updates) =>
        set((state) => {
          const updated = state.user ? { ...state.user, ...updates } : null
          if (updated) localStorage.setItem('user', JSON.stringify(updated))
          return { user: updated }
        }),
    }))
  } catch {
    return null
  }
}

export const useAuthStore = create<AuthState>((set) => ({
  user: JSON.parse(localStorage.getItem('user') || 'null'),
  accessToken: localStorage.getItem('access_token'),
  isAuthenticated: !!localStorage.getItem('access_token'),

  setAuth: (user, token, refreshToken) => {
    localStorage.setItem('access_token', token)
    localStorage.setItem('refresh_token', refreshToken)
    localStorage.setItem('user', JSON.stringify(user))
    set({ user, accessToken: token, isAuthenticated: true })
  },

  clearAuth: () => {
    localStorage.removeItem('access_token')
    localStorage.removeItem('refresh_token')
    localStorage.removeItem('user')
    set({ user: null, accessToken: null, isAuthenticated: false })
  },

  updateUser: (updates) =>
    set((state) => {
      const updated = state.user ? { ...state.user, ...updates } : null
      if (updated) localStorage.setItem('user', JSON.stringify(updated))
      return { user: updated }
    }),
}))
