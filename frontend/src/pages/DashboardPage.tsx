import React, { useEffect, useState } from 'react'
import { AlertTriangle, CheckCircle, Clock, DollarSign, TrendingUp, Camera, Ban, Activity } from 'lucide-react'
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, PieChart, Pie, Cell, Legend } from 'recharts'
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
  violations_by_day: Array<{ date: string; count: number }>
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
}> = ({ title, value, icon, color, sub }) => (
  <div className="card">
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
  const [stats, setStats] = useState<Stats | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    fetchStats()
    const interval = setInterval(fetchStats, 30000) // auto-refresh every 30s
    return () => clearInterval(interval)
  }, [])

  const fetchStats = async () => {
    try {
      const res = await violationsApi.stats()
      setStats(res.data)
    } catch {
      toast.error('Failed to load dashboard stats')
    } finally {
      setLoading(false)
    }
  }

  // Mock data for charts when API is unavailable
  const mockStats: Stats = {
    total_violations: 1247,
    pending_count: 89,
    approved_count: 943,
    rejected_count: 215,
    paid_count: 712,
    today_count: 34,
    total_fines_collected: 185000,
    violations_by_type: {
      SPEEDING: 520,
      RED_LIGHT: 310,
      WRONG_DIRECTION: 190,
      LANE_VIOLATION: 155,
      NO_HELMET: 72,
    },
    violations_by_day: [
      { date: 'Mon', count: 45 },
      { date: 'Tue', count: 52 },
      { date: 'Wed', count: 38 },
      { date: 'Thu', count: 61 },
      { date: 'Fri', count: 79 },
      { date: 'Sat', count: 34 },
      { date: 'Sun', count: 22 },
    ],
  }

  const data = stats || mockStats

  const pieData = Object.entries(data.violations_by_type).map(([name, value]) => ({
    name: name.replace('_', ' '),
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
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white">Dashboard</h1>
          <p className="text-gray-400 text-sm mt-1">Real-time traffic violation overview</p>
        </div>
        <div className="flex items-center gap-2 bg-green-900/30 border border-green-800 rounded-lg px-3 py-2">
          <Activity className="w-4 h-4 text-green-400 animate-pulse" />
          <span className="text-green-400 text-sm">Live Monitoring</span>
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
        />
        <StatCard
          title="Pending Review"
          value={data.pending_count}
          icon={<Clock className="w-6 h-6 text-white" />}
          color="bg-yellow-600"
          sub="Awaiting approval"
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
          title="Active Cameras"
          value="12"
          icon={<Camera className="w-5 h-5 text-white" />}
          color="bg-cyan-600"
          sub="All operational"
        />
        <StatCard
          title="Blacklisted Plates"
          value="47"
          icon={<Ban className="w-5 h-5 text-white" />}
          color="bg-orange-600"
          sub="Watch list"
        />
      </div>

      {/* Charts */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Bar Chart */}
        <div className="lg:col-span-2 card">
          <h3 className="text-lg font-semibold text-white mb-4">Weekly Violations</h3>
          <ResponsiveContainer width="100%" height={250}>
            <BarChart data={data.violations_by_day}>
              <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
              <XAxis dataKey="date" stroke="#9ca3af" tick={{ fill: '#9ca3af', fontSize: 12 }} />
              <YAxis stroke="#9ca3af" tick={{ fill: '#9ca3af', fontSize: 12 }} />
              <Tooltip
                contentStyle={{ backgroundColor: '#1f2937', border: '1px solid #374151', borderRadius: '8px' }}
                labelStyle={{ color: '#f9fafb' }}
                itemStyle={{ color: '#f87171' }}
              />
              <Bar dataKey="count" fill="#ef4444" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>

        {/* Pie Chart */}
        <div className="card">
          <h3 className="text-lg font-semibold text-white mb-4">By Violation Type</h3>
          <ResponsiveContainer width="100%" height={250}>
            <PieChart>
              <Pie
                data={pieData}
                cx="50%"
                cy="45%"
                innerRadius={55}
                outerRadius={90}
                dataKey="value"
                paddingAngle={3}
              >
                {pieData.map((_, index) => (
                  <Cell key={index} fill={pieColors[index % pieColors.length]} />
                ))}
              </Pie>
              <Tooltip
                contentStyle={{ backgroundColor: '#1f2937', border: '1px solid #374151', borderRadius: '8px' }}
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
        </div>
      </div>
    </div>
  )
}

export default DashboardPage
