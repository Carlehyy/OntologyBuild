import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  X, CheckCircle2, XCircle, Loader2, Clock, ChevronDown, ChevronUp, Table2,
  ArrowRight, ShieldCheck, GitBranch, Settings2, Plus, Pencil, Minus, Database,
} from 'lucide-react'
import {
  pipelineTasksApi, WRITE_MODE_META,
  type PipelineTask, type PipelineTaskRun, type WriteMode, type RunAudit,
  type RunAuditOutput, type LakeImpactDetail,
} from '@/api/v2/pipeline-tasks'

const TRIGGER_LABEL: Record<string, string> = { manual: '手动', scheduled: '定时' }

const STATUS_META: Record<string, { icon: React.ReactNode; color: string; label: string }> = {
  pending: { icon: <Clock size={13} />, color: 'text-slate-500 bg-slate-100', label: '排队中' },
  running: { icon: <Loader2 size={13} className="animate-spin" />, color: 'text-blue-600 bg-blue-50', label: '执行中' },
  success: { icon: <CheckCircle2 size={13} />, color: 'text-emerald-600 bg-emerald-50', label: '成功' },
  failed:  { icon: <XCircle size={13} />, color: 'text-rose-600 bg-rose-50', label: '失败' },
}

function formatDate(iso: string | null): string {
  if (!iso) return '-'
  try { return new Date(iso).toLocaleString('zh-CN') } catch { return iso }
}

