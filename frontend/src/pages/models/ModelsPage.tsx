import { useState, useCallback, useRef } from 'react'
import { useForm } from 'react-hook-form'
import { useTranslation } from 'react-i18next'
import {
  Plus, Trash2, TestTube2, Pencil, X, Loader2, CheckCircle2, XCircle,
  Star, LayoutGrid, Table2, Search, Upload, Download,
  Settings2, BarChart3
} from 'lucide-react'
import type { ModelConfig } from '@/types/ontology'
import ConfirmDialog from '@/components/ConfirmDialog'
import ToastContainer from './components/Toast'
import ModelStatsPanel from './components/ModelStatsPanel'
import ModelDetailDrawer from './components/ModelDetailDrawer'
import { useMockModels } from './hooks/useMockModels'

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

const USAGE_TAGS = ['VLM提取', '结构化提取', '宽表分析', 'Ontology Mapping', 'NL-to-Cypher', 'OCR文字提取']

function modelList(text?: string) {
  return text ? text.split('\n').map((s: string) => s.trim()).filter(Boolean) : []
}

function parseOptions(text?: string) {
  if (!text?.trim()) return {}
  try {
    return JSON.parse(text)
  } catch {
    return {}
  }
}

function buildPayload(data: any, usageTags: string[], mode: 'create' | 'update' = 'create') {
  const options = {
    ...parseOptions(data.options_json),
    usage_tags: usageTags,
    ...(data.config_type === 'ocr' ? {
      enabled: data.ocr_enabled === 'true',
      lang: data.ocr_lang || 'ch',
      device: data.ocr_device || 'cpu',
    } : {}),
  }
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

export default function ModelsPage() {
  const { t } = useTranslation()
  const {
    models, defaultModelId, setDefault, createModel, updateModel, deleteModel,
    testConnection, dailyStats, getModelDailyStats,
  } = useMockModels()

  // UI States
  const [viewMode, setViewMode] = useState<'table' | 'card'>('card')
  const [searchQuery, setSearchQuery] = useState('')
  const [filterType, setFilterType] = useState<string>('all')
  const [showStats, setShowStats] = useState(false)

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
    reader.onload = (e) => {
      try {
        const data = JSON.parse(e.target?.result as string)
        if (Array.isArray(data)) {
          data.forEach((item: any) => {
            if (item.name && item.provider) {
              createModel({
                name: item.name,
                config_type: item.config_type || 'llm',
                provider: item.provider,
                api_base: item.api_base || '',
                models: item.models || [],
                options: item.options || {},
              })
            }
          })
          addToast('success', `成功导入 ${data.length} 个模型配置`)
        } else {
          addToast('error', '导入文件格式不正确，应为JSON数组')
        }
      } catch {
        addToast('error', '解析JSON文件失败')
      }
    }
    reader.readAsText(file)
  }, [createModel, addToast])

  const fileInputRef = useRef<HTMLInputElement>(null)

  // Form
  const [formTags, setFormTags] = useState<string[]>([])
  const [editTags, setEditTags] = useState<string[]>([])
  const { register, handleSubmit, reset, watch, setValue: setCreateValue } = useForm<any>({
    defaultValues: { config_type: 'llm', provider: 'openai', ocr_enabled: 'false', ocr_lang: 'ch', ocr_device: 'cpu' },
  })
  const { register: regEdit, handleSubmit: handleEditSubmit, setValue, watch: watchEdit } = useForm<any>()

  // Filtered models
  const filteredModels = models.filter(m => {
    const matchSearch = !searchQuery ||
      m.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
      m.provider.toLowerCase().includes(searchQuery.toLowerCase())
    const matchType = filterType === 'all' || m.config_type === filterType
    return matchSearch && matchType
  })

  // Handlers
  const handleCreate = (data: any) => {
    createModel(buildPayload(data, formTags))
    setShowCreate(false)
    reset()
    setFormTags([])
    addToast('success', `模型 "${data.name}" 创建成功`)
  }

  const handleUpdate = (data: any) => {
    if (editTarget) {
      updateModel(editTarget.id, buildPayload(data, editTags, 'update'))
      setEditTarget(null)
      setEditTags([])
      addToast('success', '模型更新成功')
    }
  }

  const handleDelete = () => {
    if (deleteTarget) {
      deleteModel(deleteTarget.id)
      if (detailModel?.id === deleteTarget.id) setDetailModel(null)
      setDeleteTarget(null)
      addToast('success', '模型已删除')
    }
  }

  const handleTest = async (id: string) => {
    setTestStatus(prev => ({ ...prev, [id]: 'testing' }))
    const result = await testConnection(id)
    setTestStatus(prev => ({ ...prev, [id]: result.ok ? 'success' : 'error' }))
    setTestResult(prev => ({ ...prev, [id]: result.message }))
    if (result.ok) {
      addToast('success', result.message)
    } else {
      addToast('error', result.message)
    }
    setTimeout(() => setTestStatus(prev => ({ ...prev, [id]: 'idle' })), 3000)
  }

  const handleDrawerTest = async () => {
    if (!detailModel) return
    setDrawerTestStatus('testing')
    const result = await testConnection(detailModel.id)
    setDrawerTestStatus(result.ok ? 'success' : 'error')
    setDrawerTestResult(result.message)
    if (result.ok) {
      addToast('success', result.message)
    } else {
      addToast('error', result.message)
    }
    setTimeout(() => setDrawerTestStatus('idle'), 3000)
  }

  const openEdit = (m: ModelConfig) => {
    const options = m.options || {}
    setEditTarget(m)
    setEditTags((options.usage_tags as string[]) || [])
    setValue('name', m.name)
    setValue('config_type', m.config_type || 'llm')
    setValue('provider', m.provider)
    setValue('api_key', '')
    setValue('api_base', m.api_base || '')
    setValue('models_str', (m.models || []).join('\n'))
    setValue('ocr_enabled', options.enabled ? 'true' : 'false')
    setValue('ocr_lang', String(options.lang || 'ch'))
    setValue('ocr_device', String(options.device || 'cpu'))
    setValue('options_json', JSON.stringify(
      Object.fromEntries(Object.entries(options).filter(([k]) => !['usage_tags', 'lang', 'device', 'enabled'].includes(k))),
      null, 2,
    ))
  }

  const typeCount = (type: string) => models.filter(m => m.config_type === type).length

  return (
    <div className="min-h-full">
      {/* Toast Container */}
      <ToastContainer toasts={toasts} onRemove={removeToast} />

      {/* Header Section */}
      <div className="mb-6">
        <div className="flex items-center justify-between mb-4">
          <div>
            <h2 className="text-xl font-bold text-slate-800">{t('model.title')}</h2>
            <p className="text-sm text-slate-500 mt-0.5">管理 AI 模型配置，监控调用统计</p>
          </div>
          <div className="flex items-center gap-2">
            {/* Import/Export */}
            <input
              ref={fileInputRef}
              type="file"
              accept=".json"
              className="hidden"
              onChange={e => { const f = e.target.files?.[0]; if (f) { handleImport(f); e.target.value = '' } }}
            />
            <button
              onClick={() => fileInputRef.current?.click()}
              className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs font-medium bg-white text-slate-600 border border-slate-200 hover:bg-slate-50 transition-colors"
              title="导入配置"
            >
              <Upload size={14} /> 导入
            </button>
            <button
              onClick={handleExport}
              className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs font-medium bg-white text-slate-600 border border-slate-200 hover:bg-slate-50 transition-colors"
              title="导出配置"
            >
              <Download size={14} /> 导出
            </button>
            {/* Stats toggle */}
            <button
              onClick={() => setShowStats(!showStats)}
              className={`inline-flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs font-medium border transition-colors ${
                showStats ? 'bg-blue-50 text-blue-600 border-blue-200' : 'bg-white text-slate-600 border-slate-200 hover:bg-slate-50'
              }`}
            >
              <BarChart3 size={14} /> 统计
            </button>
            {/* Create */}
            <button
              onClick={() => { setShowCreate(true); reset({ config_type: 'llm', provider: 'openai', ocr_enabled: 'false', ocr_lang: 'ch', ocr_device: 'cpu' }); setFormTags([]) }}
              className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg text-xs font-medium text-white bg-slate-800 hover:bg-slate-700 transition-colors shadow-sm"
            >
              <Plus size={14} /> {t('model.create')}
            </button>
          </div>
        </div>

        {/* Toolbar */}
        <div className="flex items-center gap-3 flex-wrap">
          {/* Search */}
          <div className="relative flex-1 min-w-[200px] max-w-md">
            <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
            <input
              value={searchQuery}
              onChange={e => setSearchQuery(e.target.value)}
              placeholder="搜索模型名称、Provider..."
              className="w-full pl-9 pr-4 py-2 rounded-lg text-sm bg-white border border-slate-200 text-slate-700 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-400 transition-all"
            />
          </div>

          {/* Filter Tabs */}
          <div className="flex items-center gap-1 bg-white rounded-lg border border-slate-200 p-0.5">
            {[
              { value: 'all', label: '全部', count: models.length },
              { value: 'llm', label: 'LLM', count: typeCount('llm') },
              { value: 'ocr', label: 'OCR', count: typeCount('ocr') },
              { value: 'other', label: '其他', count: typeCount('other') },
            ].map(tab => (
              <button
                key={tab.value}
                onClick={() => setFilterType(tab.value)}
                className={`relative px-3 py-1.5 rounded-md text-xs font-medium transition-all ${
                  filterType === tab.value
                    ? 'bg-slate-800 text-white shadow-sm'
                    : 'text-slate-500 hover:text-slate-700 hover:bg-slate-50'
                }`}
              >
                {tab.label}
                <span className={`ml-1 ${filterType === tab.value ? 'text-slate-300' : 'text-slate-400'}`}>
                  {tab.count}
                </span>
              </button>
            ))}
          </div>

          {/* View Toggle */}
          <div className="flex items-center gap-1 bg-white rounded-lg border border-slate-200 p-0.5 ml-auto">
            <button
              onClick={() => setViewMode('table')}
              className={`p-1.5 rounded-md transition-all ${viewMode === 'table' ? 'bg-slate-100 text-slate-700' : 'text-slate-400 hover:text-slate-600'}`}
              title="表格视图"
            >
              <Table2 size={16} />
            </button>
            <button
              onClick={() => setViewMode('card')}
              className={`p-1.5 rounded-md transition-all ${viewMode === 'card' ? 'bg-slate-100 text-slate-700' : 'text-slate-400 hover:text-slate-600'}`}
              title="卡片视图"
            >
              <LayoutGrid size={16} />
            </button>
          </div>
        </div>
      </div>

      {/* Stats Panel */}
      {showStats && (
        <div className="mb-6 animate-fade-in">
          <ModelStatsPanel dailyStats={dailyStats} />
        </div>
      )}

      {/* Content */}
      {filteredModels.length === 0 ? (
        <div className="bg-white border border-slate-200 rounded-xl p-12 text-center">
          <div className="w-16 h-16 rounded-full bg-slate-50 flex items-center justify-center mx-auto mb-4">
            <Settings2 size={28} className="text-slate-300" />
          </div>
          <p className="text-slate-500 text-sm font-medium">暂无模型配置</p>
          <p className="text-slate-400 text-xs mt-1">点击右上角按钮创建第一个模型</p>
        </div>
      ) : viewMode === 'card' ? (
        /* Card View */
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
          {filteredModels.map(m => {
            const status = testStatus[m.id] || 'idle'
            const isDefault = m.id === defaultModelId
            return (
              <div
                key={m.id}
                className={`group bg-white rounded-xl border transition-all duration-200 hover:shadow-lg hover:border-slate-300 overflow-hidden ${
                  isDefault ? 'border-slate-700 ring-1 ring-slate-200 shadow-md' : 'border-slate-200'
                }`}
              >
                {/* Card Header */}
                <div className="p-5 pb-3">
                  <div className="flex items-start justify-between gap-3 mb-3">
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 mb-1">
                        <h3
                          className="font-semibold text-[15px] text-slate-800 truncate cursor-pointer hover:text-blue-600 transition-colors"
                          onClick={() => { setDetailModel(m); setDrawerTestStatus('idle'); setDrawerTestResult('') }}
                        >
                          {m.name}
                        </h3>
                        {isDefault && (
                          <span className="shrink-0 inline-flex items-center gap-0.5 text-[10px] font-semibold px-1.5 py-0.5 rounded-full bg-slate-100 text-slate-700 border border-slate-300">
                            <Star size={9} className="fill-slate-700" /> 默认
                          </span>
                        )}
                      </div>
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="text-[11px] px-2 py-0.5 rounded-md bg-slate-50 text-slate-500 border border-slate-200 font-medium">
                          {typeLabel(m.config_type)}
                        </span>
                        <span className={`text-[11px] px-2 py-0.5 rounded-md border font-medium capitalize ${providerColor(m.provider)}`}>
                          {m.provider}
                        </span>
                      </div>
                    </div>
                    {/* Status badge */}
                    {status === 'testing' && (
                      <span className="shrink-0 inline-flex items-center gap-1 text-[10px] font-medium px-2 py-0.5 rounded-full bg-blue-50 text-blue-600">
                        <Loader2 size={10} className="animate-spin" />测试中
                      </span>
                    )}
                    {status === 'success' && (
                      <span className="shrink-0 inline-flex items-center gap-1 text-[10px] font-medium px-2 py-0.5 rounded-full bg-emerald-50 text-emerald-600">
                        <CheckCircle2 size={10} />正常
                      </span>
                    )}
                    {status === 'error' && (
                      <span className="shrink-0 inline-flex items-center gap-1 text-[10px] font-medium px-2 py-0.5 rounded-full bg-red-50 text-red-600">
                        <XCircle size={10} />异常
                      </span>
                    )}
                  </div>

                  <p className="text-[12px] text-slate-400 mb-3 truncate">
                    {m.api_base || '无 API Base'}
                    {m.has_api_key ? ' · API Key 已保存' : ' · 未配置 API Key'}
                  </p>

                  {/* Models */}
                  {m.models?.length > 0 && (
                    <div className="flex gap-1.5 flex-wrap mb-3">
                      {m.models.slice(0, 4).map(mn => (
                        <span key={mn} className="text-[11px] bg-blue-50 text-blue-600 px-2 py-0.5 rounded-md font-medium border border-blue-100">{mn}</span>
                      ))}
                      {m.models.length > 4 && (
                        <span className="text-[11px] text-slate-400 px-1 py-0.5">+{m.models.length - 4}</span>
                      )}
                    </div>
                  )}

                  {/* Tags */}
                  {((m.options?.usage_tags as string[]) || []).length > 0 && (
                    <div className="flex gap-1.5 flex-wrap">
                      {((m.options?.usage_tags as string[]) || []).map((tag: string) => (
                        <span key={tag} className="text-[11px] bg-slate-50 text-slate-500 px-2 py-0.5 rounded-full border border-slate-200">{tag}</span>
                      ))}
                    </div>
                  )}

                  {testResult[m.id] && status === 'error' && (
                    <p className="text-xs mt-2 text-red-500 bg-red-50 px-2 py-1 rounded">{testResult[m.id]}</p>
                  )}
                </div>

                {/* Card Actions */}
                <div className="px-5 py-3 border-t border-slate-100 flex items-center gap-1.5 opacity-0 group-hover:opacity-100 transition-opacity">
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
                      onClick={() => { setDefault(m.id); addToast('success', `"${m.name}" 已设为默认模型`) }}
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
              </div>
            )
          })}
        </div>
      ) : (
        /* Table View */
        <div className="bg-white rounded-xl border border-slate-200 overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr className="border-b border-slate-200 bg-slate-50/50">
                  <th className="text-left text-[11px] font-semibold text-slate-500 uppercase tracking-wider px-4 py-3">模型</th>
                  <th className="text-left text-[11px] font-semibold text-slate-500 uppercase tracking-wider px-4 py-3">类型</th>
                  <th className="text-left text-[11px] font-semibold text-slate-500 uppercase tracking-wider px-4 py-3">Provider</th>
                  <th className="text-left text-[11px] font-semibold text-slate-500 uppercase tracking-wider px-4 py-3">可用模型</th>
                  <th className="text-left text-[11px] font-semibold text-slate-500 uppercase tracking-wider px-4 py-3">标签</th>
                  <th className="text-left text-[11px] font-semibold text-slate-500 uppercase tracking-wider px-4 py-3">状态</th>
                  <th className="text-right text-[11px] font-semibold text-slate-500 uppercase tracking-wider px-4 py-3">操作</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {filteredModels.map(m => {
                  const status = testStatus[m.id] || 'idle'
                  const isDefault = m.id === defaultModelId
                  return (
                    <tr
                      key={m.id}
                      className={`group hover:bg-slate-50/80 transition-colors ${isDefault ? 'bg-slate-50' : ''}`}
                    >
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-2">
                          <button
                            onClick={() => { setDetailModel(m); setDrawerTestStatus('idle'); setDrawerTestResult('') }}
                            className="font-medium text-sm text-slate-700 hover:text-blue-600 transition-colors text-left"
                          >
                            {m.name}
                          </button>
                          {isDefault && <Star size={12} className="text-slate-600 fill-slate-600 shrink-0" />}
                        </div>
                        <p className="text-[11px] text-slate-400 mt-0.5 truncate max-w-[200px]">{m.api_base || '-'}</p>
                      </td>
                      <td className="px-4 py-3">
                        <span className="text-[11px] px-2 py-0.5 rounded-md bg-slate-50 text-slate-500 border border-slate-200 font-medium">
                          {typeLabel(m.config_type)}
                        </span>
                      </td>
                      <td className="px-4 py-3">
                        <span className={`text-[11px] px-2 py-0.5 rounded-md border font-medium capitalize ${providerColor(m.provider)}`}>
                          {m.provider}
                        </span>
                      </td>
                      <td className="px-4 py-3">
                        <div className="flex gap-1 flex-wrap max-w-[200px]">
                          {m.models?.slice(0, 2).map(mn => (
                            <span key={mn} className="text-[10px] bg-blue-50 text-blue-600 px-1.5 py-0.5 rounded font-medium">{mn}</span>
                          ))}
                          {(m.models?.length || 0) > 2 && (
                            <span className="text-[10px] text-slate-400">+{(m.models?.length || 0) - 2}</span>
                          )}
                        </div>
                      </td>
                      <td className="px-4 py-3">
                        <div className="flex gap-1 flex-wrap max-w-[150px]">
                          {((m.options?.usage_tags as string[]) || []).slice(0, 2).map((tag: string) => (
                            <span key={tag} className="text-[10px] bg-slate-50 text-slate-500 px-1.5 py-0.5 rounded-full border border-slate-200">{tag}</span>
                          ))}
                          {((m.options?.usage_tags as string[]) || []).length > 2 && (
                            <span className="text-[10px] text-slate-400">+{((m.options?.usage_tags as string[]) || []).length - 2}</span>
                          )}
                        </div>
                      </td>
                      <td className="px-4 py-3">
                        {status === 'testing' && (
                          <span className="inline-flex items-center gap-1 text-[10px] font-medium px-2 py-0.5 rounded-full bg-blue-50 text-blue-600">
                            <Loader2 size={10} className="animate-spin" />测试中
                          </span>
                        )}
                        {status === 'success' && (
                          <span className="inline-flex items-center gap-1 text-[10px] font-medium px-2 py-0.5 rounded-full bg-emerald-50 text-emerald-600">
                            <CheckCircle2 size={10} />正常
                          </span>
                        )}
                        {status === 'error' && (
                          <span className="inline-flex items-center gap-1 text-[10px] font-medium px-2 py-0.5 rounded-full bg-red-50 text-red-600">
                            <XCircle size={10} />异常
                          </span>
                        )}
                        {status === 'idle' && (
                          <span className="inline-flex items-center gap-1 text-[10px] text-slate-400">
                            <span className="w-1.5 h-1.5 rounded-full bg-slate-300" />待测试
                          </span>
                        )}
                      </td>
                      <td className="px-4 py-3">
                        <div className="flex items-center justify-end gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                          <button
                            onClick={() => handleTest(m.id)}
                            disabled={status === 'testing'}
                            className="p-1.5 rounded-lg text-slate-400 hover:text-blue-600 hover:bg-blue-50 transition-colors"
                            title="测试连通性"
                          >
                            {status === 'testing' ? <Loader2 size={13} className="animate-spin" /> : <TestTube2 size={13} />}
                          </button>
                          <button
                            onClick={() => openEdit(m)}
                            className="p-1.5 rounded-lg text-slate-400 hover:text-slate-600 hover:bg-slate-100 transition-colors"
                            title="编辑"
                          >
                            <Pencil size={13} />
                          </button>
                          {!isDefault && (
                            <button
                              onClick={() => { setDefault(m.id); addToast('success', `"${m.name}" 已设为默认模型`) }}
                              className="p-1.5 rounded-lg text-slate-400 hover:text-slate-700 hover:bg-slate-100 transition-colors"
                              title="设为默认"
                            >
                              <Star size={13} />
                            </button>
                          )}
                          <button
                            onClick={() => setDeleteTarget(m)}
                            className="p-1.5 rounded-lg text-slate-400 hover:text-red-500 hover:bg-red-50 transition-colors"
                            title="删除"
                          >
                            <Trash2 size={13} />
                          </button>
                        </div>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Create Modal */}
      {showCreate && (
        <ModelFormModal
          title="新建模型"
          onClose={() => setShowCreate(false)}
          onSubmit={handleCreate}
          formTags={formTags}
          setFormTags={setFormTags}
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
                  <input {...regEdit('name', { required: true })} className="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm text-slate-700 focus:outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-400 transition-all" />
                </div>
                <div>
                  <label className="block text-sm font-medium text-slate-700 mb-1.5">配置分类 *</label>
                  <select {...regEdit('config_type', { required: true, onChange: e => setValue('provider', PROVIDERS[e.target.value]?.[0]?.value || 'custom') })}
                    className="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm text-slate-700 focus:outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-400 transition-all">
                    {CONFIG_TYPES.map(t => <option key={t.value} value={t.value}>{t.label}</option>)}
                  </select>
                </div>
              </div>
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-sm font-medium text-slate-700 mb-1.5">Provider *</label>
                  <select {...regEdit('provider', { required: true })}
                    className="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm text-slate-700 focus:outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-400 transition-all">
                    {(PROVIDERS[watchEdit('config_type') || 'llm'] || PROVIDERS.llm).map(p => <option key={p.value} value={p.value}>{p.label}</option>)}
                  </select>
                </div>
                <div>
                  <label className="block text-sm font-medium text-slate-700 mb-1.5">API Key</label>
                  <input {...regEdit('api_key')} type="password" placeholder={editTarget.has_api_key ? '已保存，留空保留原密钥' : 'sk-...'}
                    className="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm text-slate-700 focus:outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-400 transition-all" />
                </div>
              </div>
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1.5">API Base</label>
                <input {...regEdit('api_base')} placeholder="https://api.openai.com/v1"
                  className="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm text-slate-700 focus:outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-400 transition-all" />
              </div>
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1.5">模型名（每行一个）</label>
                <textarea {...regEdit('models_str')} rows={3} placeholder="gpt-4o\ngpt-4o-mini"
                  className="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm font-mono text-slate-700 focus:outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-400 transition-all" />
              </div>
              {(watchEdit('config_type') || 'llm') === 'ocr' && (
                <div className="grid grid-cols-3 gap-4">
                  <div>
                    <label className="block text-sm font-medium text-slate-700 mb-1.5">启用运行</label>
                    <select {...regEdit('ocr_enabled')}
                      className="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm text-slate-700 focus:outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-400 transition-all">
                      <option value="false">关闭</option><option value="true">开启</option>
                    </select>
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-slate-700 mb-1.5">OCR语言</label>
                    <input {...regEdit('ocr_lang')} placeholder="ch"
                      className="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm text-slate-700 focus:outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-400 transition-all" />
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-slate-700 mb-1.5">设备</label>
                    <select {...regEdit('ocr_device')}
                      className="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm text-slate-700 focus:outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-400 transition-all">
                      <option value="cpu">CPU</option><option value="gpu">GPU</option>
                    </select>
                  </div>
                </div>
              )}
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1.5">高级参数 JSON</label>
                <textarea {...regEdit('options_json')} rows={3} placeholder='{"timeout": 30}'
                  className="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm font-mono text-slate-700 focus:outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-400 transition-all" />
              </div>
              <div>
                <label className="text-sm font-medium text-slate-700 mb-2 block">用途标签</label>
                <div className="flex flex-wrap gap-2">
                  {USAGE_TAGS.map(tag => {
                    const sel = editTags.includes(tag)
                    return (
                      <button key={tag} type="button"
                        onClick={() => setEditTags(prev => sel ? prev.filter(t => t !== tag) : [...prev, tag])}
                        className={`text-xs px-3 py-1.5 rounded-full border transition-all ${sel ? 'bg-slate-800 text-white border-slate-800' : 'border-slate-200 text-slate-500 hover:border-slate-300 hover:text-slate-700'}`}>
                        {tag}
                      </button>
                    )
                  })}
                </div>
              </div>
              <div className="flex justify-end gap-3 pt-2">
                <button type="button" onClick={() => setEditTarget(null)}
                  className="px-4 py-2 border border-slate-200 rounded-lg text-sm text-slate-600 hover:bg-slate-50 transition-colors">取消</button>
                <button type="submit"
                  className="flex items-center gap-1.5 px-4 py-2 bg-slate-800 text-white rounded-lg text-sm hover:bg-slate-700 transition-colors">
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
        onSetDefault={() => {
          if (detailModel) {
            setDefault(detailModel.id)
            addToast('success', `"${detailModel.name}" 已设为默认模型`)
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
function ModelFormModal({ title, onClose, onSubmit, formTags, setFormTags, register, handleSubmit, configType, setValue }: any) {
  return (
    <div className="fixed inset-0 bg-black/40 backdrop-blur-sm flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-xl shadow-2xl p-6 w-[560px] max-h-[88vh] overflow-y-auto" onClick={e => e.stopPropagation()}>
        <h3 className="font-semibold text-slate-800 mb-5">{title}</h3>
        <form onSubmit={handleSubmit(onSubmit)} className="space-y-4">
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-slate-700 mb-1.5">名称 *</label>
              <input {...register('name', { required: true })} placeholder="如：GPT-4o 生产环境"
                className="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm text-slate-700 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-400 transition-all" />
            </div>
            <div>
              <label className="block text-sm font-medium text-slate-700 mb-1.5">配置分类 *</label>
              <select {...register('config_type', { required: true, onChange: (e: any) => setValue('provider', PROVIDERS[e.target.value]?.[0]?.value || 'custom') })}
                className="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm text-slate-700 focus:outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-400 transition-all">
                {CONFIG_TYPES.map(t => <option key={t.value} value={t.value}>{t.label}</option>)}
              </select>
            </div>
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-slate-700 mb-1.5">Provider *</label>
              <select {...register('provider', { required: true })}
                className="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm text-slate-700 focus:outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-400 transition-all">
                {(PROVIDERS[configType] || PROVIDERS.llm).map(p => <option key={p.value} value={p.value}>{p.label}</option>)}
              </select>
            </div>
            <div>
              <label className="block text-sm font-medium text-slate-700 mb-1.5">API Key</label>
              <input {...register('api_key')} type="password" placeholder="sk-..."
                className="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm text-slate-700 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-400 transition-all" />
            </div>
          </div>
          <div>
            <label className="block text-sm font-medium text-slate-700 mb-1.5">API Base</label>
            <input {...register('api_base')} placeholder="https://api.openai.com/v1"
              className="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm text-slate-700 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-400 transition-all" />
          </div>
          <div>
            <label className="block text-sm font-medium text-slate-700 mb-1.5">模型名（每行一个）</label>
            <textarea {...register('models_str')} rows={3} placeholder="gpt-4o\ngpt-4o-mini"
              className="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm font-mono text-slate-700 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-400 transition-all" />
          </div>
          {configType === 'ocr' && (
            <div className="grid grid-cols-3 gap-4">
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1.5">启用运行</label>
                <select {...register('ocr_enabled')}
                  className="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm text-slate-700 focus:outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-400 transition-all">
                  <option value="false">关闭</option><option value="true">开启</option>
                </select>
              </div>
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1.5">OCR语言</label>
                <input {...register('ocr_lang')} placeholder="ch"
                  className="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm text-slate-700 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-400 transition-all" />
              </div>
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1.5">设备</label>
                <select {...register('ocr_device')}
                  className="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm text-slate-700 focus:outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-400 transition-all">
                  <option value="cpu">CPU</option><option value="gpu">GPU</option>
                </select>
              </div>
            </div>
          )}
          <div>
            <label className="block text-sm font-medium text-slate-700 mb-1.5">高级参数 JSON</label>
            <textarea {...register('options_json')} rows={3} placeholder='{"timeout": 30}'
              className="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm font-mono text-slate-700 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-400 transition-all" />
          </div>
          <div>
            <label className="text-sm font-medium text-slate-700 mb-2 block">用途标签</label>
            <div className="flex flex-wrap gap-2">
              {[...USAGE_TAGS].map(tag => {
                const sel = formTags.includes(tag)
                return (
                  <button key={tag} type="button"
                    onClick={() => setFormTags((prev: string[]) => sel ? prev.filter((t: string) => t !== tag) : [...prev, tag])}
                    className={`text-xs px-3 py-1.5 rounded-full border transition-all ${sel ? 'bg-slate-800 text-white border-slate-800' : 'border-slate-200 text-slate-500 hover:border-slate-300 hover:text-slate-700'}`}>
                    {tag}
                  </button>
                )
              })}
            </div>
          </div>
          <div className="flex justify-end gap-3 pt-2">
            <button type="button" onClick={onClose}
              className="px-5 py-2 border border-slate-200 rounded-lg text-sm text-slate-600 hover:bg-slate-50 transition-colors">取消</button>
            <button type="submit"
              className="flex items-center gap-1.5 px-5 py-2 rounded-lg text-sm text-white bg-slate-800 hover:bg-slate-700 transition-colors">
              保存
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
