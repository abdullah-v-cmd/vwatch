/**
 * V-Watch — Live Monitoring Page
 * ================================
 * Key design decisions:
 *
 * 1. BACKEND OWNS CAMERAS — All camera capture runs in backend threads.
 *    Frontend NEVER starts/stops the actual video capture; it only calls
 *    REST endpoints to register cameras and control lifecycle.
 *
 * 2. MJPEG STREAM — Each camera's video is displayed via a simple <img> tag
 *    pointing to GET /api/v1/cameras/stream/{camera_id}. This is a standard
 *    HTTP multipart/x-mixed-replace response. The browser keeps the connection
 *    open. Navigating away and back simply re-renders the same <img> URL —
 *    the backend stream never stopped.
 *
 * 3. GLOBAL WEBSOCKET — The /live/ws WebSocket connection lives in the global
 *    singleton (getGlobalWs). It is NOT torn down on page unmount. When the
 *    user returns, it just re-attaches callbacks. This means violations continue
 *    arriving even when the user is on a different page.
 *
 * 4. ZUSTAND STORE — Camera list + violation feed are stored in useCameraStore
 *    (persisted to localStorage). Page navigation loses no state.
 */

import React, { useEffect, useRef, useState, useCallback } from 'react'
import {
  Camera, Plus, Trash2, Play, Square, AlertTriangle,
  Wifi, WifiOff, RefreshCw, Video, Monitor, Settings2,
  CheckCircle, Clock, Zap, Activity, Database, Server,
  RotateCcw, Eye, TrendingUp, Shield
} from 'lucide-react'
import toast from 'react-hot-toast'
import clsx from 'clsx'
import api, { cameraApi, liveApi, getGlobalWs, BASE_URL } from '../utils/api'
import { useCameraStore, BackendCamera, LiveViolation } from '../store/cameraStore'

// ── Constants ─────────────────────────────────────────────────────────────────

const VIOLATION_COLORS: Record<string, string> = {
  SPEEDING:        'text-red-400 bg-red-900/30 border-red-800',
  RED_LIGHT:       'text-orange-400 bg-orange-900/30 border-orange-800',
  WRONG_DIRECTION: 'text-purple-400 bg-purple-900/30 border-purple-800',
  LANE_VIOLATION:  'text-blue-400 bg-blue-900/30 border-blue-800',
  NO_HELMET:       'text-yellow-400 bg-yellow-900/30 border-yellow-800',
  VEHICLE_DETECTED:'text-green-400 bg-green-900/30 border-green-800',
}

const STATE_COLOR: Record<string, string> = {
  running:  'bg-green-500',
  starting: 'bg-yellow-500 animate-pulse',
  stopping: 'bg-yellow-500 animate-pulse',
  error:    'bg-red-500',
  stopped:  'bg-gray-500',
  idle:     'bg-gray-500',
  unknown:  'bg-gray-600',
}

// ── Types ─────────────────────────────────────────────────────────────────────

interface YoloStatus {
  running: boolean
  model_name?: string
  device?: string
  mock_mode?: boolean
}

interface AddCameraForm {
  name: string
  source: string
  source_type: 'webcam' | 'rtsp' | 'http'
  location: string
  speed_limit: number
}

// ── Main Component ─────────────────────────────────────────────────────────────

