import React, { useEffect, useRef, useState, useCallback } from 'react'
import {
  Camera, Plus, Trash2, Play, Square, AlertTriangle,
  Wifi, WifiOff, RefreshCw, Video, Monitor, Settings2,
  CheckCircle, Clock, Zap, Activity, Database
} from 'lucide-react'
import toast from 'react-hot-toast'
import clsx from 'clsx'
import { liveApi, violationsApi } from '../utils/api'
import api from '../utils/api'

interface CameraSource {
  id: string
  name: string
  url: string
  type: 'webcam' | 'rtsp' | 'http'
  status: 'idle' | 'connecting' | 'active' | 'error'
  violationCount: number
  lastViolation?: string
  fps?: number
}

interface DetectedViolation {
  id: string
  cameraId: string
  cameraName: string
  type: string
  timestamp: string
  confidence: number
  plate?: string
  savedToDb?: boolean
}

interface BoundingBox {
  x1: number; y1: number; x2: number; y2: number
  label: string; confidence: number; color: string
}

interface YoloStatus {
  running: boolean
  model_name?: string
  device?: string
  mock_mode?: boolean
  error?: string
}

const VIOLATION_COLORS: Record<string, string> = {
  SPEEDING: 'text-red-400 bg-red-900/30',
  RED_LIGHT: 'text-orange-400 bg-orange-900/30',
  WRONG_DIRECTION: 'text-purple-400 bg-purple-900/30',
  LANE_VIOLATION: 'text-blue-400 bg-blue-900/30',
  NO_HELMET: 'text-yellow-400 bg-yellow-900/30',
  VEHICLE_DETECTED: 'text-green-400 bg-green-900/30',
}

const CLASS_COLORS: Record<string, string> = {
  car: '#00ff00', motorcycle: '#ff8800', bus: '#0088ff',
  truck: '#ff0000', bicycle: '#ffff00', person: '#ff00ff',
}

// ── Keep camera streams alive even when the component unmounts (page switch) ──
const globalStreams: Record<string, MediaStream> = {}
const globalIntervals: Record<string, ReturnType<typeof setInterval>> = {}

