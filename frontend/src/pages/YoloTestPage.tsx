import React, { useEffect, useRef, useState } from 'react'
import {
  Upload, Activity, CheckCircle, XCircle, AlertTriangle,
  Video, RefreshCw, Database, Eye, Zap, Info
} from 'lucide-react'
import api from '../utils/api'
import toast from 'react-hot-toast'
import clsx from 'clsx'

interface YoloStatus {
  running: boolean
  model_name?: string
  device?: string
  mock_mode?: boolean
  error?: string
  last_checked?: string
}

interface Detection {
  class: string
  confidence: number
  bbox: [number, number, number, number]
  frame: number
  time_s: number
  mock?: boolean
}

interface AnalysisResult {
  success: boolean
  yolo_running: boolean
  mock_mode: boolean
  video_info: {
    total_frames: number
    fps: number
    duration_s: number
    frames_sampled: number
  }
  summary: {
    total_detections: number
    unique_vehicles_approx: number
    violations_saved: number
  }
  detections: Detection[]
  thumbnail_b64?: string
}

const CLASS_COLORS: Record<string, string> = {
  car: '#22c55e',
  motorcycle: '#f97316',
  bus: '#3b82f6',
  truck: '#ef4444',
  bicycle: '#eab308',
  person: '#a855f7',
}

