import { useState, useCallback, useEffect, useRef } from 'react'
import { useForm } from 'react-hook-form'
import { useTranslation } from 'react-i18next'
import {
  Plus, Trash2, TestTube2, Pencil, X, Loader2, CheckCircle2, XCircle,
  Star, Search, Upload, Download, Settings2, BarChart3,
} from 'lucide-react'
import type { ModelConfig } from '@/types/ontology'
import ConfirmDialog from '@/components/ConfirmDialog'
import ToastContainer from './components/Toast'
import ModelDetailDrawer from './components/ModelDetailDrawer'
import ModelHeatStrip from './components/ModelHeatStrip'
import { useMockModels, type RunStatus } from './hooks/useMockModels'

const CONFIG_TYPES = [
  { value: 'llm', label: 'LLM配置' },
  { value: 'ocr', label: 'OCR配置' },
  { value: 'other', label: '其他配置' },
]

const PROVIDERS: Record<string, Array<{ value: string; label: string }>> = {
  llm: [
    { value: 'openai', label: 'OpenAI' },
    { value: 'anthropic', label: 'Anthropic' },
    { value: 'compatible', label: 'OpenAI-Compatible' },
    { value: 'minimax', label: 'MiniMax' },
    { value: 'deepseek', label: 'DeepSeek' },
  ],
  ocr: [
    { value: 'easyocr', label: 'EasyOCR' },
    { value: 'paddleocr', label: 'PaddleOCR' },
    { value: 'tesseract', label: 'Tesseract' },
    { value: 'external_api', label: 'External OCR API' },
  ],
  other: [
    { value: 'custom', label: 'Custom' },
    { value: 'local_service', label: 'Local Service' },
    { value: 'http_api', label: 'HTTP API' },
  ],
}

// 每个提供商仅允许配置一个模型：单个模型名转成后端期望的数组结构
function modelList(text?: string) {
  const name = text?.trim()
  return name ? [name] : []
}

function parseOptions(text?: string) {
  if (!text?.trim()) return {}
  try {
    return JSON.parse(text)
  } catch {
    return {}
  }
}

function parseTokenLimit(value: any): number | undefined {
  if (value === undefined || value === null || String(value).trim() === '') return undefined
  const n = Number(value)
  return Number.isFinite(n) && n > 0 ? Math.floor(n) : undefined
}

function buildPayload(data: any, mode: 'create' | 'update' = 'create') {
  const isLlm = (data.config_type || 'llm') === 'llm'
  const maxContext = isLlm ? parseTokenLimit(data.max_context_tokens) : undefined
  const maxOutput = isLlm ? parseTokenLimit(data.max_output_tokens) : undefined
  const options: Record<string, any> = {
    ...parseOptions(data.options_json),
    ...(data.config_type === 'ocr' ? {
      enabled: data.ocr_enabled === 'true',
      lang: data.ocr_lang || 'ch',
      device: data.ocr_device || 'cpu',
    } : {}),
  }
  if (maxContext !== undefined) options.max_context_tokens = maxContext
  else delete options.max_context_tokens
  if (maxOutput !== undefined) options.max_output_tokens = maxOutput
  else delete options.max_output_tokens
  const payload: any = {
    name: data.name,
    config_type: data.config_type || 'llm',
    provider: data.provider,
    api_base: data.api_base,
    models: modelList(data.models_str),
    options,
  }
  const apiKey = typeof data.api_key === 'string' ? data.api_key.trim() : data.api_key
  if (mode === 'create' || apiKey) payload.api_key = apiKey || ''
  return payload
}

function typeLabel(type?: string) {
  return CONFIG_TYPES.find(t => t.value === (type || 'llm'))?.label || 'LLM配置'
}

function providerColor(provider: string): string {
  const colors: Record<string, string> = {
    openai: 'bg-emerald-50 text-emerald-600 border-emerald-200',
    anthropic: 'bg-purple-50 text-purple-600 border-purple-200',
    compatible: 'bg-blue-50 text-blue-600 border-blue-200',
    minimax: 'bg-rose-50 text-rose-600 border-rose-200',
    deepseek: 'bg-sky-50 text-sky-600 border-sky-200',
    easyocr: 'bg-orange-50 text-orange-600 border-orange-200',
    paddleocr: 'bg-cyan-50 text-cyan-600 border-cyan-200',
    tesseract: 'bg-pink-50 text-pink-600 border-pink-200',
    external_api: 'bg-amber-50 text-amber-600 border-amber-200',
    custom: 'bg-slate-50 text-slate-600 border-slate-200',
    local_service: 'bg-teal-50 text-teal-600 border-teal-200',
    http_api: 'bg-indigo-50 text-indigo-600 border-indigo-200',
  }
  return colors[provider] || 'bg-gray-50 text-gray-600 border-gray-200'
}

