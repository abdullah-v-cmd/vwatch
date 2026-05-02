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
export { WS_URL, BASE_URL }

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

// ─── Live Monitoring (WebSocket violation feed) ───────────────────────────────

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

// ─── Backend Camera Manager API (persistent server-side cameras) ──────────────

export const cameraApi = {
  /** List all backend cameras with live state */
  list: () => api.get('/cameras'),

  /** Register + start a camera in the backend */
  add: (data: {
    camera_id: string
    name: string
    source: string          // "0" | rtsp://... | http://...
    source_type: string     // "webcam" | "rtsp" | "http"
    location?: string
    speed_limit?: number
    enabled?: boolean
    auto_start?: boolean
  }) => api.post('/cameras', data),

  /** Start an existing camera's capture thread */
  start: (cameraId: string) => api.post(`/cameras/${cameraId}/start`),

  /** Stop a camera (config retained) */
  stop: (cameraId: string) => api.post(`/cameras/${cameraId}/stop`),

  /** Stop then restart a camera */
  restart: (cameraId: string) => api.post(`/cameras/${cameraId}/restart`),

  /** Permanently remove a camera */
  remove: (cameraId: string) => api.delete(`/cameras/${cameraId}`),

  /** Get single camera status */
  status: (cameraId: string) => api.get(`/cameras/${cameraId}/status`),

  /** Full system summary */
  systemStatus: () => api.get('/cameras/system/status'),

  /**
   * Returns the MJPEG stream URL for a camera.
   * Embed directly as: <img src={cameraApi.streamUrl("cam_001")} />
   * The stream is served by the backend and survives page navigation.
   */
  streamUrl: (cameraId: string) => `${BASE_URL}/cameras/stream/${cameraId}`,

  /**
   * Returns the WebSocket URL for per-camera base64 frame push.
   */
  wsUrl: (cameraId: string) => {
    const wsBase = import.meta.env.VITE_WS_URL || 'ws://localhost:8000/api/v1'
    return `${wsBase}/cameras/ws/${cameraId}`
  },
}

// ─── WebSocket Helper (global /live/ws feed) ──────────────────────────────────

export class LiveMonitoringWebSocket {
  private ws: WebSocket | null = null
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null
  private pingTimer: ReturnType<typeof setInterval> | null = null
  private reconnectAttempts = 0
  private maxReconnectAttempts = 99   // essentially infinite
  private reconnectDelay = 3000
  private url: string
  private _destroyed = false

  onViolation: ((data: Record<string, unknown>) => void) | null = null
  onCameraStatus: ((data: Record<string, unknown>) => void) | null = null
  onCameraState: ((data: Record<string, unknown>) => void) | null = null
  onCameraList: ((cameras: unknown[]) => void) | null = null
  onCameraAdded: ((data: Record<string, unknown>) => void) | null = null
  onCameraRemoved: ((data: Record<string, unknown>) => void) | null = null
  onConnected: ((data?: Record<string, unknown>) => void) | null = null
  onDisconnected: (() => void) | null = null
  onError: ((err: Event) => void) | null = null

  constructor(wsUrl?: string) {
    this.url = wsUrl || WS_URL
  }

  connect() {
    if (this._destroyed) return
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
        // Request current camera list immediately
        this.send({ type: 'get_cameras' })
      }

      this.ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data)
          switch (msg.type) {
            case 'connected':
              this.onConnected?.(msg)
              if (Array.isArray(msg.cameras)) {
                this.onCameraList?.(msg.cameras)
              }
              break
            case 'violation':
              this.onViolation?.(msg.data)
              break
            case 'camera_status':
              this.onCameraStatus?.(msg.data)
              break
            case 'camera_state':
              this.onCameraState?.(msg.data)
              break
            case 'camera_list':
              if (Array.isArray(msg.cameras)) {
                this.onCameraList?.(msg.cameras)
              }
              break
            case 'camera_added':
              this.onCameraAdded?.(msg.data)
              break
            case 'camera_removed':
              this.onCameraRemoved?.(msg.data)
              break
          }
        } catch { /* ignore parse errors */ }
      }

      this.ws.onclose = () => {
        this._cleanup()
        this.onDisconnected?.()
        if (!this._destroyed) this._scheduleReconnect()
      }

      this.ws.onerror = (err) => {
        this.onError?.(err)
      }
    } catch { /* ignore */ }
  }

  send(data: Record<string, unknown>) {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(data))
    }
  }

  /** Permanently close – no auto-reconnect. Call only on app unmount. */
  destroy() {
    this._destroyed = true
    this._cleanup()
    this.ws?.close()
    this.ws = null
  }

  private _cleanup() {
    if (this.pingTimer) { clearInterval(this.pingTimer); this.pingTimer = null }
    if (this.reconnectTimer) { clearTimeout(this.reconnectTimer); this.reconnectTimer = null }
  }

  private _scheduleReconnect() {
    if (this.reconnectAttempts >= this.maxReconnectAttempts) return
    const delay = this.reconnectDelay * Math.min(this.reconnectAttempts + 1, 5)
    this.reconnectTimer = setTimeout(() => {
      this.reconnectAttempts++
      this.connect()
    }, delay)
  }

  get isConnected(): boolean {
    return this.ws?.readyState === WebSocket.OPEN
  }
}

// Singleton WebSocket – lives for the entire app session (not per-page)
let _globalWs: LiveMonitoringWebSocket | null = null

export function getGlobalWs(): LiveMonitoringWebSocket {
  if (!_globalWs) {
    _globalWs = new LiveMonitoringWebSocket()
    _globalWs.connect()
  }
  return _globalWs
}
