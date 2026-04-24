import React, { useEffect, useState } from 'react'
import { Save, Plus, Trash2, Settings } from 'lucide-react'
import { configApi } from '../utils/api'
import toast from 'react-hot-toast'

interface ConfigEntry { key: string; value: string; description?: string }

const DEFAULT_CONFIGS: ConfigEntry[] = [
  { key: 'speed.limit.residential', value: '30', description: 'Speed limit in residential zones (km/h)' },
  { key: 'speed.limit.highway', value: '120', description: 'Speed limit on highways (km/h)' },
  { key: 'speed.limit.school_zone', value: '20', description: 'Speed limit in school zones (km/h)' },
  { key: 'redlight.cooldown_seconds', value: '5', description: 'Minimum seconds between red-light violations per vehicle' },
  { key: 'anpr.confidence_threshold', value: '0.6', description: 'Minimum ANPR confidence for plate recognition' },
  { key: 'evidence.retention_days', value: '365', description: 'Days to retain evidence files' },
  { key: 'notification.email_enabled', value: 'true', description: 'Enable email notifications' },
  { key: 'notification.sms_enabled', value: 'false', description: 'Enable SMS notifications' },
  { key: 'fine.speeding', value: '200', description: 'Default fine for speeding ($)' },
  { key: 'fine.red_light', value: '500', description: 'Default fine for red light violation ($)' },
  { key: 'fine.wrong_direction', value: '300', description: 'Default fine for wrong direction ($)' },
]

const ConfigPage: React.FC = () => {
  const [configs, setConfigs] = useState<ConfigEntry[]>([])
  const [loading, setLoading] = useState(true)
  const [editMode, setEditMode] = useState<Record<string, string>>({})
  const [newKey, setNewKey] = useState('')
  const [newValue, setNewValue] = useState('')
  const [newDesc, setNewDesc] = useState('')
  const [saving, setSaving] = useState<string | null>(null)

  useEffect(() => {
    fetchConfigs()
  }, [])

  const fetchConfigs = async () => {
    try {
      const res = await configApi.list()
      setConfigs(res.data.length > 0 ? res.data : DEFAULT_CONFIGS)
    } catch {
      setConfigs(DEFAULT_CONFIGS)
    } finally {
      setLoading(false)
    }
  }

  const handleSave = async (key: string) => {
    const value = editMode[key]
    if (value === undefined) return
    setSaving(key)
    try {
      const cfg = configs.find(c => c.key === key)
      await configApi.upsert({ key, value, description: cfg?.description })
      setConfigs(prev => prev.map(c => c.key === key ? { ...c, value } : c))
      setEditMode(prev => { const n = { ...prev }; delete n[key]; return n })
      toast.success(`Saved: ${key}`)
    } catch {
      toast.error('Failed to save configuration')
    } finally {
      setSaving(null)
    }
  }

  const handleAddNew = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!newKey || !newValue) return
    setSaving('new')
    try {
      await configApi.upsert({ key: newKey, value: newValue, description: newDesc })
      setConfigs(prev => [...prev, { key: newKey, value: newValue, description: newDesc }])
      setNewKey(''); setNewValue(''); setNewDesc('')
      toast.success('Configuration added')
    } catch {
      toast.error('Failed to add configuration')
    } finally {
      setSaving(null)
    }
  }

  const handleDelete = async (key: string) => {
    if (!confirm(`Delete config "${key}"?`)) return
    try {
      await configApi.delete(key)
      setConfigs(prev => prev.filter(c => c.key !== key))
      toast.success('Configuration deleted')
    } catch {
      toast.error('Failed to delete')
    }
  }

  // Group configs by category
  const grouped: Record<string, ConfigEntry[]> = {}
  configs.forEach(c => {
    const cat = c.key.split('.')[0] || 'misc'
    if (!grouped[cat]) grouped[cat] = []
    grouped[cat].push(c)
  })

  return (
    <div className="space-y-6 max-w-4xl">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white">System Configuration</h1>
          <p className="text-gray-400 text-sm mt-1">Manage detection rules and system parameters</p>
        </div>
      </div>

      {loading ? (
        <div className="flex justify-center py-12">
          <div className="animate-spin w-8 h-8 border-2 border-red-500 border-t-transparent rounded-full" />
        </div>
      ) : (
        Object.entries(grouped).map(([category, items]) => (
          <div key={category} className="card">
            <h3 className="text-base font-semibold text-white mb-4 flex items-center gap-2 capitalize">
              <Settings className="w-4 h-4 text-gray-400" />
              {category.replace('_', ' ')} Settings
            </h3>
            <div className="space-y-3">
              {items.map((config) => (
                <div key={config.key} className="flex items-center gap-3 p-3 bg-gray-800 rounded-lg">
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-mono text-gray-300">{config.key}</p>
                    {config.description && (
                      <p className="text-xs text-gray-500 mt-0.5">{config.description}</p>
                    )}
                  </div>
                  <input
                    className="input-field w-36 py-1.5 text-sm"
                    value={editMode[config.key] !== undefined ? editMode[config.key] : config.value}
                    onChange={e => setEditMode(prev => ({ ...prev, [config.key]: e.target.value }))}
                  />
                  {editMode[config.key] !== undefined && (
                    <button
                      onClick={() => handleSave(config.key)}
                      disabled={saving === config.key}
                      className="btn-primary py-1.5 px-3 text-sm"
                    >
                      <Save className="w-3.5 h-3.5" />
                      {saving === config.key ? '...' : 'Save'}
                    </button>
                  )}
                  <button
                    onClick={() => handleDelete(config.key)}
                    className="p-1.5 text-gray-500 hover:text-red-400 hover:bg-red-900/30 rounded"
                  >
                    <Trash2 className="w-4 h-4" />
                  </button>
                </div>
              ))}
            </div>
          </div>
        ))
      )}

      {/* Add New Config */}
      <div className="card">
        <h3 className="text-base font-semibold text-white mb-4 flex items-center gap-2">
          <Plus className="w-4 h-4 text-green-400" />
          Add Configuration
        </h3>
        <form onSubmit={handleAddNew} className="flex flex-wrap gap-3">
          <input
            className="input-field flex-1 min-w-36"
            placeholder="config.key"
            value={newKey}
            onChange={e => setNewKey(e.target.value)}
            required
          />
          <input
            className="input-field w-36"
            placeholder="value"
            value={newValue}
            onChange={e => setNewValue(e.target.value)}
            required
          />
          <input
            className="input-field flex-1 min-w-48"
            placeholder="Description (optional)"
            value={newDesc}
            onChange={e => setNewDesc(e.target.value)}
          />
          <button type="submit" disabled={saving === 'new'} className="btn-primary">
            <Plus className="w-4 h-4" />
            {saving === 'new' ? 'Adding...' : 'Add'}
          </button>
        </form>
      </div>
    </div>
  )
}

export default ConfigPage
