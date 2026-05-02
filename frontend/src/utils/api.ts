import axios from 'axios'

const BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000/api/v1'
const WS_URL = (import.meta.env.VITE_WS_URL || 'ws://localhost:8000/api/v1') + '/live/ws'

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
export { WS_URL }

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

// ─── Live Monitoring ─────────────────────────────────────────────────────────

export const liveApi = {
  getCameras: () => api.get('/live/cameras'),
  addCamera: (data: {
    camera_id: string
    name: string
    url: string
    source_type: string
    location?: string
    speed_limit?: number
    enabled?: boolean
  }) => api.post('/live/cameras', data),
  removeCamera: (cameraId: string) => api.delete(`/live/cameras/${cameraId}`),
  getStats: () => api.get('/live/stats'),
  getRecentViolations: (limit?: number, cameraId?: string) =>
    api.get('/live/recent-violations', { params: { limit, camera_id: cameraId } }),
  reportViolation: (data: {
    camera_id: string
    violation_type: string
    plate_number?: string
    confidence?: number
    speed?: number
    timestamp?: string
    location?: string
    frame_base64?: string
  }) => api.post('/live/violations/report', data),
  updateCameraStatus: (data: {
    camera_id: string
    status: string
    fps?: number
    message?: string
  }) => api.post('/live/cameras/status', data),
  getWsClientCount: () => api.get('/live/ws/count'),
}

// ─── WebSocket Helper ─────────────────────────────────────────────────────────

export class LiveMonitoringWebSocket {
  private ws: WebSocket | null = null
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null
  private pingTimer: ReturnType<typeof setInterval> | null = null
  private reconnectAttempts = 0
  private maxReconnectAttempts = 5
  private reconnectDelay = 3000
  private url: string

  onViolation: ((data: Record<string, unknown>) => void) | null = null
  onCameraStatus: ((data: Record<string, unknown>) => void) | null = null
  onConnected: (() => void) | null = null
  onDisconnected: (() => void) | null = null
  onError: ((err: Event) => void) | null = null

  constructor(wsUrl?: string) {
    this.url = wsUrl || WS_URL
  }

  connect() {
    if (this.ws?.readyState === WebSocket.OPEN) return
    try {
      this.ws = new WebSocket(this.url)

      this.ws.onopen = () => {
        this.reconnectAttempts = 0
        this.onConnected?.()
        // Start ping interval
        this.pingTimer = setInterval(() => {
          this.send({ type: 'ping' })
        }, 25000)
      }

      this.ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data)
          if (msg.type === 'violation') {
            this.onViolation?.(msg.data)
          } else if (msg.type === 'camera_status') {
            this.onCameraStatus?.(msg.data)
          }
        } catch {}
      }

      this.ws.onclose = () => {
        this._cleanup()
        this.onDisconnected?.()
        this._scheduleReconnect()
      }

      this.ws.onerror = (err) => {
        this.onError?.(err)
      }
    } catch {}
  }

  send(data: Record<string, unknown>) {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(data))
    }
  }

  disconnect() {
    this.maxReconnectAttempts = 0 // prevent auto reconnect
    this._cleanup()
    this.ws?.close()
    this.ws = null
  }

  private _cleanup() {
    if (this.pingTimer) {
      clearInterval(this.pingTimer)
      this.pingTimer = null
    }
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer)
      this.reconnectTimer = null
    }
  }

  private _scheduleReconnect() {
    if (this.reconnectAttempts >= this.maxReconnectAttempts) return
    this.reconnectTimer = setTimeout(() => {
      this.reconnectAttempts++
      this.connect()
    }, this.reconnectDelay * Math.min(this.reconnectAttempts + 1, 4))
  }

  get isConnected(): boolean {
    return this.ws?.readyState === WebSocket.OPEN
  }
}
