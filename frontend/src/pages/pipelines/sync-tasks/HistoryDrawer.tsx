import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { X, CheckCircle2, XCircle, Loader2, Clock, ChevronDown, ChevronUp, Table2, ArrowRight, ShieldCheck } from 'lucide-react'
import { pipelineTasksApi, WRITE_MODE_META, type PipelineTask, type PipelineTaskRun, type WriteMode } from '@/api/v2/pipeline-tasks'

const TRIGGER_LABEL: Record<string, string> = {
  manual: '手动',
  scheduled: '定时',
}

const STATUS_META: Record<string, { icon: React.ReactNode; color: string; label: string }> = {
  pending: { icon: <Clock size={13} />, color: 'text-gray-500 bg-gray-100', label: '排队中' },
  running: { icon: <Loader2 size={13} className="animate-spin" />, color: 'text-blue-600 bg-blue-50', label: '执行中' },
  success: { icon: <CheckCircle2 size={13} />, color: 'text-green-600 bg-green-50', label: '成功' },
  failed: { icon: <XCircle size={13} />, color: 'text-red-600 bg-red-50', label: '失败' },
}

function formatDate(iso: string | null): string {
  if (!iso) return '-'
  try {
    return new Date(iso).toLocaleString('zh-CN')
  } catch { return iso }
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
  const navigate = useNavigate()
  const [items, setItems] = useState<PipelineTaskRun[]>([])
  const [loading, setLoading] = useState(true)
  const [expanded, setExpanded] = useState<Set<string>>(new Set())

  const load = async () => {
    setLoading(true)
    try {
      const res = await pipelineTasksApi.histories(task.id, 1, 50)
      setItems(res.items)
    } catch (err) {
      console.error('加载历史失败', err)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [task.id])

  const toggle = (id: string) => {
    setExpanded(prev => {
      const n = new Set(prev)
      n.has(id) ? n.delete(id) : n.add(id)
      return n
    })
  }

  return (
    <div className="fixed inset-0 bg-black/30 z-50 flex justify-end" onClick={onClose}>
      <div className="w-full max-w-md bg-white h-full flex flex-col shadow-2xl" onClick={e => e.stopPropagation()}>
        <div className="px-5 py-4 border-b border-gray-200">
          <div className="flex items-center justify-between">
            <h3 className="text-base font-bold text-gray-900">执行历史</h3>
            <button onClick={onClose} className="text-gray-400 hover:text-gray-600"><X size={18} /></button>
          </div>
          <p className="text-xs text-gray-400 mt-0.5 truncate">
            {task.name} · 流水线「{task.pipeline_name}」 · {WRITE_MODE_META[task.write_mode as WriteMode]?.label || task.write_mode}
          </p>
        </div>

        <div className="flex-1 overflow-y-auto">
          {loading ? (
            <div className="py-20 text-center text-gray-400 text-sm flex items-center justify-center gap-1">
              <Loader2 size={14} className="animate-spin" />加载中...
            </div>
          ) : items.length === 0 ? (
            <div className="py-20 text-center text-gray-400 text-sm">暂无执行记录</div>
          ) : (
            <div className="divide-y divide-gray-100">
              {items.map(h => {
                const sm = STATUS_META[h.status] || STATUS_META.running
                const isOpen = expanded.has(h.id)
                const skipped = (h.skipped_outputs?.length ?? 0) > 0
                return (
                  <div key={h.id} className="px-5 py-3">
                    <div className="flex items-start justify-between gap-2 cursor-pointer" onClick={() => toggle(h.id)}>
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2">
                          <span className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-xs ${sm.color}`}>
                            {sm.icon}{sm.label}
                          </span>
                          <span className="text-xs text-gray-400">{TRIGGER_LABEL[h.trigger_type] || h.trigger_type}</span>
                          {skipped && (
                            <span className="inline-flex items-center gap-0.5 text-[10px] text-amber-600 bg-amber-50 px-1.5 py-0.5 rounded" title="流水线输出 0 行，空输出保护已跳过入库">
                              <ShieldCheck size={9} /> 已跳过入库
                            </span>
                          )}
                        </div>
                        <div className="text-xs text-gray-500 mt-1 flex items-center gap-1">
                          <Clock size={11} />
                          {formatDate(h.started_at)}
                        </div>
                        {h.status === 'success' && !skipped && (
                          <div className="text-xs text-gray-500 mt-0.5">
                            输出 {h.rows_out} 行{h.lake_rows != null ? ` · 入湖后共 ${h.lake_rows} 行` : ''}
                            {h.started_at && h.finished_at ? ` · ${formatDuration(h.started_at, h.finished_at)}` : ''}
                          </div>
                        )}
                      </div>
                      {isOpen ? <ChevronUp size={14} className="text-gray-400 mt-1" /> : <ChevronDown size={14} className="text-gray-400 mt-1" />}
                    </div>

                    {isOpen && (
                      <div className="mt-3 space-y-2 text-xs bg-gray-50 rounded-lg p-3">
                        <DetailRow label="开始时间" value={formatDate(h.started_at)} />
                        {h.finished_at && <DetailRow label="结束时间" value={formatDate(h.finished_at)} />}
                        <DetailRow label="执行耗时" value={formatDuration(h.started_at, h.finished_at)} />
                        <DetailRow label="触发方式" value={TRIGGER_LABEL[h.trigger_type] || h.trigger_type} />
                        <DetailRow label="流水线输入" value={`${h.rows_in} 行`} />
                        <DetailRow label="最终产物" value={`${h.rows_out} 行`} />
                        {h.lake_rows != null && <DetailRow label="入湖后总量" value={`${h.lake_rows} 行`} />}
                        {h.write_mode && <DetailRow label="入库方式" value={WRITE_MODE_META[h.write_mode as WriteMode]?.label || h.write_mode} />}
                        {(h.curated_dataset_ids?.length ?? 0) > 0 && (
                          <button
                            onClick={() => navigate('/data/structured?tab=curated')}
                            className="w-full flex items-center justify-center gap-1.5 px-3 py-1.5 mt-1 text-xs bg-white border border-gray-200 rounded-lg hover:bg-gray-100 text-gray-700"
                          >
                            <Table2 size={12} />
                            在资产湖查看成品数据集（{h.curated_dataset_ids.length} 个）
                            <ArrowRight size={11} />
                          </button>
                        )}
                        {h.error_message && (
                          <div>
                            <div className="text-gray-500 mb-0.5">错误信息</div>
                            <div className="text-red-600 bg-red-50 rounded p-2 font-mono text-[11px] whitespace-pre-wrap break-all">
                              {h.error_message}
                            </div>
                          </div>
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

function DetailRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between gap-2">
      <span className="text-gray-500 shrink-0">{label}</span>
      <span className="text-gray-800 text-right break-all">{value}</span>
    </div>
  )
}