const YoloTestPage: React.FC = () => {
  const [yoloStatus, setYoloStatus] = useState<YoloStatus | null>(null)
  const [loadingStatus, setLoadingStatus] = useState(true)
  const [file, setFile] = useState<File | null>(null)
  const [dragOver, setDragOver] = useState(false)
  const [analyzing, setAnalyzing] = useState(false)
  const [result, setResult] = useState<AnalysisResult | null>(null)
  const [confidence, setConfidence] = useState(0.5)
  const [saveViolations, setSaveViolations] = useState(false)
  const [location, setLocation] = useState('Admin Upload Test')
  const [cameraId, setCameraId] = useState('ADMIN_TEST')
  const fileInputRef = useRef<HTMLInputElement>(null)
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const [selectedFrame, setSelectedFrame] = useState<number | null>(null)

  const fetchStatus = async () => {
    setLoadingStatus(true)
    try {
      const res = await api.get('/yolo/status')
      setYoloStatus(res.data)
    } catch {
      setYoloStatus({ running: false, error: 'Cannot reach backend' })
    } finally {
      setLoadingStatus(false)
    }
  }

  useEffect(() => {
    fetchStatus()
    const interval = setInterval(fetchStatus, 20000)
    return () => clearInterval(interval)
  }, [])

  // Draw bboxes on canvas when result arrives
  useEffect(() => {
    if (!result?.thumbnail_b64 || !canvasRef.current) return
    const img = new Image()
    img.onload = () => {
      const canvas = canvasRef.current!
      canvas.width = img.width
      canvas.height = img.height
      const ctx = canvas.getContext('2d')!
      ctx.drawImage(img, 0, 0)

      // Filter detections for frame 0 (thumbnail)
      const frameDets = result.detections.filter(d => d.frame === 0)
      frameDets.forEach(det => {
        const [x1, y1, x2, y2] = det.bbox
        const color = CLASS_COLORS[det.class] || '#00ff00'
        ctx.strokeStyle = color
        ctx.lineWidth = 2
        ctx.strokeRect(x1, y1, x2 - x1, y2 - y1)
        const label = `${det.class} ${(det.confidence * 100).toFixed(0)}%`
        ctx.font = '13px monospace'
        const tw = ctx.measureText(label).width
        ctx.fillStyle = color + 'cc'
        ctx.fillRect(x1, Math.max(y1 - 20, 0), tw + 8, 18)
        ctx.fillStyle = '#000'
        ctx.fillText(label, x1 + 4, Math.max(y1 - 5, 13))
      })
    }
    img.src = `data:image/jpeg;base64,${result.thumbnail_b64}`
  }, [result])

  const handleFileSelect = (f: File) => {
    const allowed = ['video/mp4', 'video/avi', 'video/quicktime', 'video/x-matroska',
                     'video/webm', 'video/x-msvideo']
    if (!allowed.includes(f.type) && !f.name.match(/\.(mp4|avi|mov|mkv|webm)$/i)) {
      toast.error('Please upload a video file (MP4, AVI, MOV, MKV, WebM)')
      return
    }
    if (f.size > 200 * 1024 * 1024) {
      toast.error('File too large — max 200 MB')
      return
    }
    setFile(f)
    setResult(null)
  }

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault()
    setDragOver(false)
    const dropped = e.dataTransfer.files[0]
    if (dropped) handleFileSelect(dropped)
  }

  const handleAnalyze = async () => {
    if (!file) { toast.error('Please select a video file first'); return }
    setAnalyzing(true)
    setResult(null)
    try {
      const formData = new FormData()
      formData.append('file', file)
      formData.append('confidence', confidence.toString())
      formData.append('save_violations', saveViolations.toString())
      formData.append('location', location)
      formData.append('camera_id', cameraId)

      const res = await api.post('/yolo/analyze-video', formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
        timeout: 120000, // 2 min for large videos
      })
      setResult(res.data)
      if (res.data.success) {
        toast.success(`Analysis complete — ${res.data.summary.total_detections} detections`)
      }
    } catch (err: any) {
      const detail = err?.response?.data?.detail || 'Analysis failed'
      toast.error(detail)
    } finally {
      setAnalyzing(false)
    }
  }

  const classCount = result
    ? result.detections.reduce((acc, d) => {
        acc[d.class] = (acc[d.class] || 0) + 1
        return acc
      }, {} as Record<string, number>)
    : {}

  return (
    <div className="space-y-6 max-w-6xl">
      <div>
        <h1 className="text-2xl font-bold text-white">YOLO Model Test</h1>
        <p className="text-gray-400 text-sm mt-1">
          Upload a video to test YOLO object detection — see bounding boxes, detections, and optionally save violations.
        </p>
      </div>

      {/* YOLO Status Card */}
      <div className={clsx(
        'card border-2 transition-colors',
        loadingStatus ? 'border-gray-700' :
        yoloStatus?.running ? 'border-green-600' :
        yoloStatus?.mock_mode ? 'border-yellow-600' :
        'border-red-700'
      )}>
        <div className="flex items-center justify-between flex-wrap gap-3">
          <div className="flex items-center gap-4">
            <div className={clsx(
              'w-12 h-12 rounded-xl flex items-center justify-center flex-shrink-0',
              loadingStatus ? 'bg-gray-700' :
              yoloStatus?.running ? 'bg-green-600' :
              yoloStatus?.mock_mode ? 'bg-yellow-600' :
              'bg-red-700'
            )}>
              <Activity className="w-6 h-6 text-white" />
            </div>
            <div>
              <p className="text-white font-semibold flex items-center gap-2">
                YOLO Model Status
                {!loadingStatus && (
                  <span className={clsx(
                    'text-xs px-2 py-0.5 rounded-full font-medium',
                    yoloStatus?.running ? 'bg-green-900 text-green-300' :
                    yoloStatus?.mock_mode ? 'bg-yellow-900 text-yellow-300' :
                    'bg-red-900 text-red-300'
                  )}>
                    {yoloStatus?.running ? '● RUNNING' : yoloStatus?.mock_mode ? '⚠ MOCK MODE' : '✕ OFFLINE'}
                  </span>
                )}
              </p>
              {yoloStatus && (
                <div className="text-sm text-gray-400 mt-0.5 space-x-4">
                  {yoloStatus.model_name && <span>Model: <span className="text-white font-mono">{yoloStatus.model_name}</span></span>}
                  {yoloStatus.device && <span>Device: <span className="text-white">{yoloStatus.device}</span></span>}
                  {yoloStatus.mock_mode && <span className="text-yellow-400">Running in mock/demo mode (ultralytics not installed)</span>}
                  {yoloStatus.error && !yoloStatus.running && <span className="text-red-400">{yoloStatus.error}</span>}
                </div>
              )}
            </div>
          </div>
          <button
            onClick={fetchStatus}
            disabled={loadingStatus}
            className="btn-secondary text-sm"
          >
            <RefreshCw className={clsx('w-4 h-4', loadingStatus && 'animate-spin')} />
            Refresh
          </button>
        </div>

        {yoloStatus?.mock_mode && (
          <div className="mt-3 p-3 bg-yellow-900/20 border border-yellow-800 rounded-lg flex items-start gap-2">
            <Info className="w-4 h-4 text-yellow-400 flex-shrink-0 mt-0.5" />
            <p className="text-xs text-yellow-300">
              <strong>Mock Mode Active:</strong> The <code>ultralytics</code> package is not installed in the backend environment.
              Detection results will be simulated. To enable real YOLO detection, install <code>pip install ultralytics</code> and restart the backend.
              Mock mode still demonstrates the full API flow including bounding boxes and violation saving.
            </p>
          </div>
        )}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Upload + Config */}
        <div className="space-y-4">
          {/* Upload Zone */}
          <div className="card">
            <h3 className="text-base font-semibold text-white mb-3 flex items-center gap-2">
              <Upload className="w-4 h-4 text-blue-400" />
              Upload Video
            </h3>

            <div
              className={clsx(
                'border-2 border-dashed rounded-xl p-8 text-center cursor-pointer transition-colors',
                dragOver ? 'border-red-500 bg-red-900/20' :
                file ? 'border-green-600 bg-green-900/10' :
                'border-gray-700 hover:border-gray-500'
              )}
              onDragOver={e => { e.preventDefault(); setDragOver(true) }}
              onDragLeave={() => setDragOver(false)}
              onDrop={handleDrop}
              onClick={() => fileInputRef.current?.click()}
            >
              <input
                ref={fileInputRef}
                type="file"
                accept="video/mp4,video/avi,video/quicktime,video/x-matroska,video/webm,.mp4,.avi,.mov,.mkv,.webm"
                className="hidden"
                onChange={e => { if (e.target.files?.[0]) handleFileSelect(e.target.files[0]) }}
              />
              {file ? (
                <>
                  <Video className="w-10 h-10 text-green-400 mx-auto mb-3" />
                  <p className="text-green-400 font-medium text-sm">{file.name}</p>
                  <p className="text-gray-500 text-xs mt-1">{(file.size / 1024 / 1024).toFixed(1)} MB</p>
                  <button
                    onClick={e => { e.stopPropagation(); setFile(null); setResult(null) }}
                    className="mt-2 text-xs text-red-400 hover:text-red-300 underline"
                  >
                    Remove
                  </button>
                </>
              ) : (
                <>
                  <Video className="w-10 h-10 text-gray-600 mx-auto mb-3" />
                  <p className="text-gray-400 text-sm font-medium">Drop video here or click to browse</p>
                  <p className="text-gray-600 text-xs mt-1">MP4, AVI, MOV, MKV, WebM — max 200 MB</p>
                </>
              )}
            </div>
          </div>

          {/* Detection Settings */}
          <div className="card">
            <h3 className="text-base font-semibold text-white mb-3 flex items-center gap-2">
              <Zap className="w-4 h-4 text-yellow-400" />
              Detection Settings
            </h3>
            <div className="space-y-4">
              <div>
                <label className="block text-xs text-gray-400 mb-1.5">
                  Confidence Threshold: <span className="text-white font-mono">{confidence.toFixed(2)}</span>
                </label>
                <input
                  type="range" min="0.1" max="0.95" step="0.05"
                  value={confidence}
                  onChange={e => setConfidence(parseFloat(e.target.value))}
                  className="w-full accent-red-500"
                />
                <div className="flex justify-between text-xs text-gray-600 mt-0.5">
                  <span>0.10 (detect more)</span>
                  <span>0.95 (detect less)</span>
                </div>
              </div>

              <div>
                <label className="block text-xs text-gray-400 mb-1.5">Location Label</label>
                <input
                  className="input-field text-sm"
                  value={location}
                  onChange={e => setLocation(e.target.value)}
                  placeholder="e.g., Main Street Camera"
                />
              </div>

              <div>
                <label className="block text-xs text-gray-400 mb-1.5">Camera ID</label>
                <input
                  className="input-field text-sm"
                  value={cameraId}
                  onChange={e => setCameraId(e.target.value)}
                  placeholder="e.g., ADMIN_TEST"
                />
              </div>

              <div className="flex items-center gap-3 p-3 bg-gray-800 rounded-lg">
                <input
                  type="checkbox"
                  id="saveViolations"
                  checked={saveViolations}
                  onChange={e => setSaveViolations(e.target.checked)}
                  className="w-4 h-4 accent-red-500"
                />
                <div>
                  <label htmlFor="saveViolations" className="text-sm text-gray-300 cursor-pointer font-medium">
                    Save violations to database
                  </label>
                  <p className="text-xs text-gray-500 mt-0.5">Detected vehicles will create pending violation records</p>
                </div>
              </div>
            </div>

            <button
              onClick={handleAnalyze}
              disabled={!file || analyzing}
              className="btn-primary w-full justify-center mt-4 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {analyzing ? (
                <><RefreshCw className="w-4 h-4 animate-spin" />Analyzing Video...</>
              ) : (
                <><Activity className="w-4 h-4" />Run YOLO Analysis</>
              )}
            </button>
          </div>
        </div>

        {/* Results */}
        <div className="lg:col-span-2 space-y-4">
          {analyzing && (
            <div className="card flex flex-col items-center justify-center py-16">
              <RefreshCw className="w-12 h-12 text-red-500 animate-spin mb-4" />
              <p className="text-white font-semibold">Running YOLO Detection...</p>
              <p className="text-gray-400 text-sm mt-1">Sampling frames every 2 seconds. Please wait.</p>
            </div>
          )}

          {result && (
            <>
              {/* Summary Cards */}
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                {[
                  { label: 'Total Detections', value: result.summary.total_detections, color: 'bg-blue-600' },
                  { label: 'Unique Vehicles', value: result.summary.unique_vehicles_approx, color: 'bg-green-600' },
                  { label: 'Frames Sampled', value: result.video_info.frames_sampled, color: 'bg-purple-600' },
                  { label: 'Violations Saved', value: result.summary.violations_saved, color: 'bg-red-600' },
                ].map(({ label, value, color }) => (
                  <div key={label} className="card py-3">
                    <div className={`w-8 h-8 ${color} rounded-lg flex items-center justify-center mb-2`}>
                      <Database className="w-4 h-4 text-white" />
                    </div>
                    <p className="text-2xl font-bold text-white">{value}</p>
                    <p className="text-xs text-gray-400">{label}</p>
                  </div>
                ))}
              </div>

              {/* Video Info */}
              <div className="card">
                <h3 className="text-sm font-semibold text-gray-300 mb-3">Video Information</h3>
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 text-sm">
                  <div><span className="text-gray-400">Duration</span><p className="text-white font-mono mt-0.5">{result.video_info.duration_s.toFixed(1)}s</p></div>
                  <div><span className="text-gray-400">FPS</span><p className="text-white font-mono mt-0.5">{result.video_info.fps}</p></div>
                  <div><span className="text-gray-400">Total Frames</span><p className="text-white font-mono mt-0.5">{result.video_info.total_frames}</p></div>
                  <div><span className="text-gray-400">Mode</span><p className={clsx('font-medium mt-0.5', result.mock_mode ? 'text-yellow-400' : 'text-green-400')}>{result.mock_mode ? 'Mock' : 'Real YOLO'}</p></div>
                </div>
              </div>

              {/* Thumbnail with bboxes */}
              {result.thumbnail_b64 && (
                <div className="card">
                  <h3 className="text-sm font-semibold text-gray-300 mb-3 flex items-center gap-2">
                    <Eye className="w-4 h-4 text-blue-400" />
                    First Frame — Bounding Boxes
                  </h3>
                  <div className="rounded-lg overflow-hidden border border-gray-700">
                    <canvas ref={canvasRef} className="w-full" />
                  </div>
                </div>
              )}

              {/* Class breakdown */}
              {Object.keys(classCount).length > 0 && (
                <div className="card">
                  <h3 className="text-sm font-semibold text-gray-300 mb-3">Detection Breakdown by Class</h3>
                  <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
                    {Object.entries(classCount).sort((a, b) => b[1] - a[1]).map(([cls, cnt]) => (
                      <div key={cls} className="flex items-center gap-3 bg-gray-800 rounded-lg px-3 py-2">
                        <span
                          className="w-3 h-3 rounded-full flex-shrink-0"
                          style={{ backgroundColor: CLASS_COLORS[cls] || '#aaa' }}
                        />
                        <div>
                          <p className="text-white text-sm font-medium capitalize">{cls}</p>
                          <p className="text-gray-400 text-xs">{cnt} detections</p>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Detections Table */}
              {result.detections.length > 0 && (
                <div className="card p-0 overflow-hidden">
                  <div className="px-4 py-3 border-b border-gray-800">
                    <h3 className="text-sm font-semibold text-gray-300">
                      Detection Log ({Math.min(result.detections.length, 200)} shown)
                    </h3>
                  </div>
                  <div className="overflow-x-auto max-h-80 overflow-y-auto">
                    <table className="w-full text-xs">
                      <thead className="bg-gray-800 sticky top-0">
                        <tr>
                          <th className="px-3 py-2 text-left text-gray-400">Frame</th>
                          <th className="px-3 py-2 text-left text-gray-400">Time</th>
                          <th className="px-3 py-2 text-left text-gray-400">Class</th>
                          <th className="px-3 py-2 text-left text-gray-400">Confidence</th>
                          <th className="px-3 py-2 text-left text-gray-400">BBox (x1,y1,x2,y2)</th>
                          {result.mock_mode && <th className="px-3 py-2 text-left text-gray-400">Mode</th>}
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-gray-800">
                        {result.detections.map((d, i) => (
                          <tr key={i} className="hover:bg-gray-800/50">
                            <td className="px-3 py-2 text-gray-400 font-mono">{d.frame}</td>
                            <td className="px-3 py-2 text-gray-400 font-mono">{d.time_s.toFixed(1)}s</td>
                            <td className="px-3 py-2">
                              <span className="flex items-center gap-1.5">
                                <span
                                  className="w-2 h-2 rounded-full"
                                  style={{ backgroundColor: CLASS_COLORS[d.class] || '#aaa' }}
                                />
                                <span className="text-white capitalize">{d.class}</span>
                              </span>
                            </td>
                            <td className="px-3 py-2">
                              <span className={clsx('font-mono', d.confidence > 0.8 ? 'text-green-400' : d.confidence > 0.6 ? 'text-yellow-400' : 'text-red-400')}>
                                {(d.confidence * 100).toFixed(1)}%
                              </span>
                            </td>
                            <td className="px-3 py-2 text-gray-400 font-mono text-xs">
                              [{d.bbox.map(v => Math.round(v)).join(', ')}]
                            </td>
                            {result.mock_mode && (
                              <td className="px-3 py-2">
                                <span className="text-yellow-500 text-xs">mock</span>
                              </td>
                            )}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}

              {saveViolations && result.summary.violations_saved > 0 && (
                <div className="p-3 bg-green-900/20 border border-green-800 rounded-lg flex items-center gap-3">
                  <CheckCircle className="w-5 h-5 text-green-400 flex-shrink-0" />
                  <p className="text-sm text-green-300">
                    <strong>{result.summary.violations_saved} violation records</strong> saved to the database as PENDING.
                    Go to the <strong>Violations</strong> page to review them.
                  </p>
                </div>
              )}
            </>
          )}

          {!result && !analyzing && (
            <div className="card flex flex-col items-center justify-center py-20 text-center">
              <Video className="w-16 h-16 text-gray-700 mb-4" />
              <p className="text-gray-400 font-medium">No analysis yet</p>
              <p className="text-gray-600 text-sm mt-1">Upload a video and click "Run YOLO Analysis" to see results here.</p>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

export default YoloTestPage
