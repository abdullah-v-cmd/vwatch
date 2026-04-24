import React, { useEffect, useState, useCallback } from 'react'
import { UserPlus, Trash2, Edit2, Shield, RefreshCw } from 'lucide-react'
import { usersApi } from '../utils/api'
import toast from 'react-hot-toast'
import { format } from 'date-fns'
import clsx from 'clsx'

interface User {
  id: number
  email: string
  username: string
  full_name: string
  role: string
  badge_number?: string
  is_active: boolean
  created_at: string
  last_login?: string
}

const ROLE_BADGE: Record<string, string> = {
  admin: 'bg-red-900 text-red-300 border border-red-700',
  traffic_police: 'bg-blue-900 text-blue-300 border border-blue-700',
  viewer: 'bg-gray-700 text-gray-300 border border-gray-600',
}

const UsersPage: React.FC = () => {
  const [users, setUsers] = useState<User[]>([])
  const [loading, setLoading] = useState(true)
  const [showModal, setShowModal] = useState(false)
  const [form, setForm] = useState({
    email: '', username: '', full_name: '', password: '', role: 'traffic_police', badge_number: '', phone: ''
  })
  const [saving, setSaving] = useState(false)

  const fetchUsers = useCallback(async () => {
    setLoading(true)
    try {
      const res = await usersApi.list()
      setUsers(res.data)
    } catch {
      toast.error('Failed to load users')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { fetchUsers() }, [fetchUsers])

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault()
    setSaving(true)
    try {
      await usersApi.create(form)
      toast.success('User created successfully')
      setShowModal(false)
      setForm({ email: '', username: '', full_name: '', password: '', role: 'traffic_police', badge_number: '', phone: '' })
      fetchUsers()
    } catch (err: any) {
      toast.error(err?.response?.data?.detail || 'Failed to create user')
    } finally {
      setSaving(false)
    }
  }

  const handleDeactivate = async (id: number, name: string) => {
    if (!confirm(`Deactivate user "${name}"?`)) return
    try {
      await usersApi.delete(id)
      toast.success('User deactivated')
      fetchUsers()
    } catch (err: any) {
      toast.error(err?.response?.data?.detail || 'Failed to deactivate')
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white">User Management</h1>
          <p className="text-gray-400 text-sm mt-1">{users.length} registered users</p>
        </div>
        <div className="flex gap-2">
          <button onClick={fetchUsers} className="btn-secondary">
            <RefreshCw className={clsx('w-4 h-4', loading && 'animate-spin')} />
          </button>
          <button onClick={() => setShowModal(true)} className="btn-primary">
            <UserPlus className="w-4 h-4" />
            Add User
          </button>
        </div>
      </div>

      <div className="card p-0 overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-gray-800">
            <tr>
              <th className="px-4 py-3 text-left text-gray-400 font-medium">User</th>
              <th className="px-4 py-3 text-left text-gray-400 font-medium">Role</th>
              <th className="px-4 py-3 text-left text-gray-400 font-medium">Badge</th>
              <th className="px-4 py-3 text-left text-gray-400 font-medium">Status</th>
              <th className="px-4 py-3 text-left text-gray-400 font-medium">Last Login</th>
              <th className="px-4 py-3 text-left text-gray-400 font-medium">Created</th>
              <th className="px-4 py-3 text-left text-gray-400 font-medium">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-800">
            {loading ? (
              <tr><td colSpan={7} className="text-center py-12">
                <div className="animate-spin w-6 h-6 border-2 border-red-500 border-t-transparent rounded-full mx-auto" />
              </td></tr>
            ) : users.map((u) => (
              <tr key={u.id} className="hover:bg-gray-800/50">
                <td className="px-4 py-3">
                  <div className="flex items-center gap-3">
                    <div className="w-8 h-8 bg-gray-700 rounded-full flex items-center justify-center">
                      <Shield className="w-4 h-4 text-gray-400" />
                    </div>
                    <div>
                      <p className="text-white font-medium">{u.full_name}</p>
                      <p className="text-gray-400 text-xs">{u.email}</p>
                    </div>
                  </div>
                </td>
                <td className="px-4 py-3">
                  <span className={clsx('px-2 py-0.5 rounded-full text-xs font-medium', ROLE_BADGE[u.role] || 'bg-gray-700 text-gray-300')}>
                    {u.role.replace('_', ' ')}
                  </span>
                </td>
                <td className="px-4 py-3 text-gray-400 text-xs font-mono">{u.badge_number || '-'}</td>
                <td className="px-4 py-3">
                  <span className={clsx('px-2 py-0.5 rounded-full text-xs font-medium',
                    u.is_active ? 'bg-green-900 text-green-300' : 'bg-gray-700 text-gray-400'
                  )}>
                    {u.is_active ? 'Active' : 'Inactive'}
                  </span>
                </td>
                <td className="px-4 py-3 text-gray-400 text-xs">
                  {u.last_login ? format(new Date(u.last_login), 'MMM dd, HH:mm') : 'Never'}
                </td>
                <td className="px-4 py-3 text-gray-400 text-xs">
                  {format(new Date(u.created_at), 'MMM dd, yyyy')}
                </td>
                <td className="px-4 py-3">
                  <div className="flex gap-1">
                    <button className="p-1.5 text-gray-400 hover:text-blue-400 hover:bg-blue-900/30 rounded">
                      <Edit2 className="w-4 h-4" />
                    </button>
                    {u.is_active && (
                      <button
                        onClick={() => handleDeactivate(u.id, u.full_name)}
                        className="p-1.5 text-gray-400 hover:text-red-400 hover:bg-red-900/30 rounded"
                      >
                        <Trash2 className="w-4 h-4" />
                      </button>
                    )}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Create User Modal */}
      {showModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm">
          <div className="card w-full max-w-lg mx-4">
            <h3 className="text-lg font-semibold text-white mb-5">Create New User</h3>
            <form onSubmit={handleCreate} className="space-y-4">
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-xs text-gray-400 mb-1">Full Name</label>
                  <input className="input-field" placeholder="John Smith" value={form.full_name}
                    onChange={e => setForm(f => ({ ...f, full_name: e.target.value }))} required />
                </div>
                <div>
                  <label className="block text-xs text-gray-400 mb-1">Username</label>
                  <input className="input-field" placeholder="jsmith" value={form.username}
                    onChange={e => setForm(f => ({ ...f, username: e.target.value }))} required />
                </div>
                <div className="col-span-2">
                  <label className="block text-xs text-gray-400 mb-1">Email</label>
                  <input className="input-field" type="email" placeholder="john@vwatch.gov" value={form.email}
                    onChange={e => setForm(f => ({ ...f, email: e.target.value }))} required />
                </div>
                <div>
                  <label className="block text-xs text-gray-400 mb-1">Password</label>
                  <input className="input-field" type="password" placeholder="Min 8 chars" value={form.password}
                    onChange={e => setForm(f => ({ ...f, password: e.target.value }))} required minLength={8} />
                </div>
                <div>
                  <label className="block text-xs text-gray-400 mb-1">Role</label>
                  <select className="input-field" value={form.role}
                    onChange={e => setForm(f => ({ ...f, role: e.target.value }))}>
                    <option value="traffic_police">Traffic Police</option>
                    <option value="admin">Admin</option>
                    <option value="viewer">Viewer</option>
                  </select>
                </div>
                <div>
                  <label className="block text-xs text-gray-400 mb-1">Badge Number</label>
                  <input className="input-field" placeholder="TP-12345" value={form.badge_number}
                    onChange={e => setForm(f => ({ ...f, badge_number: e.target.value }))} />
                </div>
                <div>
                  <label className="block text-xs text-gray-400 mb-1">Phone</label>
                  <input className="input-field" placeholder="+1 234 567 8900" value={form.phone}
                    onChange={e => setForm(f => ({ ...f, phone: e.target.value }))} />
                </div>
              </div>
              <div className="flex gap-3 pt-2">
                <button type="button" onClick={() => setShowModal(false)} className="btn-secondary flex-1 justify-center">Cancel</button>
                <button type="submit" disabled={saving} className="btn-primary flex-1 justify-center disabled:opacity-50">
                  {saving ? 'Creating...' : 'Create User'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  )
}

export default UsersPage
