import React, { useEffect, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { ArrowLeft, Shield, ShieldCheck, ShieldX, CheckCircle, XCircle, Image, Video, Hash } from 'lucide-react'
import { violationsApi } from '../utils/api'
import toast from 'react-hot-toast'
import { format } from 'date-fns'
import clsx from 'clsx'

const ViolationDetailPage: React.FC = () => {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const [violation, setViolation] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [integrity, setIntegrity] = useState<any>(null)
  const [verifying, setVerifying] = useState(false)
  const [lightboxUrl, setLightboxUrl] = useState<string | null>(null)

  useEffect(() => {
    if (id) fetchViolation()
  }, [id])

  const fetchViolation = async () => {
    try {
      const res = await violationsApi.get(Number(id))
      setViolation(res.data)
    } catch {
      toast.error('Failed to load violation')
    } finally {
      setLoading(false)
    }
  }

  const verifyIntegrity = async () => {
    setVerifying(true)
    try {
      const res = await violationsApi.verifyIntegrity(Number(id))
      setIntegrity(res.data)
      if (res.data.tamper_free) {
        toast.success('Evidence integrity verified ✅')
      } else {
        toast.error('⚠️ Evidence may have been tampered with!')
      }
    } catch {
      toast.error('Integrity check failed')
    } finally {
      setVerifying(false)
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="animate-spin w-8 h-8 border-2 border-red-500 border-t-transparent rounded-full" />
      </div>
    )
  }

  if (!violation) {
    return (
      <div className="text-center py-20 text-gray-500">
        <p>Violation not found</p>
        <button onClick={() => navigate('/violations')} className="btn-secondary mt-4 mx-auto">
          Go Back
        </button>
      </div>
    )
  }

  const infoRows = [
    { label: 'Evidence ID', value: violation.evidence_id },
    { label: 'Vehicle ID', value: violation.vehicle_id },
    { label: 'Plate Number', value: <span className="font-mono font-bold text-lg">{violation.plate_number}</span> },
    { label: 'Violation Type', value: violation.violation_type?.replace('_', ' ') },
    { label: 'Status', value: <span className={`badge-${violation.status}`}>{violation.status}</span> },
    { label: 'Speed Recorded', value: violation.speed_recorded ? `${violation.speed_recorded} km/h` : 'N/A' },
    { label: 'Speed Limit', value: violation.speed_limit ? `${violation.speed_limit} km/h` : 'N/A' },
    { label: 'Location', value: violation.location },
    { label: 'Camera ID', value: violation.camera_id },
    { label: 'Violation Time', value: violation.violation_time ? format(new Date(violation.violation_time), 'PPpp') : 'N/A' },
    { label: 'AI Confidence', value: `${(violation.confidence * 100).toFixed(1)}%` },
    { label: 'Fine Amount', value: violation.fine_amount ? `$${violation.fine_amount}` : 'N/A' },
    { label: 'Fine Paid', value: violation.fine_paid ? '✅ Yes' : '❌ No' },
    { label: 'Reviewer Remarks', value: violation.reviewer_remarks || '-' },
  ]

  return (
    <div className="space-y-6 max-w-5xl">
      {/* Header */}
      <div className="flex items-center gap-4">
        <button onClick={() => navigate('/violations')} className="btn-secondary py-2 px-3">
          <ArrowLeft className="w-4 h-4" />
        </button>
        <div>
          <h1 className="text-2xl font-bold text-white">Violation #{violation.id}</h1>
          <p className="text-gray-400 text-sm">Detailed view with evidence</p>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Main Info */}
        <div className="lg:col-span-2 space-y-6">
          <div className="card">
            <h3 className="text-lg font-semibold text-white mb-4">Violation Details</h3>
            <div className="divide-y divide-gray-800">
              {infoRows.map((row) => (
                <div key={row.label} className="flex py-3 gap-4">
                  <span className="text-gray-400 text-sm w-40 flex-shrink-0">{row.label}</span>
                  <span className="text-white text-sm">{row.value}</span>
                </div>
              ))}
            </div>
          </div>

          {/* Evidence Files */}
          <div className="card">
            <h3 className="text-lg font-semibold text-white mb-4 flex items-center gap-2">
              <Image className="w-5 h-5 text-blue-400" />
              Evidence Files
            </h3>
            <div className="grid grid-cols-2 gap-4">
              {violation.frame_image_url && (
                <div
                  className="relative cursor-pointer rounded-lg overflow-hidden border border-gray-700 hover:border-blue-500 transition-colors"
                  onClick={() => setLightboxUrl(`http://localhost:8000${violation.frame_image_url}`)}
                >
                  <img
                    src={`http://localhost:8000${violation.frame_image_url}`}
                    alt="Violation Frame"
                    className="w-full h-36 object-cover"
                    onError={(e) => {
                      (e.target as HTMLImageElement).src = 'data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iMjAwIiBoZWlnaHQ9IjE1MCIgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIj48cmVjdCB3aWR0aD0iMjAwIiBoZWlnaHQ9IjE1MCIgZmlsbD0iIzFmMjkzNyIvPjx0ZXh0IHg9IjEwMCIgeT0iNzUiIGZvbnQtZmFtaWx5PSJBcmlhbCIgZm9udC1zaXplPSIxMiIgZmlsbD0iIzZiNzI4MCIgdGV4dC1hbmNob3I9Im1pZGRsZSI+Tm8gSW1hZ2U8L3RleHQ+PC9zdmc+'
                    }}
                  />
                  <div className="absolute bottom-2 left-2 bg-black/70 rounded px-1.5 py-0.5 text-xs text-gray-300">
                    Violation Frame
                  </div>
                </div>
              )}
              {violation.plate_image_url && (
                <div
                  className="relative cursor-pointer rounded-lg overflow-hidden border border-gray-700 hover:border-blue-500 transition-colors"
                  onClick={() => setLightboxUrl(`http://localhost:8000${violation.plate_image_url}`)}
                >
                  <img
                    src={`http://localhost:8000${violation.plate_image_url}`}
                    alt="License Plate"
                    className="w-full h-36 object-cover"
                    onError={(e) => {
                      (e.target as HTMLImageElement).src = 'data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iMjAwIiBoZWlnaHQ9IjE1MCIgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIj48cmVjdCB3aWR0aD0iMjAwIiBoZWlnaHQ9IjE1MCIgZmlsbD0iIzFmMjkzNyIvPjx0ZXh0IHg9IjEwMCIgeT0iNzUiIGZvbnQtZmFtaWx5PSJBcmlhbCIgZm9udC1zaXplPSIxMiIgZmlsbD0iIzZiNzI4MCIgdGV4dC1hbmNob3I9Im1pZGRsZSI+Tm8gSW1hZ2U8L3RleHQ+PC9zdmc+'
                    }}
                  />
                  <div className="absolute bottom-2 left-2 bg-black/70 rounded px-1.5 py-0.5 text-xs text-gray-300">
                    License Plate
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>

        {/* Side Panel */}
        <div className="space-y-4">
          {/* Integrity Verification */}
          <div className="card">
            <h3 className="text-base font-semibold text-white mb-3 flex items-center gap-2">
              <Hash className="w-4 h-4 text-yellow-400" />
              Evidence Integrity
            </h3>
            <div className="space-y-2 text-xs">
              <div>
                <span className="text-gray-400">Frame SHA-256</span>
                <p className="font-mono text-gray-300 break-all mt-0.5">
                  {violation.frame_sha256 ? `${violation.frame_sha256.slice(0, 32)}…` : 'N/A'}
                </p>
              </div>
              <div>
                <span className="text-gray-400">Plate SHA-256</span>
                <p className="font-mono text-gray-300 break-all mt-0.5">
                  {violation.plate_sha256 ? `${violation.plate_sha256.slice(0, 32)}…` : 'N/A'}
                </p>
              </div>
            </div>

            {integrity && (
              <div className={clsx(
                'mt-3 p-2 rounded-lg flex items-center gap-2 text-sm',
                integrity.tamper_free ? 'bg-green-900/30 text-green-400' : 'bg-red-900/30 text-red-400'
              )}>
                {integrity.tamper_free ? <ShieldCheck className="w-4 h-4" /> : <ShieldX className="w-4 h-4" />}
                {integrity.tamper_free ? 'Integrity Verified' : 'Tampering Detected'}
              </div>
            )}

            <button
              onClick={verifyIntegrity}
              disabled={verifying}
              className="btn-secondary w-full justify-center mt-3 text-sm py-2"
            >
              <Shield className="w-4 h-4" />
              {verifying ? 'Verifying...' : 'Verify Integrity'}
            </button>
          </div>

          {/* Quick Actions */}
          {violation.status === 'pending' && (
            <div className="card">
              <h3 className="text-base font-semibold text-white mb-3">Quick Actions</h3>
              <div className="space-y-2">
                <button
                  onClick={async () => {
                    try {
                      await violationsApi.approve(violation.id, 'Approved by officer')
                      toast.success('Violation approved')
                      fetchViolation()
                    } catch {
                      toast.error('Failed to approve')
                    }
                  }}
                  className="btn-success w-full justify-center text-sm"
                >
                  <CheckCircle className="w-4 h-4" />
                  Approve
                </button>
                <button
                  onClick={async () => {
                    const reason = prompt('Rejection reason:')
                    if (!reason) return
                    try {
                      await violationsApi.reject(violation.id, reason)
                      toast.success('Violation rejected')
                      fetchViolation()
                    } catch {
                      toast.error('Failed to reject')
                    }
                  }}
                  className="btn-danger w-full justify-center text-sm"
                >
                  <XCircle className="w-4 h-4" />
                  Reject
                </button>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Lightbox */}
      {lightboxUrl && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/90 backdrop-blur-sm"
          onClick={() => setLightboxUrl(null)}
        >
          <img
            src={lightboxUrl}
            alt="Evidence"
            className="max-w-4xl max-h-[85vh] rounded-xl shadow-2xl border border-gray-700"
            onClick={(e) => e.stopPropagation()}
          />
          <button
            className="absolute top-4 right-4 text-white hover:text-red-400 p-2"
            onClick={() => setLightboxUrl(null)}
          >
            ✕
          </button>
        </div>
      )}
    </div>
  )
}

export default ViolationDetailPage
