import { useState, useEffect } from 'react'
import { X, Loader2, AlertCircle, CheckCircle2, GitBranch, ArrowRight } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { pipelineTasksApi, WRITE_MODE_META, type PipelineTask, type PipelineTaskPayload, type WriteMode, type PipelineTaskScheduleType } from '@/api/v2/pipeline-tasks'
import { apiClientV2 } from '@/api/client'

interface Props {
  initialTask: PipelineTask | null
  /** 从流水线发布页跳转而来时预选的流水线 ID */
  initialPipelineId?: string | null
  onClose: () => void
  onSaved: () => void
}

const STEPS = ['基本信息', '流水线与入库方式', '调度设置']

export default function TaskFormModal({ initialTask, initialPipelineId, onClose, onSaved }: Props) {
  const navigate = useNavigate()
  const isEdit = !!initialTask
  const [step, setStep] = useState(0)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')

  const [pipelines, setPipelines] = useState<Array<{ id: string; name: string; version?: number; domain?: string }>>([])
  const [pipelinesLoading, setPipelinesLoading] = useState(true)

  const [form, setForm] = useState<PipelineTaskPayload>(() => initialTask ? {
    name: initialTask.name,
    description: initialTask.description,
    pipeline_id: initialTask.pipeline_id,
    write_mode: initialTask.write_mode,
    primary_key: initialTask.primary_key,
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
    primary_key: '',
    soft_delete_column: '',
    skip_empty: true,
    schedule_type: 'MANUAL',
    cron_expression: '',
    interval_seconds: 3600,
    enabled: true,
  })

  useEffect(() => {
    // 任务只能调度已发布的流水线
    setPipelinesLoading(true)
    apiClientV2.get('/pipelines', { params: { status: 'published' } }).then((res: any) => {
      const arr = Array.isArray(res) ? res : (res.items ?? [])
      setPipelines(arr.map((p: any) => ({ id: p.id, name: p.name, version: p.version, domain: p.domain })))
    }).catch(() => setPipelines([]))
      .finally(() => setPipelinesLoading(false))
  }, [])

  const update = <K extends keyof PipelineTaskPayload>(key: K, val: PipelineTaskPayload[K]) => {
    setForm(prev => ({ ...prev, [key]: val }))
  }

  const validateStep = (s: number): string | null => {
    if (s === 0) {
      if (!form.name.trim()) return '请填写任务名称'
    }
    if (s === 1) {
      if (!form.pipeline_id) return '请选择要调度的流水线'
      if (form.write_mode === 'upsert' && !(form.primary_key ?? '').trim()) return '「主键合并」入库方式必须指定主键列'
    }
    if (s === 2) {
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
    for (const s of [0, 1, 2]) {
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

  const selectedPipeline = pipelines.find(p => p.id === form.pipeline_id)

  return (
    <div className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-4" onClick={onClose}>
      <div className="bg-white rounded-xl shadow-2xl w-full max-w-2xl max-h-[90vh] flex flex-col" onClick={e => e.stopPropagation()}>
        {/* 头部 */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-200">
          <div>
            <h3 className="text-lg font-bold text-gray-900">
              {isEdit ? '编辑调度任务' : '新建调度任务'}
            </h3>
            <p className="text-xs text-gray-400 mt-0.5">任务负责按计划触发已发布的流水线，并把最终产物按入库方式写进数据资产湖</p>
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600">
            <X size={18} />
          </button>
        </div>

        {/* 步骤指示器 */}
        {!isEdit && (
          <div className="px-5 py-3 border-b border-gray-100 flex items-center gap-2">
            {STEPS.map((label, i) => (
              <div key={i} className="flex items-center gap-1.5">
                <div className={`w-6 h-6 rounded-full flex items-center justify-center text-xs font-medium
                  ${i < step ? 'bg-green-100 text-green-600' : i === step ? 'bg-[var(--color-nav-bg)] text-white' : 'bg-gray-100 text-gray-400'}`}>
                  {i < step ? <CheckCircle2 size={12} /> : i + 1}
                </div>
                <span className={`text-xs ${i === step ? 'text-gray-900 font-medium' : 'text-gray-400'}`}>{label}</span>
                {i < STEPS.length - 1 && <div className={`w-8 h-px ${i < step ? 'bg-green-300' : 'bg-gray-200'}`} />}
              </div>
            ))}
          </div>
        )}

        {/* 内容 */}
        <div className="flex-1 overflow-y-auto p-5 space-y-4">
          {error && (
            <div className="flex items-start gap-2 p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-600">
              <AlertCircle size={14} className="mt-0.5 shrink-0" />
              <span>{error}</span>
            </div>
          )}

          {/* Step 0: 基本信息 */}
          {(step === 0 || isEdit) && (
            <div className="space-y-4">
              <Field label="任务名称" required>
                <input type="text" value={form.name} onChange={e => update('name', e.target.value)}
                  placeholder="例如：订单数据每日入湖" autoFocus={!isEdit}
                  className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm focus:outline-none focus:border-[var(--color-nav-bg)]" />
              </Field>
              <Field label="任务描述">
                <textarea value={form.description} onChange={e => update('description', e.target.value)}
                  placeholder="任务用途说明（可选）" rows={2}
                  className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm focus:outline-none focus:border-[var(--color-nav-bg)] resize-none" />
              </Field>
            </div>
          )}

          {/* Step 1: 关联流水线 + 入库方式 */}
          {(step === 1 || isEdit) && (!isEdit ? step === 1 : true) && (
            <div className="space-y-4">
              <Field label="关联流水线" required hint="仅显示已发布的流水线；流水线负责数据的采集与加工，任务只负责调度">
                {pipelinesLoading ? (
                  <div className="text-sm text-gray-400 flex items-center gap-1"><Loader2 size={12} className="animate-spin" />加载中...</div>
                ) : pipelines.length === 0 ? (
                  <div className="text-sm text-gray-500 p-3 bg-gray-50 rounded-lg flex items-center justify-between gap-2">
                    <span>暂无已发布的流水线，请先到流水线画布完成编排并发布</span>
                    <button
                      onClick={() => navigate('/data/pipelines')}
                      className="flex items-center gap-1 text-xs px-2.5 py-1.5 border rounded-lg hover:bg-white text-[var(--color-nav-bg)] shrink-0"
                    >
                      去流水线 <ArrowRight size={11} />
                    </button>
                  </div>
                ) : (
                  <select value={form.pipeline_id} onChange={e => update('pipeline_id', e.target.value)}
                    className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm bg-white focus:outline-none focus:border-[var(--color-nav-bg)]">
                    <option value="">-- 请选择流水线 --</option>
                    {pipelines.map(p => (
                      <option key={p.id} value={p.id}>
                        {p.name}{p.version ? ` (v${p.version})` : ''}{p.domain ? ` · ${p.domain}` : ''}
                      </option>
                    ))}
                  </select>
                )}
                {initialPipelineId && form.pipeline_id === initialPipelineId && (
                  <div className="text-xs text-green-600 mt-1">已自动选中刚发布的流水线</div>
                )}
                {selectedPipeline && (
                  <div className="mt-2 p-2.5 bg-blue-50/50 border border-blue-100 rounded-lg text-xs text-blue-700 flex items-center gap-1.5">
                    <GitBranch size={12} className="shrink-0" />
                    任务触发时执行「{selectedPipeline.name}」，其<b>最终产物</b>将按下方入库方式写进资产湖的成品数据集
                  </div>
                )}
              </Field>

              <Field label="入库方式" required hint="流水线最终产物与资产湖已有数据的合并策略">
                <div className="grid grid-cols-2 gap-2">
                  {(Object.keys(WRITE_MODE_META) as WriteMode[]).map(mode => (
                    <ModeCard
                      key={mode}
                      active={form.write_mode === mode}
                      onClick={() => update('write_mode', mode)}
                      title={WRITE_MODE_META[mode].label}
                      desc={WRITE_MODE_META[mode].desc}
                    />
                  ))}
                </div>
              </Field>

              {form.write_mode === 'upsert' && (
                <div className="space-y-3 p-3 bg-purple-50/40 border border-purple-100 rounded-lg">
                  <Field label="主键列" required hint="按这些列判断同一条记录；多列用逗号分隔，如：order_id 或 order_id,line_no">
                    <input type="text" value={form.primary_key ?? ''} onChange={e => update('primary_key', e.target.value)}
                      placeholder="例如：order_id"
                      className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm font-mono focus:outline-none focus:border-[var(--color-nav-bg)]" />
                  </Field>
                  <Field label="软删除列（可选）" hint="产物中的逻辑删除标识列（如 is_deleted）；命中的行会打上 __deleted__ 标记而非物理删除">
                    <input type="text" value={form.soft_delete_column ?? ''} onChange={e => update('soft_delete_column', e.target.value)}
                      placeholder="例如：is_deleted"
                      className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm font-mono focus:outline-none focus:border-[var(--color-nav-bg)]" />
                  </Field>
                </div>
              )}

              <label className="flex items-start gap-2 text-sm text-gray-700 cursor-pointer">
                <input type="checkbox" checked={form.skip_empty ?? true} onChange={e => update('skip_empty', e.target.checked)} className="mt-0.5" />
                <span>
                  空输出保护
                  <span className="block text-xs text-gray-400">流水线本次输出 0 行时跳过入库，防止源端异常导致资产被清空（建议开启）</span>
                </span>
              </label>
            </div>
          )}

          {/* Step 2: 调度 */}
          {(step === 2 || isEdit) && (!isEdit ? step === 2 : true) && (
            <div className="space-y-4">
              <Field label="调度方式" required>
                <div className="grid grid-cols-3 gap-2">
                  <ModeCard
                    active={form.schedule_type === 'MANUAL'}
                    onClick={() => update('schedule_type', 'MANUAL' as PipelineTaskScheduleType)}
                    title="手动触发"
                    desc="仅在任务池手动执行"
                  />
                  <ModeCard
                    active={form.schedule_type === 'CRON'}
                    onClick={() => update('schedule_type', 'CRON' as PipelineTaskScheduleType)}
                    title="Cron 定时"
                    desc="按 Cron 表达式定时执行"
                  />
                  <ModeCard
                    active={form.schedule_type === 'INTERVAL'}
                    onClick={() => update('schedule_type', 'INTERVAL' as PipelineTaskScheduleType)}
                    title="固定间隔"
                    desc="每 N 秒执行一次"
                  />
                </div>
              </Field>

              {form.schedule_type === 'CRON' && (
                <Field label="Cron 表达式" required hint="5 段格式：分 时 日 月 周。如 0 2 * * * 表示每天凌晨 2 点">
                  <input type="text" value={form.cron_expression} onChange={e => update('cron_expression', e.target.value)}
                    placeholder="0 2 * * *"
                    className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm font-mono focus:outline-none focus:border-[var(--color-nav-bg)]" />
                </Field>
              )}

              {form.schedule_type === 'INTERVAL' && (
                <Field label="间隔秒数" required hint="最小 10 秒">
                  <input type="number" min={10} value={form.interval_seconds}
                    onChange={e => update('interval_seconds', parseInt(e.target.value) || 0)}
                    className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm focus:outline-none focus:border-[var(--color-nav-bg)]" />
                </Field>
              )}

              <label className="flex items-center gap-2 text-sm text-gray-700 cursor-pointer">
                <input type="checkbox" checked={form.enabled} onChange={e => update('enabled', e.target.checked)} />
                {isEdit ? '启用任务' : '创建后立即启用'}
              </label>

              {/* 确认摘要 */}
              {!isEdit && (
                <div className="border-t pt-3 space-y-1.5">
                  <p className="text-xs font-medium text-gray-500 mb-1">确认配置</p>
                  <SummaryRow label="任务名" value={form.name} />
                  <SummaryRow label="流水线" value={selectedPipeline ? `${selectedPipeline.name}${selectedPipeline.version ? ` (v${selectedPipeline.version})` : ''}` : '-'} />
                  <SummaryRow label="入库方式" value={WRITE_MODE_META[form.write_mode].label + (form.write_mode === 'upsert' && form.primary_key ? ` · 主键 ${form.primary_key}` : '')} />
                  <SummaryRow label="空输出保护" value={form.skip_empty ? '开启' : '关闭'} />
                  <SummaryRow label="调度" value={
                    form.schedule_type === 'MANUAL' ? '手动触发' :
                    form.schedule_type === 'CRON' ? `Cron: ${form.cron_expression || '-'}` : `每 ${form.interval_seconds || 0} 秒`
                  } />
                </div>
              )}
            </div>
          )}
        </div>

        {/* 底部 */}
        <div className="flex items-center justify-between px-5 py-3 border-t border-gray-200 bg-gray-50/60">
          <div>
            {step > 0 && !isEdit && (
              <button onClick={() => { setStep(s => s - 1); setError('') }}
                className="px-4 py-1.5 text-sm text-gray-600 hover:text-gray-900">上一步</button>
            )}
          </div>
          <div className="flex gap-2">
            <button onClick={onClose} className="px-4 py-1.5 text-sm text-gray-600 hover:bg-gray-100 rounded-lg">
              取消
            </button>
            {!isEdit && step < STEPS.length - 1 ? (
              <button onClick={handleNext}
                className="px-4 py-1.5 text-sm bg-[var(--color-nav-bg)] text-white rounded-lg hover:opacity-90">
                下一步
              </button>
            ) : (
              <button onClick={handleSubmit} disabled={submitting}
                className="px-4 py-1.5 text-sm bg-[var(--color-nav-bg)] text-white rounded-lg hover:opacity-90 disabled:opacity-50 flex items-center gap-1.5">
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

function Field({ label, required, hint, children }: {
  label: string; required?: boolean; hint?: string; children: React.ReactNode
}) {
  return (
    <div>
      <label className="block text-sm font-medium text-gray-700 mb-1">
        {label}{required && <span className="text-red-500 ml-0.5">*</span>}
      </label>
      {children}
      {hint && <div className="text-xs text-gray-400 mt-1">{hint}</div>}
    </div>
  )
}

function ModeCard({ active, onClick, title, desc }: {
  active: boolean; onClick: () => void; title: string; desc: string
}) {
  return (
    <button type="button" onClick={onClick}
      className={`text-left p-3 rounded-lg border-2 transition-all
        ${active ? 'border-[var(--color-nav-bg)] bg-blue-50/40' : 'border-gray-200 hover:border-gray-300'}`}>
      <div className={`text-sm font-medium ${active ? 'text-[var(--color-nav-bg)]' : 'text-gray-900'}`}>{title}</div>
      <div className="text-xs text-gray-500 mt-0.5">{desc}</div>
    </button>
  )
}

function SummaryRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between py-1 text-sm">
      <span className="text-gray-500">{label}</span>
      <span className="text-gray-900 font-medium text-right max-w-[60%] truncate">{value || '-'}</span>
    </div>
  )
}