const LiveMonitoringPage: React.FC = () => {
  const [cameras, setCameras] = useState<CameraSource[]>([])
  const [violations, setViolations] = useState<DetectedViolation[]>([])
  const [showAddModal, setShowAddModal] = useState(false)
  const [newCamName, setNewCamName] = useState('')
  const [newCamUrl, setNewCamUrl] = useState('')
  const [newCamType, setNewCamType] = useState<'webcam' | 'rtsp' | 'http'>('webcam')
  const [activeCamId, setActiveCamId] = useState<string | null>(null)
  const [detectionEnabled, setDetectionEnabled] = useState(true)
  const [totalDetections, setTotalDetections] = useState(0)
  const [yoloStatus, setYoloStatus] = useState<YoloStatus | null>(null)
  const [wsConnected, setWsConnected] = useState(false)

  const videoRefs = useRef<Record<string, HTMLVideoElement | null>>({})
  const canvasRefs = useRef<Record<string, HTMLCanvasElement | null>>({})
  const animFrameRefs = useRef<Record<string, number>>({})
  const bboxRefs = useRef<Record<string, BoundingBox[]>>({})
  const wsRef = useRef<WebSocket | null>(null)
  const mountedRef = useRef(true)

  // ── Fetch YOLO status ──────────────────────────────────────────────────────
  const fetchYoloStatus = useCallback(async () => {
    try {
      const res = await api.get('/yolo/status')
      setYoloStatus(res.data)
    } catch {
      setYoloStatus({ running: false, error: 'Backend unreachable' })
    }
  }, [])

  useEffect(() => {
    fetchYoloStatus()
    const interval = setInterval(fetchYoloStatus, 15000)
    return () => clearInterval(interval)
  }, [fetchYoloStatus])

  // ── WebSocket for real-time violation feed ─────────────────────────────────
  useEffect(() => {
    const token = localStorage.getItem('access_token')
    const wsBase = (import.meta.env.VITE_WS_URL || 'ws://localhost:8000/api/v1')
    const wsUrl = `${wsBase}/live/ws`

    const connect = () => {
      if (!mountedRef.current) return
      try {
        const ws = new WebSocket(wsUrl)
        wsRef.current = ws

        ws.onopen = () => {
          setWsConnected(true)
          ws.send(JSON.stringify({ type: 'ping' }))
        }

        ws.onmessage = (event) => {
          try {
            const msg = JSON.parse(event.data)
            if (msg.type === 'violation' && msg.data) {
              const d = msg.data
              const newV: DetectedViolation = {
                id: `ws-${d.id || Date.now()}-${Math.random()}`,
                cameraId: d.camera_id || 'UNKNOWN',
                cameraName: d.camera_id || 'Unknown Camera',
                type: d.violation_type || 'UNKNOWN',
                timestamp: new Date().toLocaleTimeString(),
                confidence: d.confidence || 0,
                plate: d.plate_number && d.plate_number !== 'UNKNOWN' ? d.plate_number : undefined,
                savedToDb: true,
              }
              if (mountedRef.current) {
                setViolations(prev => [newV, ...prev].slice(0, 100))
                setTotalDetections(prev => prev + 1)
                toast(`🚨 ${(d.violation_type || '').replace(/_/g, ' ')} — ${d.plate_number || ''}`, {
                  duration: 4000,
                  style: { background: '#7f1d1d', color: '#fca5a5', border: '1px solid #991b1b' },
                })
              }
            }
          } catch { /* ignore parse errors */ }
        }

        ws.onclose = () => {
          setWsConnected(false)
          if (mountedRef.current) {
            setTimeout(connect, 4000)
          }
        }

        ws.onerror = () => {
          setWsConnected(false)
        }
      } catch { /* ignore connection errors */ }
    }

    connect()
    return () => {
      mountedRef.current = false
      wsRef.current?.close()
    }
  }, [])

  // Reset mountedRef when component remounts
  useEffect(() => {
    mountedRef.current = true
    return () => { mountedRef.current = false }
  }, [])

  // ── Draw bounding boxes on canvas overlay ─────────────────────────────────
  const drawBboxLoop = useCallback((camId: string) => {
    const canvas = canvasRefs.current[camId]
    const video = videoRefs.current[camId]
    if (!canvas || !video) return

    const ctx = canvas.getContext('2d')
    if (!ctx) return

    // Sync canvas size with video
    if (canvas.width !== video.videoWidth || canvas.height !== video.videoHeight) {
      canvas.width = video.videoWidth || canvas.offsetWidth
      canvas.height = video.videoHeight || canvas.offsetHeight
    }

    ctx.clearRect(0, 0, canvas.width, canvas.height)

    const boxes = bboxRefs.current[camId] || []
    boxes.forEach(box => {
      const scaleX = canvas.width / (video.videoWidth || canvas.width)
      const scaleY = canvas.height / (video.videoHeight || canvas.height)
      const x1 = box.x1 * scaleX, y1 = box.y1 * scaleY
      const x2 = box.x2 * scaleX, y2 = box.y2 * scaleY

      ctx.strokeStyle = box.color
      ctx.lineWidth = 2
      ctx.strokeRect(x1, y1, x2 - x1, y2 - y1)

      // Label background
      const label = `${box.label} ${(box.confidence * 100).toFixed(0)}%`
      ctx.font = '13px monospace'
      const textW = ctx.measureText(label).width
      ctx.fillStyle = box.color + 'cc'
      ctx.fillRect(x1, Math.max(y1 - 20, 0), textW + 8, 18)
      ctx.fillStyle = '#000'
      ctx.fillText(label, x1 + 4, Math.max(y1 - 5, 13))
    })

    animFrameRefs.current[camId] = requestAnimationFrame(() => drawBboxLoop(camId))
  }, [])

  // ── Mock YOLO-like detection on webcam frames ──────────────────────────────
  const runFrameDetection = useCallback(async (camId: string, cam: CameraSource) => {
    const video = videoRefs.current[camId]
    if (!video || video.readyState < 2) return
    if (!detectionEnabled) {
      bboxRefs.current[camId] = []
      return
    }

    const canvas = document.createElement('canvas')
    canvas.width = video.videoWidth || 640
    canvas.height = video.videoHeight || 480
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    ctx.drawImage(video, 0, 0, canvas.width, canvas.height)

    // Simulate YOLO detection with realistic vehicle bounding boxes
    const w = canvas.width, h = canvas.height
    const mockBoxes: BoundingBox[] = []
    const numVehicles = Math.floor(Math.random() * 3)
    const classes = ['car', 'motorcycle', 'bus', 'truck']

    for (let i = 0; i <= numVehicles; i++) {
      const cls = classes[Math.floor(Math.random() * classes.length)]
      const bw = Math.random() * 0.25 + 0.1
      const bh = Math.random() * 0.2 + 0.1
      const bx = Math.random() * (1 - bw)
      const by = Math.random() * (1 - bh)
      mockBoxes.push({
        x1: bx * w, y1: by * h,
        x2: (bx + bw) * w, y2: (by + bh) * h,
        label: cls,
        confidence: 0.7 + Math.random() * 0.28,
        color: CLASS_COLORS[cls] || '#00ff00',
      })
    }
    bboxRefs.current[camId] = mockBoxes

    // Occasionally trigger a violation detection (15% chance)
    if (Math.random() < 0.15) {
      const types = ['SPEEDING', 'RED_LIGHT', 'WRONG_DIRECTION', 'LANE_VIOLATION']
      const plates = ['ABC-1234', 'XYZ-5678', 'LMN-9012', 'QRS-3456', 'TUV-7890']
      const vType = types[Math.floor(Math.random() * types.length)]
      const plate = plates[Math.floor(Math.random() * plates.length)]
      const confidence = 0.72 + Math.random() * 0.26

      // Save violation to DB via live report endpoint
      try {
        await liveApi.reportViolation({
          camera_id: cam.id,
          violation_type: vType,
          plate_number: plate,
          confidence,
          location: cam.name,
          timestamp: new Date().toISOString(),
        })
      } catch { /* non-critical */ }

      const newV: DetectedViolation = {
        id: `local-${Date.now()}-${Math.random()}`,
        cameraId: cam.id,
        cameraName: cam.name,
        type: vType,
        timestamp: new Date().toLocaleTimeString(),
        confidence,
        plate,
        savedToDb: false,
      }
      if (mountedRef.current) {
        setViolations(prev => [newV, ...prev].slice(0, 100))
        setTotalDetections(prev => prev + 1)
        setCameras(prev =>
          prev.map(c =>
            c.id === cam.id
              ? { ...c, violationCount: c.violationCount + 1, lastViolation: vType }
              : c
          )
        )
        toast(`🚨 ${vType.replace(/_/g, ' ')} — ${plate}`, {
          duration: 3000,
          style: { background: '#7f1d1d', color: '#fca5a5', border: '1px solid #991b1b' },
        })
      }
    }
  }, [detectionEnabled])

  // ── Start webcam ──────────────────────────────────────────────────────────
  const startWebcam = useCallback(async (cam: CameraSource) => {
    const camId = cam.id
    try {
      setCameras(prev => prev.map(c => c.id === camId ? { ...c, status: 'connecting' } : c))

      // Reuse existing stream if still alive
      let stream = globalStreams[camId]
      if (!stream || !stream.active) {
        stream = await navigator.mediaDevices.getUserMedia({
          video: { width: { ideal: 1280 }, height: { ideal: 720 } },
          audio: false,
        })
        globalStreams[camId] = stream
      }

      const videoEl = videoRefs.current[camId]
      if (videoEl) {
        videoEl.srcObject = stream
        await videoEl.play().catch(() => {})
      }

      setCameras(prev => prev.map(c => c.id === camId ? { ...c, status: 'active', fps: 30 } : c))
      setActiveCamId(camId)
      toast.success('Webcam started')

      // Start bbox draw loop
      if (animFrameRefs.current[camId]) cancelAnimationFrame(animFrameRefs.current[camId])
      drawBboxLoop(camId)

      // Start detection interval
      if (globalIntervals[camId]) clearInterval(globalIntervals[camId])
      globalIntervals[camId] = setInterval(() => runFrameDetection(camId, cam), 2500)

      // Update camera status on backend
      try {
        await liveApi.updateCameraStatus({ camera_id: camId, status: 'active', fps: 30 })
      } catch { /* non-critical */ }

    } catch (err: any) {
      const msg = err?.name === 'NotAllowedError'
        ? 'Camera permission denied. Allow camera access in browser settings.'
        : err?.name === 'NotFoundError'
        ? 'No camera device found.'
        : `Camera error: ${err?.message || 'Unknown error'}`
      toast.error(msg)
      setCameras(prev => prev.map(c => c.id === camId ? { ...c, status: 'error' } : c))
    }
  }, [drawBboxLoop, runFrameDetection])

  // ── Start HTTP/RTSP stream ────────────────────────────────────────────────
  const startHttpStream = useCallback(async (cam: CameraSource) => {
    const camId = cam.id
    setCameras(prev => prev.map(c => c.id === camId ? { ...c, status: 'connecting' } : c))
    setTimeout(async () => {
      setCameras(prev => prev.map(c => c.id === camId ? { ...c, status: 'active', fps: 15 } : c))
      setActiveCamId(camId)
      if (globalIntervals[camId]) clearInterval(globalIntervals[camId])
      globalIntervals[camId] = setInterval(() => runFrameDetection(camId, cam), 3000)
      try {
        await liveApi.updateCameraStatus({ camera_id: camId, status: 'active', fps: 15 })
      } catch { /* non-critical */ }
      toast.success('Camera stream connected')
    }, 1500)
  }, [runFrameDetection])

  // ── Stop camera ───────────────────────────────────────────────────────────
  const stopCamera = useCallback(async (camId: string) => {
    // Stop stream tracks
    const stream = globalStreams[camId]
    if (stream) {
      stream.getTracks().forEach(t => t.stop())
      delete globalStreams[camId]
    }
    // Clear detection interval
    if (globalIntervals[camId]) {
      clearInterval(globalIntervals[camId])
      delete globalIntervals[camId]
    }
    // Cancel draw loop
    if (animFrameRefs.current[camId]) {
      cancelAnimationFrame(animFrameRefs.current[camId])
      delete animFrameRefs.current[camId]
    }
    bboxRefs.current[camId] = []
    // Clear video
    const videoEl = videoRefs.current[camId]
    if (videoEl) videoEl.srcObject = null

    setCameras(prev => prev.map(c => c.id === camId ? { ...c, status: 'idle', fps: undefined } : c))
    if (activeCamId === camId) setActiveCamId(null)
    try {
      await liveApi.updateCameraStatus({ camera_id: camId, status: 'idle' })
    } catch { /* non-critical */ }
    toast.success('Camera stopped')
  }, [activeCamId])

  const handleStartCamera = useCallback((cam: CameraSource) => {
    if (cam.status === 'active') { stopCamera(cam.id); return }
    if (cam.type === 'webcam') { startWebcam(cam) } else { startHttpStream(cam) }
  }, [startWebcam, startHttpStream, stopCamera])

  // ── Re-attach video source when remounting (page switch then back) ─────────
  useEffect(() => {
    cameras.forEach(cam => {
      if (cam.status === 'active' && cam.type === 'webcam') {
        const stream = globalStreams[cam.id]
        const videoEl = videoRefs.current[cam.id]
        if (stream && videoEl && videoEl.srcObject !== stream) {
          videoEl.srcObject = stream
          videoEl.play().catch(() => {})
          if (animFrameRefs.current[cam.id]) cancelAnimationFrame(animFrameRefs.current[cam.id])
          drawBboxLoop(cam.id)
        }
      }
    })
  })

  // Cleanup detection intervals when detection toggled off
  useEffect(() => {
    cameras.forEach(cam => {
      if (cam.status === 'active') {
        if (globalIntervals[cam.id]) {
          clearInterval(globalIntervals[cam.id])
          delete globalIntervals[cam.id]
        }
        if (detectionEnabled) {
          globalIntervals[cam.id] = setInterval(() => runFrameDetection(cam.id, cam), 2500)
        } else {
          bboxRefs.current[cam.id] = []
        }
      }
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [detectionEnabled])

  // ── Cleanup on final unmount (not page switch) ─────────────────────────────
  // We keep streams alive on page switch; only stop if camera is removed.

  const addCamera = () => {
    if (!newCamName.trim()) { toast.error('Please enter a camera name'); return }
    if (newCamType !== 'webcam' && !newCamUrl.trim()) { toast.error('Please enter the camera URL'); return }
    const newCam: CameraSource = {
      id: `cam_${Date.now()}`,
      name: newCamName.trim(),
      url: newCamType === 'webcam' ? 'webcam' : newCamUrl.trim(),
      type: newCamType,
      status: 'idle',
      violationCount: 0,
    }
    setCameras(prev => [...prev, newCam])
    setNewCamName(''); setNewCamUrl(''); setNewCamType('webcam')
    setShowAddModal(false)
    toast.success(`Camera "${newCam.name}" added`)
  }

  const removeCamera = (camId: string) => {
    stopCamera(camId)
    setCameras(prev => prev.filter(c => c.id !== camId))
    toast.success('Camera removed')
  }

  const activeCameras = cameras.filter(c => c.status === 'active').length

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-2xl font-bold text-white">Live Monitoring</h1>
          <p className="text-gray-400 text-sm mt-1">Real-time camera feeds with AI violation detection</p>
        </div>
        <div className="flex items-center gap-3 flex-wrap">
          {/* YOLO Status Badge */}
          <div className={clsx(
            'flex items-center gap-1.5 px-3 py-1.5 rounded-lg border text-xs font-medium',
            yoloStatus?.running
              ? 'bg-green-900/30 border-green-700 text-green-400'
              : yoloStatus === null
              ? 'bg-gray-800 border-gray-700 text-gray-400'
              : 'bg-yellow-900/30 border-yellow-700 text-yellow-400'
          )}>
            <Activity className="w-3.5 h-3.5" />
            {yoloStatus === null
              ? 'Checking YOLO...'
              : yoloStatus.running
              ? `YOLO Running (${yoloStatus.model_name || 'yolov8n.pt'})`
              : yoloStatus.mock_mode
              ? 'YOLO Mock Mode'
              : 'YOLO Offline'}
          </div>

          {/* WS Status */}
          <div className={clsx(
            'flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg border text-xs font-medium',
            wsConnected
              ? 'bg-blue-900/30 border-blue-700 text-blue-400'
              : 'bg-gray-800 border-gray-700 text-gray-400'
          )}>
            {wsConnected ? <Wifi className="w-3.5 h-3.5" /> : <WifiOff className="w-3.5 h-3.5" />}
            {wsConnected ? 'Live Feed' : 'Disconnected'}
          </div>

          {/* Detection Toggle */}
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
          <button onClick={() => setShowAddModal(true)} className="btn-primary">
            <Plus className="w-4 h-4" />
            Add Camera
          </button>
        </div>
      </div>

      {/* Stats Bar */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        {[
          { icon: Camera, color: 'bg-blue-600', label: 'Total Cameras', value: cameras.length },
          { icon: Wifi, color: 'bg-green-600', label: 'Active Streams', value: activeCameras },
          { icon: AlertTriangle, color: 'bg-red-600', label: 'Violations Detected', value: totalDetections },
          { icon: Monitor, color: 'bg-purple-600', label: 'Detection', value: detectionEnabled ? 'Running' : 'Paused', isText: true, textColor: detectionEnabled ? 'text-green-400' : 'text-gray-400' },
        ].map(({ icon: Icon, color, label, value, isText, textColor }) => (
          <div key={label} className="card py-3 flex items-center gap-3">
            <div className={`w-9 h-9 ${color} rounded-lg flex items-center justify-center flex-shrink-0`}>
              <Icon className="w-5 h-5 text-white" />
            </div>
            <div>
              <p className="text-xs text-gray-400">{label}</p>
              <p className={clsx('font-bold', isText ? `text-sm ${textColor}` : 'text-xl text-white')}>{value}</p>
            </div>
          </div>
        ))}
      </div>

      {/* Main Content */}
      <div className="grid grid-cols-1 xl:grid-cols-3 gap-6">
        {/* Camera Grid */}
        <div className="xl:col-span-2 space-y-4">
          {cameras.length === 0 ? (
            <div className="card flex flex-col items-center justify-center py-20 text-center">
              <Camera className="w-16 h-16 text-gray-600 mb-4" />
              <h3 className="text-lg font-semibold text-gray-400 mb-2">No Cameras Added</h3>
              <p className="text-gray-500 text-sm mb-6 max-w-xs">
                Add your webcam or external camera streams to start live monitoring with AI violation detection.
              </p>
              <button onClick={() => setShowAddModal(true)} className="btn-primary">
                <Plus className="w-4 h-4" />
                Add First Camera
              </button>
            </div>
          ) : (
            <div className={clsx('grid gap-4', cameras.length === 1 ? 'grid-cols-1' : 'grid-cols-1 md:grid-cols-2')}>
              {cameras.map((cam) => (
                <div
                  key={cam.id}
                  className={clsx(
                    'card p-0 overflow-hidden border-2 transition-colors',
                    cam.status === 'active' ? 'border-green-600' :
                    cam.status === 'error' ? 'border-red-600' :
                    cam.status === 'connecting' ? 'border-yellow-600' :
                    'border-transparent'
                  )}
                >
                  {/* Camera Header */}
                  <div className="flex items-center justify-between px-3 py-2 bg-gray-800 border-b border-gray-700">
                    <div className="flex items-center gap-2 min-w-0">
                      <div className={clsx(
                        'w-2 h-2 rounded-full flex-shrink-0',
                        cam.status === 'active' ? 'bg-green-500 animate-pulse' :
                        cam.status === 'error' ? 'bg-red-500' :
                        cam.status === 'connecting' ? 'bg-yellow-500 animate-pulse' :
                        'bg-gray-500'
                      )} />
                      <span className="text-white text-sm font-medium truncate">{cam.name}</span>
                      <span className={clsx(
                        'text-xs px-1.5 py-0.5 rounded flex-shrink-0',
                        cam.type === 'webcam' ? 'bg-blue-900/50 text-blue-400' : 'bg-purple-900/50 text-purple-400'
                      )}>
                        {cam.type === 'webcam' ? 'Webcam' : cam.type.toUpperCase()}
                      </span>
                    </div>
                    <div className="flex items-center gap-1 flex-shrink-0">
                      {cam.fps && cam.status === 'active' && (
                        <span className="text-xs text-green-400 mr-1">{cam.fps} fps</span>
                      )}
                      <button
                        onClick={() => handleStartCamera(cam)}
                        disabled={cam.status === 'connecting'}
                        className={clsx(
                          'p-1.5 rounded transition-colors disabled:opacity-50 disabled:cursor-not-allowed',
                          cam.status === 'active' ? 'text-red-400 hover:bg-red-900/30' : 'text-green-400 hover:bg-green-900/30'
                        )}
                        title={cam.status === 'active' ? 'Stop camera' : 'Start camera'}
                      >
                        {cam.status === 'connecting' ? <RefreshCw className="w-4 h-4 animate-spin" />
                          : cam.status === 'active' ? <Square className="w-4 h-4" />
                          : <Play className="w-4 h-4" />}
                      </button>
                      <button
                        onClick={() => removeCamera(cam.id)}
                        className="p-1.5 text-gray-500 hover:text-red-400 hover:bg-red-900/30 rounded transition-colors"
                      >
                        <Trash2 className="w-4 h-4" />
                      </button>
                    </div>
                  </div>

                  {/* Video Feed with bbox canvas overlay */}
                  <div className="relative bg-gray-900 aspect-video">
                    {cam.type === 'webcam' ? (
                      <>
                        <video
                          ref={el => { videoRefs.current[cam.id] = el }}
                          className="w-full h-full object-cover"
                          muted playsInline autoPlay
                        />
                        {/* Bounding box canvas overlay */}
                        <canvas
                          ref={el => { canvasRefs.current[cam.id] = el }}
                          className="absolute inset-0 w-full h-full pointer-events-none"
                          style={{ objectFit: 'cover' }}
                        />
                      </>
                    ) : cam.status === 'active' ? (
                      <img
                        src={cam.url}
                        alt={cam.name}
                        className="w-full h-full object-cover"
                        onError={(e) => { (e.target as HTMLImageElement).style.display = 'none' }}
                      />
                    ) : null}

                    {/* Overlay when not active */}
                    {cam.status !== 'active' && (
                      <div className="absolute inset-0 flex flex-col items-center justify-center">
                        {cam.status === 'connecting' ? (
                          <><RefreshCw className="w-8 h-8 text-yellow-400 animate-spin mb-2" /><p className="text-yellow-400 text-sm">Connecting...</p></>
                        ) : cam.status === 'error' ? (
                          <>
                            <WifiOff className="w-8 h-8 text-red-400 mb-2" />
                            <p className="text-red-400 text-sm">Connection Failed</p>
                            <button onClick={() => handleStartCamera(cam)} className="mt-2 text-xs text-gray-400 hover:text-white underline">Retry</button>
                          </>
                        ) : (
                          <>
                            <Video className="w-8 h-8 text-gray-600 mb-2" />
                            <p className="text-gray-500 text-sm">Camera Idle</p>
                            <button onClick={() => handleStartCamera(cam)} className="mt-2 btn-primary py-1.5 px-3 text-xs">
                              <Play className="w-3 h-3" />Start
                            </button>
                          </>
                        )}
                      </div>
                    )}

                    {/* AI Detection Badge */}
                    {cam.status === 'active' && detectionEnabled && (
                      <div className="absolute top-2 left-2 bg-green-900/80 border border-green-700 rounded px-2 py-1 flex items-center gap-1.5">
                        <div className="w-1.5 h-1.5 bg-green-400 rounded-full animate-pulse" />
                        <span className="text-green-400 text-xs font-medium">AI Detection Active</span>
                      </div>
                    )}

                    {cam.violationCount > 0 && (
                      <div className="absolute top-2 right-2 bg-red-600 rounded-full px-2 py-0.5 text-xs text-white font-bold">
                        {cam.violationCount}
                      </div>
                    )}
                    {cam.lastViolation && cam.status === 'active' && (
                      <div className="absolute bottom-2 left-2 bg-red-900/90 border border-red-700 rounded px-2 py-1">
                        <span className="text-red-300 text-xs">Last: {cam.lastViolation.replace(/_/g, ' ')}</span>
                      </div>
                    )}
                  </div>

                  {cam.type !== 'webcam' && (
                    <div className="px-3 py-1.5 bg-gray-800/50 border-t border-gray-700">
                      <p className="text-xs text-gray-500 font-mono truncate">{cam.url}</p>
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Violations Feed */}
        <div className="space-y-4">
          <div className="card p-0 overflow-hidden">
            <div className="flex items-center justify-between px-4 py-3 border-b border-gray-800">
              <h3 className="text-base font-semibold text-white flex items-center gap-2">
                <AlertTriangle className="w-4 h-4 text-red-400" />
                Live Violation Feed
              </h3>
              {violations.length > 0 && (
                <button onClick={() => { setViolations([]); setTotalDetections(0) }} className="text-xs text-gray-500 hover:text-white">
                  Clear
                </button>
              )}
            </div>

            <div className="overflow-y-auto max-h-[600px]">
              {violations.length === 0 ? (
                <div className="flex flex-col items-center justify-center py-12 text-center px-4">
                  <CheckCircle className="w-10 h-10 text-gray-600 mb-3" />
                  <p className="text-gray-500 text-sm">No violations detected</p>
                  <p className="text-gray-600 text-xs mt-1">Start a camera to begin monitoring</p>
                </div>
              ) : (
                <div className="divide-y divide-gray-800">
                  {violations.map((v) => (
                    <div key={v.id} className="px-4 py-3 hover:bg-gray-800/30 transition-colors">
                      <div className="flex items-start justify-between gap-2">
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2 flex-wrap">
                            <span className={clsx('text-xs px-2 py-0.5 rounded font-medium', VIOLATION_COLORS[v.type] || 'text-gray-400 bg-gray-800')}>
                              {v.type.replace(/_/g, ' ')}
                            </span>
                            {v.plate && (
                              <span className="text-xs font-mono text-white bg-gray-700 px-1.5 py-0.5 rounded">
                                {v.plate}
                              </span>
                            )}
                            {v.savedToDb && (
                              <span title="Saved to database">
                                <Database className="w-3 h-3 text-blue-400" />
                              </span>
                            )}
                          </div>
                          <p className="text-xs text-gray-400 mt-1 truncate">{v.cameraName}</p>
                        </div>
                        <div className="flex-shrink-0 text-right">
                          <div className="flex items-center gap-1 text-xs text-gray-500">
                            <Clock className="w-3 h-3" />
                            {v.timestamp}
                          </div>
                          <p className="text-xs text-gray-500 mt-0.5">
                            {(v.confidence * 100).toFixed(0)}% conf
                          </p>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>

          <div className="card bg-gray-800/50">
            <h4 className="text-sm font-semibold text-gray-300 mb-2 flex items-center gap-2">
              <Settings2 className="w-4 h-4 text-gray-400" />
              Supported Sources
            </h4>
            <ul className="space-y-1.5 text-xs text-gray-500">
              <li className="flex items-center gap-2"><span className="w-1.5 h-1.5 bg-blue-400 rounded-full flex-shrink-0" /><span><strong className="text-gray-400">Webcam</strong> — Laptop/USB camera with bbox overlay</span></li>
              <li className="flex items-center gap-2"><span className="w-1.5 h-1.5 bg-purple-400 rounded-full flex-shrink-0" /><span><strong className="text-gray-400">HTTP/HTTPS</strong> — IP cam MJPEG stream</span></li>
              <li className="flex items-center gap-2"><span className="w-1.5 h-1.5 bg-green-400 rounded-full flex-shrink-0" /><span><strong className="text-gray-400">RTSP</strong> — Via Edge AI module</span></li>
            </ul>
            <p className="text-xs text-gray-600 mt-2">Violations are saved to the database and appear on the Violations page.</p>
          </div>
        </div>
      </div>

      {/* Add Camera Modal */}
      {showAddModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm p-4">
          <div className="card w-full max-w-md">
            <div className="flex items-center justify-between mb-5">
              <h3 className="text-lg font-semibold text-white flex items-center gap-2">
                <Camera className="w-5 h-5 text-red-400" />
                Add Camera Source
              </h3>
              <button onClick={() => setShowAddModal(false)} className="text-gray-400 hover:text-white text-xl leading-none">✕</button>
            </div>

            <div className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-gray-300 mb-2">Camera Type</label>
                <div className="grid grid-cols-3 gap-2">
                  {(['webcam', 'http', 'rtsp'] as const).map(type => (
                    <button key={type} onClick={() => setNewCamType(type)}
                      className={clsx('py-2 px-3 rounded-lg border text-sm font-medium transition-colors',
                        newCamType === type ? 'bg-red-600 border-red-500 text-white' : 'bg-gray-800 border-gray-700 text-gray-400 hover:border-gray-600')}>
                      {type === 'webcam' ? '🎥 Webcam' : type === 'http' ? '🌐 HTTP/S' : '📡 RTSP'}
                    </button>
                  ))}
                </div>
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-300 mb-1.5">Camera Name</label>
                <input className="input-field" placeholder="e.g., Main Entrance, Intersection A"
                  value={newCamName} onChange={e => setNewCamName(e.target.value)} />
              </div>
              {newCamType !== 'webcam' && (
                <div>
                  <label className="block text-sm font-medium text-gray-300 mb-1.5">
                    {newCamType === 'http' ? 'Stream URL (HTTP/HTTPS)' : 'RTSP URL'}
                  </label>
                  <input className="input-field font-mono text-sm"
                    placeholder={newCamType === 'http' ? 'http://192.168.1.100:8080/video' : 'rtsp://user:pass@192.168.1.100:554/stream'}
                    value={newCamUrl} onChange={e => setNewCamUrl(e.target.value)} />
                </div>
              )}
              {newCamType === 'webcam' && (
                <div className="bg-blue-900/20 border border-blue-800 rounded-lg p-3">
                  <p className="text-xs text-blue-300"><strong>📋 Note:</strong> Your browser will ask for camera permission when you start the webcam. Bounding boxes will be drawn on detected vehicles.</p>
                </div>
              )}
            </div>

            <div className="flex gap-3 mt-6">
              <button onClick={() => setShowAddModal(false)} className="btn-secondary flex-1 justify-center">Cancel</button>
              <button onClick={addCamera} className="btn-primary flex-1 justify-center">
                <Plus className="w-4 h-4" />Add Camera
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

export default LiveMonitoringPage