const LiveMonitoringPage: React.FC = () => {
  const {
    cameras, setCameras, updateCamera, addCamera: storAddCamera, removeCamera: storeRemoveCamera,
    violations, addViolation, clearViolations,
    wsConnected, setWsConnected,
    detectionEnabled, setDetectionEnabled,
    totalDetections,
    selectedCameraId, setSelectedCamera,
  } = useCameraStore()

  const [yoloStatus, setYoloStatus] = useState<YoloStatus | null>(null)
  const [showAddModal, setShowAddModal] = useState(false)
  const [addForm, setAddForm] = useState<AddCameraForm>({
    name: '', source: '', source_type: 'webcam', location: '', speed_limit: 60,
  })
  const [addLoading, setAddLoading] = useState(false)
  const [refreshing, setRefreshing] = useState(false)

  // Track which camera streams are loaded (to show spinner vs actual video)
  const [streamLoaded, setStreamLoaded] = useState<Record<string, boolean>>({})
  // Track stream error state
  const [streamError, setStreamError] = useState<Record<string, boolean>>({})
  // Stream key: incrementing this forces <img> to reload
  const [streamKeys, setStreamKeys] = useState<Record<string, number>>({})

  const mountedRef = useRef(true)

  // ── Fetch YOLO status ────────────────────────────────────────────────────────
  const fetchYoloStatus = useCallback(async () => {
    try {
      const res = await api.get('/yolo/status')
      if (mountedRef.current) setYoloStatus(res.data)
    } catch {
      if (mountedRef.current) setYoloStatus({ running: false, mock_mode: true })
    }
  }, [])

  // ── Fetch backend cameras ────────────────────────────────────────────────────
  const fetchCameras = useCallback(async (silent = false) => {
    if (!silent) setRefreshing(true)
    try {
      const res = await cameraApi.list()
      if (mountedRef.current) {
        setCameras(res.data.cameras || [])
      }
    } catch (err: any) {
      if (!silent) toast.error('Failed to fetch cameras from backend')
    } finally {
      if (mountedRef.current) setRefreshing(false)
    }
  }, [setCameras])

  // ── Wire up global WebSocket callbacks ───────────────────────────────────────
  useEffect(() => {
    mountedRef.current = true
    const ws = getGlobalWs()

    ws.onConnected = (data) => {
      if (mountedRef.current) setWsConnected(true)
      // Sync camera list from WS connected payload
      if (data && Array.isArray((data as any).cameras)) {
        const cams = (data as any).cameras as BackendCamera[]
        if (mountedRef.current && cams.length > 0) setCameras(cams)
      }
    }

    ws.onDisconnected = () => {
      if (mountedRef.current) setWsConnected(false)
    }

    ws.onCameraList = (cams) => {
      if (mountedRef.current && Array.isArray(cams) && cams.length > 0) {
        setCameras(cams as BackendCamera[])
      }
    }

    ws.onViolation = (data: any) => {
      if (!mountedRef.current) return
      const v: LiveViolation = {
        id: `ws-${data.id || Date.now()}-${Math.random()}`,
        camera_id: data.camera_id || 'UNKNOWN',
        camera_name: data.camera_name || data.camera_id || 'Unknown',
        violation_type: data.violation_type || 'UNKNOWN',
        plate_number: data.plate_number || 'UNKNOWN',
        confidence: data.confidence || 0,
        location: data.location || '',
        timestamp: new Date().toLocaleTimeString(),
        saved_to_db: !!data.id,
        db_id: data.id,
      }
      addViolation(v)
      toast(`🚨 ${v.violation_type.replace(/_/g, ' ')} — ${v.plate_number}`, {
        duration: 4000,
        style: { background: '#7f1d1d', color: '#fca5a5', border: '1px solid #991b1b' },
      })
      // Update violation count for this camera
      updateCamera(v.camera_id, {
        violation_count: (cameras.find(c => c.camera_id === v.camera_id)?.violation_count || 0) + 1
      })
    }

    ws.onCameraStatus = (data: any) => {
      if (!mountedRef.current) return
      updateCamera(data.camera_id, {
        state: data.status === 'active' ? 'running' : data.status,
        fps: data.fps || 0,
      })
    }

    ws.onCameraAdded = (data: any) => {
      if (!mountedRef.current) return
      toast.success(`Camera "${data.name || data.camera_id}" added`)
      fetchCameras(true)
    }

    ws.onCameraRemoved = (data: any) => {
      if (!mountedRef.current) return
      storeRemoveCamera(data.camera_id)
      toast(`Camera ${data.camera_id} removed`, { icon: '📷' })
    }

    // Ensure WS is connected
    if (!ws.isConnected) ws.connect()
    if (ws.isConnected) setWsConnected(true)

    return () => {
      mountedRef.current = false
      // ⚠️ DO NOT call ws.destroy() — global WS must persist across page changes
      // Just remove the callbacks so stale closures don't update unmounted state
      ws.onViolation = null
      ws.onCameraStatus = null
      ws.onCameraList = null
      ws.onCameraAdded = null
      ws.onCameraRemoved = null
    }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // ── On mount: fetch cameras + YOLO status ────────────────────────────────────
  useEffect(() => {
    fetchYoloStatus()
    fetchCameras()
    const yoloInterval = setInterval(fetchYoloStatus, 30_000)
    const camInterval = setInterval(() => fetchCameras(true), 10_000)
    return () => {
      clearInterval(yoloInterval)
      clearInterval(camInterval)
    }
  }, [fetchYoloStatus, fetchCameras])

  // ── Camera controls ──────────────────────────────────────────────────────────

  const handleStartCamera = useCallback(async (cam: BackendCamera) => {
    updateCamera(cam.camera_id, { state: 'starting' })
    try {
      await cameraApi.start(cam.camera_id)
      toast.success(`Camera "${cam.name}" starting…`)
      // Reload stream img
      setStreamKeys(prev => ({ ...prev, [cam.camera_id]: (prev[cam.camera_id] || 0) + 1 }))
      setStreamError(prev => ({ ...prev, [cam.camera_id]: false }))
      setStreamLoaded(prev => ({ ...prev, [cam.camera_id]: false }))
      setTimeout(() => fetchCameras(true), 2000)
    } catch {
      updateCamera(cam.camera_id, { state: 'error' })
      toast.error(`Failed to start camera "${cam.name}"`)
    }
  }, [updateCamera, fetchCameras])

  const handleStopCamera = useCallback(async (cam: BackendCamera) => {
    updateCamera(cam.camera_id, { state: 'stopping' })
    try {
      await cameraApi.stop(cam.camera_id)
      updateCamera(cam.camera_id, { state: 'stopped', fps: 0 })
      toast.success(`Camera "${cam.name}" stopped`)
    } catch {
      updateCamera(cam.camera_id, { state: 'error' })
      toast.error(`Failed to stop camera "${cam.name}"`)
    }
  }, [updateCamera])

  const handleRestartCamera = useCallback(async (cam: BackendCamera) => {
    updateCamera(cam.camera_id, { state: 'starting' })
    try {
      await cameraApi.restart(cam.camera_id)
      toast.success(`Camera "${cam.name}" restarting…`)
      setStreamKeys(prev => ({ ...prev, [cam.camera_id]: (prev[cam.camera_id] || 0) + 1 }))
      setStreamError(prev => ({ ...prev, [cam.camera_id]: false }))
      setStreamLoaded(prev => ({ ...prev, [cam.camera_id]: false }))
      setTimeout(() => fetchCameras(true), 3000)
    } catch {
      toast.error(`Failed to restart camera "${cam.name}"`)
    }
  }, [updateCamera, fetchCameras])

  const handleRemoveCamera = useCallback(async (cam: BackendCamera) => {
    if (!confirm(`Remove camera "${cam.name}"? The backend will stop capture.`)) return
    try {
      await cameraApi.remove(cam.camera_id)
      storeRemoveCamera(cam.camera_id)
      toast.success(`Camera "${cam.name}" removed`)
    } catch {
      // Also try removing via live monitoring endpoint (legacy)
      try {
        await liveApi.removeCamera(cam.camera_id)
        storeRemoveCamera(cam.camera_id)
        toast.success(`Camera "${cam.name}" removed`)
      } catch {
        toast.error(`Failed to remove camera "${cam.name}"`)
      }
    }
  }, [storeRemoveCamera])

  const handleToggleCamera = useCallback((cam: BackendCamera) => {
    if (cam.state === 'running' || cam.state === 'starting') {
      handleStopCamera(cam)
    } else {
      handleStartCamera(cam)
    }
  }, [handleStartCamera, handleStopCamera])

  // ── Add camera ───────────────────────────────────────────────────────────────

  const handleAddCamera = useCallback(async () => {
    if (!addForm.name.trim()) { toast.error('Camera name is required'); return }
    if (addForm.source_type !== 'webcam' && !addForm.source.trim()) {
      toast.error('Stream URL is required for RTSP/HTTP cameras')
      return
    }

    const camId = `cam_${Date.now()}`
    const source = addForm.source_type === 'webcam' ? '0' : addForm.source.trim()

    setAddLoading(true)
    try {
      // Register in persistent backend camera manager
      const res = await cameraApi.add({
        camera_id: camId,
        name: addForm.name.trim(),
        source,
        source_type: addForm.source_type,
        location: addForm.location.trim(),
        speed_limit: addForm.speed_limit,
        auto_start: true,
      })

      // Also register in live monitoring for backward compat
      try {
        await liveApi.addCamera({
          camera_id: camId,
          name: addForm.name.trim(),
          url: source,
          source_type: addForm.source_type,
          location: addForm.location.trim(),
          speed_limit: addForm.speed_limit,
        })
      } catch { /* non-critical */ }

      storAddCamera({
        camera_id: camId,
        name: addForm.name.trim(),
        source,
        source_type: addForm.source_type,
        location: addForm.location.trim(),
        speed_limit: addForm.speed_limit,
        enabled: true,
        state: 'starting',
        fps: 0,
        frame_count: 0,
        error_message: '',
        started_at: new Date().toISOString(),
        last_frame_at: null,
        violation_count: 0,
        has_stream: false,
        stream_url: res.data.stream_url,
        ws_url: res.data.ws_url,
      })

      setAddForm({ name: '', source: '', source_type: 'webcam', location: '', speed_limit: 60 })
      setShowAddModal(false)
      toast.success(`Camera "${addForm.name}" registered — backend capturing`)

      // Refresh after a moment to get live state
      setTimeout(() => fetchCameras(true), 3000)
    } catch (err: any) {
      const msg = err?.response?.data?.detail || 'Failed to add camera'
      toast.error(msg)
    } finally {
      setAddLoading(false)
    }
  }, [addForm, storAddCamera, fetchCameras])

  // ── MJPEG stream URL helper ───────────────────────────────────────────────────
  const getStreamUrl = (cam: BackendCamera) => {
    const key = streamKeys[cam.camera_id] || 0
    // Append token + cache-bust
    const token = localStorage.getItem('access_token') || ''
    return `${BASE_URL}/cameras/stream/${cam.camera_id}?k=${key}&t=${token}`
  }

  // ── Computed values ───────────────────────────────────────────────────────────
  const runningCameras = cameras.filter(c => c.state === 'running').length
  const errorCameras = cameras.filter(c => c.state === 'error').length

  // ── UI ────────────────────────────────────────────────────────────────────────
  return (
    <div className="space-y-6">

      {/* ── Header ── */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-2xl font-bold text-white flex items-center gap-2">
            <Shield className="w-6 h-6 text-red-400" />
            Live Monitoring
          </h1>
          <p className="text-gray-400 text-sm mt-1">
            Backend-persistent camera feeds — streams survive page navigation
          </p>
        </div>
        <div className="flex items-center gap-3 flex-wrap">

          {/* YOLO Status */}
          <div className={clsx(
            'flex items-center gap-1.5 px-3 py-1.5 rounded-lg border text-xs font-medium',
            yoloStatus?.running
              ? 'bg-green-900/30 border-green-700 text-green-400'
              : 'bg-yellow-900/30 border-yellow-700 text-yellow-400'
          )}>
            <Activity className="w-3.5 h-3.5" />
            {yoloStatus === null ? 'Checking YOLO…'
              : yoloStatus.running ? `YOLO Active (${yoloStatus.model_name || 'yolov8n'})`
              : 'YOLO Mock Mode'}
          </div>

          {/* WS Status */}
          <div className={clsx(
            'flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg border text-xs font-medium',
            wsConnected
              ? 'bg-blue-900/30 border-blue-700 text-blue-400'
              : 'bg-gray-800 border-gray-700 text-gray-400'
          )}>
            {wsConnected ? <Wifi className="w-3.5 h-3.5" /> : <WifiOff className="w-3.5 h-3.5" />}
            {wsConnected ? 'Live Feed' : 'Reconnecting…'}
          </div>

          {/* Detection toggle */}
          <button
            onClick={() => setDetectionEnabled(!detectionEnabled)}
            className={clsx(
              'flex items-center gap-2 px-3 py-2 rounded-lg border text-sm font-medium transition-colors',
              detectionEnabled
                ? 'bg-green-900/30 border-green-700 text-green-400 hover:bg-green-900/50'
                : 'bg-gray-800 border-gray-700 text-gray-400 hover:bg-gray-700'
            )}
          >
            <Zap className="w-4 h-4" />
            {detectionEnabled ? 'Detection ON' : 'Detection OFF'}
          </button>

          {/* Refresh */}
          <button
            onClick={() => fetchCameras()}
            disabled={refreshing}
            className="flex items-center gap-1.5 px-3 py-2 rounded-lg border border-gray-700 text-gray-400 hover:text-white hover:border-gray-600 text-sm transition-colors disabled:opacity-50"
          >
            <RefreshCw className={clsx('w-4 h-4', refreshing && 'animate-spin')} />
            Refresh
          </button>

          {/* Add Camera */}
          <button onClick={() => setShowAddModal(true)} className="btn-primary">
            <Plus className="w-4 h-4" />
            Add Camera
          </button>
        </div>
      </div>

      {/* ── Stats Bar ── */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        {[
          { icon: Camera,        color: 'bg-blue-600',   label: 'Total Cameras',      value: cameras.length },
          { icon: Server,        color: 'bg-green-600',  label: 'Backend Running',     value: runningCameras },
          { icon: AlertTriangle, color: 'bg-red-600',    label: 'Violations Detected', value: totalDetections },
          { icon: TrendingUp,    color: 'bg-purple-600', label: 'Avg FPS',
            value: cameras.length
              ? (cameras.reduce((s, c) => s + (c.fps || 0), 0) / cameras.length).toFixed(1)
              : '—',
            isText: true, textColor: 'text-green-400' },
        ].map(({ icon: Icon, color, label, value, isText, textColor }) => (
          <div key={label} className="card py-3 flex items-center gap-3">
            <div className={`w-9 h-9 ${color} rounded-lg flex items-center justify-center flex-shrink-0`}>
              <Icon className="w-5 h-5 text-white" />
            </div>
            <div>
              <p className="text-xs text-gray-400">{label}</p>
              <p className={clsx('font-bold', isText ? `text-base ${textColor || 'text-white'}` : 'text-xl text-white')}>{value}</p>
            </div>
          </div>
        ))}
      </div>

      {/* ── Main Content ── */}
      <div className="grid grid-cols-1 xl:grid-cols-3 gap-6">

        {/* Camera Grid */}
        <div className="xl:col-span-2 space-y-4">
          {cameras.length === 0 ? (
            <div className="card flex flex-col items-center justify-center py-20 text-center">
              <Server className="w-16 h-16 text-gray-600 mb-4" />
              <h3 className="text-lg font-semibold text-gray-400 mb-2">No Backend Cameras</h3>
              <p className="text-gray-500 text-sm mb-2 max-w-xs">
                Add a camera to start persistent backend capture. The stream will run
                even when you navigate to other pages.
              </p>
              <p className="text-gray-600 text-xs mb-6 max-w-xs">
                Supports: Webcam (device 0), RTSP streams, HTTP MJPEG feeds
              </p>
              <button onClick={() => setShowAddModal(true)} className="btn-primary">
                <Plus className="w-4 h-4" />
                Add First Camera
              </button>
            </div>
          ) : (
            <div className={clsx('grid gap-4', cameras.length === 1 ? 'grid-cols-1' : 'grid-cols-1 md:grid-cols-2')}>
              {cameras.map((cam) => (
                <CameraCard
                  key={cam.camera_id}
                  cam={cam}
                  streamUrl={getStreamUrl(cam)}
                  streamLoaded={streamLoaded[cam.camera_id] || false}
                  streamError={streamError[cam.camera_id] || false}
                  detectionEnabled={detectionEnabled}
                  selected={selectedCameraId === cam.camera_id}
                  onSelect={() => setSelectedCamera(
                    selectedCameraId === cam.camera_id ? null : cam.camera_id
                  )}
                  onToggle={() => handleToggleCamera(cam)}
                  onRestart={() => handleRestartCamera(cam)}
                  onRemove={() => handleRemoveCamera(cam)}
                  onStreamLoad={() => {
                    setStreamLoaded(p => ({ ...p, [cam.camera_id]: true }))
                    setStreamError(p => ({ ...p, [cam.camera_id]: false }))
                    updateCamera(cam.camera_id, { has_stream: true })
                  }}
                  onStreamError={() => {
                    setStreamError(p => ({ ...p, [cam.camera_id]: true }))
                    setStreamLoaded(p => ({ ...p, [cam.camera_id]: false }))
                  }}
                />
              ))}
            </div>
          )}
        </div>

        {/* Right Panel */}
        <div className="space-y-4">

          {/* Violation Feed */}
          <div className="card p-0 overflow-hidden">
            <div className="flex items-center justify-between px-4 py-3 border-b border-gray-800">
              <h3 className="text-base font-semibold text-white flex items-center gap-2">
                <AlertTriangle className="w-4 h-4 text-red-400" />
                Live Violation Feed
              </h3>
              {violations.length > 0 && (
                <button onClick={clearViolations} className="text-xs text-gray-500 hover:text-white">
                  Clear
                </button>
              )}
            </div>
            <div className="overflow-y-auto max-h-[480px]">
              {violations.length === 0 ? (
                <div className="flex flex-col items-center justify-center py-12 text-center px-4">
                  <CheckCircle className="w-10 h-10 text-gray-600 mb-3" />
                  <p className="text-gray-500 text-sm">No violations yet</p>
                  <p className="text-gray-600 text-xs mt-1">Backend cameras detect automatically</p>
                </div>
              ) : (
                <div className="divide-y divide-gray-800">
                  {violations.map((v) => (
                    <ViolationItem key={v.id} v={v} />
                  ))}
                </div>
              )}
            </div>
          </div>

          {/* Architecture Info */}
          <div className="card bg-gray-800/50">
            <h4 className="text-sm font-semibold text-gray-300 mb-3 flex items-center gap-2">
              <Settings2 className="w-4 h-4 text-gray-400" />
              Stream Architecture
            </h4>
            <ul className="space-y-2 text-xs text-gray-500">
              <li className="flex items-start gap-2">
                <Server className="w-3.5 h-3.5 text-green-400 flex-shrink-0 mt-0.5" />
                <span><strong className="text-gray-400">Backend threads</strong> — each camera runs in its own Python thread, independent of any browser connection</span>
              </li>
              <li className="flex items-start gap-2">
                <Eye className="w-3.5 h-3.5 text-blue-400 flex-shrink-0 mt-0.5" />
                <span><strong className="text-gray-400">MJPEG streams</strong> — served as HTTP multipart/x-mixed-replace; the <code className="bg-gray-700 px-1 rounded">&lt;img&gt;</code> tag just reconnects on return</span>
              </li>
              <li className="flex items-start gap-2">
                <Wifi className="w-3.5 h-3.5 text-purple-400 flex-shrink-0 mt-0.5" />
                <span><strong className="text-gray-400">Global WebSocket</strong> — /live/ws persists across page changes; violations arrive even while browsing elsewhere</span>
              </li>
              <li className="flex items-start gap-2">
                <Activity className="w-3.5 h-3.5 text-yellow-400 flex-shrink-0 mt-0.5" />
                <span><strong className="text-gray-400">YOLO detection</strong> — runs every 3rd frame in the backend thread; bounding boxes drawn server-side on MJPEG frames</span>
              </li>
            </ul>
          </div>

          {/* Error cameras */}
          {errorCameras > 0 && (
            <div className="card border border-red-800/50 bg-red-900/10">
              <h4 className="text-sm font-semibold text-red-400 mb-2 flex items-center gap-2">
                <AlertTriangle className="w-4 h-4" />
                {errorCameras} Camera{errorCameras > 1 ? 's' : ''} in Error State
              </h4>
              {cameras.filter(c => c.state === 'error').map(cam => (
                <div key={cam.camera_id} className="flex items-center justify-between mt-2">
                  <div>
                    <p className="text-sm text-gray-300">{cam.name}</p>
                    <p className="text-xs text-red-400 truncate max-w-[180px]">
                      {cam.error_message || 'Unknown error'}
                    </p>
                  </div>
                  <button
                    onClick={() => handleRestartCamera(cam)}
                    className="flex items-center gap-1 text-xs text-yellow-400 hover:text-yellow-300 border border-yellow-800 rounded px-2 py-1"
                  >
                    <RotateCcw className="w-3 h-3" />
                    Retry
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* ── Add Camera Modal ── */}
      {showAddModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm p-4">
          <div className="card w-full max-w-lg">
            <div className="flex items-center justify-between mb-5">
              <h3 className="text-lg font-semibold text-white flex items-center gap-2">
                <Server className="w-5 h-5 text-red-400" />
                Add Persistent Backend Camera
              </h3>
              <button onClick={() => setShowAddModal(false)} className="text-gray-400 hover:text-white text-xl">✕</button>
            </div>

            <div className="bg-blue-900/20 border border-blue-800 rounded-lg p-3 mb-4">
              <p className="text-xs text-blue-300">
                <strong>ℹ️ Persistent capture:</strong> Once added, the camera runs in a backend thread.
                Navigating away from this page will NOT stop the stream or detection.
              </p>
            </div>

            <div className="space-y-4">
              {/* Source Type */}
              <div>
                <label className="block text-sm font-medium text-gray-300 mb-2">Camera Type</label>
                <div className="grid grid-cols-3 gap-2">
                  {(['webcam', 'rtsp', 'http'] as const).map(type => (
                    <button
                      key={type}
                      onClick={() => setAddForm(f => ({ ...f, source_type: type }))}
                      className={clsx(
                        'py-2 px-3 rounded-lg border text-sm font-medium transition-colors',
                        addForm.source_type === type
                          ? 'bg-red-600 border-red-500 text-white'
                          : 'bg-gray-800 border-gray-700 text-gray-400 hover:border-gray-600'
                      )}
                    >
                      {type === 'webcam' ? '🎥 Webcam' : type === 'rtsp' ? '📡 RTSP' : '🌐 HTTP'}
                    </button>
                  ))}
                </div>
              </div>

              {/* Name */}
              <div>
                <label className="block text-sm font-medium text-gray-300 mb-1.5">Camera Name *</label>
                <input
                  className="input-field"
                  placeholder="e.g., Main Entrance, Intersection A"
                  value={addForm.name}
                  onChange={e => setAddForm(f => ({ ...f, name: e.target.value }))}
                />
              </div>

              {/* Source URL */}
              {addForm.source_type !== 'webcam' && (
                <div>
                  <label className="block text-sm font-medium text-gray-300 mb-1.5">
                    {addForm.source_type === 'rtsp' ? 'RTSP URL *' : 'HTTP Stream URL *'}
                  </label>
                  <input
                    className="input-field font-mono text-sm"
                    placeholder={
                      addForm.source_type === 'rtsp'
                        ? 'rtsp://user:pass@192.168.1.100:554/stream1'
                        : 'http://192.168.1.100:8080/video'
                    }
                    value={addForm.source}
                    onChange={e => setAddForm(f => ({ ...f, source: e.target.value }))}
                  />
                </div>
              )}

              {addForm.source_type === 'webcam' && (
                <div className="bg-gray-800/50 border border-gray-700 rounded-lg p-3">
                  <p className="text-xs text-gray-400">
                    <strong>Webcam (device 0):</strong> The backend will open the system's default camera.
                    On the server, this is device index <code className="bg-gray-700 px-1 rounded">0</code>.
                  </p>
                </div>
              )}

              {/* Location */}
              <div>
                <label className="block text-sm font-medium text-gray-300 mb-1.5">Location</label>
                <input
                  className="input-field"
                  placeholder="e.g., North Gate, Highway 5 KM 12"
                  value={addForm.location}
                  onChange={e => setAddForm(f => ({ ...f, location: e.target.value }))}
                />
              </div>

              {/* Speed Limit */}
              <div>
                <label className="block text-sm font-medium text-gray-300 mb-1.5">
                  Speed Limit (km/h)
                </label>
                <input
                  type="number"
                  min={10} max={200}
                  className="input-field"
                  value={addForm.speed_limit}
                  onChange={e => setAddForm(f => ({ ...f, speed_limit: Number(e.target.value) }))}
                />
              </div>
            </div>

            <div className="flex gap-3 mt-6">
              <button
                onClick={() => setShowAddModal(false)}
                className="btn-secondary flex-1 justify-center"
                disabled={addLoading}
              >
                Cancel
              </button>
              <button
                onClick={handleAddCamera}
                className="btn-primary flex-1 justify-center"
                disabled={addLoading}
              >
                {addLoading ? (
                  <><RefreshCw className="w-4 h-4 animate-spin" />Adding…</>
                ) : (
                  <><Server className="w-4 h-4" />Add &amp; Start Camera</>
                )}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}


// ── Camera Card Sub-component ─────────────────────────────────────────────────

interface CameraCardProps {
  cam: BackendCamera
  streamUrl: string
  streamLoaded: boolean
  streamError: boolean
  detectionEnabled: boolean
  selected: boolean
  onSelect: () => void
  onToggle: () => void
  onRestart: () => void
  onRemove: () => void
  onStreamLoad: () => void
  onStreamError: () => void
}

const CameraCard: React.FC<CameraCardProps> = ({
  cam, streamUrl, streamLoaded, streamError, detectionEnabled,
  selected, onSelect, onToggle, onRestart, onRemove, onStreamLoad, onStreamError,
}) => {
  const isRunning  = cam.state === 'running'
  const isStarting = cam.state === 'starting' || cam.state === 'stopping'
  const isError    = cam.state === 'error'

  return (
    <div
      className={clsx(
        'card p-0 overflow-hidden border-2 transition-colors cursor-pointer',
        selected       ? 'border-blue-500' :
        isRunning      ? 'border-green-600' :
        isError        ? 'border-red-600' :
        isStarting     ? 'border-yellow-600 animate-pulse' :
        'border-transparent'
      )}
      onClick={onSelect}
    >
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2 bg-gray-800 border-b border-gray-700">
        <div className="flex items-center gap-2 min-w-0">
          <div className={clsx('w-2 h-2 rounded-full flex-shrink-0', STATE_COLOR[cam.state] || 'bg-gray-600')} />
          <span className="text-white text-sm font-medium truncate">{cam.name}</span>
          <span className={clsx(
            'text-xs px-1.5 py-0.5 rounded flex-shrink-0 capitalize',
            cam.source_type === 'webcam' ? 'bg-blue-900/50 text-blue-400' :
            cam.source_type === 'rtsp'   ? 'bg-purple-900/50 text-purple-400' :
                                           'bg-teal-900/50 text-teal-400'
          )}>
            {cam.source_type}
          </span>
        </div>
        <div className="flex items-center gap-1 flex-shrink-0" onClick={e => e.stopPropagation()}>
          {isRunning && cam.fps > 0 && (
            <span className="text-xs text-green-400 mr-1">{cam.fps.toFixed(1)} fps</span>
          )}
          {/* Toggle start/stop */}
          <button
            onClick={onToggle}
            disabled={isStarting}
            className={clsx(
              'p-1.5 rounded transition-colors disabled:opacity-50',
              isRunning ? 'text-red-400 hover:bg-red-900/30' : 'text-green-400 hover:bg-green-900/30'
            )}
            title={isRunning ? 'Stop camera' : 'Start camera'}
          >
            {isStarting ? <RefreshCw className="w-4 h-4 animate-spin" />
              : isRunning ? <Square className="w-4 h-4" />
              : <Play className="w-4 h-4" />}
          </button>
          {/* Restart */}
          <button
            onClick={onRestart}
            className="p-1.5 text-gray-500 hover:text-yellow-400 hover:bg-yellow-900/30 rounded transition-colors"
            title="Restart camera"
          >
            <RotateCcw className="w-3.5 h-3.5" />
          </button>
          {/* Remove */}
          <button
            onClick={onRemove}
            className="p-1.5 text-gray-500 hover:text-red-400 hover:bg-red-900/30 rounded transition-colors"
            title="Remove camera"
          >
            <Trash2 className="w-4 h-4" />
          </button>
        </div>
      </div>

      {/* Video Feed — MJPEG from backend */}
      <div className="relative bg-gray-900 aspect-video">

        {/* The MJPEG stream. Backend serves this as multipart/x-mixed-replace.
            Browser keeps the HTTP connection open. Navigating away + back
            simply reloads the <img> which reconnects to the already-running stream. */}
        {isRunning && !streamError && (
          <img
            src={streamUrl}
            alt={`${cam.name} stream`}
            className={clsx(
              'w-full h-full object-cover transition-opacity duration-300',
              streamLoaded ? 'opacity-100' : 'opacity-0'
            )}
            onLoad={onStreamLoad}
            onError={onStreamError}
          />
        )}

        {/* Loading spinner while stream connects */}
        {isRunning && !streamLoaded && !streamError && (
          <div className="absolute inset-0 flex flex-col items-center justify-center">
            <RefreshCw className="w-8 h-8 text-green-400 animate-spin mb-2" />
            <p className="text-green-400 text-sm">Connecting to stream…</p>
          </div>
        )}

        {/* Stream error — show retry */}
        {isRunning && streamError && (
          <div className="absolute inset-0 flex flex-col items-center justify-center">
            <WifiOff className="w-8 h-8 text-orange-400 mb-2" />
            <p className="text-orange-400 text-sm">Stream connection error</p>
            <button
              onClick={(e) => { e.stopPropagation(); onRestart() }}
              className="mt-2 text-xs text-gray-400 hover:text-white underline"
            >
              Restart backend capture
            </button>
          </div>
        )}

        {/* Offline overlay */}
        {!isRunning && !isStarting && (
          <div className="absolute inset-0 flex flex-col items-center justify-center">
            {isError ? (
              <>
                <WifiOff className="w-8 h-8 text-red-400 mb-2" />
                <p className="text-red-400 text-sm">Capture Error</p>
                <p className="text-red-500 text-xs mt-1 max-w-[80%] text-center truncate">
                  {cam.error_message || 'Unknown error'}
                </p>
                <button
                  onClick={(e) => { e.stopPropagation(); onRestart() }}
                  className="mt-2 btn-secondary py-1 px-3 text-xs"
                >
                  <RotateCcw className="w-3 h-3" />Retry
                </button>
              </>
            ) : (
              <>
                <Video className="w-8 h-8 text-gray-600 mb-2" />
                <p className="text-gray-500 text-sm">Stream Stopped</p>
                <button
                  onClick={(e) => { e.stopPropagation(); onToggle() }}
                  className="mt-2 btn-primary py-1.5 px-3 text-xs"
                >
                  <Play className="w-3 h-3" />Start Backend Capture
                </button>
              </>
            )}
          </div>
        )}

        {/* Starting overlay */}
        {isStarting && (
          <div className="absolute inset-0 flex flex-col items-center justify-center">
            <RefreshCw className="w-8 h-8 text-yellow-400 animate-spin mb-2" />
            <p className="text-yellow-400 text-sm capitalize">{cam.state}…</p>
          </div>
        )}

        {/* AI Detection Badge */}
        {isRunning && detectionEnabled && (
          <div className="absolute top-2 left-2 bg-green-900/80 border border-green-700 rounded px-2 py-1 flex items-center gap-1.5">
            <div className="w-1.5 h-1.5 bg-green-400 rounded-full animate-pulse" />
            <span className="text-green-400 text-xs font-medium">AI Active (Backend)</span>
          </div>
        )}

        {/* Violation count badge */}
        {cam.violation_count > 0 && (
          <div className="absolute top-2 right-2 bg-red-600 rounded-full px-2 py-0.5 text-xs text-white font-bold">
            {cam.violation_count}
          </div>
        )}

        {/* Frame count */}
        {isRunning && cam.frame_count > 0 && (
          <div className="absolute bottom-2 right-2 bg-black/60 rounded px-2 py-0.5">
            <span className="text-gray-300 text-xs font-mono">
              {cam.frame_count.toLocaleString()} frames
            </span>
          </div>
        )}
      </div>

      {/* Footer: location + source */}
      {(cam.location || cam.source) && (
        <div className="px-3 py-1.5 bg-gray-800/50 border-t border-gray-700 flex items-center justify-between gap-2">
          {cam.location && (
            <span className="text-xs text-gray-500 truncate">{cam.location}</span>
          )}
          {cam.source && cam.source !== '0' && (
            <span className="text-xs text-gray-600 font-mono truncate">{cam.source}</span>
          )}
        </div>
      )}
    </div>
  )
}


// ── Violation Item Sub-component ──────────────────────────────────────────────

const ViolationItem: React.FC<{ v: LiveViolation }> = ({ v }) => (
  <div className="px-4 py-3 hover:bg-gray-800/30 transition-colors">
    <div className="flex items-start justify-between gap-2">
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 flex-wrap">
          <span className={clsx(
            'text-xs px-2 py-0.5 rounded border font-medium',
            VIOLATION_COLORS[v.violation_type] || 'text-gray-400 bg-gray-800 border-gray-700'
          )}>
            {v.violation_type.replace(/_/g, ' ')}
          </span>
          {v.plate_number && v.plate_number !== 'UNKNOWN' && (
            <span className="text-xs font-mono text-white bg-gray-700 px-1.5 py-0.5 rounded">
              {v.plate_number}
            </span>
          )}
          {v.saved_to_db && (
            <Database className="w-3 h-3 text-blue-400" title="Saved to database" />
          )}
        </div>
        <p className="text-xs text-gray-400 mt-1 truncate">
          {v.camera_name} {v.location ? `• ${v.location}` : ''}
        </p>
      </div>
      <div className="flex-shrink-0 text-right">
        <div className="flex items-center gap-1 text-xs text-gray-500">
          <Clock className="w-3 h-3" />
          {v.timestamp}
        </div>
        <p className="text-xs text-gray-500 mt-0.5">{(v.confidence * 100).toFixed(0)}% conf</p>
      </div>
    </div>
  </div>
)

export default LiveMonitoringPage
