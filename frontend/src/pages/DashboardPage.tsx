import React, { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  AlertTriangle, CheckCircle, Clock, DollarSign, TrendingUp,
  Camera, Ban, Activity, Video, ExternalLink
} from 'lucide-react'
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, PieChart, Pie, Cell
} from 'recharts'
import { violationsApi } from '../utils/api'
import toast from 'react-hot-toast'

interface Stats {
  total_violations: number
  pending_count: number
  approved_count: number
  rejected_count: number
  paid_count: number
  today_count: number
  total_fines_collected: number
  violations_by_type: Record<string, number>
  violations_by_day: Array<{ date: string; count: number; full_date?: string }>
}

const VIOLATION_COLORS: Record<string, string> = {
  SPEEDING: '#ef4444',
  RED_LIGHT: '#f97316',
  WRONG_DIRECTION: '#a855f7',
  LANE_VIOLATION: '#3b82f6',
  NO_HELMET: '#eab308',
  NO_SEATBELT: '#14b8a6',
}

const StatCard: React.FC<{
  title: string
  value: string | number
  icon: React.ReactNode
  color: string
  sub?: string
  onClick?: () => void
}> = ({ title, value, icon, color, sub, onClick }) => (
  <div
    className={`card ${onClick ? 'cursor-pointer hover:border-gray-600 transition-colors' : ''}`}
    onClick={onClick}
  >
    <div className="flex items-start justify-between">
      <div>
        <p className="text-gray-400 text-sm">{title}</p>
        <p className="text-3xl font-bold text-white mt-1">{value}</p>
        {sub && <p className="text-gray-500 text-xs mt-1">{sub}</p>}
      </div>
      <div className={`w-12 h-12 rounded-xl flex items-center justify-center ${color}`}>
        {icon}
      </div>
    </div>
  </div>
)

