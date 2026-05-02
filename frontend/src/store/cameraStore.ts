/**
 * V-Watch - Global Camera Store (Zustand)
 * =========================================
 * Persists camera list and state GLOBALLY so it survives React page navigation.
 * When the user navigates away from /live-monitoring and returns, the camera
 * list + violation feed is exactly as they left it.
 *
 * The actual video capture runs 100% in the backend. This store only tracks
 * UI state: which cameras are registered, their last-known backend state, and
 * the live violation feed received over WebSocket.
 */

import { create } from 'zustand'
import { persist } from 'zustand/middleware'

// ── Types ─────────────────────────────────────────────────────────────────────

export type CameraState = 'idle' | 'starting' | 'running' | 'error' | 'stopping' | 'stopped' | 'unknown'

export interface BackendCamera {
  camera_id: string
  name: string
  source: string
  source_type: 'webcam' | 'rtsp' | 'http'
  location: string
  speed_limit: number
  enabled: boolean
  state: CameraState
  fps: number
  frame_count: number
  error_message: string
  started_at: string | null
  last_frame_at: string | null
  violation_count: number
  has_stream: boolean
  stream_url?: string
  ws_url?: string
}

export interface LiveViolation {
  id: string
  camera_id: string
  camera_name: string
  violation_type: string
  plate_number: string
  confidence: number
  location: string
  timestamp: string
  saved_to_db: boolean
  db_id?: number
}

interface CameraStore {
  // Camera list (synced from backend)
  cameras: BackendCamera[]
  setCameras: (cameras: BackendCamera[]) => void
  updateCamera: (camera_id: string, updates: Partial<BackendCamera>) => void
  addCamera: (cam: BackendCamera) => void
  removeCamera: (camera_id: string) => void

  // Live violation feed (last 200)
  violations: LiveViolation[]
  addViolation: (v: LiveViolation) => void
  clearViolations: () => void

  // UI state
  selectedCameraId: string | null
  setSelectedCamera: (id: string | null) => void

  wsConnected: boolean
  setWsConnected: (v: boolean) => void

  detectionEnabled: boolean
  setDetectionEnabled: (v: boolean) => void

  totalDetections: number
  incrementDetections: () => void
  resetDetections: () => void
}

// ── Store ─────────────────────────────────────────────────────────────────────

export const useCameraStore = create<CameraStore>()(
  persist(
    (set) => ({
      cameras: [],
      setCameras: (cameras) => set({ cameras }),
      updateCamera: (camera_id, updates) =>
        set((s) => ({
          cameras: s.cameras.map((c) =>
            c.camera_id === camera_id ? { ...c, ...updates } : c
          ),
        })),
      addCamera: (cam) =>
        set((s) => ({
          cameras: s.cameras.some((c) => c.camera_id === cam.camera_id)
            ? s.cameras.map((c) => (c.camera_id === cam.camera_id ? { ...c, ...cam } : c))
            : [...s.cameras, cam],
        })),
      removeCamera: (camera_id) =>
        set((s) => ({
          cameras: s.cameras.filter((c) => c.camera_id !== camera_id),
        })),

      violations: [],
      addViolation: (v) =>
        set((s) => ({
          violations: [v, ...s.violations].slice(0, 200),
          totalDetections: s.totalDetections + 1,
        })),
      clearViolations: () => set({ violations: [], totalDetections: 0 }),

      selectedCameraId: null,
      setSelectedCamera: (id) => set({ selectedCameraId: id }),

      wsConnected: false,
      setWsConnected: (v) => set({ wsConnected: v }),

      detectionEnabled: true,
      setDetectionEnabled: (v) => set({ detectionEnabled: v }),

      totalDetections: 0,
      incrementDetections: () => set((s) => ({ totalDetections: s.totalDetections + 1 })),
      resetDetections: () => set({ totalDetections: 0 }),
    }),
    {
      name: 'vwatch-cameras',
      // Only persist non-ephemeral UI state
      partialize: (s) => ({
        cameras: s.cameras,
        violations: s.violations.slice(0, 50), // keep last 50 across refreshes
        detectionEnabled: s.detectionEnabled,
        totalDetections: s.totalDetections,
        selectedCameraId: s.selectedCameraId,
      }),
    }
  )
)
