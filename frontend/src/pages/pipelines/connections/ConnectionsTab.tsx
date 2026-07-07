import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { Plus, Database, FileUp, Globe, X, Loader2, RefreshCw, Table2 } from 'lucide-react'
import { apiClientV2 } from '@/api/client'
import ConfirmDialog from '@/components/ConfirmDialog'

interface Connection {
  id: string
  name: string
  kind: string
  status: string
}

const KIND_META: Record<string, { icon: React.ReactNode; label: string }> = {
  file:     { icon: <FileUp size={14} />,   label: '文件上传' },
  mysql:    { icon: <Database size={14} />, label: 'MySQL' },
  postgres: { icon: <Database size={14} />, label: 'PostgreSQL' },
  mongo:    { icon: <Database size={14} />, label: 'MongoDB' },
  rest:     { icon: <Globe size={14} />,    label: 'REST API' },
}

const STATUS_STYLE: Record<string, string> = {
  active:   'text-green-600 bg-green-50 border-green-200',
  inactive: 'text-gray-400 bg-gray-50 border-gray-200',
  error:    'text-red-500 bg-red-50 border-red-200',
}

const STATUS_LABEL: Record<string, string> = {
  active: '活跃', inactive: '未激活', error: '错误',
}

// 新建连接仅支持外部系统；文件类数据统一在「数据资产湖 → 原始数据集」上传与维护
const CREATABLE_KINDS = ['mysql', 'postgres', 'mongo', 'rest'] as const

const KIND_CONFIG_FIELDS: Record<string, { key: string; label: string; placeholder: string; type?: string }[]> = {
  mysql:    [
    { key: 'host', label: '主机', placeholder: 'localhost' },
    { key: 'port', label: '端口', placeholder: '3306' },
    { key: 'database', label: '数据库名', placeholder: 'mydb' },
    { key: 'user', label: '用户名', placeholder: 'root' },
    { key: 'password', label: '密码', placeholder: '••••••', type: 'password' },
  ],
  postgres: [
    { key: 'host', label: '主机', placeholder: 'localhost' },
    { key: 'port', label: '端口', placeholder: '5432' },
    { key: 'database', label: '数据库名', placeholder: 'mydb' },
    { key: 'user', label: '用户名', placeholder: 'postgres' },
    { key: 'password', label: '密码', placeholder: '••••••', type: 'password' },
  ],
  mongo:    [
    { key: 'uri', label: '连接字符串', placeholder: 'mongodb://localhost:27017/mydb' },
  ],
  rest:     [
    { key: 'url', label: 'API URL', placeholder: 'https://api.example.com/data' },
    { key: 'headers', label: '请求头 (JSON)', placeholder: '{"Authorization": "Bearer token"}' },
  ],
  file: [],
}