const DashboardPage: React.FC = () => {
  const navigate = useNavigate()
  const [stats, setStats] = useState<Stats | null>(null)
  const [loading, setLoading] = useState(true)
  const [apiAvailable, setApiAvailable] = useState(true)

  useEffect(() => {
    fetchStats()
    const interval = setInterval(fetchStats, 30000) // auto-refresh every 30s
    return () => clearInterval(interval)
  }, [])

  const fetchStats = async () => {
    try {
      const res = await violationsApi.stats()
      setStats(res.data)
      setApiAvailable(true)
    } catch {
      setApiAvailable(false)
      toast.error('Failed to load dashboard stats')
    } finally {
      setLoading(false)
    }
  }

  // Fallback mock data for when API is unavailable / no data yet
  const mockStats: Stats = {
    total_violations: 0,
    pending_count: 0,
    approved_count: 0,
    rejected_count: 0,
    paid_count: 0,
    today_count: 0,
    total_fines_collected: 0,
    violations_by_type: {},
    violations_by_day: [
      { date: 'Mon', count: 0 },
      { date: 'Tue', count: 0 },
      { date: 'Wed', count: 0 },
      { date: 'Thu', count: 0 },
      { date: 'Fri', count: 0 },
      { date: 'Sat', count: 0 },
      { date: 'Sun', count: 0 },
    ],
  }

  const data = stats || mockStats

  // Always ensure 7-day data in chart even if some days are 0
  const chartData = (() => {
    const d = data.violations_by_day
    if (!d || d.length === 0) return mockStats.violations_by_day
    return d
  })()

  const pieData = Object.entries(data.violations_by_type)
    .filter(([, v]) => v > 0)
    .map(([name, value]) => ({
      name: name.replace(/_/g, ' '),
      value,
    }))

  const pieColors = Object.values(VIOLATION_COLORS)

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="animate-spin w-8 h-8 border-2 border-red-500 border-t-transparent rounded-full" />
      </div>
    )
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-2xl font-bold text-white">Dashboard</h1>
          <p className="text-gray-400 text-sm mt-1">Real-time traffic violation overview</p>
        </div>
        <div className="flex items-center gap-3">
          {!apiAvailable && (
            <span className="text-xs text-yellow-400 bg-yellow-900/30 border border-yellow-800 px-2 py-1 rounded">
              ⚠ API Unavailable
            </span>
          )}
          <button
            onClick={() => navigate('/live-monitoring')}
            className="flex items-center gap-2 bg-green-900/30 border border-green-800 rounded-lg px-3 py-2 hover:bg-green-900/50 transition-colors cursor-pointer"
          >
            <Activity className="w-4 h-4 text-green-400 animate-pulse" />
            <span className="text-green-400 text-sm">Live Monitoring</span>
            <ExternalLink className="w-3 h-3 text-green-500" />
          </button>
        </div>
      </div>

      {/* Stat Cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard
          title="Total Violations"
          value={data.total_violations.toLocaleString()}
          icon={<AlertTriangle className="w-6 h-6 text-white" />}
          color="bg-red-600"
          sub="All time"
          onClick={() => navigate('/violations')}
        />
        <StatCard
          title="Pending Review"
          value={data.pending_count}
          icon={<Clock className="w-6 h-6 text-white" />}
          color="bg-yellow-600"
          sub="Awaiting approval"
          onClick={() => navigate('/violations?status=pending')}
        />
        <StatCard
          title="Approved"
          value={data.approved_count.toLocaleString()}
          icon={<CheckCircle className="w-6 h-6 text-white" />}
          color="bg-green-600"
          sub={`${data.rejected_count} rejected`}
        />
        <StatCard
          title="Fines Collected"
          value={`$${(data.total_fines_collected / 1000).toFixed(1)}K`}
          icon={<DollarSign className="w-6 h-6 text-white" />}
          color="bg-blue-600"
          sub={`${data.paid_count} paid`}
        />
      </div>

      {/* Secondary Stats */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <StatCard
          title="Today's Violations"
          value={data.today_count}
          icon={<TrendingUp className="w-5 h-5 text-white" />}
          color="bg-purple-600"
        />
        <StatCard
          title="Live Cameras"
          value="Monitor"
          icon={<Camera className="w-5 h-5 text-white" />}
          color="bg-cyan-600"
          sub="Click to open live view"
          onClick={() => navigate('/live-monitoring')}
        />
        <StatCard
          title="Blacklisted Plates"
          value="0"
          icon={<Ban className="w-5 h-5 text-white" />}
          color="bg-orange-600"
          sub="Watch list"
        />
      </div>

      {/* Charts */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Bar Chart - Weekly */}
        <div className="lg:col-span-2 card">
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-lg font-semibold text-white">Weekly Violations</h3>
            {data.total_violations === 0 && (
              <span className="text-xs text-gray-500 bg-gray-800 px-2 py-1 rounded">
                No data yet — start the Edge AI or add violations
              </span>
            )}
          </div>
          <ResponsiveContainer width="100%" height={250}>
            <BarChart data={chartData}>
              <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
              <XAxis
                dataKey="date"
                stroke="#9ca3af"
                tick={{ fill: '#9ca3af', fontSize: 12 }}
              />
              <YAxis
                stroke="#9ca3af"
                tick={{ fill: '#9ca3af', fontSize: 12 }}
                allowDecimals={false}
              />
              <Tooltip
                contentStyle={{
                  backgroundColor: '#1f2937',
                  border: '1px solid #374151',
                  borderRadius: '8px',
                }}
                labelStyle={{ color: '#f9fafb' }}
                itemStyle={{ color: '#f87171' }}
                formatter={(value: number) => [value, 'Violations']}
              />
              <Bar dataKey="count" fill="#ef4444" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>

        {/* Pie Chart */}
        <div className="card">
          <h3 className="text-lg font-semibold text-white mb-4">By Violation Type</h3>
          {pieData.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-48 text-gray-600">
              <Video className="w-10 h-10 mb-2 opacity-30" />
              <p className="text-sm">No violations recorded</p>
              <p className="text-xs mt-1">Connect cameras to start detecting</p>
            </div>
          ) : (
            <>
              <ResponsiveContainer width="100%" height={200}>
                <PieChart>
                  <Pie
                    data={pieData}
                    cx="50%"
                    cy="45%"
                    innerRadius={50}
                    outerRadius={85}
                    dataKey="value"
                    paddingAngle={3}
                  >
                    {pieData.map((_, index) => (
                      <Cell key={index} fill={pieColors[index % pieColors.length]} />
                    ))}
                  </Pie>
                  <Tooltip
                    contentStyle={{
                      backgroundColor: '#1f2937',
                      border: '1px solid #374151',
                      borderRadius: '8px',
                    }}
                    labelStyle={{ color: '#f9fafb' }}
                  />
                </PieChart>
              </ResponsiveContainer>
              <div className="space-y-2 mt-2">
                {pieData.map((entry, index) => (
                  <div key={entry.name} className="flex items-center justify-between text-sm">
                    <div className="flex items-center gap-2">
                      <span
                        className="w-3 h-3 rounded-full"
                        style={{ backgroundColor: pieColors[index % pieColors.length] }}
                      />
                      <span className="text-gray-400">{entry.name}</span>
                    </div>
                    <span className="text-white font-medium">{entry.value}</span>
                  </div>
                ))}
              </div>
            </>
          )}
        </div>
      </div>

      {/* Quick Links */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <button
          onClick={() => navigate('/live-monitoring')}
          className="card flex items-center gap-4 hover:border-green-700 transition-colors cursor-pointer text-left"
        >
          <div className="w-12 h-12 bg-green-900/50 border border-green-800 rounded-xl flex items-center justify-center flex-shrink-0">
            <Video className="w-6 h-6 text-green-400" />
          </div>
          <div>
            <p className="text-white font-semibold">Live Monitoring</p>
            <p className="text-gray-400 text-sm">Open webcam & RTSP camera feeds with AI detection</p>
          </div>
          <ExternalLink className="w-4 h-4 text-gray-500 ml-auto flex-shrink-0" />
        </button>

        <button
          onClick={() => navigate('/violations')}
          className="card flex items-center gap-4 hover:border-red-700 transition-colors cursor-pointer text-left"
        >
          <div className="w-12 h-12 bg-red-900/50 border border-red-800 rounded-xl flex items-center justify-center flex-shrink-0">
            <AlertTriangle className="w-6 h-6 text-red-400" />
          </div>
          <div>
            <p className="text-white font-semibold">Review Violations</p>
            <p className="text-gray-400 text-sm">
              {data.pending_count > 0
                ? `${data.pending_count} violation${data.pending_count !== 1 ? 's' : ''} pending review`
                : 'No pending violations'}
            </p>
          </div>
          <ExternalLink className="w-4 h-4 text-gray-500 ml-auto flex-shrink-0" />
        </button>
      </div>
    </div>
  )
}

export default DashboardPage
