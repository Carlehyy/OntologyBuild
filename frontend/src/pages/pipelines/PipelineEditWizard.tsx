import { useState, useEffect, useCallback } from 'react'
import {
  X, Loader2, CheckCircle2, XCircle, AlertTriangle, ChevronLeft, ChevronRight,
  KeyRound, Eye, Save, Rocket, Info, Lock, Sparkles, Undo2, Table2, GitBranch,
} from 'lucide-react'
import pipelinesApi, { CONTRACT_FIELD_TYPES } from '@/api/v2/pipelines'
import type { Pipeline, DryRunResult, DryRunRowsPage, ColumnDefinition, ValidateDefinitionsResult } from '@/api/v2/pipelines'

const splitPk = (s?: string): string[] =>
  (s ?? '').split(',').map(x => x.trim()).filter(Boolean)

/** 旧词表（int/bool/datetime）映射到平台词表，读旧契约时兜底 */
const LEGACY_TYPE_MAP: Record<string, string> = {
  int: 'integer', bool: 'boolean', datetime: 'timestamp', str: 'string', number: 'float',
}
const normType = (t?: string): string => {
  const v = (t || 'string').toLowerCase()
  const mapped = LEGACY_TYPE_MAP[v] || v
  return (CONTRACT_FIELD_TYPES as readonly string[]).includes(mapped) ? mapped : 'string'
}

interface Props {
  pipeline: Pipeline
  onClose: () => void
  onSaved: () => void
}

