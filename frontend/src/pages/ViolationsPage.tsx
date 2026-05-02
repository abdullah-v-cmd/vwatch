import React, { useEffect, useState, useCallback, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Search, RefreshCw, Eye, CheckCircle, XCircle,
  AlertTriangle, ChevronLeft, ChevronRight, Wifi, WifiOff
} from 'lucide-react'
import { violationsApi } from '../utils/api'
import toast from 'react-hot-toast'
import { format } from 'date-fns'
import clsx from 'clsx'

interface Violation {
  id: number
  evidence_id: string
  plate_number: string
  violation_type: string
  status: string
  speed_recorded?: number
  location: string
  camera_id: string
  violation_time: string
  confidence: number
  fine_amount?: number
  frame_image_url?: string
}

const STATUS_BADGE: Record<string, string> = {
  pending: 'badge-pending',
  approved: 'badge-approved',
  rejected: 'badge-rejected',
  paid: 'badge-paid',
  appealed: 'badge-pending',
}

const TYPE_COLORS: Record<string, string> = {
  SPEEDING: 'text-red-400',
  RED_LIGHT: 'text-orange-400',
  WRONG_DIRECTION: 'text-purple-400',
  LANE_VIOLATION: 'text-blue-400',
  NO_HELMET: 'text-yellow-400',
}

