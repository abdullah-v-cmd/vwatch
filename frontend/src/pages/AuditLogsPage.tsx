import React, { useEffect, useState, useCallback } from 'react'
import { Search, RefreshCw, FileText, ChevronLeft, ChevronRight } from 'lucide-react'
import { auditApi } from '../utils/api'
import toast from 'react-hot-toast'
import { format } from 'date-fns'
import clsx from 'clsx'

interface AuditLog {
  id: number
  user_id?: number
  action: string
  resource_type?: string
  resource_id?: string
  details?: Record<string, unknown>
  ip_address?: string
  created_at: string
}

const ACTION_COLORS: Record<string, string> = {
  LOGIN: 'text-green-400',
  LOGOUT: 'text-gray-400',
  APPROVE_VIOLATION: 'text-blue-400',
  REJECT_VIOLATION: 'text-red-400',
  CREATE_USER: 'text-purple-400',
  UPDATE_USER: 'text-yellow-400',
  DEACTIVATE_USER: 'text-red-400',
}

const AuditLogsPage: React.FC = () => {
  const [logs, setLogs] = useState<AuditLog[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [loading, setLoading] = useState(true)
  const [search, setSearch] = useState('')
  const [expandedId, setExpandedId] = useState<number | null>(null)

  const fetchLogs = useCallback(async () => {
    setLoading(true)
    try {
      const params: Record<string, unknown> = { page, page_size: 50 }
      if (search) params.action = search
      const res = await auditApi.list(params)
      setLogs(res.data.items || [])
      setTotal(res.data.total || 0)
    } catch {
      toast.error('Failed to load audit logs')
    } finally {
      setLoading(false)
    }
  }, [page, search])

  useEffect(() => { fetchLogs() }, [fetchLogs])

  const totalPages = Math.ceil(total / 50)

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white">Audit Logs</h1>
          <p className="text-gray-400 text-sm mt-1">{total} total log entries</p>
        </div>
        <button onClick={fetchLogs} className="btn-secondary">
          <RefreshCw className={clsx('w-4 h-4', loading && 'animate-spin')} />
          Refresh
        </button>
      </div>

      {/* Search */}
      <div className="card py-3">
        <div className="relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
          <input
            className="input-field pl-9"
            placeholder="Filter by action (LOGIN, APPROVE_VIOLATION...)"
            value={search}
            onChange={e => { setSearch(e.target.value); setPage(1) }}
          />
        </div>
      </div>

      {/* Logs Table */}
      <div className="card p-0 overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-gray-800">
            <tr>
              <th className="px-4 py-3 text-left text-gray-400 font-medium">Time</th>
              <th className="px-4 py-3 text-left text-gray-400 font-medium">Action</th>
              <th className="px-4 py-3 text-left text-gray-400 font-medium">User ID</th>
              <th className="px-4 py-3 text-left text-gray-400 font-medium">Resource</th>
              <th className="px-4 py-3 text-left text-gray-400 font-medium">IP Address</th>
              <th className="px-4 py-3 text-left text-gray-400 font-medium">Details</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-800">
            {loading ? (
              <tr><td colSpan={6} className="text-center py-12">
                <div className="animate-spin w-6 h-6 border-2 border-red-500 border-t-transparent rounded-full mx-auto" />
              </td></tr>
            ) : logs.length === 0 ? (
              <tr><td colSpan={6} className="text-center py-12 text-gray-500">
                <FileText className="w-8 h-8 mx-auto mb-2 opacity-50" />
                No audit logs found
              </td></tr>
            ) : logs.map(log => (
              <React.Fragment key={log.id}>
                <tr
                  className="hover:bg-gray-800/50 cursor-pointer"
                  onClick={() => setExpandedId(expandedId === log.id ? null : log.id)}
                >
                  <td className="px-4 py-3 text-gray-400 text-xs font-mono">
                    {format(new Date(log.created_at), 'MMM dd HH:mm:ss')}
                  </td>
                  <td className="px-4 py-3">
                    <span className={clsx('font-medium', ACTION_COLORS[log.action] || 'text-gray-300')}>
                      {log.action}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-gray-400">#{log.user_id || '-'}</td>
                  <td className="px-4 py-3 text-gray-400 text-xs">
                    {log.resource_type && <span>{log.resource_type} #{log.resource_id}</span>}
                  </td>
                  <td className="px-4 py-3 text-gray-500 text-xs font-mono">{log.ip_address || '-'}</td>
                  <td className="px-4 py-3 text-gray-500 text-xs">
                    {log.details ? '▼ Click to expand' : '-'}
                  </td>
                </tr>
                {expandedId === log.id && log.details && (
                  <tr className="bg-gray-900">
                    <td colSpan={6} className="px-8 py-3">
                      <pre className="text-xs text-gray-300 bg-gray-800 rounded p-3 overflow-x-auto">
                        {JSON.stringify(log.details, null, 2)}
                      </pre>
                    </td>
                  </tr>
                )}
              </React.Fragment>
            ))}
          </tbody>
        </table>

        {totalPages > 1 && (
          <div className="flex items-center justify-between px-4 py-3 border-t border-gray-800">
            <span className="text-gray-400 text-sm">Page {page} of {totalPages}</span>
            <div className="flex gap-2">
              <button onClick={() => setPage(Math.max(1, page - 1))} disabled={page === 1}
                className="btn-secondary py-1.5 px-3 disabled:opacity-40">
                <ChevronLeft className="w-4 h-4" />
              </button>
              <button onClick={() => setPage(Math.min(totalPages, page + 1))} disabled={page === totalPages}
                className="btn-secondary py-1.5 px-3 disabled:opacity-40">
                <ChevronRight className="w-4 h-4" />
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

export default AuditLogsPage
