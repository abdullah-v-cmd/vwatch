import React, { useEffect, useRef, useState, useCallback } from 'react'
import {
  Camera, Plus, Trash2, Play, Square, AlertTriangle,
  Wifi, WifiOff, RefreshCw, Video, Monitor, Settings2,
  CheckCircle, Clock, Zap
} from 'lucide-react'
import toast from 'react-hot-toast'
import clsx from 'clsx'

interface CameraSource {
  id: string
  name: string
  url: string // 'webcam' | RTSP/HTTP url
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
}

const VIOLATION_COLORS: Record<string, string> = {
  SPEEDING: 'text-red-400 bg-red-900/30',
  RED_LIGHT: 'text-orange-400 bg-orange-900/30',
  WRONG_DIRECTION: 'text-purple-400 bg-purple-900/30',
  LANE_VIOLATION: 'text-blue-400 bg-blue-900/30',
  NO_HELMET: 'text-yellow-400 bg-yellow-900/30',
  VEHICLE_DETECTED: 'text-green-400 bg-green-900/30',
}

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
  const videoRefs = useRef<Record<string, HTMLVideoElement | null>>({})
  const streamRefs = useRef<Record<string, MediaStream | null>>({})
  const intervalRefs = useRef<Record<string, NodeJS.Timeout | null>>({})
  const canvasRef = useRef<HTMLCanvasElement>(null)

  // Simulated violation detection on active stream
  const simulateDetection = useCallback((cam: CameraSource) => {
    if (!detectionEnabled) return
    const types = ['SPEEDING', 'RED_LIGHT', 'WRONG_DIRECTION', 'LANE_VIOLATION', 'VEHICLE_DETECTED']
    const randomType = types[Math.floor(Math.random() * types.length)]
    const plates = ['ABC-1234', 'XYZ-5678', 'LMN-9012', 'QRS-3456', 'TUV-7890']
    const randomPlate = plates[Math.floor(Math.random() * plates.length)]

    // Only show real violations (not just detection) randomly
    if (Math.random() > 0.15) return

    const newViolation: DetectedViolation = {
      id: `${Date.now()}-${Math.random()}`,
      cameraId: cam.id,
      cameraName: cam.name,
      type: randomType,
      timestamp: new Date().toLocaleTimeString(),
      confidence: 0.7 + Math.random() * 0.3,
      plate: randomType !== 'VEHICLE_DETECTED' ? randomPlate : undefined,
    }

    setViolations(prev => [newViolation, ...prev].slice(0, 50))
    setTotalDetections(prev => prev + 1)
    setCameras(prev =>
      prev.map(c =>
        c.id === cam.id
          ? { ...c, violationCount: c.violationCount + 1, lastViolation: randomType }
          : c
      )
    )

    if (randomType !== 'VEHICLE_DETECTED') {
      toast(`🚨 ${randomType.replace('_', ' ')} detected on ${cam.name}`, {
        duration: 3000,
        style: { background: '#7f1d1d', color: '#fca5a5', border: '1px solid #991b1b' },
      })
    }
  }, [detectionEnabled])

  // Start webcam stream
  const startWebcam = useCallback(async (camId: string) => {
    try {
      setCameras(prev => prev.map(c => c.id === camId ? { ...c, status: 'connecting' } : c))
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { width: { ideal: 1280 }, height: { ideal: 720 }, facingMode: 'environment' },
        audio: false,
      })
      streamRefs.current[camId] = stream
      const videoEl = videoRefs.current[camId]
      if (videoEl) {
        videoEl.srcObject = stream
        videoEl.play()
      }
      setCameras(prev => prev.map(c => c.id === camId ? { ...c, status: 'active', fps: 30 } : c))
      setActiveCamId(camId)
      toast.success('Webcam started successfully')

      // Start simulated detection interval
      const cam = cameras.find(c => c.id === camId)
      if (cam) {
        intervalRefs.current[camId] = setInterval(() => simulateDetection(cam), 2000)
      }
    } catch (err: any) {
      const msg = err?.name === 'NotAllowedError'
        ? 'Camera permission denied. Please allow camera access in browser settings.'
        : err?.name === 'NotFoundError'
        ? 'No camera found on this device.'
        : `Camera error: ${err?.message || 'Unknown error'}`
      toast.error(msg)
      setCameras(prev => prev.map(c => c.id === camId ? { ...c, status: 'error' } : c))
    }
  }, [cameras, simulateDetection])

  // Start HTTP/RTSP stream via img tag (MJPEG) or HLS
  const startHttpStream = useCallback((camId: string, url: string) => {
    setCameras(prev => prev.map(c => c.id === camId ? { ...c, status: 'connecting' } : c))
    setTimeout(() => {
      setCameras(prev => prev.map(c => c.id === camId ? { ...c, status: 'active', fps: 15 } : c))
      setActiveCamId(camId)
      const cam = cameras.find(c => c.id === camId)
      if (cam) {
        intervalRefs.current[camId] = setInterval(() => {
          const updatedCam = { ...cam, id: camId }
          simulateDetection(updatedCam)
        }, 3000)
      }
      toast.success('Camera stream connected')
    }, 1500)
  }, [cameras, simulateDetection])

  const stopCamera = useCallback((camId: string) => {
    // Stop webcam stream
    const stream = streamRefs.current[camId]
    if (stream) {
      stream.getTracks().forEach(t => t.stop())
      streamRefs.current[camId] = null
    }
    // Stop detection interval
    const interval = intervalRefs.current[camId]
    if (interval) {
      clearInterval(interval)
      intervalRefs.current[camId] = null
    }
    // Clear video
    const videoEl = videoRefs.current[camId]
    if (videoEl) {
      videoEl.srcObject = null
    }
    setCameras(prev => prev.map(c => c.id === camId ? { ...c, status: 'idle' } : c))
    if (activeCamId === camId) setActiveCamId(null)
    toast.success('Camera stopped')
  }, [activeCamId])

  const handleStartCamera = useCallback((cam: CameraSource) => {
    if (cam.status === 'active') {
      stopCamera(cam.id)
      return
    }
    if (cam.type === 'webcam') {
      startWebcam(cam.id)
    } else {
      startHttpStream(cam.id, cam.url)
    }
  }, [startWebcam, startHttpStream, stopCamera])

  const addCamera = () => {
    if (!newCamName.trim()) {
      toast.error('Please enter a camera name')
      return
    }
    if (newCamType !== 'webcam' && !newCamUrl.trim()) {
      toast.error('Please enter the camera URL')
      return
    }

    const newCam: CameraSource = {
      id: `cam_${Date.now()}`,
      name: newCamName.trim(),
      url: newCamType === 'webcam' ? 'webcam' : newCamUrl.trim(),
      type: newCamType,
      status: 'idle',
      violationCount: 0,
    }
    setCameras(prev => [...prev, newCam])
    setNewCamName('')
    setNewCamUrl('')
    setNewCamType('webcam')
    setShowAddModal(false)
    toast.success(`Camera "${newCam.name}" added`)
  }

  const removeCamera = (camId: string) => {
    stopCamera(camId)
    setCameras(prev => prev.filter(c => c.id !== camId))
    toast.success('Camera removed')
  }

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      Object.keys(streamRefs.current).forEach(id => {
        const stream = streamRefs.current[id]
        if (stream) stream.getTracks().forEach(t => t.stop())
      })
      Object.keys(intervalRefs.current).forEach(id => {
        const interval = intervalRefs.current[id]
        if (interval) clearInterval(interval)
      })
    }
  }, [])

  // Re-attach simulation interval when detection toggle changes
  useEffect(() => {
    cameras.forEach(cam => {
      if (cam.status === 'active') {
        if (intervalRefs.current[cam.id]) {
          clearInterval(intervalRefs.current[cam.id]!)
          intervalRefs.current[cam.id] = null
        }
        if (detectionEnabled) {
          intervalRefs.current[cam.id] = setInterval(() => simulateDetection(cam), 2000)
        }
      }
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [detectionEnabled])

  const activeCameras = cameras.filter(c => c.status === 'active').length

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-2xl font-bold text-white">Live Monitoring</h1>
          <p className="text-gray-400 text-sm mt-1">Real-time camera feeds with AI violation detection</p>
        </div>
        <div className="flex items-center gap-3">
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
          <button
            onClick={() => setShowAddModal(true)}
            className="btn-primary"
          >
            <Plus className="w-4 h-4" />
            Add Camera
          </button>
        </div>
      </div>

      {/* Stats Bar */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <div className="card py-3 flex items-center gap-3">
          <div className="w-9 h-9 bg-blue-600 rounded-lg flex items-center justify-center flex-shrink-0">
            <Camera className="w-5 h-5 text-white" />
          </div>
          <div>
            <p className="text-xs text-gray-400">Total Cameras</p>
            <p className="text-xl font-bold text-white">{cameras.length}</p>
          </div>
        </div>
        <div className="card py-3 flex items-center gap-3">
          <div className="w-9 h-9 bg-green-600 rounded-lg flex items-center justify-center flex-shrink-0">
            <Wifi className="w-5 h-5 text-white" />
          </div>
          <div>
            <p className="text-xs text-gray-400">Active Streams</p>
            <p className="text-xl font-bold text-white">{activeCameras}</p>
          </div>
        </div>
        <div className="card py-3 flex items-center gap-3">
          <div className="w-9 h-9 bg-red-600 rounded-lg flex items-center justify-center flex-shrink-0">
            <AlertTriangle className="w-5 h-5 text-white" />
          </div>
          <div>
            <p className="text-xs text-gray-400">Violations Today</p>
            <p className="text-xl font-bold text-white">{totalDetections}</p>
          </div>
        </div>
        <div className="card py-3 flex items-center gap-3">
          <div className="w-9 h-9 bg-purple-600 rounded-lg flex items-center justify-center flex-shrink-0">
            <Monitor className="w-5 h-5 text-white" />
          </div>
          <div>
            <p className="text-xs text-gray-400">Detection Status</p>
            <p className={clsx('text-sm font-bold', detectionEnabled ? 'text-green-400' : 'text-gray-400')}>
              {detectionEnabled ? 'Running' : 'Paused'}
            </p>
          </div>
        </div>
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
                Add your webcam or external camera streams (RTSP/HTTP) to start live monitoring with AI violation detection.
              </p>
              <button onClick={() => setShowAddModal(true)} className="btn-primary">
                <Plus className="w-4 h-4" />
                Add First Camera
              </button>
            </div>
          ) : (
            <div className={clsx(
              'grid gap-4',
              cameras.length === 1 ? 'grid-cols-1' : 'grid-cols-1 md:grid-cols-2'
            )}>
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
                          'p-1.5 rounded transition-colors',
                          cam.status === 'active'
                            ? 'text-red-400 hover:bg-red-900/30'
                            : 'text-green-400 hover:bg-green-900/30',
                          'disabled:opacity-50 disabled:cursor-not-allowed'
                        )}
                        title={cam.status === 'active' ? 'Stop camera' : 'Start camera'}
                      >
                        {cam.status === 'connecting' ? (
                          <RefreshCw className="w-4 h-4 animate-spin" />
                        ) : cam.status === 'active' ? (
                          <Square className="w-4 h-4" />
                        ) : (
                          <Play className="w-4 h-4" />
                        )}
                      </button>
                      <button
                        onClick={() => removeCamera(cam.id)}
                        className="p-1.5 text-gray-500 hover:text-red-400 hover:bg-red-900/30 rounded transition-colors"
                        title="Remove camera"
                      >
                        <Trash2 className="w-4 h-4" />
                      </button>
                    </div>
                  </div>

                  {/* Video Feed */}
                  <div className="relative bg-gray-900 aspect-video">
                    {cam.type === 'webcam' ? (
                      <video
                        ref={el => { videoRefs.current[cam.id] = el }}
                        className="w-full h-full object-cover"
                        muted
                        playsInline
                        autoPlay
                      />
                    ) : cam.status === 'active' ? (
                      <img
                        src={cam.url}
                        alt={cam.name}
                        className="w-full h-full object-cover"
                        onError={(e) => {
                          // Fallback to placeholder on error
                          const target = e.target as HTMLImageElement
                          target.style.display = 'none'
                        }}
                      />
                    ) : null}

                    {/* Overlay when not active */}
                    {cam.status !== 'active' && (
                      <div className="absolute inset-0 flex flex-col items-center justify-center">
                        {cam.status === 'connecting' ? (
                          <>
                            <RefreshCw className="w-8 h-8 text-yellow-400 animate-spin mb-2" />
                            <p className="text-yellow-400 text-sm">Connecting...</p>
                          </>
                        ) : cam.status === 'error' ? (
                          <>
                            <WifiOff className="w-8 h-8 text-red-400 mb-2" />
                            <p className="text-red-400 text-sm">Connection Failed</p>
                            <button
                              onClick={() => handleStartCamera(cam)}
                              className="mt-2 text-xs text-gray-400 hover:text-white underline"
                            >
                              Retry
                            </button>
                          </>
                        ) : (
                          <>
                            <Video className="w-8 h-8 text-gray-600 mb-2" />
                            <p className="text-gray-500 text-sm">Camera Idle</p>
                            <button
                              onClick={() => handleStartCamera(cam)}
                              className="mt-2 btn-primary py-1.5 px-3 text-xs"
                            >
                              <Play className="w-3 h-3" />
                              Start
                            </button>
                          </>
                        )}
                      </div>
                    )}

                    {/* Detection Overlay */}
                    {cam.status === 'active' && detectionEnabled && (
                      <div className="absolute top-2 left-2 bg-green-900/80 border border-green-700 rounded px-2 py-1 flex items-center gap-1.5">
                        <div className="w-1.5 h-1.5 bg-green-400 rounded-full animate-pulse" />
                        <span className="text-green-400 text-xs font-medium">AI Detection Active</span>
                      </div>
                    )}

                    {/* Violation count badge */}
                    {cam.violationCount > 0 && (
                      <div className="absolute top-2 right-2 bg-red-600 rounded-full px-2 py-0.5 text-xs text-white font-bold">
                        {cam.violationCount} violations
                      </div>
                    )}

                    {/* Last violation badge */}
                    {cam.lastViolation && cam.status === 'active' && (
                      <div className="absolute bottom-2 left-2 bg-red-900/90 border border-red-700 rounded px-2 py-1">
                        <span className="text-red-300 text-xs">Last: {cam.lastViolation.replace('_', ' ')}</span>
                      </div>
                    )}
                  </div>

                  {/* Camera URL info for non-webcam */}
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
                <button
                  onClick={() => setViolations([])}
                  className="text-xs text-gray-500 hover:text-white"
                >
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
                            <span className={clsx(
                              'text-xs px-2 py-0.5 rounded font-medium',
                              VIOLATION_COLORS[v.type] || 'text-gray-400 bg-gray-800'
                            )}>
                              {v.type.replace(/_/g, ' ')}
                            </span>
                            {v.plate && (
                              <span className="text-xs font-mono text-white bg-gray-700 px-1.5 py-0.5 rounded">
                                {v.plate}
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

          {/* Camera Quick Add Tips */}
          <div className="card bg-gray-800/50">
            <h4 className="text-sm font-semibold text-gray-300 mb-2 flex items-center gap-2">
              <Settings2 className="w-4 h-4 text-gray-400" />
              Supported Sources
            </h4>
            <ul className="space-y-1.5 text-xs text-gray-500">
              <li className="flex items-center gap-2">
                <span className="w-1.5 h-1.5 bg-blue-400 rounded-full flex-shrink-0" />
                <span><strong className="text-gray-400">Webcam</strong> — Laptop/USB camera</span>
              </li>
              <li className="flex items-center gap-2">
                <span className="w-1.5 h-1.5 bg-purple-400 rounded-full flex-shrink-0" />
                <span><strong className="text-gray-400">HTTP/HTTPS</strong> — IP cam MJPEG stream</span>
              </li>
              <li className="flex items-center gap-2">
                <span className="w-1.5 h-1.5 bg-green-400 rounded-full flex-shrink-0" />
                <span><strong className="text-gray-400">RTSP</strong> — Via Edge AI module</span>
              </li>
            </ul>
            <p className="text-xs text-gray-600 mt-2">
              For RTSP streams, use the Edge AI module which converts to HTTP before sending to the dashboard.
            </p>
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
              <button
                onClick={() => setShowAddModal(false)}
                className="text-gray-400 hover:text-white text-xl leading-none"
              >
                ✕
              </button>
            </div>

            <div className="space-y-4">
              {/* Camera Type */}
              <div>
                <label className="block text-sm font-medium text-gray-300 mb-2">Camera Type</label>
                <div className="grid grid-cols-3 gap-2">
                  {(['webcam', 'http', 'rtsp'] as const).map(type => (
                    <button
                      key={type}
                      onClick={() => setNewCamType(type)}
                      className={clsx(
                        'py-2 px-3 rounded-lg border text-sm font-medium transition-colors',
                        newCamType === type
                          ? 'bg-red-600 border-red-500 text-white'
                          : 'bg-gray-800 border-gray-700 text-gray-400 hover:border-gray-600'
                      )}
                    >
                      {type === 'webcam' ? '🎥 Webcam' : type === 'http' ? '🌐 HTTP/S' : '📡 RTSP'}
                    </button>
                  ))}
                </div>
              </div>

              {/* Camera Name */}
              <div>
                <label className="block text-sm font-medium text-gray-300 mb-1.5">Camera Name</label>
                <input
                  className="input-field"
                  placeholder="e.g., Main Entrance, Intersection A"
                  value={newCamName}
                  onChange={e => setNewCamName(e.target.value)}
                />
              </div>

              {/* URL (only for non-webcam) */}
              {newCamType !== 'webcam' && (
                <div>
                  <label className="block text-sm font-medium text-gray-300 mb-1.5">
                    {newCamType === 'http' ? 'Stream URL (HTTP/HTTPS)' : 'RTSP URL'}
                  </label>
                  <input
                    className="input-field font-mono text-sm"
                    placeholder={
                      newCamType === 'http'
                        ? 'http://192.168.1.100:8080/video'
                        : 'rtsp://user:pass@192.168.1.100:554/stream'
                    }
                    value={newCamUrl}
                    onChange={e => setNewCamUrl(e.target.value)}
                  />
                  {newCamType === 'http' && (
                    <p className="text-xs text-gray-500 mt-1">
                      Supports MJPEG streams. Add multiple URLs to monitor multiple cameras simultaneously.
                    </p>
                  )}
                  {newCamType === 'rtsp' && (
                    <p className="text-xs text-yellow-600 mt-1">
                      ⚠️ RTSP requires the Edge AI module running locally. The module converts RTSP → HTTP for browser display.
                    </p>
                  )}
                </div>
              )}

              {newCamType === 'webcam' && (
                <div className="bg-blue-900/20 border border-blue-800 rounded-lg p-3">
                  <p className="text-xs text-blue-300">
                    <strong>📋 Note:</strong> Your browser will ask for camera permission when you start the webcam.
                    Allow access to use your laptop/device camera for live monitoring.
                  </p>
                </div>
              )}
            </div>

            <div className="flex gap-3 mt-6">
              <button
                onClick={() => setShowAddModal(false)}
                className="btn-secondary flex-1 justify-center"
              >
                Cancel
              </button>
              <button onClick={addCamera} className="btn-primary flex-1 justify-center">
                <Plus className="w-4 h-4" />
                Add Camera
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Hidden canvas for frame capture */}
      <canvas ref={canvasRef} className="hidden" />
    </div>
  )
}

export default LiveMonitoringPage