function formatDuration(start: string | null, end: string | null): string {
  if (!start || !end) return '-'
  try {
    const ms = new Date(end).getTime() - new Date(start).getTime()
    if (ms < 1000) return `${ms}ms`
    if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`
    return `${Math.floor(ms / 60000)}m${Math.floor((ms % 60000) / 1000)}s`
  } catch { return '-' }
}

export default function HistoryDrawer({ task, onClose }: { task: PipelineTask; onClose: () => void }) {
  const [items, setItems] = useState<PipelineTaskRun[]>([])
  const [loading, setLoading] = useState(true)
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [audits, setAudits] = useState<Record<string, RunAudit | 'loading' | 'error'>>({})

  useEffect(() => {
    setLoading(true)
    pipelineTasksApi.histories(task.id, 1, 50)
      .then(res => setItems(res.items))
      .catch(err => console.error('加载历史失败', err))
      .finally(() => setLoading(false))
  }, [task.id])

  const toggle = (runId: string) => {
    setExpanded(prev => {
      const n = new Set(prev)
      if (n.has(runId)) { n.delete(runId); return n }
      n.add(runId)
      if (!audits[runId]) {
        setAudits(a => ({ ...a, [runId]: 'loading' }))
        pipelineTasksApi.runAudit(task.id, runId)
          .then(res => setAudits(a => ({ ...a, [runId]: res })))
          .catch(() => setAudits(a => ({ ...a, [runId]: 'error' })))
      }
      return n
    })
  }

  return (
    <div className="fixed inset-0 bg-slate-900/40 backdrop-blur-sm z-50 flex justify-end" onClick={onClose}>
      <div className="w-full max-w-2xl bg-white h-full flex flex-col shadow-2xl" onClick={e => e.stopPropagation()}>
        <div className="px-5 py-4 border-b border-slate-200 shrink-0">
          <div className="flex items-center justify-between">
            <h3 className="text-base font-semibold text-slate-800">执行记录与审计</h3>
            <button onClick={onClose} className="text-slate-400 hover:text-slate-600"><X size={18} /></button>
          </div>
          <p className="text-xs text-slate-400 mt-0.5 truncate">
            {task.name} · 流水线「{task.pipeline_name}」 · 每次执行可逐条追溯配置、产物与资产湖影响
          </p>
        </div>

        <div className="flex-1 overflow-y-auto">
          {loading ? (
            <div className="py-20 text-center text-slate-400 text-sm flex items-center justify-center gap-1">
              <Loader2 size={14} className="animate-spin" />加载中...
            </div>
          ) : items.length === 0 ? (
            <div className="py-20 text-center text-slate-400 text-sm">暂无执行记录</div>
          ) : (
            <div className="divide-y divide-slate-100">
              {items.map(h => {
                const sm = STATUS_META[h.status] || STATUS_META.running
                const isOpen = expanded.has(h.id)
                const skipped = (h.skipped_outputs?.length ?? 0) > 0
                const imp = h.lake_impact
                return (
                  <div key={h.id} className="px-5 py-3">
                    <div className="flex items-start justify-between gap-2 cursor-pointer" onClick={() => toggle(h.id)}>
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 flex-wrap">
                          <span className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-xs ${sm.color}`}>
                            {sm.icon}{sm.label}
                          </span>
                          <span className="text-xs text-slate-400">{TRIGGER_LABEL[h.trigger_type] || h.trigger_type}</span>
                          {skipped && (
                            <span className="inline-flex items-center gap-0.5 text-[10px] text-amber-600 bg-amber-50 px-1.5 py-0.5 rounded" title="流水线输出 0 行，空输出保护已跳过入库">
                              <ShieldCheck size={9} /> 已跳过入库
                            </span>
                          )}
                          {imp && (
                            <span className="inline-flex items-center gap-1.5 text-[10.5px]">
                              {imp.added > 0 && <span className="text-emerald-600">+{imp.added}</span>}
                              {imp.updated > 0 && <span className="text-amber-600">~{imp.updated}</span>}
                              {imp.deleted > 0 && <span className="text-rose-600">-{imp.deleted}</span>}
                            </span>
                          )}
                        </div>
                        <div className="text-xs text-slate-500 mt-1 flex items-center gap-1">
                          <Clock size={11} />{formatDate(h.started_at)}
                          {h.status === 'success' && !skipped && (
                            <span className="text-slate-400">· 输出 {h.rows_out} 行{h.lake_rows != null ? ` · 湖内共 ${h.lake_rows} 行` : ''}</span>
                          )}
                        </div>
                      </div>
                      {isOpen ? <ChevronUp size={14} className="text-slate-400 mt-1" /> : <ChevronDown size={14} className="text-slate-400 mt-1" />}
                    </div>

                    {isOpen && (
                      <div className="mt-3">
                        {audits[h.id] === 'loading' || audits[h.id] === undefined ? (
                          <div className="py-6 text-center text-slate-400 text-xs flex items-center justify-center gap-1.5">
                            <Loader2 size={13} className="animate-spin" /> 加载审计明细...
                          </div>
                        ) : audits[h.id] === 'error' ? (
                          <div className="py-6 text-center text-rose-500 text-xs">审计明细加载失败</div>
                        ) : (
                          <RunAuditView audit={audits[h.id] as RunAudit} run={h} />
                        )}
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function RunAuditView({ audit, run }: { audit: RunAudit; run: PipelineTaskRun }) {
  const navigate = useNavigate()
  const cfg = audit.config_snapshot
  const wmLabel = (m?: string) => (m ? WRITE_MODE_META[m as WriteMode]?.label || m : '-')
  return (
    <div className="space-y-3 text-xs bg-slate-50/70 rounded-xl p-3">
      {/* 执行信息 */}
      <Section title="执行信息" icon={<Clock size={12} />}>
        <KV label="开始时间" value={formatDate(audit.started_at)} />
        {audit.finished_at && <KV label="结束时间" value={formatDate(audit.finished_at)} />}
        <KV label="执行耗时" value={formatDuration(audit.started_at, audit.finished_at)} />
        <KV label="触发方式" value={TRIGGER_LABEL[audit.trigger_type] || audit.trigger_type} />
        <KV label="流水线输入" value={`${audit.rows_in} 行`} />
        <KV label="流水线产物" value={`${audit.rows_out} 行`} />
      </Section>

      {/* 调用的流水线 */}
      <Section title="调用的流水线" icon={<GitBranch size={12} />}>
        <div className="flex items-center justify-between">
          <div className="text-slate-700 font-medium">
            {audit.pipeline.name}{audit.pipeline.version ? ` (v${audit.pipeline.version})` : ''}
          </div>
          <span className="text-[10.5px] text-slate-400">{audit.pipeline.domain || '通用'} · {audit.pipeline.status}</span>
        </div>
      </Section>

      {/* 配置快照 */}
      {cfg && (
        <Section title="执行时配置快照" icon={<Settings2 size={12} />}>
          <KV label="入库方式" value={wmLabel(cfg.write_mode)} />
          {cfg.primary_key ? <KV label="主键" value={cfg.primary_key} mono /> : null}
          {cfg.soft_delete_column ? <KV label="软删除列" value={cfg.soft_delete_column} mono /> : null}
          <KV label="空输出保护" value={cfg.skip_empty ? '开启' : '关闭'} />
          <KV label="调度方式" value={
            cfg.schedule_type === 'MANUAL' ? '手动触发'
              : cfg.schedule_type === 'CRON' ? `Cron: ${cfg.cron_expression || '-'}`
              : `每 ${cfg.interval_seconds || 0} 秒`
          } />
        </Section>
      )}

      {/* 每个成品数据集：产物 + 资产湖影响 */}
      {audit.outputs.map((o, i) => (
        <OutputAudit key={o.curated_dataset_id || i} out={o}
          onOpenLake={() => navigate('/data/structured?tab=curated')} />
      ))}

      {audit.error_message && (
        <div>
          <div className="text-slate-500 mb-0.5 font-medium">错误信息</div>
          <div className="text-rose-600 bg-rose-50 rounded-lg p-2 font-mono text-[11px] whitespace-pre-wrap break-all">
            {audit.error_message}
          </div>
        </div>
      )}
      {run.status === 'success' && audit.outputs.length === 0 && !audit.error_message && (
        <div className="text-slate-400 text-center py-2">本次未产生入湖产物</div>
      )}
    </div>
  )
}

function OutputAudit({ out, onOpenLake }: { out: RunAuditOutput; onOpenLake: () => void }) {
  const [tab, setTab] = useState<'' | 'output' | 'added' | 'updated' | 'deleted'>('')
  const im = out.lake_impact
  const toggleTab = (t: typeof tab) => setTab(cur => (cur === t ? '' : t))

  return (
    <div className="rounded-lg border border-slate-200 bg-white overflow-hidden">
      <div className="px-3 py-2 border-b border-slate-100 flex items-center justify-between gap-2 bg-slate-50/60">
        <div className="flex items-center gap-1.5 min-w-0">
          <Database size={12} className="text-slate-400 shrink-0" />
          <span className="text-[12px] font-medium text-slate-700 truncate">{out.curated_dataset_name || '成品数据集'}</span>
          {out.table_name && <span className="text-[10px] text-slate-400">/{out.table_name}</span>}
        </div>
        <button onClick={onOpenLake} className="text-[10.5px] text-blue-600 hover:underline flex items-center gap-0.5 shrink-0">
          资产湖 <ArrowRight size={10} />
        </button>
      </div>

      <div className="px-3 py-2.5 space-y-2.5">
        {/* 流水线输出 */}
        <div>
          <button onClick={() => toggleTab('output')}
            className="w-full flex items-center justify-between text-[11.5px] text-slate-600 hover:text-slate-800">
            <span className="flex items-center gap-1.5"><Table2 size={11} className="text-slate-400" /> 流水线输出 <b className="text-slate-800">{out.rows_out ?? 0}</b> 行</span>
            {out.output_sample.length > 0 && (tab === 'output' ? <ChevronUp size={12} /> : <ChevronDown size={12} />)}
          </button>
          {tab === 'output' && <RowTable rows={out.output_sample} columns={out.output_columns} />}
        </div>

        {/* 资产湖影响 */}
        {im ? (
          <div className="border-t border-slate-100 pt-2 space-y-1.5">
            <div className="flex items-center gap-1.5 text-[11px] text-slate-500">
              资产湖影响
              {im.keyed_by ? <span className="text-[10px] text-slate-400">（按主键 {im.keyed_by.join(',')} 识别）</span>
                : <span className="text-[10px] text-slate-400">（按整行内容比对）</span>}
            </div>
            <div className="grid grid-cols-3 gap-1.5">
              <ImpactChip tone="emerald" icon={<Plus size={11} />} label="新增" count={im.added_count}
                active={tab === 'added'} onClick={() => im.added_count && toggleTab('added')} />
              <ImpactChip tone="amber" icon={<Pencil size={10} />} label="更新" count={im.updated_count}
                active={tab === 'updated'} onClick={() => im.updated_count && toggleTab('updated')} />
              <ImpactChip tone="rose" icon={<Minus size={11} />} label="删除" count={im.deleted_count}
                active={tab === 'deleted'} onClick={() => im.deleted_count && toggleTab('deleted')} />
            </div>
            <div className="text-[10px] text-slate-400">
              入库前 {im.total_before} 行 → 入库后 {im.total_after} 行（未变 {im.unchanged_count} 行）
            </div>
            {tab === 'added' && <RowTable rows={im.added_sample} columns={sampleCols(im.added_sample, out.output_columns)} />}
            {tab === 'deleted' && <RowTable rows={im.deleted_sample} columns={sampleCols(im.deleted_sample, out.output_columns)} />}
            {tab === 'updated' && <UpdatedTable pairs={im.updated_sample} pk={im.keyed_by} />}
            {im.sample_truncated && tab && (
              <div className="text-[10px] text-slate-400 italic">仅展示前若干条样本，完整明细以资产湖版本为准</div>
            )}
          </div>
        ) : (
          <div className="border-t border-slate-100 pt-2 text-[10.5px] text-slate-400">本次为手动画布运行，无入库影响记录</div>
        )}
      </div>
    </div>
  )
}

function sampleCols(rows: Array<Record<string, unknown>>, fallback: string[]): string[] {
  if (fallback && fallback.length) return fallback
  const seen: string[] = []
  for (const r of rows) for (const k of Object.keys(r)) if (!seen.includes(k)) seen.push(k)
  return seen
}

function fmt(v: unknown): string {
  if (v === null || v === undefined) return ''
  if (typeof v === 'object') { try { return JSON.stringify(v) } catch { return String(v) } }
  return String(v)
}

function RowTable({ rows, columns }: { rows: Array<Record<string, unknown>>; columns: string[] }) {
  if (!rows.length) return <div className="text-[10.5px] text-slate-400 py-2 text-center">无数据样本</div>
  const cols = columns.length ? columns : sampleCols(rows, [])
  return (
    <div className="mt-1.5 overflow-x-auto rounded-lg border border-slate-100">
      <table className="w-full text-[10.5px] border-collapse">
        <thead className="bg-slate-50">
          <tr>{cols.map(c => <th key={c} className="px-2 py-1 text-left font-medium text-slate-500 font-mono whitespace-nowrap">{c}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} className="border-t border-slate-50">
              {cols.map(c => <td key={c} className="px-2 py-1 text-slate-600 whitespace-nowrap max-w-[180px] truncate" title={fmt(r[c])}>{fmt(r[c])}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function UpdatedTable({ pairs, pk }: { pairs: LakeImpactDetail['updated_sample']; pk: string[] | null }) {
  if (!pairs.length) return <div className="text-[10.5px] text-slate-400 py-2 text-center">无数据样本</div>
  // 只展示发生变化的列（+ 主键列作为定位）
  return (
    <div className="mt-1.5 space-y-1.5">
      {pairs.map((p, i) => {
        const keys = Array.from(new Set([...Object.keys(p.before), ...Object.keys(p.after)]))
        const changed = keys.filter(k => fmt(p.before[k]) !== fmt(p.after[k]))
        const pkStr = (pk || []).map(k => `${k}=${fmt(p.after[k] ?? p.before[k])}`).join(', ')
        return (
          <div key={i} className="rounded-lg border border-amber-100 bg-amber-50/40 p-2">
            {pkStr && <div className="text-[10px] text-slate-500 mb-1 font-mono">{pkStr}</div>}
            <div className="space-y-0.5">
              {changed.map(k => (
                <div key={k} className="flex items-center gap-1.5 text-[10.5px]">
                  <span className="text-slate-500 font-mono shrink-0">{k}:</span>
                  <span className="text-rose-500 line-through truncate max-w-[130px]" title={fmt(p.before[k])}>{fmt(p.before[k]) || '∅'}</span>
                  <ArrowRight size={9} className="text-slate-400 shrink-0" />
                  <span className="text-emerald-600 truncate max-w-[130px]" title={fmt(p.after[k])}>{fmt(p.after[k]) || '∅'}</span>
                </div>
              ))}
            </div>
          </div>
        )
      })}
    </div>
  )
}

function ImpactChip({ tone, icon, label, count, active, onClick }: {
  tone: 'emerald' | 'amber' | 'rose'; icon: React.ReactNode; label: string
  count: number; active: boolean; onClick: () => void
}) {
  const map = {
    emerald: 'text-emerald-600 bg-emerald-50 border-emerald-200',
    amber:   'text-amber-600 bg-amber-50 border-amber-200',
    rose:    'text-rose-600 bg-rose-50 border-rose-200',
  }[tone]
  const disabled = count === 0
  return (
    <button onClick={onClick} disabled={disabled}
      className={`flex items-center justify-center gap-1 px-1.5 py-1 rounded-lg border text-[11px] transition
        ${disabled ? 'text-slate-300 bg-slate-50 border-slate-100 cursor-default' : map}
        ${active ? 'ring-2 ring-offset-1 ring-current/30' : ''}`}>
      {icon}<span>{label}</span><b className="tabular-nums">{count}</b>
    </button>
  )
}

function Section({ title, icon, children }: { title: string; icon: React.ReactNode; children: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-slate-100 bg-white p-2.5">
      <div className="flex items-center gap-1.5 text-[11px] font-medium text-slate-500 mb-1.5">
        <span className="text-slate-400">{icon}</span>{title}
      </div>
      <div className="space-y-1">{children}</div>
    </div>
  )
}

function KV({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="flex justify-between gap-2">
      <span className="text-slate-500 shrink-0">{label}</span>
      <span className={`text-slate-800 text-right break-all ${mono ? 'font-mono' : ''}`}>{value}</span>
    </div>
  )
}
