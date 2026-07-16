import { useState, useEffect } from 'react'
import {
  X, Loader2, AlertCircle, CheckCircle2, GitBranch, ArrowRight,
  Database, KeyRound, ChevronLeft, ChevronRight, Table2, Check, Sparkles,
} from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import {
  pipelineTasksApi, WRITE_MODE_META,
  type PipelineTask, type PipelineTaskPayload, type WriteMode,
  type PipelineTaskScheduleType, type SelectablePipeline, type CuratedDataset, type CuratedPreview,
} from '@/api/v2/pipeline-tasks'

interface Props {
  initialTask: PipelineTask | null
  /** 从流水线发布页跳转而来时预选的流水线 ID */
  initialPipelineId?: string | null
  onClose: () => void
  onSaved: () => void
}

const STEPS = ['基本信息', '选择流水线', '设置入库方式', '调度设置', '确认配置']

const splitPk = (s?: string): string[] =>
  (s ?? '').split(',').map(x => x.trim()).filter(Boolean)
export default function TaskFormModal({ initialTask, initialPipelineId, onClose, onSaved }: Props) {
  const navigate = useNavigate()
  const isEdit = !!initialTask
  const [step, setStep] = useState(0)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')

  const [pipelines, setPipelines] = useState<SelectablePipeline[]>([])
  const [pipelinesLoading, setPipelinesLoading] = useState(true)
  const [pipelinesError, setPipelinesError] = useState('')
  /** 当前用于「预览 + 主键勾选」的成品数据集 id（流水线可能产出多个） */
  const [activeCuratedId, setActiveCuratedId] = useState<string>('')

  const [form, setForm] = useState<PipelineTaskPayload>(() => initialTask ? {
    name: initialTask.name,
    description: initialTask.description,
    pipeline_id: initialTask.pipeline_id,
    write_mode: initialTask.write_mode,
    soft_delete_column: initialTask.soft_delete_column,
    skip_empty: initialTask.skip_empty,
    schedule_type: initialTask.schedule_type,
    cron_expression: initialTask.cron_expression,
    interval_seconds: initialTask.interval_seconds,
    enabled: initialTask.enabled,
  } : {
    name: '',
    description: '',
    pipeline_id: initialPipelineId || '',
    write_mode: 'overwrite',
    soft_delete_column: '',
    skip_empty: true,
    schedule_type: 'MANUAL',
    cron_expression: '',
    interval_seconds: 3600,
    enabled: true,
  })

  useEffect(() => {
    setPipelinesLoading(true)
    setPipelinesError('')
    pipelineTasksApi.selectablePipelines()
      .then(res => setPipelines(res.items ?? []))
      .catch((err: any) => {
        setPipelines([])
        setPipelinesError(err?.detail || err?.message || '可用流水线加载失败，请稍后重试')
      })
      .finally(() => setPipelinesLoading(false))
  }, [])

  const selectedPipeline = pipelines.find(p => p.id === form.pipeline_id) || null
  const curatedList: CuratedDataset[] = selectedPipeline?.curated_datasets || []
  const activeCurated = curatedList.find(c => c.id === activeCuratedId) || curatedList[0] || null
  const contract = selectedPipeline?.contract || null
  // 主键唯一权威源是流水线发布契约；湖中值只用于发现历史漂移，不能反向改写任务。
  const declaredPk = (activeCurated?.primary_key || '').trim()
  const contractPk = (contract?.primary_key || '').trim()
  const pkMismatch = Boolean(
    declaredPk && contractPk
    && splitPk(declaredPk).join('\u0000') !== splitPk(contractPk).join('\u0000'),
  )
  const contractColumns = contract?.columns?.map(c => ({ name: c.name, type: c.type })) ?? []

  // 选中流水线后默认锁定第一个成品数据集
  useEffect(() => {
    if (selectedPipeline && curatedList.length > 0 && !curatedList.some(c => c.id === activeCuratedId)) {
      setActiveCuratedId(curatedList[0]?.id || '')
    }
  }, [selectedPipeline, curatedList, activeCuratedId])

  const update = <K extends keyof PipelineTaskPayload>(key: K, val: PipelineTaskPayload[K]) => {
    setForm(prev => ({ ...prev, [key]: val }))
  }

  const onPickPipeline = (id: string) => {
    update('pipeline_id', id)
    const np = pipelines.find(p => p.id === id)
    setActiveCuratedId(np?.curated_datasets[0]?.id || '')
    setError('')
  }

  const validateStep = (s: number): string | null => {
    if (s === 0 && !form.name.trim()) return '请填写任务名称'
    if (s === 1 && !form.pipeline_id) return '请选择一条已产出数据的流水线'
    if (s === 2 && pkMismatch)
      return '资产湖主键与流水线发布契约不一致，请先修复契约漂移后再创建任务'
    if (s === 2 && form.write_mode === 'upsert' && !contractPk)
      return '「主键合并」要求流水线在发布契约中声明主键'
    if (s === 3) {
      if (form.schedule_type === 'CRON' && !(form.cron_expression ?? '').trim()) return '请填写 Cron 表达式'
      if (form.schedule_type === 'INTERVAL' && (!form.interval_seconds || form.interval_seconds < 10)) return '间隔必须 ≥ 10 秒'
    }
    return null
  }

  const handleNext = () => {
    const err = validateStep(step)
    if (err) { setError(err); return }
    setError('')
    setStep(s => s + 1)
  }

  const handleSubmit = async () => {
    for (const s of [0, 1, 2, 3]) {
      const err = validateStep(s)
      if (err) { setError(err); setStep(s); return }
    }
    setSubmitting(true)
    setError('')
    try {
      if (isEdit && initialTask) {
        await pipelineTasksApi.update(initialTask.id, form)
      } else {
        await pipelineTasksApi.create(form)
      }
      onSaved()
    } catch (err: any) {
      setError(err?.detail || err?.message || '保存失败')
    } finally {
      setSubmitting(false)
    }
  }

  // ── 确认阶段的自然语言预期效果 ─────────────────────────────
  const buildEffectText = (): string => {
    const pName = selectedPipeline
      ? `「${selectedPipeline.name}${selectedPipeline.version ? ` v${selectedPipeline.version}` : ''}」`
      : '所选流水线'
    const sched = form.schedule_type === 'MANUAL' ? '在任务池手动点击「执行」时触发'
      : form.schedule_type === 'CRON' ? `按 Cron 表达式「${form.cron_expression || '-'}」定时自动触发`
      : `每隔 ${form.interval_seconds || 0} 秒自动触发一次`
    const modeLabel = WRITE_MODE_META[form.write_mode].label
    const targets = curatedList.length ? curatedList.map(c => `「${c.name}」`).join('、') : '其成品数据集'
    const modeDetail =
      form.write_mode === 'upsert'
        ? `按流水线契约主键（${contractPk || '未声明'}）识别同一条记录并保留最新` + (form.soft_delete_column ? `，依据「${form.soft_delete_column}」处理逻辑删除` : '')
      : form.write_mode === 'overwrite' ? '先清空再写入本次全部产物（全量覆盖）'
      : form.write_mode === 'append' ? '直接追加到已有数据尾部'
      : '按整行内容去重后追加'
    const guard = form.skip_empty
      ? '若某次运行产出 0 行，将自动跳过入库、保护资产不被误清空。'
      : '未开启空输出保护：即使产出 0 行也会执行入库（可能清空资产），请谨慎。'
    const enable = form.enabled ? '创建后任务立即启用并纳入调度。' : '创建后任务处于停用状态，需手动启用后才会调度。'
    return `任务将${sched}，运行流水线${pName}；每次把最终产物以【${modeLabel}】方式写入 ${targets}（${modeDetail}）。${guard}${enable}`
  }

  const show = (s: number) => isEdit || step === s

  return (
    <div className="fixed inset-0 bg-slate-900/40 backdrop-blur-sm z-50 flex items-center justify-center p-4" onClick={onClose}>
      <div className="bg-white rounded-2xl shadow-[0_24px_70px_rgba(15,23,42,0.18)] border border-white/70 w-full max-w-3xl max-h-[90vh] flex flex-col overflow-hidden" onClick={e => e.stopPropagation()}>
        {/* 头部（居中） */}
        <div className="relative px-6 py-5 border-b border-slate-100 text-center">
          <h3 className="text-lg font-semibold text-slate-800">
            {isEdit ? '编辑调度任务' : '新建调度任务'}
          </h3>
          <p className="text-xs text-slate-400 mt-1 max-w-lg mx-auto">
            任务按计划触发已发布的流水线，并把最终产物按入库方式写进数据资产湖
          </p>
          <button onClick={onClose} className="absolute right-5 top-5 text-slate-400 hover:text-slate-600 transition-colors">
            <X size={18} />
          </button>
        </div>

        {/* 步骤指示器（居中） */}
        {!isEdit && (
          <div className="px-6 py-4 border-b border-slate-100 flex items-center justify-center flex-wrap gap-y-2">
            {STEPS.map((label, i) => (
              <div key={i} className="flex items-center">
                <div className="flex items-center gap-1.5">
                  <div className={`w-6 h-6 rounded-full flex items-center justify-center text-xs font-medium transition-colors
                    ${i < step ? 'bg-emerald-100 text-emerald-600'
                      : i === step ? 'bg-blue-500 text-white shadow-sm shadow-blue-500/30'
                      : 'bg-slate-100 text-slate-400'}`}>
                    {i < step ? <Check size={12} /> : i + 1}
                  </div>
                  <span className={`text-xs whitespace-nowrap ${i === step ? 'text-slate-800 font-medium' : 'text-slate-400'}`}>{label}</span>
                </div>
                {i < STEPS.length - 1 && <div className={`w-6 h-px mx-2 ${i < step ? 'bg-emerald-300' : 'bg-slate-200'}`} />}
              </div>
            ))}
          </div>
        )}

        {/* 内容 */}
        <div className="flex-1 overflow-y-auto px-6 py-5">
          {error && (
            <div className="max-w-xl mx-auto mb-4 flex items-start gap-2 p-3 bg-red-50 border border-red-100 rounded-xl text-sm text-red-600">
              <AlertCircle size={14} className="mt-0.5 shrink-0" />
              <span>{error}</span>
            </div>
          )}

          {/* Step 0: 基本信息 */}
          {show(0) && (
            <StepShell title="基本信息" subtitle="给任务起个一眼能认出用途的名字">
              <Field label="任务名称" required>
                <input type="text" value={form.name} onChange={e => update('name', e.target.value)}
                  placeholder="例如：订单数据每日入湖" autoFocus={!isEdit}
                  className="w-full px-3 py-2 border border-slate-200 rounded-xl text-sm focus:outline-none focus:border-blue-400 focus:ring-2 focus:ring-blue-100 transition" />
              </Field>
              <Field label="任务描述">
                <textarea value={form.description} onChange={e => update('description', e.target.value)}
                  placeholder="任务用途说明（可选）" rows={2}
                  className="w-full px-3 py-2 border border-slate-200 rounded-xl text-sm focus:outline-none focus:border-blue-400 focus:ring-2 focus:ring-blue-100 resize-none transition" />
              </Field>
            </StepShell>
          )}

          {/* Step 1: 选择流水线（已发布且已启用）+ 数据预览 */}
          {show(1) && (
            <StepShell title="选择流水线" subtitle="只列出已发布且已启用的流水线；有字段契约的流水线即使还没产出数据也可挂接" wide>
              {pipelinesLoading ? (
                <div className="text-sm text-slate-400 flex items-center justify-center gap-1.5 py-8">
                  <Loader2 size={14} className="animate-spin" />加载可用流水线...
                </div>
              ) : pipelinesError ? (
                <div className="text-sm text-red-600 p-5 bg-red-50 border border-red-100 rounded-xl text-center space-y-2">
                  <AlertCircle size={22} className="mx-auto text-red-400" />
                  <p>{pipelinesError}</p>
                  <button type="button" onClick={() => window.location.reload()}
                    className="text-xs px-3 py-1.5 border border-red-200 rounded-lg hover:bg-white">重新加载</button>
                </div>
              ) : pipelines.length === 0 ? (
                <div className="text-sm text-slate-500 p-5 bg-slate-50 rounded-xl text-center space-y-2">
                  <Database size={22} className="mx-auto text-slate-300" />
                  <p>还没有「已发布且已启用」的流水线。</p>
                  <p className="text-xs text-slate-400">请先在编辑向导中发布流水线，再回到流水线列表打开启用开关。</p>
                  <button onClick={() => navigate('/data/pipelines')}
                    className="mt-1 inline-flex items-center gap-1 text-xs px-3 py-1.5 border border-blue-200 rounded-lg hover:bg-blue-50 text-blue-600">
                    去流水线 <ArrowRight size={11} />
                  </button>
                </div>
              ) : (
                <div className="space-y-2.5">
                  {pipelines.map(p => (
                    <PipelineCard key={p.id} pipeline={p} active={form.pipeline_id === p.id} onClick={() => onPickPipeline(p.id)} />
                  ))}
                </div>
              )}

              {initialPipelineId && form.pipeline_id === initialPipelineId && (
                <div className="text-xs text-emerald-600 mt-2 text-center">已自动选中刚发布的流水线</div>
              )}

              {/* 成品数据集选择 + 数据预览 */}
              {selectedPipeline && activeCurated && (
                <div className="mt-4 border border-slate-100 rounded-xl overflow-hidden">
                  <div className="px-3.5 py-2.5 bg-slate-50/70 flex items-center gap-2 flex-wrap">
                    <Table2 size={13} className="text-slate-400 shrink-0" />
                    <span className="text-xs text-slate-500">预览成品数据集</span>
                    {curatedList.length > 1 ? (
                      <select value={activeCurated.id} onChange={e => setActiveCuratedId(e.target.value)}
                        className="text-xs px-2 py-1 border border-slate-200 rounded-lg bg-white focus:outline-none focus:border-blue-400">
                        {curatedList.map(c => <option key={c.id} value={c.id}>{c.name}（{c.rowcount} 行）</option>)}
                      </select>
                    ) : (
                      <span className="text-xs font-medium text-slate-700">{activeCurated.name}</span>
                    )}
                    <span className="text-xs text-slate-400 ml-auto">共 {activeCurated.rowcount} 行 · {activeCurated.columns.length} 列</span>
                  </div>
                  <CuratedDataPreview key={activeCurated.id} datasetId={activeCurated.id} totalRows={activeCurated.rowcount} />
                </div>
              )}

              {/* 尚未产出数据但有字段契约：展示契约列，说明首次入湖由任务完成 */}
              {selectedPipeline && !activeCurated && contract && (
                <div className="mt-4 border border-slate-100 rounded-xl p-4 bg-slate-50/50 space-y-2.5">
                  <div className="flex items-center gap-1.5 text-xs text-slate-500">
                    <Database size={13} className="text-slate-400" />
                    该流水线还没有产出过数据——创建任务后，首次执行将按下方字段契约完成首次入湖。
                  </div>
                  <div className="flex flex-wrap gap-1.5">
                    {contract.columns.map(c => (
                      <span key={c.name}
                        className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-lg border text-[11px] font-mono ${
                          splitPk(contract.primary_key).includes(c.name)
                            ? 'border-violet-200 bg-violet-50 text-violet-700'
                            : 'border-slate-200 bg-white text-slate-600'}`}>
                        {splitPk(contract.primary_key).includes(c.name) && <KeyRound size={9} />}
                        {c.name}
                        <span className="text-slate-400 font-sans">{c.type}</span>
                      </span>
                    ))}
                  </div>
                </div>
              )}
            </StepShell>
          )}

          {/* Step 2: 入库方式；主键只读继承流水线发布契约 */}
          {show(2) && (
            <StepShell title="设置入库方式" subtitle="流水线最终产物与资产湖已有数据的合并策略">
              <Field label="入库方式" required>
                <div className="grid grid-cols-2 gap-2">
                  {(Object.keys(WRITE_MODE_META) as WriteMode[]).map(mode => (
                    <ModeCard key={mode} active={form.write_mode === mode}
                      disabled={mode === 'upsert' && !contractPk}
                      onClick={() => update('write_mode', mode)}
                      title={WRITE_MODE_META[mode].label} desc={WRITE_MODE_META[mode].desc} />
                  ))}
                </div>
              </Field>

              <div className={`space-y-3 p-3.5 rounded-xl border ${contractPk ? 'bg-violet-50/40 border-violet-100' : 'bg-slate-50/60 border-slate-100'}`}>
                <div className="flex items-center gap-1.5 text-sm font-medium text-slate-700">
                  <KeyRound size={13} className="text-slate-400" />
                  流水线主键契约
                  <span className="ml-1 text-[11px] px-1.5 py-0.5 rounded-full border bg-violet-50 text-violet-600 border-violet-100">
                    发布时封版 · 任务只读
                  </span>
                </div>
                {contractPk ? (
                  <div className="flex flex-wrap gap-1.5">
                    {splitPk(contractPk).map(col => (
                      <span key={col} className="inline-flex items-center gap-1 px-2 py-1 rounded-lg border border-violet-200 bg-white text-xs font-mono text-violet-700">
                        <KeyRound size={10} />{col}
                      </span>
                    ))}
                  </div>
                ) : (
                  <p className="text-xs text-amber-600 leading-relaxed">
                    当前流水线发布契约未声明主键，因此不能使用「主键合并」。如业务记录需要稳定身份，请返回流水线补齐主键后重新发布。
                  </p>
                )}

                {pkMismatch && (
                  <div className="text-xs text-red-600 bg-red-50 border border-red-100 rounded-lg p-2.5">
                    检测到历史资产主键「{declaredPk}」与流水线契约「{contractPk}」不一致，已阻止创建任务，避免错误合并。
                  </div>
                )}

                {form.write_mode === 'upsert' && contractPk && (
                  <Field label="软删除列（可选）" hint="产物中的逻辑删除标识列；命中的行会打上 __deleted__ 标记而非物理删除">
                    {contractColumns.length > 0 ? (
                      <select value={form.soft_delete_column ?? ''} onChange={e => update('soft_delete_column', e.target.value)}
                        className="w-full px-3 py-2 border border-slate-200 rounded-lg text-sm bg-white focus:outline-none focus:border-blue-400">
                        <option value="">— 不使用 —</option>
                        {contractColumns.map(c => <option key={c.name} value={c.name}>{c.name}</option>)}
                      </select>
                    ) : (
                      <input type="text" value={form.soft_delete_column ?? ''} onChange={e => update('soft_delete_column', e.target.value)}
                        placeholder="例如：is_deleted"
                        className="w-full px-3 py-2 border border-slate-200 rounded-lg text-sm font-mono focus:outline-none focus:border-blue-400" />
                    )}
                  </Field>
                )}
              </div>

              <label className="flex items-start gap-2 text-sm text-slate-700 cursor-pointer">
                <input type="checkbox" checked={form.skip_empty ?? true} onChange={e => update('skip_empty', e.target.checked)} className="mt-0.5 accent-blue-500" />
                <span>
                  空输出保护
                  <span className="block text-xs text-slate-400">流水线本次输出 0 行时跳过入库，防止源端异常导致资产被清空（建议开启）</span>
                </span>
              </label>
            </StepShell>
          )}

          {/* Step 3: 调度 */}
          {show(3) && (
            <StepShell title="调度设置" subtitle="决定任务何时触发">
              <Field label="调度方式" required>
                <div className="grid grid-cols-3 gap-2">
                  <ModeCard active={form.schedule_type === 'MANUAL'} onClick={() => update('schedule_type', 'MANUAL' as PipelineTaskScheduleType)} title="手动触发" desc="仅在任务池手动执行" />
                  <ModeCard active={form.schedule_type === 'CRON'} onClick={() => update('schedule_type', 'CRON' as PipelineTaskScheduleType)} title="Cron 定时" desc="按 Cron 表达式定时执行" />
                  <ModeCard active={form.schedule_type === 'INTERVAL'} onClick={() => update('schedule_type', 'INTERVAL' as PipelineTaskScheduleType)} title="固定间隔" desc="每 N 秒执行一次" />
                </div>
              </Field>

              {form.schedule_type === 'CRON' && (
                <Field label="Cron 表达式" required hint="5 段格式：分 时 日 月 周。如 0 2 * * * 表示每天凌晨 2 点">
                  <input type="text" value={form.cron_expression} onChange={e => update('cron_expression', e.target.value)}
                    placeholder="0 2 * * *"
                    className="w-full px-3 py-2 border border-slate-200 rounded-xl text-sm font-mono focus:outline-none focus:border-blue-400 focus:ring-2 focus:ring-blue-100 transition" />
                </Field>
              )}

              {form.schedule_type === 'INTERVAL' && (
                <Field label="间隔秒数" required hint="最小 10 秒">
                  <input type="number" min={10} value={form.interval_seconds}
                    onChange={e => update('interval_seconds', parseInt(e.target.value) || 0)}
                    className="w-full px-3 py-2 border border-slate-200 rounded-xl text-sm focus:outline-none focus:border-blue-400 focus:ring-2 focus:ring-blue-100 transition" />
                </Field>
              )}

              <label className="flex items-center gap-2 text-sm text-slate-700 cursor-pointer">
                <input type="checkbox" checked={form.enabled} onChange={e => update('enabled', e.target.checked)} className="accent-blue-500" />
                {isEdit ? '启用任务' : '创建后立即启用'}
              </label>
            </StepShell>
          )}

          {/* Step 4: 确认配置 */}
          {!isEdit && step === 4 && (
            <StepShell title="确认配置" subtitle="核对所有设置，确认无误后创建">
              <div className="rounded-xl border border-slate-100 bg-slate-50/60 divide-y divide-slate-100">
                <SummaryRow label="任务名" value={form.name} />
                <SummaryRow label="流水线" value={selectedPipeline ? `${selectedPipeline.name}${selectedPipeline.version ? ` (v${selectedPipeline.version})` : ''}` : '-'} />
                <SummaryRow label="产出资产" value={curatedList.length ? curatedList.map(c => c.name).join('、') : '-'} />
                <SummaryRow label="入库方式" value={WRITE_MODE_META[form.write_mode].label} />
                <SummaryRow label="主键（流水线契约）" value={contractPk || '未声明（无主键模式）'} mono />
                {form.write_mode === 'upsert' && form.soft_delete_column && <SummaryRow label="软删除列" value={form.soft_delete_column} mono />}
                <SummaryRow label="空输出保护" value={form.skip_empty ? '开启' : '关闭'} />
                <SummaryRow label="调度" value={
                  form.schedule_type === 'MANUAL' ? '手动触发'
                    : form.schedule_type === 'CRON' ? `Cron: ${form.cron_expression || '-'}`
                    : `每 ${form.interval_seconds || 0} 秒`
                } />
                <SummaryRow label="创建后状态" value={form.enabled ? '立即启用' : '停用'} />
              </div>

              {/* 预期效果（自然语言） */}
              <div className="mt-4 p-4 rounded-xl bg-gradient-to-br from-blue-50/70 to-violet-50/50 border border-blue-100">
                <div className="flex items-center gap-1.5 text-xs font-medium text-blue-600 mb-1.5">
                  <Sparkles size={13} /> 预期效果
                </div>
                <p className="text-sm text-slate-600 leading-relaxed">{buildEffectText()}</p>
              </div>
            </StepShell>
          )}
        </div>

        {/* 底部 */}
        <div className="flex items-center justify-between px-6 py-3.5 border-t border-slate-100 bg-slate-50/60">
          <div>
            {step > 0 && !isEdit && (
              <button onClick={() => { setStep(s => s - 1); setError('') }}
                className="inline-flex items-center gap-1 px-3 py-1.5 text-sm text-slate-500 hover:text-slate-800">
                <ChevronLeft size={14} /> 上一步
              </button>
            )}
          </div>
          <div className="flex gap-2">
            <button onClick={onClose} className="px-4 py-1.5 text-sm text-slate-500 hover:bg-slate-100 rounded-lg transition">取消</button>
            {!isEdit && step < STEPS.length - 1 ? (
              <button onClick={handleNext}
                className="inline-flex items-center gap-1 px-4 py-1.5 text-sm bg-blue-500 text-white rounded-lg hover:bg-blue-600 transition shadow-sm shadow-blue-500/30">
                下一步 <ChevronRight size={14} />
              </button>
            ) : (
              <button onClick={handleSubmit} disabled={submitting}
                className="inline-flex items-center gap-1.5 px-4 py-1.5 text-sm bg-blue-500 text-white rounded-lg hover:bg-blue-600 disabled:opacity-50 transition shadow-sm shadow-blue-500/30">
                {submitting && <Loader2 size={13} className="animate-spin" />}
                {isEdit ? '保存修改' : '创建任务'}
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

/** 每个步骤的居中外壳 */
function StepShell({ title, subtitle, wide, children }: {
  title: string; subtitle?: string; wide?: boolean; children: React.ReactNode
}) {
  return (
    <div className={`mx-auto w-full ${wide ? 'max-w-2xl' : 'max-w-lg'}`}>
      <div className="text-center mb-4">
        <h4 className="text-base font-semibold text-slate-800">{title}</h4>
        {subtitle && <p className="text-xs text-slate-400 mt-1">{subtitle}</p>}
      </div>
      <div className="space-y-4">{children}</div>
    </div>
  )
}

function PipelineCard({ pipeline, active, onClick }: {
  pipeline: SelectablePipeline; active: boolean; onClick: () => void
}) {
  return (
    <button type="button" onClick={onClick}
      className={`w-full text-left p-3.5 rounded-xl border-2 transition-all
        ${active ? 'border-blue-400 bg-blue-50/40 shadow-sm' : 'border-slate-200 bg-white hover:border-slate-300'}`}>
      <div className="flex items-center gap-2">
        <GitBranch size={14} className={active ? 'text-blue-500' : 'text-slate-400'} />
        <span className="text-sm font-medium text-slate-800 truncate">{pipeline.name}</span>
        {pipeline.version ? <span className="text-[11px] px-1.5 py-0.5 rounded-full bg-slate-100 text-slate-500">v{pipeline.version}</span> : null}
        {active && <CheckCircle2 size={15} className="text-blue-500 ml-auto shrink-0" />}
      </div>
      <div className="flex items-center gap-2 mt-1.5 text-[11px] text-slate-400 flex-wrap">
        {pipeline.domain && <span className="px-1.5 py-0.5 rounded-full bg-slate-50 border border-slate-100">{pipeline.domain}</span>}
        {pipeline.contract && (
          <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full bg-violet-50 border border-violet-100 text-violet-600">
            <KeyRound size={9} /> 字段契约{pipeline.contract.primary_key ? `（主键 ${pipeline.contract.primary_key}）` : ''}
          </span>
        )}
        {pipeline.curated_datasets.length > 0 ? (
          <>
            <span className="inline-flex items-center gap-1"><Database size={10} />{pipeline.curated_datasets.length} 个成品集</span>
            <span className="text-emerald-500">已产出 {pipeline.total_rows.toLocaleString()} 行</span>
          </>
        ) : (
          <span className="text-slate-400">尚未产出数据 · 首次入湖由任务完成</span>
        )}
      </div>
    </button>
  )
}

/** 成品数据集分页预览面板 */
function CuratedDataPreview({ datasetId, totalRows }: { datasetId: string; totalRows: number }) {
  const [open, setOpen] = useState(false)
  const [data, setData] = useState<CuratedPreview | null>(null)
  const [loading, setLoading] = useState(false)
  const [page, setPage] = useState(1)
  const pageSize = 10

  useEffect(() => { setOpen(false); setData(null); setPage(1) }, [datasetId])

  useEffect(() => {
    if (!open) return
    let alive = true
    setLoading(true)
    pipelineTasksApi.previewCurated(datasetId, page, pageSize)
      .then(res => { if (alive) setData(res) })
      .catch(() => { if (alive) setData(null) })
      .finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [datasetId, page, open])

  const total = data?.total_rows ?? totalRows
  const totalPages = Math.max(1, Math.ceil(total / pageSize))
  const columns = data?.columns ?? []

  if (!open) {
    return (
      <button type="button" onClick={() => setOpen(true)}
        className="w-full py-2.5 text-xs text-blue-600 hover:bg-blue-50/50 transition flex items-center justify-center gap-1.5">
        <Table2 size={12} /> 查看实际数据（分页预览）
      </button>
    )
  }

  return (
    <div className="bg-white">
      <div className="overflow-x-auto max-h-64 overflow-y-auto">
        {loading ? (
          <div className="py-10 text-center text-xs text-slate-400 flex items-center justify-center gap-1.5">
            <Loader2 size={13} className="animate-spin" /> 加载数据...
          </div>
        ) : !data || data.rows.length === 0 ? (
          <div className="py-10 text-center text-xs text-slate-400">该页没有数据</div>
        ) : (
          <table className="w-full text-xs border-collapse">
            <thead className="sticky top-0 bg-slate-50 z-10">
              <tr>
                <th className="px-2 py-1.5 text-left text-slate-400 font-medium border-b border-slate-100 whitespace-nowrap">#</th>
                {columns.map(c => (
                  <th key={c} className="px-2.5 py-1.5 text-left text-slate-500 font-medium border-b border-slate-100 whitespace-nowrap font-mono">{c}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.rows.map((row, ri) => (
                <tr key={ri} className="hover:bg-slate-50/60">
                  <td className="px-2 py-1.5 text-slate-300 border-b border-slate-50 whitespace-nowrap tabular-nums">
                    {(page - 1) * pageSize + ri + 1}
                  </td>
                  {columns.map(c => (
                    <td key={c} className="px-2.5 py-1.5 text-slate-600 border-b border-slate-50 whitespace-nowrap max-w-[220px] truncate" title={fmtCell(row[c])}>
                      {fmtCell(row[c])}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      {/* 分页条 */}
      <div className="flex items-center justify-between px-3 py-2 border-t border-slate-100 bg-slate-50/50 text-xs text-slate-400">
        <span>共 {total.toLocaleString()} 行 · 第 {page}/{totalPages} 页</span>
        <div className="flex items-center gap-1">
          <button disabled={page <= 1 || loading} onClick={() => setPage(p => Math.max(1, p - 1))}
            className="p-1 rounded hover:bg-white disabled:opacity-40 disabled:cursor-not-allowed"><ChevronLeft size={14} /></button>
          <button disabled={page >= totalPages || loading} onClick={() => setPage(p => Math.min(totalPages, p + 1))}
            className="p-1 rounded hover:bg-white disabled:opacity-40 disabled:cursor-not-allowed"><ChevronRight size={14} /></button>
        </div>
      </div>
    </div>
  )
}

function fmtCell(v: unknown): string {
  if (v === null || v === undefined) return ''
  if (typeof v === 'object') { try { return JSON.stringify(v) } catch { return String(v) } }
  return String(v)
}

function Field({ label, required, hint, children }: {
  label: string; required?: boolean; hint?: string; children: React.ReactNode
}) {
  return (
    <div>
      <label className="block text-sm font-medium text-slate-700 mb-1">
        {label}{required && <span className="text-red-500 ml-0.5">*</span>}
      </label>
      {children}
      {hint && <div className="text-xs text-slate-400 mt-1">{hint}</div>}
    </div>
  )
}

function ModeCard({ active, onClick, title, desc, disabled = false }: {
  active: boolean; onClick: () => void; title: string; desc: string; disabled?: boolean
}) {
  return (
    <button type="button" onClick={onClick} disabled={disabled}
      className={`text-left p-3 rounded-xl border-2 transition-all
        ${disabled ? 'border-slate-100 bg-slate-50 opacity-55 cursor-not-allowed'
          : active ? 'border-blue-400 bg-blue-50/40' : 'border-slate-200 hover:border-slate-300'}`}>
      <div className={`text-sm font-medium ${active ? 'text-blue-600' : 'text-slate-800'}`}>{title}</div>
      <div className="text-xs text-slate-500 mt-0.5">{disabled ? `${desc}（流水线未声明主键）` : desc}</div>
    </button>
  )
}

function SummaryRow({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="flex justify-between py-2 px-3.5 text-sm">
      <span className="text-slate-500">{label}</span>
      <span className={`text-slate-800 font-medium text-right max-w-[62%] truncate ${mono ? 'font-mono' : ''}`}>{value || '-'}</span>
    </div>
  )
}