// 运行 / 健康状态样式
const RUN_META: Record<RunStatus, { label: string; color: string; bg: string; dot: string }> = {
  normal:   { label: '正常', color: '#2d8a4e', bg: '#e8f5e9', dot: '#2d8a4e' },
  degraded: { label: '降级', color: '#c9861a', bg: '#fff8e1', dot: '#c9861a' },
  error:    { label: '异常', color: '#c23b3b', bg: '#fde8e8', dot: '#c23b3b' },
  disabled: { label: '已停用', color: '#8b8ba3', bg: '#f1f3f5', dot: '#c2c6d0' },
}

function availColor(av: string): string {
  if (av === '—') return '#b8b8c8'
  const n = parseFloat(av)
  return n >= 98 ? '#2d8a4e' : n >= 92 ? '#c9861a' : '#c23b3b'
}

function latColor(ms: number): string {
  if (!ms) return '#b8b8c8'
  return ms < 1500 ? '#2d8a4e' : ms < 3000 ? '#c9861a' : '#c23b3b'
}

export default function ModelsPage() {
  const { t } = useTranslation()
  const {
    models, loading, error, defaultModelId, setDefault, createModel, updateModel, deleteModel,
    testConnection, getModelDailyStats,
    isEnabled, toggleEnabled, getModelRunStatus, getModelSummary, getModelHeatCells,
  } = useMockModels()

  // UI States
  const [searchQuery, setSearchQuery] = useState('')
  const [filterType, setFilterType] = useState<string>('all')
  const [enabledFilter, setEnabledFilter] = useState<string>('all')
  const filterTabsRef = useRef<HTMLDivElement>(null)
  const [indicatorPos, setIndicatorPos] = useState({ left: 0, width: 0 })
  useEffect(() => {
    const container = filterTabsRef.current
    if (!container) return
    const activeBtn = container.querySelector(`[data-tab-value="${filterType}"]`) as HTMLElement | null
    if (!activeBtn) return
    const containerRect = container.getBoundingClientRect()
    const btnRect = activeBtn.getBoundingClientRect()
    setIndicatorPos({
      left: btnRect.left - containerRect.left,
      width: btnRect.width,
    })
  }, [filterType, models.length])

  // Modal States
  const [showCreate, setShowCreate] = useState(false)
  const [editTarget, setEditTarget] = useState<ModelConfig | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<ModelConfig | null>(null)

  // Detail Drawer
  const [detailModel, setDetailModel] = useState<ModelConfig | null>(null)

  // Test States
  const [testStatus, setTestStatus] = useState<Record<string, 'idle' | 'testing' | 'success' | 'error'>>({})
  const [testResult, setTestResult] = useState<Record<string, string>>({})
  const [drawerTestStatus, setDrawerTestStatus] = useState<'idle' | 'testing' | 'success' | 'error'>('idle')
  const [drawerTestResult, setDrawerTestResult] = useState('')

  // Toast
  const [toasts, setToasts] = useState<Array<{ id: string; type: 'success' | 'error' | 'warning' | 'info'; message: string }>>([])
  const toastIdRef = useRef(0)

  const addToast = useCallback((type: 'success' | 'error' | 'warning' | 'info', message: string) => {
    const id = `toast-${++toastIdRef.current}`
    setToasts(prev => [...prev, { id, type, message }])
  }, [])

  const removeToast = useCallback((id: string) => {
    setToasts(prev => prev.filter(t => t.id !== id))
  }, [])

  // Export/Import
  const handleExport = useCallback(() => {
    const exportData = models.map(m => ({
      name: m.name,
      config_type: m.config_type,
      provider: m.provider,
      api_base: m.api_base,
      models: m.models,
      options: m.options,
    }))
    const blob = new Blob([JSON.stringify(exportData, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `model-configs-${new Date().toISOString().split('T')[0]}.json`
    a.click()
    URL.revokeObjectURL(url)
    addToast('success', `已导出 ${models.length} 个模型配置`)
  }, [models, addToast])

  const handleImport = useCallback((file: File) => {
    const reader = new FileReader()
    reader.onload = async (e) => {
      try {
        const data = JSON.parse(e.target?.result as string)
        if (Array.isArray(data)) {
          const validItems = data.filter((item: any) => item.name && item.provider)
          for (const item of validItems) {
            await createModel({
              name: item.name,
              config_type: item.config_type || 'llm',
              provider: item.provider,
              api_base: item.api_base || '',
              models: (item.models || []).slice(0, 1),
              options: item.options || {},
              enabled: item.enabled !== false,
              is_default: Boolean(item.is_default),
            })
          }
          addToast('success', `成功导入 ${validItems.length} 个模型配置`)
        } else {
          addToast('error', '导入文件格式不正确，应为JSON数组')
        }
      } catch {
        addToast('error', '导入模型配置失败')
      }
    }
    reader.readAsText(file)
  }, [createModel, addToast])

  const fileInputRef = useRef<HTMLInputElement>(null)

  // Form
  const { register, handleSubmit, reset, watch, setValue: setCreateValue } = useForm<any>({
    defaultValues: { config_type: 'llm', provider: 'openai', ocr_enabled: 'false', ocr_lang: 'ch', ocr_device: 'cpu' },
  })
  const { register: regEdit, handleSubmit: handleEditSubmit, setValue, watch: watchEdit } = useForm<any>()

  // Filtered + sorted models: default first, then by created_at ascending
  const filteredModels = models.filter(m => {
    const matchSearch = !searchQuery ||
      m.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
      m.provider.toLowerCase().includes(searchQuery.toLowerCase())
    const matchType = filterType === 'all' || m.config_type === filterType
    const matchEnabled = enabledFilter === 'all' ||
      (enabledFilter === 'enabled' && m.enabled !== false) ||
      (enabledFilter === 'disabled' && m.enabled === false)
    return matchSearch && matchType && matchEnabled
  })

  const sortedModels = [...filteredModels].sort((a, b) => {
    if (a.is_default && !b.is_default) return -1
    if (!a.is_default && b.is_default) return 1
    return new Date(a.created_at).getTime() - new Date(b.created_at).getTime()
  })

  // Handlers
  const handleCreate = async (data: any) => {
    try {
      await createModel(buildPayload(data))
      setShowCreate(false)
      reset()
      addToast('success', `模型 "${data.name}" 创建成功`)
    } catch {
      addToast('error', `模型 "${data.name}" 创建失败`)
    }
  }

  const handleUpdate = async (data: any) => {
    if (!editTarget) return
    try {
      await updateModel(editTarget.id, buildPayload(data, 'update'))
      setEditTarget(null)
      addToast('success', '模型更新成功')
    } catch {
      addToast('error', '模型更新失败')
    }
  }

  const handleDelete = async () => {
    if (!deleteTarget) return
    try {
      await deleteModel(deleteTarget.id)
      if (detailModel?.id === deleteTarget.id) setDetailModel(null)
      setDeleteTarget(null)
      addToast('success', '模型已删除')
    } catch {
      addToast('error', '模型删除失败')
    }
  }

  const handleTest = async (id: string) => {
    setTestStatus(prev => ({ ...prev, [id]: 'testing' }))
    const result = await testConnection(id)
    setTestStatus(prev => ({ ...prev, [id]: result.ok ? 'success' : 'error' }))
    setTestResult(prev => ({ ...prev, [id]: result.message }))
    if (result.ok) addToast('success', result.message)
    else addToast('error', result.message)
    setTimeout(() => setTestStatus(prev => ({ ...prev, [id]: 'idle' })), 3000)
  }

  const handleDrawerTest = async () => {
    if (!detailModel) return
    setDrawerTestStatus('testing')
    const result = await testConnection(detailModel.id)
    setDrawerTestStatus(result.ok ? 'success' : 'error')
    setDrawerTestResult(result.message)
    if (result.ok) addToast('success', result.message)
    else addToast('error', result.message)
    setTimeout(() => setDrawerTestStatus('idle'), 3000)
  }

  const openEdit = (m: ModelConfig) => {
    const options = m.options || {}
    setEditTarget(m)
    setValue('name', m.name)
    setValue('config_type', m.config_type || 'llm')
    setValue('provider', m.provider)
    setValue('api_key', '')
    setValue('api_base', m.api_base || '')
    setValue('models_str', (m.models || [])[0] || '')
    setValue('ocr_enabled', options.enabled ? 'true' : 'false')
    setValue('ocr_lang', String(options.lang || 'ch'))
    setValue('ocr_device', String(options.device || 'cpu'))
    setValue('max_context_tokens', options.max_context_tokens != null ? String(options.max_context_tokens) : '')
    setValue('max_output_tokens', options.max_output_tokens != null ? String(options.max_output_tokens) : '')
    setValue('options_json', JSON.stringify(
      Object.fromEntries(Object.entries(options).filter(([k]) => !['lang', 'device', 'enabled', 'max_context_tokens', 'max_output_tokens'].includes(k))),
      null, 2,
    ))
  }

  const typeCount = (type: string) => models.filter(m => m.config_type === type).length

  return (
    <div className="min-h-full">
      {/* Toast Container */}
      <ToastContainer toasts={toasts} onRemove={removeToast} />

      {/* 搜索、筛选、操作按钮 */}
      <div className="flex items-center gap-3 bg-white rounded-xl border border-slate-200 px-4 py-3 flex-wrap mb-5 shadow-sm/50">
        <div className="relative w-full sm:w-64">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
          <input
            value={searchQuery}
            onChange={e => setSearchQuery(e.target.value)}
            placeholder="搜索提供商..."
            className="w-full pl-8 pr-3 py-1.5 rounded-lg text-sm border border-slate-200 text-slate-700 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-teal-500/20 focus:border-teal-400 transition-all"
          />
        </div>

        <div ref={filterTabsRef} className="relative flex items-center gap-1 rounded-lg border border-slate-200 bg-slate-50/70 p-0.5">
          <div
            className="absolute top-0.5 h-[calc(100%-4px)] rounded-md bg-teal-600 shadow-sm transition-all duration-300 ease-out"
            style={{ left: `${indicatorPos.left}px`, width: `${indicatorPos.width}px` }}
          />
          {[
            { value: 'all', label: '全部', count: models.length },
            { value: 'llm', label: 'LLM', count: typeCount('llm') },
            { value: 'ocr', label: 'OCR', count: typeCount('ocr') },
            { value: 'other', label: '其他', count: typeCount('other') },
          ].map(tab => (
            <button
              key={tab.value}
              data-tab-value={tab.value}
              onClick={() => setFilterType(tab.value)}
              className={`relative px-3 py-1.5 rounded-md text-xs font-medium z-10 transition-colors duration-200 ${
                filterType === tab.value
                  ? 'text-white'
                  : 'text-slate-500 hover:text-slate-700'
              }`}
            >
              {tab.label}
              <span className={`ml-1 ${filterType === tab.value ? 'text-slate-300' : 'text-slate-400'}`}>
                {tab.count}
              </span>
            </button>
          ))}
        </div>

        <select
          value={enabledFilter}
          onChange={e => setEnabledFilter(e.target.value)}
          className="rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs font-medium text-slate-600 focus:outline-none focus:ring-2 focus:ring-teal-500/20 focus:border-teal-400 transition-all cursor-pointer"
        >
          <option value="all">全部状态</option>
          <option value="enabled">已启用</option>
          <option value="disabled">已禁用</option>
        </select>

        <div className="ml-auto flex items-center gap-2">
          <input
            ref={fileInputRef}
            type="file"
            accept=".json"
            className="hidden"
            onChange={e => { const f = e.target.files?.[0]; if (f) { handleImport(f); e.target.value = '' } }}
          />
          <button
            onClick={() => fileInputRef.current?.click()}
            className="inline-flex items-center gap-1.5 px-3.5 py-2 rounded-lg text-sm font-medium bg-white text-slate-600 border border-slate-200 hover:bg-slate-50 transition-colors"
            title="导入配置"
          >
            <Upload size={14} /> 导入
          </button>
          <button
            onClick={handleExport}
            className="inline-flex items-center gap-1.5 px-3.5 py-2 rounded-lg text-sm font-medium bg-white text-slate-600 border border-slate-200 hover:bg-slate-50 transition-colors"
            title="导出配置"
          >
            <Download size={14} /> 导出
          </button>
          <button
            onClick={() => { setShowCreate(true); reset({ config_type: 'llm', provider: 'openai', ocr_enabled: 'false', ocr_lang: 'ch', ocr_device: 'cpu' }) }}
            className="inline-flex items-center gap-1.5 px-3.5 py-2 rounded-lg text-sm font-medium text-white bg-[var(--color-nav-bg)] hover:opacity-90 transition-colors shadow-sm"
          >
            <Plus size={14} /> 添加提供商
          </button>
        </div>
      </div>

      {/* Content */}
      {error ? (
        <div className="bg-white border border-red-100 rounded-xl p-8 text-center text-sm text-red-600">
          {error}
        </div>
      ) : loading ? (
        <div className="bg-white border border-slate-200 rounded-xl p-8 text-center text-sm text-slate-400">
          加载中...
        </div>
      ) : sortedModels.length === 0 ? (
        <div className="bg-white border border-slate-200 rounded-xl p-12 text-center">
          <div className="w-16 h-16 rounded-full bg-slate-50 flex items-center justify-center mx-auto mb-4">
            <Settings2 size={28} className="text-slate-300" />
          </div>
          <p className="text-slate-500 text-sm font-medium">暂无提供商配置</p>
          <p className="text-slate-400 text-xs mt-1">点击右上角按钮添加第一个提供商</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
          {sortedModels.map(m => {
            const status = testStatus[m.id] || 'idle'
            const isDefault = m.id === defaultModelId
            const enabled = isEnabled(m.id)
            const run = getModelRunStatus(m.id)
            const runMeta = RUN_META[run]
            const summary = getModelSummary(m.id)
            const cells = getModelHeatCells(m.id, 60)
            return (
              <div
                key={m.id}
                className={`group bg-white rounded-2xl border transition-all duration-200 hover:shadow-lg overflow-hidden ${
                  isDefault ? 'border-teal-600 ring-1 ring-teal-100' : 'border-slate-200'
                } ${enabled ? '' : 'opacity-95'}`}
              >
                <div className="p-4 pb-3.5">
                  {/* 名称 + 默认 + 启用开关 */}
                  <div className="flex items-start justify-between gap-2.5">
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-1.5">
                        <h3
                          className={`font-semibold text-[14px] truncate cursor-pointer hover:text-teal-700 transition-colors ${enabled ? 'text-slate-800' : 'text-slate-400'}`}
                          onClick={() => { setDetailModel(m); setDrawerTestStatus('idle'); setDrawerTestResult('') }}
                        >
                          {m.name}
                        </h3>
                        {isDefault && <Star size={13} className="shrink-0 text-amber-500 fill-amber-500" />}
                      </div>
                      <div className="flex items-center gap-1.5 mt-1.5">
                        <span className="text-[11px] px-1.5 py-0.5 rounded-md bg-slate-50 text-slate-500 border border-slate-200 font-medium">
                          {typeLabel(m.config_type)}
                        </span>
                        <span className={`text-[11px] px-1.5 py-0.5 rounded-md border font-medium capitalize ${providerColor(m.provider)}`}>
                          {m.provider}
                        </span>
                      </div>
                    </div>
                    {/* 启用 / 停用开关 */}
                    <button
                      onClick={async () => {
                        try {
                          await toggleEnabled(m.id)
                          addToast('info', `"${m.name}" 已${enabled ? '停用' : '启用'}`)
                        } catch {
                          addToast('error', `"${m.name}" 状态更新失败`)
                        }
                      }}
                      title={enabled ? '点击停用' : '点击启用'}
                      className="relative shrink-0 w-[38px] h-[22px] rounded-full transition-colors"
                      style={{ background: enabled ? '#0d9488' : '#cbd2dc' }}
                    >
                      <span
                        className="absolute top-0.5 w-[18px] h-[18px] rounded-full bg-white shadow transition-all"
                        style={{ left: enabled ? 18 : 2 }}
                      />
                    </button>
                  </div>

                  {/* 运行状态 + 最近调用 */}
                  <div className="flex items-center gap-2 mt-3.5">
                    <span
                      className="inline-flex items-center gap-1.5 text-[11px] font-semibold px-2.5 py-1 rounded-full"
                      style={{ background: runMeta.bg, color: runMeta.color }}
                    >
                      <span className="w-1.5 h-1.5 rounded-full" style={{ background: runMeta.dot }} />
                      {runMeta.label}
                    </span>
                    <span className="text-[11px] text-slate-300">·</span>
                    <span className="text-[11px] text-slate-400 whitespace-nowrap">最近调用 {summary.lastCall}</span>
                    {/* 测试状态（临时） */}
                    {status === 'testing' && (
                      <span className="ml-auto inline-flex items-center gap-1 text-[10px] font-medium px-2 py-0.5 rounded-full bg-blue-50 text-blue-600">
                        <Loader2 size={10} className="animate-spin" />测试中
                      </span>
                    )}
                  </div>

                  {/* 指标 */}
                  <div className="flex gap-2 mt-3.5">
                        <div className="flex-1 bg-slate-50 rounded-lg px-2.5 py-2">
                          <div className="text-[10px] text-slate-400 font-medium">今日调用</div>
                          <div className="text-[16px] font-bold text-slate-800 mt-0.5">{enabled ? summary.todayCalls : '—'}</div>
                        </div>
                        <div className="flex-1 bg-slate-50 rounded-lg px-2.5 py-2">
                          <div className="text-[10px] text-slate-400 font-medium">30天可用率</div>
                          <div className="text-[16px] font-bold mt-0.5" style={{ color: availColor(summary.availability) }}>
                            {summary.availability === '—' ? '—' : `${summary.availability}%`}
                          </div>
                        </div>
                        <div className="flex-1 bg-slate-50 rounded-lg px-2.5 py-2">
                          <div className="text-[10px] text-slate-400 font-medium">平均延迟</div>
                          <div className="text-[16px] font-bold mt-0.5" style={{ color: latColor(summary.avgLatency) }}>
                            {enabled && summary.avgLatency ? `${summary.avgLatency}ms` : '—'}
                          </div>
                        </div>
                      </div>

                      <div className="mt-3.5">
                        <div className="flex items-center justify-between mb-1.5">
                          <span className="text-[11px] font-semibold text-slate-500">近 60 次调用</span>
                          <span className="text-[10px] text-slate-300">← 早 · 近 →</span>
                        </div>
                        <ModelHeatStrip cells={cells} />
                      </div>
                </div>

                {/* Card Actions */}
                <div className="px-4 py-2.5 border-t border-slate-100 flex items-center gap-1.5 transition-opacity">
                  <button
                    onClick={() => handleTest(m.id)}
                    disabled={status === 'testing'}
                    className={`inline-flex items-center gap-1 px-2.5 py-1.5 rounded-lg text-[11px] font-medium transition-all disabled:opacity-50 ${
                      status === 'testing' ? 'bg-blue-50 text-blue-600' :
                      status === 'success' ? 'bg-emerald-50 text-emerald-600' :
                      status === 'error' ? 'bg-red-50 text-red-600' :
                      'bg-slate-50 text-slate-600 hover:bg-slate-100'
                    }`}
                  >
                    {status === 'testing' ? <Loader2 size={11} className="animate-spin" /> :
                     status === 'success' ? <CheckCircle2 size={11} /> :
                     status === 'error' ? <XCircle size={11} /> :
                     <TestTube2 size={11} />}
                    测试
                  </button>
                  <button
                    onClick={() => { setDetailModel(m); setDrawerTestStatus('idle'); setDrawerTestResult('') }}
                    className="inline-flex items-center gap-1 px-2.5 py-1.5 rounded-lg text-[11px] font-medium bg-slate-50 text-slate-600 hover:bg-slate-100 transition-colors"
                  >
                    <BarChart3 size={11} /> 详情
                  </button>
                  {!isDefault && (
                    <button
                      onClick={async () => {
                        try {
                          await setDefault(m.id)
                          addToast('success', `"${m.name}" 已设为默认模型`)
                        } catch {
                          addToast('error', `"${m.name}" 设置默认失败`)
                        }
                      }}
                      className="inline-flex items-center gap-1 px-2.5 py-1.5 rounded-lg text-[11px] font-medium bg-slate-100 text-slate-700 hover:bg-slate-200 transition-colors"
                    >
                      <Star size={11} /> 默认
                    </button>
                  )}
                  <div className="ml-auto flex items-center gap-1">
                    <button
                      onClick={() => openEdit(m)}
                      className="p-1.5 rounded-lg text-slate-400 hover:text-slate-600 hover:bg-slate-100 transition-colors"
                      title="编辑"
                    >
                      <Pencil size={13} />
                    </button>
                    <button
                      onClick={() => setDeleteTarget(m)}
                      className="p-1.5 rounded-lg text-slate-400 hover:text-red-500 hover:bg-red-50 transition-colors"
                      title="删除"
                    >
                      <Trash2 size={13} />
                    </button>
                  </div>
                </div>

                {testResult[m.id] && status === 'error' && (
                  <p className="text-xs mx-4 mb-3 text-red-500 bg-red-50 px-2 py-1 rounded">{testResult[m.id]}</p>
                )}
              </div>
            )
          })}
        </div>
      )}

      {/* 图例 */}
      {sortedModels.length > 0 && (
        <div className="flex items-center gap-4 flex-wrap mt-5 px-4 py-3 bg-white border border-slate-200 rounded-xl">
          <span className="text-[11px] font-semibold text-slate-500">调用热力条图例</span>
          <div className="flex items-center gap-1.5 text-[11px] text-slate-400">
            成功
            {['#216e39', '#2d8a4e', '#40c463', '#9be9a8'].map(c => (
              <span key={c} className="w-3 h-3 rounded-[2px]" style={{ background: c }} />
            ))}
            <span className="text-slate-300">快←→慢</span>
          </div>
          <div className="flex items-center gap-1.5 text-[11px] text-slate-400">
            <span className="w-3 h-3 rounded-[2px]" style={{ background: '#f0a020' }} /> 超时
          </div>
          <div className="flex items-center gap-1.5 text-[11px] text-slate-400">
            <span className="w-3 h-3 rounded-[2px]" style={{ background: '#e5484d' }} /> 异常
          </div>
          <div className="flex items-center gap-1.5 text-[11px] text-slate-400">
            <span className="w-3 h-3 rounded-[2px]" style={{ background: '#eceef1' }} /> 已停用
          </div>
        </div>
      )}

      {/* Create Modal */}
      {showCreate && (
        <ModelFormModal
          title="添加提供商"
          onClose={() => setShowCreate(false)}
          onSubmit={handleCreate}
          register={register}
          handleSubmit={handleSubmit}
          configType={watch('config_type') || 'llm'}
          setValue={setCreateValue}
        />
      )}

      {/* Edit Modal */}
      {editTarget && (
        <div className="fixed inset-0 bg-black/40 backdrop-blur-sm flex items-center justify-center z-50 p-4" onClick={() => setEditTarget(null)}>
          <div className="bg-white rounded-xl shadow-2xl p-6 w-[560px] max-h-[88vh] overflow-y-auto" onClick={e => e.stopPropagation()}>
            <div className="flex justify-between items-center mb-5">
              <h3 className="font-semibold text-slate-800">编辑模型</h3>
              <button onClick={() => setEditTarget(null)} className="text-slate-400 hover:text-slate-600 p-1 rounded-lg hover:bg-slate-100 transition-colors">
                <X size={16} />
              </button>
            </div>
            <form onSubmit={handleEditSubmit(handleUpdate)} className="space-y-4">
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-sm font-medium text-slate-700 mb-1.5">名称 *</label>
                  <input {...regEdit('name', { required: true })} className="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm text-slate-700 focus:outline-none focus:ring-2 focus:ring-teal-500/20 focus:border-teal-400 transition-all" />
                </div>
                <div>
                  <label className="block text-sm font-medium text-slate-700 mb-1.5">配置分类 *</label>
                  <select {...regEdit('config_type', { required: true, onChange: e => setValue('provider', PROVIDERS[e.target.value]?.[0]?.value || 'custom') })}
                    className="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm text-slate-700 focus:outline-none focus:ring-2 focus:ring-teal-500/20 focus:border-teal-400 transition-all">
                    {CONFIG_TYPES.map(t => <option key={t.value} value={t.value}>{t.label}</option>)}
                  </select>
                </div>
              </div>
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-sm font-medium text-slate-700 mb-1.5">Provider *</label>
                  <select {...regEdit('provider', { required: true })}
                    className="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm text-slate-700 focus:outline-none focus:ring-2 focus:ring-teal-500/20 focus:border-teal-400 transition-all">
                    {(PROVIDERS[watchEdit('config_type') || 'llm'] || PROVIDERS.llm).map(p => <option key={p.value} value={p.value}>{p.label}</option>)}
                  </select>
                </div>
                <div>
                  <label className="block text-sm font-medium text-slate-700 mb-1.5">API Key</label>
                  <input {...regEdit('api_key')} type="password" placeholder={editTarget.has_api_key ? '已保存，留空保留原密钥' : 'sk-...'}
                    className="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm text-slate-700 focus:outline-none focus:ring-2 focus:ring-teal-500/20 focus:border-teal-400 transition-all" />
                </div>
              </div>
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1.5">API Base</label>
                <input {...regEdit('api_base')} placeholder="https://api.openai.com/v1"
                  className="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm text-slate-700 focus:outline-none focus:ring-2 focus:ring-teal-500/20 focus:border-teal-400 transition-all" />
              </div>
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1.5">模型名</label>
                <input {...regEdit('models_str')} placeholder="gpt-4o"
                  className="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm font-mono text-slate-700 focus:outline-none focus:ring-2 focus:ring-teal-500/20 focus:border-teal-400 transition-all" />
                <p className="text-[11px] text-slate-400 mt-1">每个提供商仅支持配置一个模型</p>
              </div>
              {(watchEdit('config_type') || 'llm') === 'llm' && (
                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <label className="block text-sm font-medium text-slate-700 mb-1.5">最大上下文（tokens）</label>
                    <input {...regEdit('max_context_tokens')} type="number" min={1} placeholder="如：128000"
                      className="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm text-slate-700 focus:outline-none focus:ring-2 focus:ring-teal-500/20 focus:border-teal-400 transition-all" />
                    <p className="text-[11px] text-slate-400 mt-1">模型可接受的最大输入上下文，留空则不限制</p>
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-slate-700 mb-1.5">最大输出（tokens）</label>
                    <input {...regEdit('max_output_tokens')} type="number" min={1} placeholder="如：4096"
                      className="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm text-slate-700 focus:outline-none focus:ring-2 focus:ring-teal-500/20 focus:border-teal-400 transition-all" />
                    <p className="text-[11px] text-slate-400 mt-1">单次调用最大生成 tokens，留空用默认值</p>
                  </div>
                </div>
              )}
              {(watchEdit('config_type') || 'llm') === 'ocr' && (
                <div className="grid grid-cols-3 gap-4">
                  <div>
                    <label className="block text-sm font-medium text-slate-700 mb-1.5">启用运行</label>
                    <select {...regEdit('ocr_enabled')}
                      className="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm text-slate-700 focus:outline-none focus:ring-2 focus:ring-teal-500/20 focus:border-teal-400 transition-all">
                      <option value="false">关闭</option><option value="true">开启</option>
                    </select>
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-slate-700 mb-1.5">OCR语言</label>
                    <input {...regEdit('ocr_lang')} placeholder="ch"
                      className="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm text-slate-700 focus:outline-none focus:ring-2 focus:ring-teal-500/20 focus:border-teal-400 transition-all" />
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-slate-700 mb-1.5">设备</label>
                    <select {...regEdit('ocr_device')}
                      className="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm text-slate-700 focus:outline-none focus:ring-2 focus:ring-teal-500/20 focus:border-teal-400 transition-all">
                      <option value="cpu">CPU</option><option value="gpu">GPU</option>
                    </select>
                  </div>
                </div>
              )}
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1.5">高级参数 JSON</label>
                <textarea {...regEdit('options_json')} rows={3} placeholder='{"timeout": 30}'
                  className="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm font-mono text-slate-700 focus:outline-none focus:ring-2 focus:ring-teal-500/20 focus:border-teal-400 transition-all" />
              </div>
              <div className="flex justify-center gap-3 pt-2">
                <button type="button" onClick={() => setEditTarget(null)}
                  className="px-5 py-2 border border-slate-200 rounded-lg text-sm text-slate-600 hover:bg-slate-50 transition-colors">取消</button>
                <button type="submit"
                  className="flex items-center gap-1.5 px-5 py-2 bg-teal-600 text-white rounded-lg text-sm hover:bg-teal-700 transition-colors">
                  保存
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Detail Drawer */}
      <ModelDetailDrawer
        model={detailModel}
        isOpen={!!detailModel}
        onClose={() => setDetailModel(null)}
        isDefault={detailModel?.id === defaultModelId}
        onSetDefault={async () => {
          if (!detailModel) return
          try {
            await setDefault(detailModel.id)
            addToast('success', `"${detailModel.name}" 已设为默认模型`)
          } catch {
            addToast('error', `"${detailModel.name}" 设置默认失败`)
          }
        }}
        testStatus={drawerTestStatus}
        testResult={drawerTestResult}
        onTest={handleDrawerTest}
        dailyStats={detailModel ? getModelDailyStats(detailModel.id) : []}
      />

      {/* Delete Confirm */}
      <ConfirmDialog
        open={!!deleteTarget}
        title={t('model.confirm_delete')}
        message={t('model.confirm_delete_msg', { name: deleteTarget?.name })}
        onConfirm={handleDelete}
        onCancel={() => setDeleteTarget(null)}
      />
    </div>
  )
}

/** Model Form Modal (Create) */
function ModelFormModal({ title, onClose, onSubmit, register, handleSubmit, configType, setValue }: any) {
  return (
    <div className="fixed inset-0 bg-black/40 backdrop-blur-sm flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-xl shadow-2xl p-6 w-[560px] max-h-[88vh] overflow-y-auto" onClick={e => e.stopPropagation()}>
        <h3 className="font-semibold text-slate-800 mb-5">{title}</h3>
        <form onSubmit={handleSubmit(onSubmit)} className="space-y-4">
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-slate-700 mb-1.5">名称 *</label>
              <input {...register('name', { required: true })} placeholder="如：GPT-4o 生产环境"
                className="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm text-slate-700 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-teal-500/20 focus:border-teal-400 transition-all" />
            </div>
            <div>
              <label className="block text-sm font-medium text-slate-700 mb-1.5">配置分类 *</label>
              <select {...register('config_type', { required: true, onChange: (e: any) => setValue('provider', PROVIDERS[e.target.value]?.[0]?.value || 'custom') })}
                className="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm text-slate-700 focus:outline-none focus:ring-2 focus:ring-teal-500/20 focus:border-teal-400 transition-all">
                {CONFIG_TYPES.map(t => <option key={t.value} value={t.value}>{t.label}</option>)}
              </select>
            </div>
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-slate-700 mb-1.5">Provider *</label>
              <select {...register('provider', { required: true })}
                className="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm text-slate-700 focus:outline-none focus:ring-2 focus:ring-teal-500/20 focus:border-teal-400 transition-all">
                {(PROVIDERS[configType] || PROVIDERS.llm).map(p => <option key={p.value} value={p.value}>{p.label}</option>)}
              </select>
            </div>
            <div>
              <label className="block text-sm font-medium text-slate-700 mb-1.5">API Key</label>
              <input {...register('api_key')} type="password" placeholder="sk-..."
                className="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm text-slate-700 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-teal-500/20 focus:border-teal-400 transition-all" />
            </div>
          </div>
          <div>
            <label className="block text-sm font-medium text-slate-700 mb-1.5">API Base</label>
            <input {...register('api_base')} placeholder="https://api.openai.com/v1"
              className="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm text-slate-700 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-teal-500/20 focus:border-teal-400 transition-all" />
          </div>
          <div>
            <label className="block text-sm font-medium text-slate-700 mb-1.5">模型名</label>
            <input {...register('models_str')} placeholder="gpt-4o"
              className="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm font-mono text-slate-700 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-teal-500/20 focus:border-teal-400 transition-all" />
            <p className="text-[11px] text-slate-400 mt-1">每个提供商仅支持配置一个模型</p>
          </div>
          {configType === 'llm' && (
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1.5">最大上下文（tokens）</label>
                <input {...register('max_context_tokens')} type="number" min={1} placeholder="如：128000"
                  className="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm text-slate-700 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-teal-500/20 focus:border-teal-400 transition-all" />
                <p className="text-[11px] text-slate-400 mt-1">模型可接受的最大输入上下文，留空则不限制</p>
              </div>
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1.5">最大输出（tokens）</label>
                <input {...register('max_output_tokens')} type="number" min={1} placeholder="如：4096"
                  className="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm text-slate-700 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-teal-500/20 focus:border-teal-400 transition-all" />
                <p className="text-[11px] text-slate-400 mt-1">单次调用最大生成 tokens，留空用默认值</p>
              </div>
            </div>
          )}
          {configType === 'ocr' && (
            <div className="grid grid-cols-3 gap-4">
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1.5">启用运行</label>
                <select {...register('ocr_enabled')}
                  className="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm text-slate-700 focus:outline-none focus:ring-2 focus:ring-teal-500/20 focus:border-teal-400 transition-all">
                  <option value="false">关闭</option><option value="true">开启</option>
                </select>
              </div>
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1.5">OCR语言</label>
                <input {...register('ocr_lang')} placeholder="ch"
                  className="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm text-slate-700 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-teal-500/20 focus:border-teal-400 transition-all" />
              </div>
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1.5">设备</label>
                <select {...register('ocr_device')}
                  className="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm text-slate-700 focus:outline-none focus:ring-2 focus:ring-teal-500/20 focus:border-teal-400 transition-all">
                  <option value="cpu">CPU</option><option value="gpu">GPU</option>
                </select>
              </div>
            </div>
          )}
          <div>
            <label className="block text-sm font-medium text-slate-700 mb-1.5">高级参数 JSON</label>
            <textarea {...register('options_json')} rows={3} placeholder='{"timeout": 30}'
              className="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm font-mono text-slate-700 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-teal-500/20 focus:border-teal-400 transition-all" />
          </div>
          <div className="flex justify-center gap-3 pt-2">
            <button type="button" onClick={onClose}
              className="px-5 py-2 border border-slate-200 rounded-lg text-sm text-slate-600 hover:bg-slate-50 transition-colors">取消</button>
            <button type="submit"
              className="flex items-center gap-1.5 px-5 py-2 rounded-lg text-sm text-white bg-teal-600 hover:bg-teal-700 transition-colors">
              保存
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