const ViolationsPage: React.FC = () => {
  const navigate = useNavigate()
  const [violations, setViolations] = useState<Violation[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [pageSize] = useState(20)
  const [loading, setLoading] = useState(true)
  const [liveConnected, setLiveConnected] = useState(false)
  const [newCount, setNewCount] = useState(0)

  // Filters
  const [search, setSearch] = useState('')
  const [filterStatus, setFilterStatus] = useState('')
  const [filterType, setFilterType] = useState('')

  // Review modal
  const [reviewModal, setReviewModal] = useState<{ id: number; action: 'approve' | 'reject' } | null>(null)
  const [remarks, setRemarks] = useState('')
  const [reviewing, setReviewing] = useState(false)

  const wsRef = useRef<WebSocket | null>(null)
  const mountedRef = useRef(true)

  const fetchViolations = useCallback(async () => {
    setLoading(true)
    try {
      const params: Record<string, unknown> = { page, page_size: pageSize }
      if (search) params.search = search
      if (filterStatus) params.status = filterStatus
      if (filterType) params.violation_type = filterType
      const res = await violationsApi.list(params)
      setViolations(res.data.items || [])
      setTotal(res.data.total || 0)
      setNewCount(0)
    } catch {
      toast.error('Failed to load violations')
    } finally {
      setLoading(false)
    }
  }, [page, pageSize, search, filterStatus, filterType])

  useEffect(() => {
    fetchViolations()
  }, [fetchViolations])

  // ── WebSocket: listen for new violations and refresh the list ──────────────
  useEffect(() => {
    mountedRef.current = true

    const wsBase = (import.meta.env.VITE_WS_URL || 'ws://localhost:8000/api/v1')
    const wsUrl = `${wsBase}/live/ws`

    const connect = () => {
      if (!mountedRef.current) return
      try {
        const ws = new WebSocket(wsUrl)
        wsRef.current = ws

        ws.onopen = () => {
          if (mountedRef.current) setLiveConnected(true)
        }

        ws.onmessage = (event) => {
          try {
            const msg = JSON.parse(event.data)
            if (msg.type === 'violation' && mountedRef.current) {
              // Increment new count — user can click refresh to see them
              setNewCount(prev => prev + 1)
              // Auto-refresh if on first page with no filters active
              if (page === 1 && !search && !filterStatus && !filterType) {
                setTimeout(() => {
                  if (mountedRef.current) fetchViolations()
                }, 800)
              }
            }
          } catch { /* ignore */ }
        }

        ws.onclose = () => {
          if (mountedRef.current) {
            setLiveConnected(false)
            setTimeout(connect, 5000)
          }
        }

        ws.onerror = () => {
          if (mountedRef.current) setLiveConnected(false)
        }
      } catch { /* ignore */ }
    }

    connect()

    return () => {
      mountedRef.current = false
      wsRef.current?.close()
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const handleReview = async () => {
    if (!reviewModal) return
    setReviewing(true)
    try {
      if (reviewModal.action === 'approve') {
        await violationsApi.approve(reviewModal.id, remarks)
        toast.success('Violation approved')
      } else {
        await violationsApi.reject(reviewModal.id, remarks)
        toast.success('Violation rejected')
      }
      setReviewModal(null)
      setRemarks('')
      fetchViolations()
    } catch (err: any) {
      toast.error(err?.response?.data?.detail || 'Review failed')
    } finally {
      setReviewing(false)
    }
  }

  const totalPages = Math.max(1, Math.ceil(total / pageSize))

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-2xl font-bold text-white">Violations</h1>
          <p className="text-gray-400 text-sm mt-1">{total} total violations</p>
        </div>
        <div className="flex items-center gap-3">
          {/* Live indicator */}
          <div className={clsx(
            'flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg border text-xs font-medium',
            liveConnected
              ? 'bg-green-900/30 border-green-700 text-green-400'
              : 'bg-gray-800 border-gray-700 text-gray-400'
          )}>
            {liveConnected ? <Wifi className="w-3.5 h-3.5" /> : <WifiOff className="w-3.5 h-3.5" />}
            {liveConnected ? 'Live' : 'Offline'}
          </div>

          {/* New violations notification */}
          {newCount > 0 && (
            <button
              onClick={fetchViolations}
              className="flex items-center gap-2 bg-blue-900/30 border border-blue-700 rounded-lg px-3 py-1.5 text-xs text-blue-400 hover:bg-blue-900/50 transition-colors"
            >
              <AlertTriangle className="w-3.5 h-3.5" />
              {newCount} new — click to refresh
            </button>
          )}

          <button onClick={fetchViolations} className="btn-secondary">
            <RefreshCw className={clsx('w-4 h-4', loading && 'animate-spin')} />
            Refresh
          </button>
        </div>
      </div>

      {/* Filters */}
      <div className="card">
        <div className="flex flex-wrap gap-3">
          <div className="relative flex-1 min-w-48">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
            <input
              className="input-field pl-9"
              placeholder="Search plate, location..."
              value={search}
              onChange={(e) => { setSearch(e.target.value); setPage(1) }}
            />
          </div>
          <select
            className="input-field w-40"
            value={filterStatus}
            onChange={(e) => { setFilterStatus(e.target.value); setPage(1) }}
          >
            <option value="">All Status</option>
            <option value="pending">Pending</option>
            <option value="approved">Approved</option>
            <option value="rejected">Rejected</option>
            <option value="paid">Paid</option>
          </select>
          <select
            className="input-field w-44"
            value={filterType}
            onChange={(e) => { setFilterType(e.target.value); setPage(1) }}
          >
            <option value="">All Types</option>
            <option value="SPEEDING">Speeding</option>
            <option value="RED_LIGHT">Red Light</option>
            <option value="WRONG_DIRECTION">Wrong Direction</option>
            <option value="LANE_VIOLATION">Lane Violation</option>
            <option value="NO_HELMET">No Helmet</option>
          </select>
        </div>
      </div>

      {/* Table */}
      <div className="card p-0 overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-gray-800">
              <tr>
                <th className="px-4 py-3 text-left text-gray-400 font-medium">ID</th>
                <th className="px-4 py-3 text-left text-gray-400 font-medium">Plate</th>
                <th className="px-4 py-3 text-left text-gray-400 font-medium">Type</th>
                <th className="px-4 py-3 text-left text-gray-400 font-medium">Status</th>
                <th className="px-4 py-3 text-left text-gray-400 font-medium">Speed</th>
                <th className="px-4 py-3 text-left text-gray-400 font-medium">Location</th>
                <th className="px-4 py-3 text-left text-gray-400 font-medium">Date/Time</th>
                <th className="px-4 py-3 text-left text-gray-400 font-medium">Fine</th>
                <th className="px-4 py-3 text-left text-gray-400 font-medium">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-800">
              {loading ? (
                <tr>
                  <td colSpan={9} className="text-center py-12">
                    <div className="animate-spin w-6 h-6 border-2 border-red-500 border-t-transparent rounded-full mx-auto" />
                  </td>
                </tr>
              ) : violations.length === 0 ? (
                <tr>
                  <td colSpan={9} className="text-center py-12 text-gray-500">
                    <AlertTriangle className="w-8 h-8 mx-auto mb-2 opacity-50" />
                    No violations found
                  </td>
                </tr>
              ) : (
                violations.map((v) => (
                  <tr key={v.id} className="hover:bg-gray-800/50 transition-colors">
                    <td className="px-4 py-3 text-gray-400 font-mono text-xs">#{v.id}</td>
                    <td className="px-4 py-3">
                      <span className="font-bold text-white font-mono">{v.plate_number}</span>
                    </td>
                    <td className="px-4 py-3">
                      <span className={clsx('font-medium', TYPE_COLORS[v.violation_type] || 'text-gray-300')}>
                        {v.violation_type.replace(/_/g, ' ')}
                      </span>
                    </td>
                    <td className="px-4 py-3">
                      <span className={STATUS_BADGE[v.status] || 'badge-pending'}>
                        {v.status}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-gray-300">
                      {v.speed_recorded ? `${v.speed_recorded.toFixed(0)} km/h` : '-'}
                    </td>
                    <td className="px-4 py-3 text-gray-400 max-w-36 truncate">{v.location}</td>
                    <td className="px-4 py-3 text-gray-400 text-xs">
                      {v.violation_time ? format(new Date(v.violation_time), 'MMM dd, HH:mm') : '-'}
                    </td>
                    <td className="px-4 py-3 text-green-400 font-medium">
                      {v.fine_amount ? `$${v.fine_amount}` : '-'}
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-1">
                        <button
                          onClick={() => navigate(`/violations/${v.id}`)}
                          className="p-1.5 text-gray-400 hover:text-blue-400 hover:bg-blue-900/30 rounded transition-colors"
                          title="View details"
                        >
                          <Eye className="w-4 h-4" />
                        </button>
                        {v.status === 'pending' && (
                          <>
                            <button
                              onClick={() => setReviewModal({ id: v.id, action: 'approve' })}
                              className="p-1.5 text-gray-400 hover:text-green-400 hover:bg-green-900/30 rounded transition-colors"
                              title="Approve"
                            >
                              <CheckCircle className="w-4 h-4" />
                            </button>
                            <button
                              onClick={() => setReviewModal({ id: v.id, action: 'reject' })}
                              className="p-1.5 text-gray-400 hover:text-red-400 hover:bg-red-900/30 rounded transition-colors"
                              title="Reject"
                            >
                              <XCircle className="w-4 h-4" />
                            </button>
                          </>
                        )}
                      </div>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>

        {/* Pagination */}
        {totalPages > 1 && (
          <div className="flex items-center justify-between px-4 py-3 border-t border-gray-800">
            <span className="text-gray-400 text-sm">
              Page {page} of {totalPages} ({total} total)
            </span>
            <div className="flex gap-2">
              <button
                onClick={() => setPage(Math.max(1, page - 1))}
                disabled={page === 1}
                className="btn-secondary py-1.5 px-3 disabled:opacity-40 disabled:cursor-not-allowed"
              >
                <ChevronLeft className="w-4 h-4" />
              </button>
              <button
                onClick={() => setPage(Math.min(totalPages, page + 1))}
                disabled={page === totalPages}
                className="btn-secondary py-1.5 px-3 disabled:opacity-40 disabled:cursor-not-allowed"
              >
                <ChevronRight className="w-4 h-4" />
              </button>
            </div>
          </div>
        )}
      </div>

      {/* Review Modal */}
      {reviewModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm">
          <div className="card w-full max-w-md mx-4">
            <h3 className="text-lg font-semibold text-white mb-4">
              {reviewModal.action === 'approve' ? '✅ Approve Violation' : '❌ Reject Violation'}
            </h3>
            <p className="text-gray-400 text-sm mb-4">
              {reviewModal.action === 'approve'
                ? 'This will mark the violation as approved and notify the vehicle owner.'
                : 'This will reject the violation. Please provide a reason.'}
            </p>
            <textarea
              className="input-field min-h-24 resize-none"
              placeholder={`Remarks / reason ${reviewModal.action === 'reject' ? '(required)' : '(optional)'}`}
              value={remarks}
              onChange={(e) => setRemarks(e.target.value)}
            />
            <div className="flex gap-3 mt-4">
              <button
                onClick={() => { setReviewModal(null); setRemarks('') }}
                className="btn-secondary flex-1 justify-center"
                disabled={reviewing}
              >
                Cancel
              </button>
              <button
                onClick={handleReview}
                disabled={reviewing || (reviewModal.action === 'reject' && !remarks.trim())}
                className={clsx(
                  'flex-1 justify-center',
                  reviewModal.action === 'approve' ? 'btn-success' : 'btn-danger',
                  'disabled:opacity-50 disabled:cursor-not-allowed'
                )}
              >
                {reviewing ? 'Processing...' : reviewModal.action === 'approve' ? 'Approve' : 'Reject'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

export default ViolationsPage
