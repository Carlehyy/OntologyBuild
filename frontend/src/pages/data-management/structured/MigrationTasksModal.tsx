import { useCallback, useEffect, useRef, useState } from 'react'
import {
  CheckCircle2, FolderInput, Loader2, RefreshCw, X, XCircle,
} from 'lucide-react'
import datasetsApi, {
  type DatasetMigrationJob,
  type DatasetMigrationStatus,
} from '@/api/v2/datasets'

const STATUS_LABELS: Record<DatasetMigrationStatus, string> = {
  queued: '排队中',
  running: '迁移中',
  completed: '已完成',
  failed: '失败',
}

const ACTIVE_STATUSES: DatasetMigrationStatus[] = ['queued', 'running']

function formatTime(iso?: string | null): string {
  if (!iso) return '—'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso
  return date.toLocaleString('zh-CN', {
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hour12: false,
  })
}

function StatusChip({ job }: { job: DatasetMigrationJob }) {
  if (job.status === 'completed') {
    return (
      <span className="inline-flex items-center gap-1 rounded border border-green-200 bg-green-50 px-1.5 py-0.5 text-[11px] font-medium text-green-700">
        <CheckCircle2 size={11} /> 已完成
      </span>
    )
  }
  if (job.status === 'failed') {
    return (
      <span className="inline-flex items-center gap-1 rounded border border-red-200 bg-red-50 px-1.5 py-0.5 text-[11px] font-medium text-red-600">
        <XCircle size={11} /> 失败
      </span>
    )
  }
  return (
    <span className="inline-flex items-center gap-1 rounded border border-teal-200 bg-teal-50 px-1.5 py-0.5 text-[11px] font-medium text-teal-700">
      <Loader2 size={11} className="animate-spin" />
      {STATUS_LABELS[job.status]} {job.progress != null ? `${job.progress}%` : ''}
    </span>
  )
}

/**
 * 成品数据集 → 人工数据集 异步迁移任务弹窗。
 * 打开期间自动轮询；有任务转为完成时广播数据资产变化，让总览计数即时刷新。
 */
