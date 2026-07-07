import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useForm } from 'react-hook-form'
import { useTranslation } from 'react-i18next'
import { modelApi } from '@/api/ontologies'
import ConfirmDialog from '@/components/ConfirmDialog'
import type { ModelConfig } from '@/types/ontology'
import { Trash2, TestTube2, Plus, Pencil, X, Loader2, CheckCircle2, XCircle, AlertCircle } from 'lucide-react'

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
  return JSON.parse(text)
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

export default function ModelsPage() {
  const { t } = useTranslation()
  const qc = useQueryClient()
  const [showCreate, setShowCreate] = useState(false)
  const [deleteTarget, setDeleteTarget] = useState<ModelConfig | null>(null)
  const [testResult, setTestResult] = useState<Record<string, string>>({})
  const [testStatus, setTestStatus] = useState<Record<string, 'idle' | 'testing' | 'success' | 'error'>>({})
  const [formTags, setFormTags] = useState<string[]>([])
  const { register, handleSubmit, reset, watch, setValue: setCreateValue } = useForm<any>({
    defaultValues: { config_type: 'llm', provider: 'openai', ocr_enabled: 'false', ocr_lang: 'ch', ocr_device: 'cpu' },
  })

  const { data: models = [], isLoading } = useQuery({
    queryKey: ['models'], queryFn: () => modelApi.list() as any,
  })

  const createMut = useMutation({
    mutationFn: (data: any) => modelApi.create(buildPayload(data, formTags)),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['models'] }); setShowCreate(false); reset(); setFormTags([]) },
  })

  const deleteMut = useMutation({
    mutationFn: (id: string) => modelApi.delete(id),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['models'] }); setDeleteTarget(null) },
  })

  const testMut = useMutation({
    mutationFn: (id: string) => modelApi.test(id),
    onMutate: (id) => {
      setTestStatus(prev => ({ ...prev, [id]: 'testing' }))
    },
    onSuccess: (res: any, id) => {
      const data = res?.data || res
      const success = data?.ok !== false
      setTestStatus(prev => ({ ...prev, [id]: success ? 'success' : 'error' }))
      setTestResult(prev => ({ ...prev, [id]: success ? '连接成功' : `未启用：${data.response || ''}` }))
      // 3秒后自动重置为 idle
      setTimeout(() => setTestStatus(prev => ({ ...prev, [id]: 'idle' })), 3000)
    },
    onError: (err: any, id) => {
      setTestStatus(prev => ({ ...prev, [id]: 'error' }))
      setTestResult(prev => ({ ...prev, [id]: `${err?.detail || '连接失败'}` }))
      setTimeout(() => setTestStatus(prev => ({ ...prev, [id]: 'idle' })), 3000)
    },
  })

  // ── 编辑 ──
  const [editTarget, setEditTarget] = useState<ModelConfig | null>(null)
  const [editTags, setEditTags] = useState<string[]>([])
  const { register: regEdit, handleSubmit: handleEditSubmit, setValue, watch: watchEdit } = useForm<any>()

  const updateMut = useMutation({
    mutationFn: ({ id, data }: { id: string; data: any }) => modelApi.update(id, buildPayload(data, editTags, 'update')),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['models'] }); setEditTarget(null); setEditTags([]) },
  })

  const openEdit = (m: ModelConfig) => {
    const options = m.options || {}
    setEditTarget(m); setEditTags((options.usage_tags as string[]) || [])
    setValue('name', m.name); setValue('config_type', m.config_type || 'llm'); setValue('provider', m.provider)
    setValue('api_key', '')
    setValue('api_base', m.api_base || '')
    setValue('models_str', (m.models || []).join('\n'))
    setValue('ocr_enabled', options.enabled ? 'true' : 'false')
    setValue('ocr_lang', String(options.lang || 'ch'))
    setValue('ocr_device', String(options.device || 'cpu'))
    setValue('options_json', JSON.stringify(
      Object.fromEntries(Object.entries(options).filter(([k]) => !['usage_tags', 'lang', 'device'].includes(k))),
      null,
      2,
    ))
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h2 className="text-xl font-semibold text-[var(--color-text-primary)]">{t('model.title')}</h2>
        <button onClick={() => { setShowCreate(true); reset({ config_type: 'llm', provider: 'openai', ocr_enabled: 'false', ocr_lang: 'ch', ocr_device: 'cpu' }); setFormTags([]) }}
          className="flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium text-white transition-colors" style={{ background: 'var(--color-primary)' }}>
          <Plus size={14} /> {t('model.create')}
        </button>
      </div>

      <div className="grid gap-4">
        {isLoading ? <p className="text-gray-400 text-sm">{t('common.loading')}</p> :
          (models as ModelConfig[]).map(m => {
            const status = testStatus[m.id] || 'idle'
            return (
            <div key={m.id} className="group bg-[var(--color-bg-elevated)] rounded-2xl border border-[var(--color-border)] p-5 transition-all duration-200 hover:border-[var(--color-border-hover)] hover:shadow-sm">
              <div className="flex items-start justify-between gap-4">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2.5 mb-1.5">
                    <h3 className="font-semibold text-[15px] text-[var(--color-text-primary)] truncate">{m.name}</h3>
                    <span className="text-[11px] px-2 py-0.5 rounded-md bg-[var(--color-bg-base)] text-[var(--color-text-tertiary)] border border-[var(--color-border)] shrink-0">
                      {typeLabel(m.config_type)}
                    </span>
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
                  </div>
                  <p className="text-[13px] text-[var(--color-text-tertiary)] mb-3">
                    {m.provider}{m.api_base ? ` · ${m.api_base}` : ''}{m.has_api_key ? ' · API Key 已保存' : ' · 未配置 API Key'}
                  </p>
                  {m.models?.length > 0 && (
                    <div className="flex gap-1.5 flex-wrap mb-2">
                      {m.models.map(mn => (
                        <span key={mn} className="text-[11px] bg-[var(--color-primary-light)] text-[var(--color-primary)] px-2 py-0.5 rounded-md font-medium">{mn}</span>
                      ))}
                    </div>
                  )}
                  {((m.options?.usage_tags as string[]) || []).length > 0 && (
                    <div className="flex gap-1.5 flex-wrap">
                      {((m.options?.usage_tags as string[]) || []).map((tag: string) => (
                        <span key={tag} className="text-[11px] bg-[var(--color-bg-base)] text-[var(--color-text-secondary)] px-2.5 py-0.5 rounded-full border border-[var(--color-border)]">{tag}</span>
                      ))}
                    </div>
                  )}
                  {testResult[m.id] && status === 'error' && (
                    <p className="text-xs mt-2 text-red-500">{testResult[m.id]}</p>
                  )}
                </div>
                <div className="flex items-center gap-1.5 shrink-0">
                  <button
                    onClick={() => testMut.mutate(m.id)}
                    disabled={status === 'testing'}
                    className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-all disabled:opacity-50 ${
                      status === 'testing' ? 'bg-blue-50 text-blue-600' :
                      status === 'success' ? 'bg-emerald-50 text-emerald-600' :
                      status === 'error' ? 'bg-red-50 text-red-600' :
                      'bg-[var(--color-bg-base)] text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)]'
                    }`}
                  >
                    {status === 'testing' ? <Loader2 size={12} className="animate-spin" /> :
                     status === 'success' ? <CheckCircle2 size={12} /> :
                     status === 'error' ? <AlertCircle size={12} /> :
                     <TestTube2 size={12} />}
                    <span className="hidden sm:inline">{status === 'testing' ? '测试中' : status === 'success' ? '已连通' : status === 'error' ? '重试' : '测试'}</span>
                  </button>
                  <button onClick={() => openEdit(m)} className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-[var(--color-bg-base)] text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)] transition-colors">
                    <Pencil size={12} /><span className="hidden sm:inline">编辑</span>
                  </button>
                  <button onClick={() => setDeleteTarget(m)} className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-[var(--color-bg-base)] text-red-500 hover:bg-red-50 transition-colors">
                    <Trash2 size={12} /><span className="hidden sm:inline">删除</span>
                  </button>
                </div>
              </div>
            </div>
          )})
        }
        {!isLoading && (models as ModelConfig[]).length === 0 && (
          <div className="bg-white border rounded-lg p-8 text-center text-gray-400">{t('model.empty')}</div>
        )}
      </div>

      {/* 新建弹窗 */}
      {showCreate && <ModelFormModal title="新建模型" onClose={() => setShowCreate(false)} onSubmit={(d: any) => createMut.mutate(d)}
        isPending={createMut.isPending} formTags={formTags} setFormTags={setFormTags} register={register}
        handleSubmit={handleSubmit} configType={watch('config_type') || 'llm'} setValue={setCreateValue} />}

      {/* 编辑弹窗 */}
      {editTarget && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4" onClick={() => setEditTarget(null)}>
          <div className="bg-white rounded-lg shadow-lg p-6 w-[520px] max-h-[88vh] overflow-y-auto" onClick={e => e.stopPropagation()}>
            <div className="flex justify-between items-center mb-4">
              <h3 className="font-semibold">编辑模型</h3>
              <button onClick={() => setEditTarget(null)} className="text-gray-400 hover:text-black"><X size={16} /></button>
            </div>
            <form onSubmit={handleEditSubmit(d => updateMut.mutate({ id: editTarget.id, data: d }))} className="space-y-3">
              <div><label className="block text-sm font-medium mb-1">名称 *</label>
                <input {...regEdit('name', { required: true })} className="w-full border rounded-lg px-3 py-2 text-sm" /></div>
              <div><label className="block text-sm font-medium mb-1">配置分类 *</label>
                <select {...regEdit('config_type', { required: true, onChange: e => setValue('provider', PROVIDERS[e.target.value]?.[0]?.value || 'custom') })} className="w-full border rounded-lg px-3 py-2 text-sm">
                  {CONFIG_TYPES.map(t => <option key={t.value} value={t.value}>{t.label}</option>)}
                </select></div>
              <div><label className="block text-sm font-medium mb-1">Provider *</label>
                <select {...regEdit('provider', { required: true })} className="w-full border rounded-lg px-3 py-2 text-sm">
                  {(PROVIDERS[watchEdit('config_type') || 'llm'] || PROVIDERS.llm).map(p => <option key={p.value} value={p.value}>{p.label}</option>)}
                </select></div>
              <div><label className="block text-sm font-medium mb-1">API Base</label>
                <input {...regEdit('api_base')} className="w-full border rounded-lg px-3 py-2 text-sm" /></div>
              <div><label className="block text-sm font-medium mb-1">API Key</label>
                <input {...regEdit('api_key')} type="password" placeholder={editTarget.has_api_key ? '已保存，留空保留原密钥' : 'sk-...'} className="w-full border rounded-lg px-3 py-2 text-sm" />
                <p className="mt-1 text-xs text-gray-400">{editTarget.has_api_key ? '如需更换密钥，请在此输入新密钥。' : '当前模型还没有保存 API Key。'}</p>
              </div>
              <div><label className="block text-sm font-medium mb-1">模型名（每行一个）</label>
                <textarea {...regEdit('models_str')} rows={3} className="w-full border rounded-lg px-3 py-2 text-sm font-mono" /></div>
              {(watchEdit('config_type') || 'llm') === 'ocr' && (
                <div className="grid grid-cols-3 gap-3">
                  <div><label className="block text-sm font-medium mb-1">启用运行</label>
                    <select {...regEdit('ocr_enabled')} className="w-full border rounded-lg px-3 py-2 text-sm">
                      <option value="false">关闭</option><option value="true">开启</option>
                    </select></div>
                  <div><label className="block text-sm font-medium mb-1">OCR语言</label>
                    <input {...regEdit('ocr_lang')} placeholder="ch" className="w-full border rounded-lg px-3 py-2 text-sm" /></div>
                  <div><label className="block text-sm font-medium mb-1">设备</label>
                    <select {...regEdit('ocr_device')} className="w-full border rounded-lg px-3 py-2 text-sm">
                      <option value="cpu">CPU</option><option value="gpu">GPU</option>
                    </select></div>
                </div>
              )}
              <div><label className="block text-sm font-medium mb-1">高级参数 JSON</label>
                <textarea {...regEdit('options_json')} rows={3} placeholder={'{\"timeout\": 30}'} className="w-full border rounded-lg px-3 py-2 text-sm font-mono" /></div>
              <div><label className="text-xs text-gray-500 mb-2 block">用途标签</label>
                <div className="flex flex-wrap gap-2">
                  {USAGE_TAGS.map(tag => {
                    const sel = editTags.includes(tag)
                    return <button key={tag} type="button" onClick={() => setEditTags(prev => sel ? prev.filter(t => t !== tag) : [...prev, tag])}
                      className={`text-xs px-3 py-1.5 rounded-full border ${sel ? 'bg-black text-white border-black' : 'border-gray-200 text-gray-600'}`}>{tag}</button>
                  })}
                </div></div>
              <div className="flex justify-end gap-3 pt-2">
                <button type="button" onClick={() => setEditTarget(null)} className="px-4 py-2 border rounded-lg text-sm">取消</button>
                <button type="submit" disabled={updateMut.isPending} className="flex items-center gap-1.5 px-4 py-2 bg-black text-white rounded-lg text-sm disabled:opacity-50">
                  {updateMut.isPending && <Loader2 size={13} className="animate-spin" />}保存
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      <ConfirmDialog open={!!deleteTarget} title={t('model.confirm_delete')} message={t('model.confirm_delete_msg', { name: deleteTarget?.name })}
        onConfirm={() => deleteTarget && deleteMut.mutate(deleteTarget.id)} onCancel={() => setDeleteTarget(null)} />
    </div>
  )
}

/** 新建模型表单弹窗 */
function ModelFormModal({ title, onClose, onSubmit, isPending, formTags, setFormTags, register, handleSubmit, configType, setValue }: any) {
  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-xl shadow-2xl p-6 w-[640px] max-h-[88vh] overflow-y-auto" onClick={e => e.stopPropagation()}>
        <h3 className="font-semibold mb-5">{title}</h3>
        <form onSubmit={handleSubmit(onSubmit)} className="space-y-4">
          <div className="grid grid-cols-2 gap-4">
            <div><label className="block text-sm font-medium mb-1.5">名称 *</label>
              <input {...register('name', { required: true })} placeholder="如：GPT-4o 生产环境" className="w-full border rounded-lg px-3 py-2 text-sm" /></div>
            <div><label className="block text-sm font-medium mb-1.5">配置分类 *</label>
              <select {...register('config_type', { required: true, onChange: (e: any) => setValue('provider', PROVIDERS[e.target.value]?.[0]?.value || 'custom') })} className="w-full border rounded-lg px-3 py-2 text-sm">
                {CONFIG_TYPES.map(t => <option key={t.value} value={t.value}>{t.label}</option>)}
              </select></div>
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div><label className="block text-sm font-medium mb-1.5">Provider *</label>
              <select {...register('provider', { required: true })} className="w-full border rounded-lg px-3 py-2 text-sm">
                {(PROVIDERS[configType] || PROVIDERS.llm).map(p => <option key={p.value} value={p.value}>{p.label}</option>)}
              </select></div>
            <div><label className="block text-sm font-medium mb-1.5">API Key</label>
              <input {...register('api_key')} type="password" placeholder="sk-..." className="w-full border rounded-lg px-3 py-2 text-sm" /></div>
          </div>
          <div><label className="block text-sm font-medium mb-1.5">API Base</label>
            <input {...register('api_base')} placeholder="https://api.openai.com/v1" className="w-full border rounded-lg px-3 py-2 text-sm" /></div>
          <div><label className="block text-sm font-medium mb-1.5">模型名（每行一个）</label>
            <textarea {...register('models_str')} rows={3} placeholder="gpt-4o&#10;gpt-4o-mini" className="w-full border rounded-lg px-3 py-2 text-sm font-mono" /></div>
          {configType === 'ocr' && (
            <div className="grid grid-cols-3 gap-4">
              <div><label className="block text-sm font-medium mb-1.5">启用运行</label>
                <select {...register('ocr_enabled')} className="w-full border rounded-lg px-3 py-2 text-sm">
                  <option value="false">关闭</option><option value="true">开启</option>
                </select></div>
              <div><label className="block text-sm font-medium mb-1.5">OCR语言</label>
                <input {...register('ocr_lang')} placeholder="ch" className="w-full border rounded-lg px-3 py-2 text-sm" /></div>
              <div><label className="block text-sm font-medium mb-1.5">设备</label>
                <select {...register('ocr_device')} className="w-full border rounded-lg px-3 py-2 text-sm">
                  <option value="cpu">CPU</option><option value="gpu">GPU</option>
                </select></div>
            </div>
          )}
          <div><label className="block text-sm font-medium mb-1.5">高级参数 JSON</label>
            <textarea {...register('options_json')} rows={3} placeholder={'{\"timeout\": 30}'} className="w-full border rounded-lg px-3 py-2 text-sm font-mono" /></div>
          <div><label className="text-sm font-medium mb-2 block">用途标签</label>
            <div className="flex flex-wrap gap-2">{[...USAGE_TAGS].map(tag => {
              const sel = formTags.includes(tag)
              return <button key={tag} type="button" onClick={() => setFormTags((prev: string[]) => sel ? prev.filter((t: string) => t !== tag) : [...prev, tag])}
                className={`text-xs px-3 py-1.5 rounded-full border transition-colors ${sel ? 'border-[var(--color-nav-bg)] text-[var(--color-nav-bg)] bg-[var(--color-nav-light)]' : 'border-gray-200 text-gray-500 hover:text-gray-700 hover:border-gray-300'}`}>{tag}</button>
            })}</div></div>
          <div className="flex justify-center gap-3 pt-2">
            <button type="button" onClick={onClose} className="px-5 py-2 border rounded-lg text-sm text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)] transition-colors">取消</button>
            <button type="submit" disabled={isPending} className="flex items-center gap-1.5 px-5 py-2 rounded-lg text-sm text-white bg-black disabled:opacity-50 transition-colors">
              {isPending && <Loader2 size={13} className="animate-spin" />}保存
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