export default function ConnectionsTab() {
  const navigate = useNavigate()
  const [connections, setConnections] = useState<Connection[]>([])
  const [loading, setLoading] = useState(true)
  const [showForm, setShowForm] = useState(false)
  const [saving, setSaving] = useState(false)
  const [formError, setFormError] = useState('')
  const [syncing, setSyncing] = useState<string | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<Connection | null>(null)
  const [testResult, setTestResult] = useState<{ id: string; ok: boolean; detail?: string } | null>(null)
  const [testing, setTesting] = useState<string | null>(null)

  const [formName, setFormName] = useState('')
  const [formKind, setFormKind] = useState('mysql')
  const [formConfig, setFormConfig] = useState<Record<string, string>>({})
  const [formSyncMode, setFormSyncMode] = useState<'snapshot' | 'append'>('snapshot')

  const loadConnections = () => {
    setLoading(true)
    apiClientV2.get('/connections')
      .then((res: unknown) => setConnections(Array.isArray(res) ? res : ((res as { data?: Connection[] })?.data ?? [])))
      .catch(() => setConnections([]))
      .finally(() => setLoading(false))
  }

  useEffect(() => { loadConnections() }, [])

  const resetForm = () => {
    setFormName('')
    setFormKind('mysql')
    setFormConfig({})
    setFormSyncMode('snapshot')
    setFormError('')
  }

  const handleSave = async () => {
    if (!formName.trim()) { setFormError('请填写连接名称'); return }
    setSaving(true)
    setFormError('')
    try {
      await apiClientV2.post('/connections', {
        name: formName, kind: formKind,
        config: { ...formConfig, sync_mode: formSyncMode },
      })
      setShowForm(false)
      resetForm()
      loadConnections()
    } catch (e: unknown) {
      const err = e as { detail?: string; response?: { data?: { detail?: string } }; message?: string }
      setFormError(err?.detail || err?.response?.data?.detail || err?.message || '保存失败')
    } finally {
      setSaving(false)
    }
  }

  const handleTest = async (id: string) => {
    setTesting(id)
    setTestResult(null)
    try {
      const res = await apiClientV2.post<{ success: boolean; detail?: string }>(`/connections/${id}/test`, {})
      setTestResult({ id, ok: !!res.success, detail: res.detail })
      loadConnections()
    } catch (e: unknown) {
      const err = e as { detail?: string }
      setTestResult({ id, ok: false, detail: err?.detail || '测试失败' })
    } finally {
      setTesting(null)
    }
  }

  const handleSync = async (id: string) => {
    setSyncing(id)
    try {
      await apiClientV2.post(`/connections/${id}/sync`, {})
      loadConnections()
    } catch {
      // ignore sync errors silently
    } finally {
      setSyncing(null)
    }
  }

  const handleDelete = async () => {
    if (!deleteTarget) return
    await apiClientV2.delete(`/connections/${deleteTarget.id}`)
    setDeleteTarget(null)
    loadConnections()
  }

  if (loading) return <div className="text-gray-400 text-sm p-4">加载中...</div>

  return (
    <div className="space-y-4">
      <div className="flex justify-between items-center">
        <p className="text-xs text-gray-400">管理外部数据源连接（数据库 / API），作为流水线连接器的数据来源</p>
        <button
          onClick={() => { resetForm(); setShowForm(true) }}
          className="flex items-center gap-2 bg-black text-white px-4 py-2 rounded-lg text-sm"
        >
          <Plus size={14} /> 新建连接
        </button>
      </div>

      {/* 文件类数据引导 */}
      <div className="flex items-center gap-2 px-4 py-2.5 bg-blue-50/60 border border-blue-100 rounded-lg text-xs text-blue-700">
        <Table2 size={14} className="shrink-0" />
        <span className="flex-1">
          Excel / CSV / JSON / 文档等<b>文件类数据</b>无需创建连接：直接到资产湖上传为原始数据集，后续在同一数据集上追加新版本即可完成数据更新
        </span>
        <button
          onClick={() => navigate('/data/structured?tab=raw')}
          className="px-2.5 py-1 bg-white border border-blue-200 rounded-lg hover:bg-blue-100 shrink-0"
        >
          去资产湖上传
        </button>
      </div>

      {showForm && (
        <div className="border rounded-xl p-5 bg-white space-y-4">
          <div className="flex justify-between items-center">
            <h3 className="font-medium text-sm">新建连接</h3>
            <button onClick={() => { setShowForm(false); resetForm() }} className="text-gray-400 hover:text-black">
              <X size={16} />
            </button>
          </div>

          <div>
            <label className="text-xs text-gray-500 mb-1 block">连接名称 *</label>
            <input
              value={formName}
              onChange={e => setFormName(e.target.value)}
              placeholder="例：ERP 订单数据库"
              className="w-full border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-black"
            />
          </div>

          <div>
            <label className="text-xs text-gray-500 mb-2 block">连接类型</label>
            <div className="flex gap-2 flex-wrap">
              {CREATABLE_KINDS.map(k => {
                const m = KIND_META[k]
                return (
                  <button
                    key={k}
                    type="button"
                    onClick={() => { setFormKind(k); setFormConfig({}) }}
                    className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs border transition-colors
                      ${formKind === k ? 'bg-black text-white border-black' : 'border-gray-200 text-gray-600 hover:bg-gray-50'}`}
                  >
                    {m.icon} {m.label}
                  </button>
                )
              })}
            </div>
          </div>

          <div className="space-y-3">
            {KIND_CONFIG_FIELDS[formKind]?.map(f => (
              <div key={f.key}>
                <label className="text-xs text-gray-500 mb-1 block">{f.label}</label>
                <input
                  type={f.type || 'text'}
                  value={formConfig[f.key] || ''}
                  onChange={e => setFormConfig(p => ({ ...p, [f.key]: e.target.value }))}
                  placeholder={f.placeholder}
                  className="w-full border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-black"
                />
              </div>
            ))}
          </div>

          <div>
            <label className="text-xs text-gray-500 mb-2 block">同步模式</label>
            <div className="flex gap-4">
              {(['snapshot', 'append'] as const).map(m => (
                <label key={m} className="flex items-center gap-2 text-sm cursor-pointer">
                  <input
                    type="radio"
                    name="sync_mode"
                    value={m}
                    checked={formSyncMode === m}
                    onChange={() => setFormSyncMode(m)}
                    className="accent-black"
                  />
                  <span>{m === 'snapshot' ? 'SNAPSHOT（全量覆盖）' : 'APPEND（增量追加）'}</span>
                </label>
              ))}
            </div>
          </div>

          {formError && <p className="text-red-500 text-xs">{formError}</p>}

          <div className="flex gap-2 justify-end">
            <button onClick={() => { setShowForm(false); resetForm() }} className="px-4 py-2 text-sm border rounded-lg hover:bg-gray-50">
              取消
            </button>
            <button
              onClick={handleSave}
              disabled={saving}
              className="flex items-center gap-2 px-4 py-2 text-sm bg-black text-white rounded-lg disabled:opacity-50"
            >
              {saving && <Loader2 size={13} className="animate-spin" />}
              {saving ? '保存中...' : '保存'}
            </button>
          </div>
        </div>
      )}

      {connections.length === 0 ? (
        <div className="border-2 border-dashed rounded-xl p-10 text-center text-gray-400 space-y-2">
          <Database size={28} className="mx-auto opacity-30" />
          <p className="text-sm">暂无数据连接</p>
          <p className="text-xs">点击「新建连接」添加数据源</p>
        </div>
      ) : (
        <div className="border rounded-xl divide-y overflow-hidden">
          {connections.map(c => {
            const meta = KIND_META[c.kind] ?? KIND_META.file
            const statusStyle = STATUS_STYLE[c.status] ?? STATUS_STYLE.inactive
            const statusLabel = STATUS_LABEL[c.status] ?? c.status
            return (
              <div key={c.id} className="p-4">
                <div className="flex items-center gap-3">
                  <div className="w-8 h-8 bg-gray-100 rounded-lg flex items-center justify-center text-gray-500">
                    {meta.icon}
                  </div>
                  <div className="flex-1 min-w-0">
                    <p className="font-medium text-sm truncate">{c.name}</p>
                    <p className="text-xs text-gray-400">{meta.label}</p>
                  </div>
                  <span className={`text-xs font-medium px-2 py-0.5 rounded border ${statusStyle}`}>
                    {statusLabel}
                  </span>
                  <button
                    onClick={() => handleTest(c.id)}
                    disabled={testing === c.id}
                    className="flex items-center gap-1 text-xs px-2.5 py-1.5 border rounded-lg hover:bg-gray-50 disabled:opacity-50 transition-colors"
                    title="测试连接是否可用"
                  >
                    {testing === c.id ? <Loader2 size={11} className="animate-spin" /> : null}
                    测试
                  </button>
                  <button
                    onClick={() => handleSync(c.id)}
                    disabled={syncing === c.id}
                    className="flex items-center gap-1 text-xs px-2.5 py-1.5 border rounded-lg hover:bg-gray-50 disabled:opacity-50 transition-colors"
                    title="立即拉取一次数据到资产湖"
                  >
                    <RefreshCw size={11} className={syncing === c.id ? 'animate-spin' : ''} />
                    同步
                  </button>
                  <button
                    onClick={() => setDeleteTarget(c)}
                    className="text-gray-400 hover:text-red-500 text-xs px-1 transition-colors"
                  >
                    删除
                  </button>
                </div>
                {testResult?.id === c.id && (
                  <p className={`text-xs mt-2 ml-11 ${testResult.ok ? 'text-green-600' : 'text-red-500'}`}>
                    {testResult.ok ? '✓ 连接可用' : `✗ 连接失败${testResult.detail ? `：${testResult.detail}` : ''}`}
                  </p>
                )}
              </div>
            )
          })}
        </div>
      )}

      <ConfirmDialog
        open={!!deleteTarget}
        title="删除连接"
        message={`确认删除连接「${deleteTarget?.name}」？依赖该连接的同步任务将无法执行。`}
        onConfirm={handleDelete}
        onCancel={() => setDeleteTarget(null)}
      />
    </div>
  )
}