export default function MigrationTasksModal({
  onClose,
  onSwitchToManual,
}: {
  onClose: () => void
  /** 点击已完成任务的「前往人工数据集」时触发 */
  onSwitchToManual?: (datasetName?: string) => void
}) {
  const [jobs, setJobs] = useState<DatasetMigrationJob[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  // 记录上一轮仍活跃的任务 id：本轮消失或转终态时通知资产已变化
  const activeIdsRef = useRef<Set<string>>(new Set())

  const load = useCallback(async () => {
    try {
      const list = await datasetsApi.migrations()
      setJobs(Array.isArray(list) ? list : [])
      setError('')
      const nextActive = new Set(
        (Array.isArray(list) ? list : [])
          .filter(job => ACTIVE_STATUSES.includes(job.status))
          .map(job => job.job_id),
      )
      let settledToCompleted = false
      for (const jobId of activeIdsRef.current) {
        if (nextActive.has(jobId)) continue
        const job = (Array.isArray(list) ? list : []).find(item => item.job_id === jobId)
        if (!job || job.status === 'completed') settledToCompleted = true
      }
      activeIdsRef.current = nextActive
      if (settledToCompleted && !nextActive.size) {
        window.dispatchEvent(new Event('ontoprompt:data-assets-changed'))
      }
    } catch (err) {
      const detail = (err as { detail?: string; message?: string })?.detail
        || (err as { message?: string })?.message
      setError(detail || '迁移任务列表加载失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
    const timer = window.setInterval(() => { void load() }, 2000)
    return () => window.clearInterval(timer)
  }, [load])

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/35 p-4 backdrop-blur-[2px]">
      <div
        className="flex max-h-[82vh] w-[min(96vw,880px)] flex-col overflow-hidden rounded-2xl border border-white/80 bg-white shadow-[0_24px_80px_rgba(15,23,42,0.18)]"
        role="dialog"
        aria-modal="true"
        aria-labelledby="migration-tasks-title"
      >
        <header className="flex shrink-0 items-start gap-3 border-b border-slate-100 px-5 py-4">
          <span className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-teal-50 text-teal-700"><FolderInput size={16} /></span>
          <div className="min-w-0 flex-1">
            <h3 id="migration-tasks-title" className="text-sm font-semibold text-slate-900">迁移任务</h3>
            <p className="mt-1 text-xs text-slate-400">成品数据集拷贝为人工数据集的异步任务进度；完成后副本会出现在「人工数据集」页签。</p>
          </div>
          <button type="button" onClick={onClose}
            className="grid h-8 w-8 place-items-center rounded-lg text-slate-400 transition hover:bg-slate-100 hover:text-slate-700"
            aria-label="关闭迁移任务"><X size={16} /></button>
        </header>

        {error && (
          <div className="mx-5 mt-3 flex shrink-0 items-start gap-2 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">
            <XCircle size={13} className="mt-0.5 shrink-0" />
            <span className="flex-1">{error}</span>
            <button type="button" onClick={() => { setLoading(true); void load() }}
              className="rounded border border-red-200 bg-white px-2 py-0.5 hover:bg-red-100">重试</button>
          </div>
        )}

        <main className="min-h-0 flex-1 overflow-auto px-5 py-4">
          {loading && jobs.length === 0 ? (
            <div className="flex items-center justify-center gap-2 p-10 text-sm text-gray-400">
              <Loader2 size={16} className="animate-spin" /> 加载迁移任务...
            </div>
          ) : jobs.length === 0 ? (
            <div className="rounded-xl border-2 border-dashed p-10 text-center text-gray-400">
              <FolderInput size={28} className="mx-auto mb-2 opacity-30" />
              <p className="text-sm font-medium">暂无迁移任务</p>
              <p className="mt-1 text-xs">在成品数据集列表点击「迁移」后，任务会出现在这里</p>
            </div>
          ) : (
            <div className="overflow-x-auto rounded-xl border border-slate-200">
              <table className="w-full min-w-[680px] text-sm">
                <thead className="bg-gray-50 border-b">
                  <tr>
                    <th className="px-4 py-2.5 text-left text-xs font-medium text-gray-600">目标人工数据集</th>
                    <th className="px-4 py-2.5 text-left text-xs font-medium text-gray-600">源成品数据集</th>
                    <th className="px-4 py-2.5 text-center text-xs font-medium text-gray-600">状态</th>
                    <th className="px-4 py-2.5 text-center text-xs font-medium text-gray-600">行数</th>
                    <th className="px-4 py-2.5 text-center text-xs font-medium text-gray-600">创建时间</th>
                  </tr>
                </thead>
                <tbody className="divide-y">
                  {jobs.map(job => {
                    const active = ACTIVE_STATUSES.includes(job.status)
                    return (
                      <tr key={job.job_id} className="transition-colors hover:bg-slate-50/70">
                        <td className="max-w-[220px] px-4 py-3">
                          <div className="flex items-center gap-1.5">
                            <span className="block truncate font-medium text-gray-900" title={job.target_name}>
                              {job.target_name || '—'}
                            </span>
                            {!active && job.status === 'completed' && onSwitchToManual && (
                              <button type="button" onClick={() => onSwitchToManual(job.target_name)}
                                className="shrink-0 text-[11px] font-medium text-teal-700 underline decoration-teal-300 underline-offset-2 hover:text-teal-900">
                                前往人工数据集
                              </button>
                            )}
                          </div>
                          {job.status === 'failed' && job.error && (
                            <p className="mt-1 max-w-[280px] truncate text-[11px] text-red-500" title={job.error}>
                              失败原因：{job.error}
                            </p>
                          )}
                        </td>
                        <td className="max-w-[180px] truncate px-4 py-3 text-xs text-gray-500" title={job.source_dataset_name}>
                          {job.source_dataset_name || '—'}
                        </td>
                        <td className="px-4 py-3 text-center">
                          <StatusChip job={job} />
                          {active && job.phase && (
                            <p className="mt-1 text-[11px] text-slate-400">{job.phase}</p>
                          )}
                        </td>
                        <td className="px-4 py-3 text-center text-xs tabular-nums text-gray-600">
                          {job.result?.rowcount != null ? job.result.rowcount.toLocaleString() : '—'}
                        </td>
                        <td className="whitespace-nowrap px-4 py-3 text-center text-xs tabular-nums text-gray-500">
                          {formatTime(job.created_at)}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
        </main>

        <footer className="flex shrink-0 items-center justify-between gap-2 border-t border-slate-100 bg-slate-50/70 px-5 py-3">
          <span className="text-[11px] text-slate-400">打开期间每 2 秒自动刷新</span>
          <div className="flex items-center gap-2">
            <button type="button" onClick={() => { setLoading(true); void load() }}
              className="inline-flex h-8 items-center gap-1 rounded-lg border border-slate-200 bg-white px-3 text-xs font-medium text-slate-600 transition hover:bg-slate-100">
              <RefreshCw size={12} /> 刷新
            </button>
            <button type="button" onClick={onClose}
              className="h-8 rounded-lg bg-teal-700 px-4 text-xs font-medium text-white transition hover:bg-teal-800">
              关闭
            </button>
          </div>
        </footer>
      </div>
    </div>
  )
}
