import axios from 'axios'

const BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000/api/v1'

const api = axios.create({
  baseURL: BASE_URL,
  headers: { 'Content-Type': 'application/json' },
  timeout: 30000,
})

// Attach JWT token to every request
api.interceptors.request.use((config) => {
  const token = localStorage.getItem('access_token')
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

// Handle 401 – redirect to login
api.interceptors.response.use(
  (response) => response,
  async (error) => {
    if (error.response?.status === 401) {
      localStorage.removeItem('access_token')
      localStorage.removeItem('refresh_token')
      localStorage.removeItem('user')
      window.location.href = '/login'
    }
    return Promise.reject(error)
  }
)

export default api

// ─── Auth ────────────────────────────────────────────────────────────────────

export const authApi = {
  login: (username: string, password: string) =>
    api.post('/auth/login', { username, password }),
  me: () => api.get('/auth/me'),
  changePassword: (current: string, newPwd: string) =>
    api.post('/auth/change-password', {
      current_password: current,
      new_password: newPwd,
    }),
  logout: () => api.post('/auth/logout'),
}

// ─── Violations ───────────────────────────────────────────────────────────────

export const violationsApi = {
  list: (params?: Record<string, unknown>) => api.get('/violations', { params }),
  get: (id: number) => api.get(`/violations/${id}`),
  stats: () => api.get('/violations/stats'),
  approve: (id: number, remarks?: string, fine?: number) =>
    api.post(`/violations/${id}/approve`, { status: 'approved', remarks, fine_amount: fine }),
  reject: (id: number, remarks?: string) =>
    api.post(`/violations/${id}/reject`, { status: 'rejected', remarks }),
  verifyIntegrity: (id: number) => api.post(`/violations/${id}/verify-integrity`),
}

// ─── Users ────────────────────────────────────────────────────────────────────

export const usersApi = {
  list: (params?: Record<string, unknown>) => api.get('/users', { params }),
  get: (id: number) => api.get(`/users/${id}`),
  create: (data: Record<string, unknown>) => api.post('/users', data),
  update: (id: number, data: Record<string, unknown>) => api.patch(`/users/${id}`, data),
  delete: (id: number) => api.delete(`/users/${id}`),
}

// ─── Config ───────────────────────────────────────────────────────────────────

export const configApi = {
  list: () => api.get('/config'),
  get: (key: string) => api.get(`/config/${key}`),
  upsert: (data: { key: string; value: string; description?: string }) =>
    api.put('/config', data),
  delete: (key: string) => api.delete(`/config/${key}`),
}

// ─── Audit Logs ───────────────────────────────────────────────────────────────

export const auditApi = {
  list: (params?: Record<string, unknown>) => api.get('/audit-logs', { params }),
}
