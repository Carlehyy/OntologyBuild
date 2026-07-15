import { useEffect, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { ontologyApi, modelApi } from '@/api/ontologies'
import {
  agentApi,
  type AnalysisReportRun,
  type AnalysisReportTemplate,
  type ReportQuality,
  type ReportQuery,
  type ReportSection,
  type ReportVisualization,
} from '@/api/agent'
import { LoadingState } from '@/components/ui/LoadingState'

type OntologyOption = { id: string; name: string }

const VISUALIZATIONS: { value: ReportVisualization; label: string }[] = [
  { value: 'auto', label: '智能选择' },
  { value: 'kpi', label: '关键指标' },
  { value: 'bar', label: '柱状图' },
  { value: 'line', label: '趋势折线' },
  { value: 'pie', label: '占比图' },
  { value: 'table', label: '数据表' },
  { value: 'none', label: '仅文字' },
]

const inputClass = 'w-full rounded-xl bg-white/85 px-3.5 py-2.5 text-sm text-slate-800 shadow-[inset_0_0_0_1px_rgba(71,85,105,0.16)] outline-none transition-[box-shadow,background] duration-300 ease-[cubic-bezier(0.32,0.72,0,1)] focus:bg-white focus:shadow-[inset_0_0_0_1.5px_rgba(13,148,136,0.72),0_0_0_4px_rgba(13,148,136,0.10)] disabled:cursor-not-allowed disabled:bg-slate-100/80 disabled:text-slate-500'
const secondaryButton = 'inline-flex min-h-10 items-center justify-center rounded-full bg-white px-4 text-xs font-semibold text-slate-600 shadow-[inset_0_0_0_1px_rgba(71,85,105,0.14),0_8px_24px_rgba(15,23,42,0.05)] transition-[transform,box-shadow,color] duration-300 ease-[cubic-bezier(0.32,0.72,0,1)] hover:-translate-y-0.5 hover:text-teal-700 hover:shadow-[inset_0_0_0_1px_rgba(13,148,136,0.22),0_12px_30px_rgba(15,118,110,0.10)] active:translate-y-0 disabled:cursor-not-allowed disabled:opacity-45'
const primaryButton = 'inline-flex min-h-11 items-center justify-center rounded-full bg-slate-900 px-5 text-xs font-semibold text-white shadow-[0_12px_30px_rgba(15,23,42,0.18)] transition-[transform,box-shadow,background] duration-300 ease-[cubic-bezier(0.32,0.72,0,1)] hover:-translate-y-0.5 hover:bg-teal-800 hover:shadow-[0_16px_36px_rgba(15,118,110,0.22)] active:translate-y-0 disabled:cursor-not-allowed disabled:opacity-40'

function errorText(error: unknown, fallback: string): string {
  if (!error || typeof error !== 'object') return fallback
  const candidate = error as { detail?: unknown; message?: unknown }
  if (typeof candidate.detail === 'string') return candidate.detail
  if (candidate.detail && typeof candidate.detail === 'object') {
    const detail = candidate.detail as { message?: unknown }
    if (typeof detail.message === 'string') return detail.message
  }
  return typeof candidate.message === 'string' ? candidate.message : fallback
}

function formatTime(value?: string | null): string {
  if (!value) return '—'
  return new Date(value).toLocaleString('zh-CN', { hour12: false })
}

function statusLabel(status: AnalysisReportTemplate['status']): string {
  return status === 'published' ? '已发布' : '草稿'
}

function StepRail({ template, dirty = false }: { template?: AnalysisReportTemplate | null; dirty?: boolean }) {
  const previewCurrent = Boolean(!dirty && template?.lastPreviewRunId && template.lastPreviewRevision === template.revision)
  const steps = [
    { label: 'AI 草拟', done: Boolean(template) },
    { label: '人工编辑', done: Boolean(template) },
    { label: '真实数据试运行', done: previewCurrent || template?.status === 'published' },
    { label: '正式发布', done: template?.status === 'published' },
  ]
  return (
    <ol className="grid grid-cols-4 gap-2" aria-label="报告模板发布流程">
      {steps.map((step, index) => (
        <li key={step.label} className={`relative rounded-2xl px-3 py-2.5 ${step.done ? 'bg-teal-50 text-teal-800' : 'bg-slate-100/70 text-slate-400'}`}>
          <div className="text-[10px] font-semibold tracking-[0.15em]">0{index + 1}</div>
          <div className="mt-0.5 text-[11px] font-medium">{step.label}</div>
          <span className={`absolute right-3 top-3 h-2 w-2 rounded-full ${step.done ? 'bg-teal-500' : 'bg-slate-300'}`} aria-hidden="true" />
        </li>
      ))}
    </ol>
  )
}

function QualityPanel({ quality }: { quality: ReportQuality }) {
  return (
    <section className={`rounded-2xl p-4 ${quality.passed ? 'bg-emerald-50/90 text-emerald-950' : 'bg-amber-50/90 text-amber-950'}`} aria-label="报告质量检查">
      <div className="flex items-end justify-between gap-3">
        <div>
          <div className="text-[10px] font-semibold uppercase tracking-[0.16em] opacity-60">Quality gate</div>
          <h3 className="mt-1 text-sm font-semibold">{quality.summary}</h3>
        </div>
        <div className="font-mono text-3xl font-semibold tabular-nums">{quality.score}<span className="text-xs opacity-50">/100</span></div>
      </div>
      <div className="mt-3 grid grid-cols-2 gap-2">
        {quality.checks.map(check => (
          <div key={check.key} className="rounded-xl bg-white/55 px-3 py-2">
            <div className="flex items-center gap-2 text-[11px] font-semibold">
              <span className={`h-1.5 w-1.5 rounded-full ${check.passed ? 'bg-emerald-500' : 'bg-rose-500'}`} />
              {check.label}
            </div>
            <p className="mt-1 text-[10px] leading-relaxed opacity-70">{check.detail}</p>
          </div>
        ))}
      </div>
      {quality.blockers.length > 0 && (
        <div className="mt-3 rounded-xl bg-white/60 px-3 py-2">
          <div className="text-[10px] font-semibold uppercase tracking-[0.12em] text-rose-700">发布阻断</div>
          <ul className="mt-1 space-y-1 text-[11px] leading-relaxed">
            {quality.blockers.map((item, index) => <li key={index}>· {item}</li>)}
          </ul>
        </div>
      )}
      {quality.warnings.length > 0 && (
        <details className="mt-2 text-[11px]"><summary className="cursor-pointer font-medium">查看 {quality.warnings.length} 项质量提醒</summary>
          <ul className="mt-1 space-y-1 opacity-75">{quality.warnings.map((item, index) => <li key={index}>· {item}</li>)}</ul>
        </details>
      )}
    </section>
  )
}

function NewReportView({ ontologies, initialOntologyId, conversationId }: {
  ontologies: OntologyOption[]
  initialOntologyId: string
  conversationId: string
}) {
  const navigate = useNavigate()
  const [oid, setOid] = useState(initialOntologyId || ontologies[0]?.id || '')
  const [brief, setBrief] = useState('')
  const [modelId, setModelId] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const touched = useRef(false)
  const { data: models = [] } = useQuery({ queryKey: ['models'], queryFn: () => modelApi.list() as any })
  const llmModels = Array.isArray(models) ? (models as any[]).filter(item => item.config_type === 'llm' || !item.config_type) : []
  const { data: conversation } = useQuery({
    queryKey: ['agent-conversation-report-context', oid, conversationId],
    queryFn: () => agentApi.conversation(oid, conversationId),
    enabled: Boolean(oid && conversationId),
  })

  useEffect(() => {
    if (!modelId && llmModels[0]?.id) setModelId(llmModels[0].id)
  }, [llmModels, modelId])
  useEffect(() => {
    if (touched.current || !conversation?.messages?.length) return
    const questions = conversation.messages.filter(item => item.role === 'user').map(item => item.content).slice(-6)
    if (questions.length) setBrief(`基于当前智能助手会话形成一份管理层数据分析报告，重点回答：\n${questions.map(item => `- ${item}`).join('\n')}`)
  }, [conversation])

  const submit = async (event: React.FormEvent) => {
    event.preventDefault()
    if (!oid || brief.trim().length < 8 || busy) return
    setBusy(true); setError('')
    try {
      const created = await agentApi.createReportTemplate(oid, {
        brief: brief.trim(), modelId: modelId || undefined, conversationId: conversationId || undefined,
      })
      navigate(`/agent/reports/${created.id}?ontologyId=${encodeURIComponent(oid)}`, { replace: true })
    } catch (cause) {
      setError(errorText(cause, '报告模板生成失败，请检查模型配置后重试'))
    } finally { setBusy(false) }
  }

  return (
    <div className="mx-auto flex min-h-full w-full max-w-6xl items-center px-6 py-12">
      <div className="grid w-full overflow-hidden rounded-[2rem] bg-slate-200/45 p-1.5 shadow-[0_26px_90px_rgba(15,23,42,0.10)] lg:grid-cols-[0.85fr_1.15fr]">
        <section className="relative min-h-[580px] overflow-hidden rounded-[calc(2rem-0.375rem)] bg-[#16332e] px-10 py-12 text-white">
          <div className="absolute -right-24 -top-24 h-72 w-72 rounded-full bg-teal-300/10" />
          <div className="relative">
            <button onClick={() => navigate(`/agent${oid ? `?ontologyId=${encodeURIComponent(oid)}` : ''}`)} className="text-xs text-white/60 transition-colors hover:text-white">返回智能助手</button>
            <div className="mt-24 text-[10px] font-semibold uppercase tracking-[0.2em] text-teal-200">Assistant capability</div>
            <h1 className="mt-5 max-w-md font-serif text-5xl leading-[1.08] tracking-[-0.035em]">把一次分析意图，变成可复用的汇报资产。</h1>
            <p className="mt-7 max-w-md text-sm leading-7 text-white/65">AI 负责起草，用户保留编辑权。模板必须经过真实数据试运行和质量检查，才能正式发布。</p>
            <div className="mt-16 grid grid-cols-2 gap-3 text-xs">
              {['可编辑模板', '真实数据确认', '查询口径留痕', '汇报级 HTML'].map((item, index) => (
                <div key={item} className="rounded-2xl bg-white/[0.055] px-4 py-3 shadow-[inset_0_0_0_1px_rgba(255,255,255,0.08)]">
                  <span className="font-mono text-[10px] text-teal-200">0{index + 1}</span><div className="mt-1 text-white/80">{item}</div>
                </div>
              ))}
            </div>
          </div>
        </section>
        <form onSubmit={submit} className="rounded-[calc(2rem-0.375rem)] bg-[#fbfaf6] px-8 py-10 lg:px-12">
          <div className="text-[10px] font-semibold uppercase tracking-[0.2em] text-teal-700">New report template</div>
          <h2 className="mt-3 font-serif text-3xl text-slate-900">告诉 AI，这份报告要解决什么问题</h2>
          <p className="mt-2 text-xs leading-6 text-slate-500">建议写清汇报对象、关注指标、时间范围和希望重点解释的问题。</p>
          <div className="mt-8 space-y-5">
            <label className="block"><span className="mb-2 block text-xs font-semibold text-slate-700">数据本体</span>
              <select value={oid} onChange={event => setOid(event.target.value)} className={inputClass} required>
                <option value="">选择本体</option>{ontologies.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
              </select></label>
            <label className="block"><span className="mb-2 block text-xs font-semibold text-slate-700">报告目标</span>
              <textarea value={brief} onChange={event => { touched.current = true; setBrief(event.target.value) }} rows={9} className={`${inputClass} resize-none leading-6`} placeholder="例如：给供应链负责人生成月度运营报告，重点说明订单规模、履约状态分布、异常订单及供应商集中度。" required />
              <span className="mt-1.5 block text-[10px] text-slate-400">至少 8 个字；AI 只会使用当前本体授权范围内的对象和字段。</span></label>
            <label className="block"><span className="mb-2 block text-xs font-semibold text-slate-700">辅助模型</span>
              <select value={modelId} onChange={event => setModelId(event.target.value)} className={inputClass}>
                {llmModels.length === 0 && <option value="">使用确定性安全模板</option>}
                {llmModels.map((item: any) => <option key={item.id} value={item.id}>{item.name}</option>)}
              </select></label>
          </div>
          {error && <div role="alert" className="mt-5 rounded-xl bg-rose-50 px-4 py-3 text-xs text-rose-700">{error}</div>}
          <button type="submit" disabled={!oid || brief.trim().length < 8 || busy} className={`${primaryButton} mt-7 w-full`}>
            {busy ? 'AI 正在设计模板…' : '生成可编辑报告模板'}
          </button>
        </form>
      </div>
    </div>
  )
}

function ReportLibrary({ ontologies, initialOntologyId }: { ontologies: OntologyOption[]; initialOntologyId: string }) {
  const navigate = useNavigate()
  const [oid, setOid] = useState(initialOntologyId || ontologies[0]?.id || '')
  const { data: templates = [], isLoading } = useQuery({
    queryKey: ['analysis-report-templates', oid], queryFn: () => agentApi.reportTemplates(oid), enabled: Boolean(oid),
  })
  const ontology = ontologies.find(item => item.id === oid)
  return (
    <div className="min-h-full bg-[#f5f4ef] px-6 py-8">
      <div className="mx-auto max-w-7xl">
        <header className="flex flex-wrap items-end justify-between gap-5 rounded-[2rem] bg-white/75 px-8 py-7 shadow-[inset_0_0_0_1px_rgba(71,85,105,0.07),0_20px_70px_rgba(15,23,42,0.06)]">
          <div><button onClick={() => navigate('/agent')} className="text-xs text-slate-400 hover:text-teal-700">返回智能助手</button>
            <div className="mt-5 text-[10px] font-semibold uppercase tracking-[0.2em] text-teal-700">Report assets</div>
            <h1 className="mt-2 font-serif text-4xl tracking-[-0.03em] text-slate-900">分析报告工作台</h1>
            <p className="mt-2 max-w-xl text-xs leading-6 text-slate-500">管理 AI 草稿、真实数据试运行、发布模板和历史报告输出。</p></div>
          <div className="flex items-center gap-3"><select value={oid} onChange={event => setOid(event.target.value)} className={`${inputClass} min-w-52`}>
            {ontologies.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select>
            <button onClick={() => navigate(`/agent/reports/new?ontologyId=${encodeURIComponent(oid)}`)} disabled={!oid} className={primaryButton}>AI 生成新模板</button></div>
        </header>
        <section className="mt-7">
          <div className="mb-4 flex items-center justify-between"><h2 className="text-sm font-semibold text-slate-800">{ontology?.name || '当前本体'} · 报告模板</h2><span className="text-xs text-slate-400">{templates.length} 个资产</span></div>
          {isLoading ? <LoadingState message="加载报告模板…" /> : templates.length === 0 ? (
            <div className="rounded-[2rem] bg-white/70 px-8 py-20 text-center shadow-[inset_0_0_0_1px_rgba(71,85,105,0.07)]">
              <div className="mx-auto h-12 w-px bg-gradient-to-b from-transparent via-teal-500 to-transparent" />
              <h3 className="mt-5 font-serif text-2xl text-slate-800">还没有分析报告模板</h3>
              <p className="mx-auto mt-2 max-w-md text-xs leading-6 text-slate-500">从一个真实业务问题开始，让 AI 基于本体结构起草，再由你编辑和试运行确认。</p>
              <button onClick={() => navigate(`/agent/reports/new?ontologyId=${encodeURIComponent(oid)}`)} className={`${primaryButton} mt-6`}>创建第一份模板</button>
            </div>
          ) : (
            <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
              {templates.map(template => {
                const previewCurrent = template.lastPreviewRevision === template.revision && template.lastPreviewRunId
                return <button key={template.id} onClick={() => navigate(`/agent/reports/${template.id}?ontologyId=${encodeURIComponent(oid)}`)} className="group rounded-[1.7rem] bg-slate-200/40 p-1.5 text-left transition-transform duration-500 ease-[cubic-bezier(0.32,0.72,0,1)] hover:-translate-y-1">
                  <article className="h-full rounded-[calc(1.7rem-0.375rem)] bg-white px-6 py-5 shadow-[0_14px_45px_rgba(15,23,42,0.055),inset_0_0_0_1px_rgba(71,85,105,0.06)]">
                    <div className="flex items-center justify-between"><span className={`rounded-full px-2.5 py-1 text-[10px] font-semibold ${template.status === 'published' ? 'bg-emerald-50 text-emerald-700' : 'bg-amber-50 text-amber-700'}`}>{statusLabel(template.status)}</span><span className="font-mono text-[10px] text-slate-400">R{template.revision}</span></div>
                    <h3 className="mt-6 font-serif text-xl text-slate-900 transition-colors group-hover:text-teal-800">{template.name}</h3>
                    <p className="mt-2 line-clamp-2 text-xs leading-5 text-slate-500">{template.description || '暂无说明'}</p>
                    <div className="mt-7 flex items-center justify-between border-t border-slate-100 pt-4 text-[10px] text-slate-400"><span>{template.sections.length} 个章节</span><span>{previewCurrent ? '真实数据已确认' : '等待试运行'}</span></div>
                  </article></button>
              })}
            </div>
          )}
        </section>
      </div>
    </div>
  )
}

function ReportEditor({ remote, ontologyName }: { remote: AnalysisReportTemplate; ontologyName: string }) {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [template, setTemplate] = useState(remote)
  const [queryTexts, setQueryTexts] = useState<Record<string, string>>({})
  const [currentRun, setCurrentRun] = useState<AnalysisReportRun | null>(null)
  const [modelId, setModelId] = useState(remote.defaultModelId || '')
  const [saving, setSaving] = useState(false)
  const [running, setRunning] = useState(false)
  const [publishing, setPublishing] = useState(false)
  const [dirty, setDirty] = useState(false)
  const [error, setError] = useState('')
  const { data: models = [] } = useQuery({ queryKey: ['models'], queryFn: () => modelApi.list() as any })
  const llmModels = Array.isArray(models) ? (models as any[]).filter(item => item.config_type === 'llm' || !item.config_type) : []
  const { data: runs = [], refetch: refetchRuns } = useQuery({
    queryKey: ['analysis-report-runs', template.ontologyId, template.id],
    queryFn: () => agentApi.reportRuns(template.ontologyId, template.id),
  })

  useEffect(() => {
    setTemplate(remote)
    setModelId(remote.defaultModelId || '')
    setQueryTexts(Object.fromEntries(remote.sections.map(section => [section.id, JSON.stringify(section.queryPlan, null, 2)])))
    setDirty(false)
  }, [remote.id, remote.revision])
  useEffect(() => {
    if (currentRun || !runs[0]) return
    let cancelled = false
    void agentApi.reportRun(template.ontologyId, runs[0].id).then(run => {
      if (!cancelled) setCurrentRun(run)
    }).catch(cause => {
      if (!cancelled) setError(errorText(cause, '历史报告加载失败'))
    })
    return () => { cancelled = true }
  }, [runs, currentRun, template.ontologyId])

  const readonly = template.status === 'published'
  const previewCurrent = Boolean(!dirty && template.lastPreviewRunId && template.lastPreviewRevision === template.revision)

  const parseSections = (): ReportSection[] => template.sections.map(section => {
    const text = queryTexts[section.id]
    if (!text) return section
    let plan: unknown
    try { plan = JSON.parse(text) } catch { throw new Error(`章节「${section.title}」的数据查询配置不是有效 JSON`) }
    if (!Array.isArray(plan) || plan.length === 0) throw new Error(`章节「${section.title}」至少需要一个数据查询`)
    return { ...section, queryPlan: plan as ReportQuery[] }
  })

  const save = async (): Promise<AnalysisReportTemplate> => {
    if (readonly) return template
    setSaving(true); setError('')
    try {
      const saved = await agentApi.updateReportTemplate(template.ontologyId, template.id, {
        expectedRevision: template.revision,
        name: template.name, description: template.description, sections: parseSections(),
        style: template.style, defaultModelId: modelId || null,
      })
      setTemplate(saved)
      setQueryTexts(Object.fromEntries(saved.sections.map(section => [section.id, JSON.stringify(section.queryPlan, null, 2)])))
      setDirty(false)
      queryClient.setQueryData(['analysis-report-template', template.ontologyId, template.id], saved)
      return saved
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : errorText(cause, '模板保存失败')
      setError(message); throw cause
    } finally { setSaving(false) }
  }

  const execute = async () => {
    if (running) return
    setRunning(true); setError('')
    try {
      const target = readonly ? template : await save()
      const run = readonly
        ? await agentApi.runReportTemplate(target.ontologyId, target.id, modelId || null)
        : await agentApi.previewReportTemplate(target.ontologyId, target.id, modelId || null)
      setCurrentRun(run)
      const refreshed = await agentApi.reportTemplate(target.ontologyId, target.id)
      setTemplate(refreshed)
      setDirty(false)
      queryClient.setQueryData(['analysis-report-template', target.ontologyId, target.id], refreshed)
      await refetchRuns()
    } catch (cause) { setError(errorText(cause, '真实数据运行失败，请检查查询配置后重试')) }
    finally { setRunning(false) }
  }

  const publish = async () => {
    setPublishing(true); setError('')
    try {
      const published = await agentApi.publishReportTemplate(template.ontologyId, template.id)
      setTemplate(published)
      queryClient.setQueryData(['analysis-report-template', template.ontologyId, template.id], published)
    } catch (cause) { setError(errorText(cause, '发布失败，请先完成真实数据试运行并处理质量问题')) }
    finally { setPublishing(false) }
  }

  const updateSection = (id: string, patch: Partial<ReportSection>) => {
    setTemplate(prev => ({
      ...prev, sections: prev.sections.map(section => section.id === id ? { ...section, ...patch } : section),
    }))
    setDirty(true)
  }
  const removeSection = (id: string) => {
    if (template.sections.length <= 1) { setError('报告至少需要一个章节'); return }
    setTemplate(prev => ({ ...prev, sections: prev.sections.filter(section => section.id !== id) }))
    setQueryTexts(prev => { const next = { ...prev }; delete next[id]; return next })
    setDirty(true)
  }
  const addSection = () => {
    const sourceQuery = template.sections[0]?.queryPlan?.[0] || { tool: 'aggregate_objects', arguments: {} }
    const id = `section-${Date.now()}`
    const section: ReportSection = {
      id, title: '新的分析章节', goal: '说明本章节要回答的业务问题、需要比较的指标和预期结论。',
      visualization: 'auto', queryPlan: [sourceQuery],
    }
    setTemplate(prev => ({ ...prev, sections: [...prev.sections, section] }))
    setQueryTexts(prev => ({ ...prev, [id]: JSON.stringify(section.queryPlan, null, 2) }))
    setDirty(true)
  }
  const download = () => {
    if (!currentRun?.htmlContent) return
    const blob = new Blob([currentRun.htmlContent], { type: 'text/html;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a'); anchor.href = url; anchor.download = `${template.name}.html`; anchor.click()
    URL.revokeObjectURL(url)
  }
  const loadRun = async (runId: string) => {
    setError('')
    try { setCurrentRun(await agentApi.reportRun(template.ontologyId, runId)) }
    catch (cause) { setError(errorText(cause, '历史报告加载失败')) }
  }

  return (
    <div className="flex h-full min-h-0 flex-col bg-[#f3f2ed]">
      <header className="shrink-0 border-b border-slate-200/70 bg-white/80 px-5 py-3">
        <div className="flex items-center justify-between gap-5">
          <div className="min-w-0"><button onClick={() => navigate(`/agent/reports?ontologyId=${encodeURIComponent(template.ontologyId)}`)} className="text-[11px] text-slate-400 hover:text-teal-700">报告工作台</button>
            <div className="mt-1 flex items-center gap-2"><h1 className="truncate font-serif text-xl text-slate-900">{template.name}</h1><span className={`rounded-full px-2 py-0.5 text-[10px] font-semibold ${readonly ? 'bg-emerald-50 text-emerald-700' : 'bg-amber-50 text-amber-700'}`}>{statusLabel(template.status)} · R{template.revision}</span><span className="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] text-slate-500">{template.generationMode === 'ai' ? 'AI 草拟' : '规则草拟'}</span></div>
            <p className="mt-0.5 text-[10px] text-slate-400">{ontologyName} · 模板修改后必须重新取数确认</p></div>
          <div className="flex shrink-0 items-center gap-2">
            <select value={modelId} onChange={event => { setModelId(event.target.value); if (!readonly) setDirty(true) }} className={`${inputClass} w-44 py-2 text-xs`} disabled={running}>
              {llmModels.length === 0 && <option value="">确定性叙述</option>}{llmModels.map((item: any) => <option key={item.id} value={item.id}>{item.name}</option>)}
            </select>
            {!readonly && <button onClick={() => void save()} disabled={saving || running} className={secondaryButton}>{saving ? '保存中…' : '保存草稿'}</button>}
            <button onClick={() => void execute()} disabled={running || saving} className={primaryButton}>{running ? '正在查询真实数据…' : readonly ? '重新生成报告' : '真实数据试运行'}</button>
            {!readonly && <button onClick={() => void publish()} disabled={dirty || !previewCurrent || !currentRun?.qualityReport?.passed || publishing || running} title={dirty ? '请先保存并重新完成真实数据试运行' : undefined} className="inline-flex min-h-11 items-center rounded-full bg-teal-700 px-5 text-xs font-semibold text-white transition-[transform,background] duration-300 ease-[cubic-bezier(0.32,0.72,0,1)] hover:-translate-y-0.5 hover:bg-teal-800 disabled:cursor-not-allowed disabled:opacity-35">{publishing ? '发布中…' : '发布正式模板'}</button>}
            <button onClick={download} disabled={!currentRun?.htmlContent} className={secondaryButton}>下载 HTML</button>
          </div>
        </div>
      </header>

      <div className="grid min-h-0 flex-1 grid-cols-1 gap-1.5 overflow-hidden bg-slate-200/50 p-1.5 xl:grid-cols-[minmax(430px,0.42fr)_minmax(620px,0.58fr)]">
        <aside className="scrollbar-thin min-h-0 overflow-y-auto rounded-[1.5rem] bg-[#fbfaf6] px-5 py-5 shadow-[inset_0_0_0_1px_rgba(71,85,105,0.07)]">
          <StepRail template={template} dirty={dirty} />
          {error && <div role="alert" className="mt-4 rounded-xl bg-rose-50 px-3 py-2.5 text-xs leading-5 text-rose-700">{error}</div>}
          {dirty && !readonly && <div className="mt-4 rounded-xl bg-sky-50 px-3 py-2.5 text-xs leading-5 text-sky-800">存在未保存修改。保存后需重新执行真实数据试运行，才可发布。</div>}
          {!readonly ? <div className="mt-5 space-y-4">
            <label className="block"><span className="mb-1.5 block text-[11px] font-semibold text-slate-700">报告名称</span><input value={template.name} onChange={event => { setTemplate(prev => ({ ...prev, name: event.target.value })); setDirty(true) }} className={inputClass} /></label>
            <label className="block"><span className="mb-1.5 block text-[11px] font-semibold text-slate-700">用途与说明</span><textarea value={template.description} onChange={event => { setTemplate(prev => ({ ...prev, description: event.target.value })); setDirty(true) }} rows={3} className={`${inputClass} resize-none`} /></label>
          </div> : <div className="mt-5 rounded-2xl bg-emerald-50/75 p-4 text-xs leading-6 text-emerald-900">已发布模板被冻结为正式版本。正式运行会复用通过真实数据验证的查询计划，保证自动输出口径稳定。</div>}

          <div className="mt-6 flex items-center justify-between"><div><h2 className="text-xs font-semibold text-slate-800">报告章节</h2><p className="mt-0.5 text-[10px] text-slate-400">编辑业务问题、图表与确定性查询计划</p></div>{!readonly && <button onClick={addSection} className={secondaryButton}>添加章节</button>}</div>
          <div className="mt-3 space-y-3">
            {template.sections.map((section, index) => (
              <section key={section.id} className="rounded-[1.4rem] bg-slate-200/45 p-1">
                <div className="rounded-[calc(1.4rem-0.25rem)] bg-white px-4 py-4 shadow-[inset_0_0_0_1px_rgba(71,85,105,0.055)]">
                  <div className="flex items-center justify-between"><span className="font-mono text-[10px] font-semibold text-teal-700">CHAPTER {String(index + 1).padStart(2, '0')}</span>{!readonly && <button onClick={() => removeSection(section.id)} className="text-[10px] text-slate-400 hover:text-rose-600">移除</button>}</div>
                  <div className="mt-3 space-y-3"><input value={section.title} onChange={event => updateSection(section.id, { title: event.target.value })} className={`${inputClass} font-semibold`} disabled={readonly} />
                    <textarea value={section.goal} onChange={event => updateSection(section.id, { goal: event.target.value })} rows={3} className={`${inputClass} resize-none text-xs leading-5`} disabled={readonly} />
                    <label className="block"><span className="mb-1 block text-[10px] font-semibold text-slate-500">呈现方式</span><select value={section.visualization} onChange={event => updateSection(section.id, { visualization: event.target.value as ReportVisualization })} className={`${inputClass} py-2 text-xs`} disabled={readonly}>{VISUALIZATIONS.map(item => <option key={item.value} value={item.value}>{item.label}</option>)}</select></label>
                    <details className="rounded-xl bg-slate-50/80 px-3 py-2" open={index === 0}><summary className="cursor-pointer text-[10px] font-semibold text-slate-500">数据查询配置</summary>
                      <p className="mt-2 text-[10px] leading-4 text-slate-400">只允许 aggregate_objects 与 search_objects。字段修改后应立即重新试运行核对。</p>
                      <textarea value={queryTexts[section.id] ?? JSON.stringify(section.queryPlan, null, 2)} onChange={event => { setQueryTexts(prev => ({ ...prev, [section.id]: event.target.value })); setDirty(true) }} rows={8} className={`${inputClass} mt-2 resize-y font-mono text-[11px] leading-5`} spellCheck={false} disabled={readonly} aria-label={`${section.title}数据查询配置`} />
                    </details></div>
                </div>
              </section>
            ))}
          </div>
          {currentRun?.qualityReport && <div className="mt-5"><QualityPanel quality={currentRun.qualityReport} /></div>}
          {runs.length > 0 && <details className="mt-5 rounded-2xl bg-white/70 px-4 py-3"><summary className="cursor-pointer text-xs font-semibold text-slate-700">运行记录 · {runs.length}</summary><div className="mt-2 space-y-1.5">{runs.slice(0, 8).map(run => <button key={run.id} onClick={() => void loadRun(run.id)} className="flex w-full items-center justify-between rounded-xl px-3 py-2 text-left text-[10px] text-slate-500 hover:bg-slate-50"><span>{run.triggerType === 'preview' ? '试运行' : '正式运行'} · {formatTime(run.startedAt)}</span><span className={run.qualityReport?.passed ? 'text-emerald-600' : 'text-amber-600'}>{run.qualityReport?.score ?? 0} 分</span></button>)}</div></details>}
        </aside>

        <main className="flex min-h-0 flex-col overflow-hidden rounded-[1.5rem] bg-slate-700/15 p-1 shadow-[inset_0_0_0_1px_rgba(71,85,105,0.08)]">
          <div className="flex shrink-0 items-center justify-between rounded-t-[calc(1.5rem-0.25rem)] bg-white/90 px-5 py-3"><div><div className="text-xs font-semibold text-slate-800">HTML 报告预览</div><p className="mt-0.5 text-[10px] text-slate-400">真实数据快照 · 汇报/打印双重布局</p></div>{currentRun && <span className="rounded-full bg-slate-100 px-3 py-1 text-[10px] text-slate-500">{formatTime(currentRun.startedAt)}</span>}</div>
          <div className="min-h-0 flex-1 overflow-auto rounded-b-[calc(1.5rem-0.25rem)] bg-[#d9d7d0] p-4">
            {running ? <div className="flex min-h-full items-center justify-center"><div className="rounded-[2rem] bg-white/90 px-10 py-9 text-center shadow-[0_30px_90px_rgba(15,23,42,0.15)]"><div className="mx-auto h-10 w-10 animate-spin rounded-full border-2 border-teal-100 border-t-teal-700" /><h3 className="mt-5 font-serif text-xl text-slate-800">正在查询真实数据并排版</h3><p className="mt-2 text-xs text-slate-500">系统会逐章校验数据、生成结论并执行发布质量检查。</p></div></div> : currentRun?.htmlContent ? (
              <iframe title={`${template.name}报告预览`} srcDoc={currentRun.htmlContent} sandbox="" className="mx-auto block min-h-[900px] w-full max-w-[1180px] rounded-xl bg-white shadow-[0_25px_80px_rgba(15,23,42,0.20)]" data-testid="analysis-report-preview" />
            ) : <div className="flex min-h-full items-center justify-center text-center"><div className="max-w-sm"><div className="mx-auto h-16 w-px bg-gradient-to-b from-transparent via-teal-600 to-transparent" /><h3 className="mt-5 font-serif text-2xl text-slate-700">等待真实数据试运行</h3><p className="mt-2 text-xs leading-6 text-slate-500">保存模板后点击“真实数据试运行”。只有当前修订版通过质量门，才允许发布。</p></div></div>}
          </div>
        </main>
      </div>
    </div>
  )
}

export default function ReportStudioPage() {
  const { templateId } = useParams<{ templateId?: string }>()
  const [searchParams] = useSearchParams()
  const initialOntologyId = searchParams.get('ontologyId') || ''
  const conversationId = searchParams.get('conversationId') || ''
  const { data: ontologies = [], isLoading: ontologiesLoading } = useQuery({
    queryKey: ['ontologies'], queryFn: () => ontologyApi.list() as any,
  })
  const ontologyList: OntologyOption[] = (ontologies as any)?.items || ontologies || []
  const oid = initialOntologyId
  const { data: template, isLoading: templateLoading, error } = useQuery({
    queryKey: ['analysis-report-template', oid, templateId],
    queryFn: () => agentApi.reportTemplate(oid, templateId!),
    enabled: Boolean(oid && templateId && templateId !== 'new'),
  })

  if (ontologiesLoading) return <LoadingState message="加载本体与报告能力…" />
  if (templateId === 'new') return <NewReportView ontologies={ontologyList} initialOntologyId={initialOntologyId} conversationId={conversationId} />
  if (!templateId) return <ReportLibrary ontologies={ontologyList} initialOntologyId={initialOntologyId} />
  if (!oid) return <div className="p-8 text-sm text-rose-600">缺少本体上下文，请从智能助手或报告工作台重新进入。</div>
  if (templateLoading) return <LoadingState message="加载报告模板…" />
  if (error || !template) return <div className="p-8 text-sm text-rose-600">{errorText(error, '报告模板不存在或无权访问')}</div>
  return <ReportEditor remote={template} ontologyName={ontologyList.find(item => item.id === oid)?.name || '当前本体'} />
}