export default function PipelineEditWizard({ pipeline, onClose, onSaved }: Props) {
  const isPublished = pipeline.status === 'published'
  const isN8n = (pipeline.definition as { engine?: string } | null)?.engine === 'n8n'
  const [step, setStep] = useState<1 | 2 | 3 | 4>(1)

  // ── 阶段 1: 流水线信息 ──
  const [name, setName] = useState(pipeline.name || '')
  const [description, setDescription] = useState(pipeline.description || '')

  // ── 阶段 2: 执行预览 ──
  const [dryRunPhase, setDryRunPhase] = useState<'idle' | 'running' | 'done' | 'error'>('idle')
  const [dryRunResult, setDryRunResult] = useState<DryRunResult | null>(null)
  const [dryRunError, setDryRunError] = useState('')
  const [cachedColumns, setCachedColumns] = useState<string[]>([])
  const [cachedSample, setCachedSample] = useState<Record<string, unknown>[]>([])
  const [totalRows, setTotalRows] = useState(0)
  const [expanded, setExpanded] = useState(false)
  // 已发布：展示最近一次运行的输出样本（不重跑，避免触发生产 workflow）
  const [lastRun, setLastRun] = useState<'loading' | 'none' | { at: string | null; columns: string[]; sample: Record<string, unknown>[] }>('loading')

  const multiOutput = (dryRunResult?.outputs.length ?? 0) > 1
  // 湖中已固化的主键（试运行预检返回）——预填提示但可修改：改动后的契约
  // 需以「全量覆盖」运行一次重建资产（重写湖中声明），增量/合并入库在对齐前会失败
  const lakeDeclaredPk = dryRunResult && dryRunResult.outputs.length === 1 && dryRunResult.outputs[0].pk_source === 'lake'
    ? dryRunResult.outputs[0].pk : ''
  const lakePkCols = new Set(splitPk(lakeDeclaredPk))

  // ── 阶段 3: 设置主键组 ──
  const [columnDefs, setColumnDefs] = useState<ColumnDefinition[]>([])
  const [validateResult, setValidateResult] = useState<ValidateDefinitionsResult | null>(null)
  const [validating, setValidating] = useState(false)

  // ── 阶段 4: 确认配置 ──
  const [saving, setSaving] = useState(false)
  const [publishing, setPublishing] = useState(false)
  const [publishEnable, setPublishEnable] = useState(true)
  const [confirmUnpublish, setConfirmUnpublish] = useState(false)
  const [unpublishing, setUnpublishing] = useState(false)
  const [actionError, setActionError] = useState('')

  // ── 初始化字段契约：按原始列合并已有定义（旧数据缺 source_key 时回退 field_key）──
  const initColumnDefs = useCallback((columns: string[], existing?: ColumnDefinition[] | null, lockedPk?: Set<string>) => {
    const existingMap = new Map((existing || []).map(d => [d.source_key || d.field_key, d]))
    return columns.map(col => {
      const prev = existingMap.get(col)
      const def: ColumnDefinition = prev ? {
        source_key: prev.source_key || prev.field_key,
        field_key: prev.field_key,
        field_name: prev.field_name || prev.field_key,
        field_type: normType(prev.field_type),
        is_primary_key: !!prev.is_primary_key,
        nullable: prev.nullable !== false,
      } : {
        source_key: col,
        field_key: col,
        field_name: col,
        field_type: 'string',
        is_primary_key: false,
        nullable: true,
      }
      // 湖中已固化主键：预填勾选（列名即入湖列名）；仍可修改，改动走全量覆盖重建
      if (lockedPk?.has(def.field_key)) def.is_primary_key = true
      return def
    })
  }, [])

  // ── 阶段 2: 执行 dryRun ──
  const runDryRun = async () => {
    setDryRunPhase('running')
    setDryRunError('')
    setExpanded(false)
    try {
      const res = await pipelinesApi.dryRun(pipeline.id, 100)
      setDryRunResult(res)
      const allCols: string[] = []
      const allSample: Record<string, unknown>[] = []
      let total = 0
      for (const o of res.outputs) {
        for (const c of o.columns) {
          if (!allCols.includes(c)) allCols.push(c)
        }
        if (o.sample?.length) allSample.push(...o.sample)
        total += o.rows_out
      }
      setCachedColumns(allCols)
      setCachedSample(allSample)
      setTotalRows(total)
      const declared = res.outputs.length === 1 && res.outputs[0].pk_source === 'lake'
        ? new Set(splitPk(res.outputs[0].pk)) : undefined
      setColumnDefs(initColumnDefs(allCols, pipeline.column_definitions, declared))
      setDryRunPhase('done')
    } catch (e: unknown) {
      const err = e as { detail?: string; message?: string }
      setDryRunError(err?.detail || err?.message || '试运行失败')
      setDryRunPhase('error')
    }
  }

  // 进入阶段 2：未发布自动触发一次；已发布只读，改为加载最近一次运行样本
  useEffect(() => {
    if (step !== 2) return
    if (!isPublished && dryRunPhase === 'idle') {
      runDryRun()
      return
    }
    if (isPublished && dryRunPhase === 'idle') {
      const defs = (pipeline.column_definitions || []).map(d => ({
        ...d,
        source_key: d.source_key || d.field_key,
        field_type: normType(d.field_type),
      }))
      setColumnDefs(defs)
      setCachedColumns(defs.map(d => d.field_key))
      setDryRunPhase('done')
      pipelinesApi.runs(pipeline.id).then(runs => {
        const ok = runs.find(r => r.status === 'success' && r.stats)
        const outputs = ((ok?.stats as { meta?: { outputs?: Array<Record<string, unknown>> } } | null)?.meta?.outputs) || []
        const first = outputs[0] as { output_sample?: Record<string, unknown>[]; output_columns?: string[] } | undefined
        if (ok && first?.output_sample?.length) {
          const cols = first.output_columns?.length ? first.output_columns : Object.keys(first.output_sample[0] || {})
          setLastRun({ at: ok.finished_at || ok.started_at, columns: cols, sample: first.output_sample })
          setCachedColumns(prev => prev.length ? prev : cols)
        } else {
          setLastRun('none')
        }
      }).catch(() => setLastRun('none'))
    }
  }, [step])  // eslint-disable-line react-hooks/exhaustive-deps

  // ── 阶段 3: 校验（全量，基于第 2 步暂存数据）──
  const runValidate = async () => {
    if (!dryRunResult?.dry_run_id) {
      setValidateResult({ valid: false, errors: [{ field_key: '', message: '缺少试运行数据，请回到「执行预览」重新执行', severity: 'error' }] })
      return
    }
    setValidating(true)
    setValidateResult(null)
    try {
      const res = await pipelinesApi.validateDefinitions(pipeline.id, { column_definitions: columnDefs }, dryRunResult.dry_run_id)
      setValidateResult(res)
      if (res.valid && (res.errors || []).length === 0) setStep(4)
    } catch (e: unknown) {
      const err = e as { detail?: string; message?: string }
      setValidateResult({ valid: false, errors: [{ field_key: '', message: err?.detail || err?.message || '校验请求失败', severity: 'error' }] })
    } finally {
      setValidating(false)
    }
  }

  // ── 阶段 4: 保存 / 发布 / 撤回发布 ──
  const handleSave = async () => {
    setSaving(true)
    setActionError('')
    try {
      await pipelinesApi.update(pipeline.id, {
        name,
        description,
        column_definitions: (isPublished || multiOutput) ? undefined : columnDefs,
      })
      onSaved()
      onClose()
    } catch (e: unknown) {
      const err = e as { detail?: string; message?: string }
      setActionError(err?.detail || err?.message || '保存失败')
    } finally {
      setSaving(false)
    }
  }

  const handlePublish = async () => {
    setPublishing(true)
    setActionError('')
    try {
      await pipelinesApi.update(pipeline.id, {
        name,
        description,
        column_definitions: multiOutput ? undefined : columnDefs,
      })
      await pipelinesApi.publish(pipeline.id, publishEnable)
      onSaved()
      onClose()
    } catch (e: unknown) {
      const err = e as { detail?: string; message?: string }
      setActionError(err?.detail || err?.message || '发布失败')
    } finally {
      setPublishing(false)
    }
  }

  const handleUnpublish = async () => {
    setUnpublishing(true)
    setActionError('')
    try {
      await pipelinesApi.unpublish(pipeline.id)
      onSaved()
      onClose()
    } catch (e: unknown) {
      const err = e as { detail?: string; message?: string }
      setActionError(err?.detail || err?.message || '撤回发布失败')
      setConfirmUnpublish(false)
    } finally {
      setUnpublishing(false)
    }
  }

  // ── 辅助 ──
  const pkFields = columnDefs.filter(d => d.is_primary_key)
  const hasErrors = validateResult && !validateResult.valid
  const errorMap = new Map((validateResult?.errors || []).map(e => [e.field_key, e]))
  const contractEditable = !isPublished && !multiOutput

  // 名称/描述到第 4 步保存才落库——中途关窗要提醒，避免静默丢改动
  const infoDirty = name !== (pipeline.name || '') || description !== (pipeline.description || '')
  const handleClose = () => {
    if (infoDirty && !window.confirm('流水线名称/描述有未保存的修改，关闭后将丢失。确认关闭？')) return
    onClose()
  }

  const updateColDef = (index: number, patch: Partial<ColumnDefinition>) => {
    setColumnDefs(prev => prev.map((d, i) => (i === index ? { ...d, ...patch } : d)))
  }

  const steps = [
    { num: 1, label: '流水线信息' },
    { num: 2, label: '执行预览' },
    { num: 3, label: '设置主键组' },
    { num: 4, label: '确认配置' },
  ]

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-6" onClick={handleClose}>
      <div
        className="bg-white rounded-xl shadow-xl w-[900px] max-w-full max-h-[90vh] flex flex-col"
        onClick={e => e.stopPropagation()}
      >
        {/* 头部 */}
        <div className="flex items-center justify-between px-5 pt-4 pb-3 border-b border-gray-100 shrink-0">
          <div>
            <h3 className="font-semibold text-gray-900 flex items-center gap-2">
              编辑流水线「{pipeline.name}」
              {isN8n ? (
                <span className="inline-flex items-center gap-1 rounded border border-violet-200 bg-violet-50 px-1.5 py-0.5 text-[11px] font-normal text-violet-600">
                  <Sparkles size={10} /> n8n流水线
                </span>
              ) : (
                <span className="inline-flex items-center gap-1 rounded border border-blue-200 bg-blue-50 px-1.5 py-0.5 text-[11px] font-normal text-blue-600">
                  <GitBranch size={10} /> 系统流水线
                </span>
              )}
            </h3>
          </div>
          <button onClick={handleClose} className="text-gray-400 hover:text-black shrink-0">
            <X size={16} />
          </button>
        </div>

        {/* 步骤指示器 */}
        <div className="flex items-center justify-center px-6 py-3 gap-1 shrink-0 border-b border-gray-50">
          {steps.map((s, i) => (
            <div key={s.num} className="flex items-center gap-1">
              {i > 0 && <div className={`h-px w-6 ${step > i + 1 ? 'bg-[var(--color-nav-bg)]' : 'bg-gray-200'}`} />}
              <button
                disabled={s.num > step || (s.num === 3 && !cachedColumns.length)}
                onClick={() => setStep(s.num as 1 | 2 | 3 | 4)}
                className={`flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium transition-colors ${
                  step === s.num
                    ? 'bg-[var(--color-nav-bg)] text-white'
                    : step > s.num
                      ? 'bg-emerald-50 text-emerald-700 cursor-pointer'
                      : 'bg-gray-100 text-gray-400 cursor-not-allowed'
                }`}
              >
                {step > s.num ? <CheckCircle2 size={12} /> : <span className="w-4 h-4 rounded-full border border-current flex items-center justify-center text-[10px]">{s.num}</span>}
                {s.label}
              </button>
            </div>
          ))}
        </div>

        {/* 内容区 */}
        <div className="flex-1 overflow-auto p-6">
          {/* ──────── 阶段 1: 流水线信息 ──────── */}
          {step === 1 && (
            <div className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">流水线名称</label>
                <input
                  value={name}
                  onChange={e => setName(e.target.value)}
                  className="w-full px-3 py-2 border rounded-lg text-sm"
                  placeholder="输入流水线名称"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">描述</label>
                <textarea
                  value={description}
                  onChange={e => setDescription(e.target.value)}
                  className="w-full px-3 py-2 border rounded-lg text-sm h-24 resize-none"
                  placeholder="输入流水线描述（可选）"
                />
              </div>
              <p className="text-xs text-gray-400">名称和描述在任何状态下都可以修改，不影响契约、编排与已产出资产的归属（在第 4 步确认后保存）。</p>
            </div>
          )}

          {/* ──────── 阶段 2: 执行预览 ──────── */}
          {step === 2 && (
            <div className="space-y-3">
              {dryRunPhase === 'running' && (
                <div className="flex items-center gap-3 text-blue-600 py-8 justify-center">
                  <Loader2 size={20} className="animate-spin" />
                  <span className="text-sm">正在执行流水线，获取预览数据…</span>
                </div>
              )}

              {dryRunPhase === 'error' && (
                <div className="bg-red-50 border border-red-200 rounded-lg p-4 flex items-start gap-2">
                  <XCircle size={16} className="text-red-500 mt-0.5 shrink-0" />
                  <div>
                    <p className="text-sm text-red-700 font-medium">试运行失败</p>
                    <p className="text-xs text-red-600 mt-0.5">{dryRunError}</p>
                    <button
                      onClick={runDryRun}
                      className="mt-2 text-xs text-red-600 underline hover:text-red-800"
                    >
                      重试
                    </button>
                  </div>
                </div>
              )}

              {dryRunPhase === 'done' && !isPublished && (
                <div className="flex items-center justify-between text-xs text-gray-400 mb-1">
                  <span>
                    共 {totalRows.toLocaleString()} 行，默认展示前 {Math.min(cachedSample.length, 100)} 行
                  </span>
                  <div className="flex items-center gap-3">
                    {totalRows > cachedSample.length && dryRunResult && !expanded && (
                      <button
                        onClick={() => setExpanded(true)}
                        className="text-[var(--color-nav-bg)] hover:underline flex items-center gap-1"
                      >
                        <Table2 size={12} /> 展开查看全部数据
                      </button>
                    )}
                    <button
                      onClick={runDryRun}
                      className="text-[var(--color-nav-bg)] hover:underline flex items-center gap-1"
                    >
                      <Eye size={12} /> 重新执行
                    </button>
                  </div>
                </div>
              )}

              {dryRunPhase === 'done' && !isPublished && (dryRunResult?.outputs || []).some(o => o.gate_error || o.warnings.length > 0) && (
                <div className="bg-amber-50 border border-amber-200 rounded-lg px-3 py-2 space-y-1">
                  {(dryRunResult?.outputs || []).map((o, i) => (
                    <div key={i} className="text-xs text-amber-700 space-y-0.5">
                      {o.gate_error && <p className="text-red-600">入湖预检：{o.gate_error}</p>}
                      {o.warnings.map((w, j) => <p key={j}>{w}</p>)}
                    </div>
                  ))}
                </div>
              )}

              {dryRunPhase === 'done' && isPublished && (
                <div className="flex items-center gap-2 text-xs text-gray-400 mb-1">
                  <Info size={12} />
                  <span>
                    已发布流水线不在此处重新执行（避免触发生产工作流）。
                    {typeof lastRun === 'object' && lastRun.at ? `下方为最近一次执行（${new Date(lastRun.at).toLocaleString('zh-CN')}）的输出样本。` : ''}
                  </span>
                </div>
              )}

              {/* 数据表格：采样 / 展开分页 / 最近一次运行样本 */}
              {dryRunPhase === 'done' && expanded && dryRunResult ? (
                <DryRunPagedTable
                  pipelineId={pipeline.id}
                  dryRunId={dryRunResult.dry_run_id}
                  outputCount={dryRunResult.outputs.length}
                  onCollapse={() => setExpanded(false)}
                />
              ) : dryRunPhase === 'done' && (
                <SampleTable
                  columns={isPublished && typeof lastRun === 'object' ? lastRun.columns : cachedColumns}
                  rows={isPublished ? (typeof lastRun === 'object' ? lastRun.sample : []) : cachedSample.slice(0, 100)}
                  emptyText={
                    isPublished
                      ? (lastRun === 'loading' ? '加载最近一次执行结果…' : '暂无成功的执行记录，可在列表页「执行」查看输出')
                      : '暂无数据'
                  }
                />
              )}

              {dryRunPhase === 'done' && !isPublished && cachedColumns.length === 0 && (
                <div className="text-center py-8 text-gray-400 text-sm">
                  <AlertTriangle size={24} className="mx-auto mb-2 opacity-40" />
                  <p>流水线执行未产生任何数据列</p>
                </div>
              )}
            </div>
          )}

          {/* ──────── 阶段 3: 设置主键组 ──────── */}
          {step === 3 && (
            <div className="space-y-3">
              <div className="flex items-center gap-2 text-xs text-gray-400">
                <KeyRound size={12} />
                <span>定义每个字段的入湖契约：字段标识（入湖列名，可改名）、名称、类型、主键、允许空值</span>
                {isPublished && <span className="text-amber-600 font-medium">（已发布 · 只读）</span>}
              </div>

              {multiOutput && !isPublished && (
                <div className="bg-amber-50 border border-amber-200 rounded-lg px-3 py-2 text-xs text-amber-700 flex items-start gap-1.5">
                  <AlertTriangle size={13} className="mt-0.5 shrink-0" />
                  <span>
                    该流水线单次执行产出多个数据集，流水线级字段契约暂不适用（主键的正确粒度是每个数据集一个，
                    请在任务/资产湖粒度管理）。可直接进入下一步。
                  </span>
                </div>
              )}

              {lakePkCols.size > 0 && (
                <div className="bg-amber-50 border border-amber-100 rounded-lg px-3 py-2 text-xs text-amber-700 flex items-center gap-1.5">
                  <Lock size={11} className="shrink-0" />
                  <span>
                    资产湖已固化主键契约 <b className="font-mono">{lakeDeclaredPk}</b>（已自动勾选）。
                    可以修改，但改动后需以「全量覆盖」方式运行一次重建资产（会重写湖中声明并重建实例身份）；
                    增量/合并入库在对齐前会失败。
                  </span>
                </div>
              )}

              {pkFields.length > 0 && (
                <div className="bg-blue-50 border border-blue-200 rounded-lg px-3 py-2 text-xs text-blue-700">
                  主键：{pkFields.map(d => d.field_key).join('、')}
                  {pkFields.length > 1 && '（复合主键）'}
                </div>
              )}

              <div className="border rounded-lg overflow-x-auto">
                <table className="w-full min-w-[700px] text-xs">
                  <thead className="bg-gray-50 border-b">
                    <tr>
                      <th className="text-left px-3 py-2 font-medium text-gray-600 w-28">原始列名</th>
                      <th className="text-left px-3 py-2 font-medium text-gray-600 w-32">字段标识（入湖列名）</th>
                      <th className="text-left px-3 py-2 font-medium text-gray-600 w-32">字段名称</th>
                      <th className="text-left px-3 py-2 font-medium text-gray-600 w-24">字段类型</th>
                      <th className="text-center px-3 py-2 font-medium text-gray-600 w-16">主键</th>
                      <th className="text-center px-3 py-2 font-medium text-gray-600 w-16">允许空</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y">
                    {columnDefs.map((d, i) => {
                      const declaredInLake = lakePkCols.has(d.field_key)
                      const disabled = !contractEditable
                      const fieldErrors = errorMap.get(d.field_key)
                      const allErrors = [fieldErrors].filter(Boolean)
                      return (
                        <tr key={i} className={allErrors.length > 0 ? 'bg-red-50' : 'hover:bg-gray-50'}>
                          <td className="px-3 py-1.5 text-gray-500 font-mono text-[11px]" title="流水线输出的原始列名">
                            {d.source_key}
                          </td>
                          <td className="px-1 py-1">
                            <div className="relative">
                              <input
                                value={d.field_key}
                                disabled={disabled}
                                onChange={e => updateColDef(i, { field_key: e.target.value })}
                                title={declaredInLake ? '湖中已固化的主键列：改名后需以「全量覆盖」运行一次重建资产' : d.source_key !== d.field_key ? `入湖时 ${d.source_key} → ${d.field_key}` : undefined}
                                className={`w-full px-2 py-1 border rounded text-xs font-mono ${disabled ? 'bg-gray-50 text-gray-400 cursor-not-allowed' : d.source_key !== d.field_key ? 'border-blue-300 bg-blue-50/40' : ''}`}
                              />
                              {declaredInLake && <Lock size={10} className="absolute right-1.5 top-1/2 -translate-y-1/2 text-amber-400 pointer-events-none" />}
                            </div>
                          </td>
                          <td className="px-1 py-1">
                            <input
                              value={d.field_name}
                              disabled={disabled}
                              onChange={e => updateColDef(i, { field_name: e.target.value })}
                              className={`w-full px-2 py-1 border rounded text-xs ${disabled ? 'bg-gray-50 text-gray-400 cursor-not-allowed' : ''}`}
                            />
                          </td>
                          <td className="px-1 py-1">
                            <select
                              value={d.field_type}
                              disabled={disabled}
                              onChange={e => updateColDef(i, { field_type: e.target.value })}
                              className={`w-full px-2 py-1 border rounded text-xs ${disabled ? 'bg-gray-50 text-gray-400 cursor-not-allowed' : ''}`}
                            >
                              {CONTRACT_FIELD_TYPES.map(t => (
                                <option key={t} value={t}>{t}</option>
                              ))}
                            </select>
                          </td>
                          <td className="px-1 py-1 text-center">
                            <input
                              type="checkbox"
                              checked={d.is_primary_key}
                              disabled={disabled}
                              onChange={e => updateColDef(i, { is_primary_key: e.target.checked })}
                              className={disabled ? 'opacity-50 cursor-not-allowed' : ''}
                            />
                          </td>
                          <td className="px-1 py-1 text-center">
                            <input
                              type="checkbox"
                              checked={d.nullable}
                              disabled={disabled}
                              onChange={e => updateColDef(i, { nullable: e.target.checked })}
                              className={disabled ? 'opacity-50 cursor-not-allowed' : ''}
                            />
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>

              {/* 校验错误/警告展示 */}
              {hasErrors && (
                <div className="bg-red-50 border border-red-200 rounded-lg p-3 space-y-1">
                  {(validateResult?.errors || []).map((e, i) => (
                    <div key={i} className="flex items-start gap-1.5 text-xs">
                      {e.severity === 'warning'
                        ? <AlertTriangle size={12} className="text-amber-500 mt-0.5 shrink-0" />
                        : <XCircle size={12} className="text-red-500 mt-0.5 shrink-0" />}
                      <span className={e.severity === 'warning' ? 'text-amber-700' : 'text-red-700'}>
                        {e.field_key && <span className="font-mono mr-1">[{e.field_key}]</span>}
                        {e.message}
                      </span>
                    </div>
                  ))}
                </div>
              )}

              {validateResult?.valid && (validateResult.errors || []).length === 0 && (
                <div className="bg-emerald-50 border border-emerald-200 rounded-lg p-3">
                  <div className="flex items-center gap-2 text-xs text-emerald-700">
                    <CheckCircle2 size={14} />
                    全量校验通过（共 {totalRows.toLocaleString()} 行），可以进入下一步
                  </div>
                </div>
              )}

              {validateResult?.valid && (validateResult.errors || []).length > 0 && (
                <div className="bg-amber-50 border border-amber-200 rounded-lg p-3 space-y-1">
                  <div className="flex items-center gap-2 text-xs text-amber-700 font-medium">
                    <AlertTriangle size={14} />
                    校验完成，但存在以下问题需要处理
                  </div>
                  {(validateResult.errors || []).map((e, i) => (
                    <div key={i} className="flex items-start gap-1.5 text-xs text-amber-700 ml-6">
                      <span>{e.field_key && <span className="font-mono mr-1">[{e.field_key}]</span>}{e.message}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          {/* ──────── 阶段 4: 确认配置 ──────── */}
          {step === 4 && (
            <div className="space-y-4">
              <div className="grid grid-cols-2 gap-3">
                <div className="border rounded-lg p-3">
                  <p className="text-xs text-gray-400 mb-1">流水线名称</p>
                  <p className="text-sm font-medium text-gray-800">{name || '—'}</p>
                </div>
                <div className="border rounded-lg p-3">
                  <p className="text-xs text-gray-400 mb-1">描述</p>
                  <p className="text-sm text-gray-800">{description || '—'}</p>
                </div>
                <div className="border rounded-lg p-3">
                  <p className="text-xs text-gray-400 mb-1">列数</p>
                  <p className="text-sm font-medium text-gray-800">{columnDefs.length}</p>
                </div>
                <div className="border rounded-lg p-3">
                  <p className="text-xs text-gray-400 mb-1">主键</p>
                  <p className="text-sm font-medium text-gray-800">
                    {pkFields.length > 0 ? pkFields.map(d => d.field_key).join('、') : '未设置'}
                  </p>
                </div>
              </div>

              {!multiOutput && columnDefs.length > 0 && (
                <div>
                  <p className="text-xs text-gray-400 mb-2">字段契约预览{isPublished ? '（已封版）' : '（发布后封版）'}</p>
                  <div className="border rounded-lg overflow-x-auto max-h-[200px]">
                    <table className="w-full min-w-[560px] text-xs">
                      <thead className="bg-gray-50 border-b sticky top-0">
                        <tr>
                          <th className="text-left px-3 py-1.5 font-medium text-gray-600">原始列名</th>
                          <th className="text-left px-3 py-1.5 font-medium text-gray-600">字段标识</th>
                          <th className="text-left px-3 py-1.5 font-medium text-gray-600">字段名称</th>
                          <th className="text-left px-3 py-1.5 font-medium text-gray-600">类型</th>
                          <th className="text-center px-3 py-1.5 font-medium text-gray-600">主键</th>
                          <th className="text-center px-3 py-1.5 font-medium text-gray-600">允许空</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y">
                        {columnDefs.map((d, i) => (
                          <tr key={i} className="hover:bg-gray-50">
                            <td className="px-3 py-1 font-mono text-[11px] text-gray-500">{d.source_key}</td>
                            <td className="px-3 py-1 font-mono text-[11px]">
                              {d.field_key}
                              {d.source_key !== d.field_key && <span className="ml-1 text-blue-500">（改名）</span>}
                            </td>
                            <td className="px-3 py-1">{d.field_name}</td>
                            <td className="px-3 py-1">
                              <span className="px-1.5 py-0.5 bg-gray-100 rounded text-[11px]">{d.field_type}</span>
                            </td>
                            <td className="px-3 py-1 text-center">
                              {d.is_primary_key ? <KeyRound size={12} className="text-amber-500 inline" /> : '—'}
                            </td>
                            <td className="px-3 py-1 text-center">
                              {d.nullable ? '是' : <span className="text-red-500 font-medium">否</span>}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}

              {multiOutput && (
                <div className="bg-amber-50 border border-amber-200 rounded-lg px-3 py-2 text-xs text-amber-700">
                  多产物流水线：本次仅保存名称与描述，字段契约不适用（主键在任务/资产湖粒度管理）。
                </div>
              )}

              {!isPublished && (
                <label className="flex items-start gap-2 text-sm text-gray-700 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={publishEnable}
                    onChange={e => setPublishEnable(e.target.checked)}
                    className="mt-0.5 accent-emerald-600"
                  />
                  <span>
                    发布后立即启用
                    <span className="block text-xs text-gray-400">启用后才能被数据任务池挂接调度；也可以稍后在列表打开启用开关</span>
                  </span>
                </label>
              )}

              {!isPublished && isN8n && (
                <div className="bg-violet-50 border border-violet-200 rounded-lg px-3 py-2 text-xs text-violet-700 flex items-start gap-1.5">
                  <Sparkles size={13} className="mt-0.5 shrink-0" />
                  <span>
                    发布 n8n 流水线会激活 n8n 侧工作流并封版字段契约；发布后编排只读，
                    如需修改可随时撤回发布，再回数据管家继续编排。
                  </span>
                </div>
              )}

              {isPublished && (
                <div className="flex items-start justify-between gap-3 border border-gray-100 rounded-lg px-3 py-2.5 bg-gray-50/60">
                  <p className="text-xs text-gray-500 leading-relaxed">
                    已发布（封版）：契约与编排不可修改。仅当没有任务引用时可撤回发布回到草稿态（会自动停用{isN8n ? '，并停用 n8n 侧工作流' : ''}）。
                  </p>
                  <button
                    onClick={() => confirmUnpublish ? handleUnpublish() : setConfirmUnpublish(true)}
                    disabled={unpublishing}
                    className={`shrink-0 flex items-center gap-1 px-2.5 py-1 text-xs rounded-lg border transition-colors ${
                      confirmUnpublish
                        ? 'border-red-300 bg-red-50 text-red-600 hover:bg-red-100'
                        : 'border-gray-200 text-gray-500 hover:text-red-600 hover:border-red-200'
                    }`}
                  >
                    {unpublishing ? <Loader2 size={12} className="animate-spin" /> : <Undo2 size={12} />}
                    {confirmUnpublish ? '确认撤回发布？' : '撤回发布'}
                  </button>
                </div>
              )}

              {actionError && (
                <div className="bg-red-50 border border-red-200 rounded-lg px-3 py-2 text-xs text-red-600 flex items-start gap-1.5">
                  <XCircle size={13} className="mt-0.5 shrink-0" />
                  <span>{actionError}</span>
                </div>
              )}
            </div>
          )}
        </div>

        {/* 底部按钮 */}
        <div className="flex items-center justify-between px-5 py-3 border-t border-gray-100 shrink-0">
          <div>
            {step > 1 && (
              <button
                onClick={() => setStep((step - 1) as 1 | 2 | 3 | 4)}
                className="flex items-center gap-1 px-3 py-1.5 text-sm text-gray-500 hover:text-gray-800 border rounded-lg"
              >
                <ChevronLeft size={14} /> 上一步
              </button>
            )}
          </div>
          <div className="flex items-center gap-2">
            {/* 第 3 步（草稿、契约适用）只能走「校验并下一步」——校验通过才能继续 */}
            {step < 4 && !(step === 3 && contractEditable) && (
              <button
                onClick={() => {
                  setValidateResult(null)
                  setStep((step + 1) as 1 | 2 | 3 | 4)
                }}
                disabled={step === 2 && dryRunPhase !== 'done'}
                className={`flex items-center gap-1 px-4 py-1.5 text-sm rounded-lg font-medium transition-colors ${
                  (step === 2 && dryRunPhase !== 'done')
                    ? 'bg-gray-200 text-gray-400 cursor-not-allowed'
                    : 'bg-[var(--color-nav-bg)] text-white hover:opacity-90'
                }`}
              >
                下一步 <ChevronRight size={14} />
              </button>
            )}
            {step === 3 && contractEditable && (
              <button
                onClick={runValidate}
                disabled={validating}
                className={`flex items-center gap-1 px-4 py-1.5 text-sm rounded-lg font-medium transition-colors ${
                  validating
                    ? 'bg-gray-200 text-gray-400 cursor-not-allowed'
                    : 'bg-emerald-600 text-white hover:bg-emerald-700'
                }`}
              >
                {validating ? (
                  <><Loader2 size={14} className="animate-spin" /> 全量校验中…</>
                ) : (
                  '校验并下一步'
                )}
              </button>
            )}
            {step === 4 && !isPublished && (
              <>
                <button
                  onClick={handleSave}
                  disabled={saving || publishing}
                  className="flex items-center gap-1.5 px-4 py-1.5 text-sm rounded-lg font-medium border border-gray-300 text-gray-700 hover:bg-gray-50 disabled:opacity-60"
                >
                  {saving ? <Loader2 size={14} className="animate-spin" /> : <Save size={14} />}
                  保存草稿
                </button>
                <button
                  onClick={handlePublish}
                  disabled={publishing || saving}
                  className="flex items-center gap-1.5 px-4 py-1.5 text-sm rounded-lg font-medium bg-[var(--color-nav-bg)] text-white hover:opacity-90 disabled:opacity-60"
                >
                  {publishing ? <Loader2 size={14} className="animate-spin" /> : <Rocket size={14} />}
                  {publishEnable ? '发布并启用' : '发布'}
                </button>
              </>
            )}
            {step === 4 && isPublished && (
              <button
                onClick={handleSave}
                disabled={saving}
                className="flex items-center gap-1.5 px-4 py-1.5 text-sm rounded-lg font-medium bg-[var(--color-nav-bg)] text-white hover:opacity-90 disabled:opacity-60"
              >
                {saving ? <Loader2 size={14} className="animate-spin" /> : <Save size={14} />}
                保存
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

/** 采样数据表：水平滚动 + 吸顶表头 */
function SampleTable({ columns, rows, emptyText }: {
  columns: string[]
  rows: Record<string, unknown>[]
  emptyText: string
}) {
  if (columns.length === 0) return null
  return (
    <div className="border rounded-lg overflow-hidden">
      <div className="overflow-x-auto max-h-[400px]">
        <table className="w-full min-w-[600px] text-xs table-fixed">
          <thead className="bg-gray-50 border-b sticky top-0">
            <tr>
              <th className="text-left px-3 py-2 font-medium text-gray-600 w-10">#</th>
              {columns.map(col => (
                <th key={col} className="text-left px-3 py-2 font-medium text-gray-600 whitespace-nowrap" style={{ minWidth: 120 }}>
                  {col}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y">
            {rows.length === 0 ? (
              <tr>
                <td colSpan={columns.length + 1} className="px-3 py-8 text-center text-gray-400">
                  {emptyText}
                </td>
              </tr>
            ) : (
              rows.map((row, i) => (
                <tr key={i} className="hover:bg-gray-50">
                  <td className="px-3 py-1.5 text-gray-400">{i + 1}</td>
                  {columns.map(col => (
                    <td key={col} className="px-3 py-1.5 text-gray-700 whitespace-nowrap overflow-hidden text-ellipsis" title={String(row[col] ?? '')}>
                      {row[col] === null || row[col] === undefined ? (
                        <span className="text-gray-300 italic">null</span>
                      ) : (
                        String(row[col])
                      )}
                    </td>
                  ))}
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}

/** 全量数据分页浏览：读取 dry-run 暂存（服务端分页，不重跑流水线） */
function DryRunPagedTable({ pipelineId, dryRunId, outputCount, onCollapse }: {
  pipelineId: string
  dryRunId: string
  outputCount: number
  onCollapse: () => void
}) {
  const [outputIndex, setOutputIndex] = useState(0)
  const [page, setPage] = useState(1)
  const [data, setData] = useState<DryRunRowsPage | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const pageSize = 50

  useEffect(() => {
    let alive = true
    setLoading(true)
    setError('')
    pipelinesApi.dryRunRows(pipelineId, dryRunId, { output_index: outputIndex, page, page_size: pageSize })
      .then(res => { if (alive) setData(res) })
      .catch((e: { detail?: string; message?: string }) => {
        if (alive) setError(e?.detail || e?.message || '加载失败')
      })
      .finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [pipelineId, dryRunId, outputIndex, page])

  const total = data?.total ?? 0
  const totalPages = Math.max(1, Math.ceil(total / pageSize))
  const columns = data?.columns ?? []

  return (
    <div className="border rounded-lg overflow-hidden">
      <div className="px-3 py-2 bg-gray-50/80 border-b flex items-center gap-2 text-xs text-gray-500">
        <Table2 size={12} className="text-gray-400" />
        <span>全量数据浏览</span>
        {outputCount > 1 && (
          <select
            value={outputIndex}
            onChange={e => { setOutputIndex(Number(e.target.value)); setPage(1) }}
            className="text-xs px-2 py-0.5 border rounded bg-white"
          >
            {Array.from({ length: outputCount }, (_, i) => (
              <option key={i} value={i}>产物 {i + 1}</option>
            ))}
          </select>
        )}
        <button onClick={onCollapse} className="ml-auto text-[var(--color-nav-bg)] hover:underline">
          收起
        </button>
      </div>
      <div className="overflow-x-auto max-h-[400px] overflow-y-auto">
        {loading ? (
          <div className="py-10 text-center text-xs text-gray-400 flex items-center justify-center gap-1.5">
            <Loader2 size={13} className="animate-spin" /> 加载数据…
          </div>
        ) : error ? (
          <div className="py-10 text-center text-xs text-red-500">{error}</div>
        ) : !data || data.rows.length === 0 ? (
          <div className="py-10 text-center text-xs text-gray-400">该页没有数据</div>
        ) : (
          <table className="w-full min-w-[600px] text-xs">
            <thead className="bg-gray-50 sticky top-0">
              <tr>
                <th className="text-left px-3 py-2 font-medium text-gray-600 border-b whitespace-nowrap w-12">#</th>
                {columns.map(c => (
                  <th key={c} className="text-left px-3 py-2 font-medium text-gray-600 border-b whitespace-nowrap" style={{ minWidth: 120 }}>{c}</th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y">
              {data.rows.map((row, ri) => (
                <tr key={ri} className="hover:bg-gray-50">
                  <td className="px-3 py-1.5 text-gray-400 whitespace-nowrap tabular-nums">
                    {(page - 1) * pageSize + ri + 1}
                  </td>
                  {columns.map(c => (
                    <td key={c} className="px-3 py-1.5 text-gray-700 whitespace-nowrap max-w-[220px] overflow-hidden text-ellipsis" title={String(row[c] ?? '')}>
                      {row[c] === null || row[c] === undefined ? (
                        <span className="text-gray-300 italic">null</span>
                      ) : (
                        String(row[c])
                      )}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      <div className="flex items-center justify-between px-3 py-2 border-t bg-gray-50/60 text-xs text-gray-400">
        <span>共 {total.toLocaleString()} 行 · 第 {page}/{totalPages} 页</span>
        <div className="flex items-center gap-1">
          <button
            disabled={page <= 1 || loading}
            onClick={() => setPage(p => Math.max(1, p - 1))}
            className="p-1 rounded hover:bg-white disabled:opacity-40 disabled:cursor-not-allowed"
          >
            <ChevronLeft size={14} />
          </button>
          <button
            disabled={page >= totalPages || loading}
            onClick={() => setPage(p => Math.min(totalPages, p + 1))}
            className="p-1 rounded hover:bg-white disabled:opacity-40 disabled:cursor-not-allowed"
          >
            <ChevronRight size={14} />
          </button>
        </div>
      </div>
    </div>
  )
}
