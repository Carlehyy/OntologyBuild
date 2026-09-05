/**
 * 运行日志标签 — 发布态场景的规则命中/恢复日志查询（分页+级别过滤）。
 */
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Info } from 'lucide-react'
import { scenesApi } from '@/api/scenes'
import { EmptyState, LoadingState } from '@/components/ui/LoadingState'

const LEVEL_FILTERS: { key: string; label: string }[] = [
  { key: 'all', label: '全部' },
  { key: 'info', label: '信息' },
  { key: 'normal', label: '正常' },
  { key: 'warning', label: '预警' },
  { key: 'alarm', label: '告警' },
]

const LEVEL_BADGES: Record<string, string> = {
  info: 'bg-muted text-muted-foreground dark:bg-accent dark:text-[var(--color-text-tertiary)]',
  normal: 'bg-[var(--color-success-bg)] text-[var(--color-success)]',
  warning: 'bg-[var(--color-warning-bg)] text-[var(--color-warning)]',
  alarm: 'bg-[var(--color-danger-bg)] text-[var(--color-danger)] dark:bg-[var(--color-danger-hover)] dark:text-[var(--color-danger)]',
}
const LEVEL_LABELS: Record<string, string> = {
  info: '信息', normal: '正常', warning: '预警', alarm: '告警',
}

const PAGE_SIZE = 20

export function LogsTab({ sceneId, everPublished }: { sceneId: string; everPublished: boolean }) {
  const [level, setLevel] = useState('all')
  const [page, setPage] = useState(1)

  const logsQuery = useQuery({
    queryKey: ['scenes', sceneId, 'runtime-logs', level, page],
    queryFn: () => scenesApi.runtimeLogs(sceneId, {
      level: level === 'all' ? undefined : level,
      page,
      page_size: PAGE_SIZE,
    }),
  })
  const total = logsQuery.data?.total ?? 0
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  return (
    <div className="space-y-3">
      {!everPublished && (
        <div className="flex items-center gap-2 rounded-lg border border-[var(--color-border)] bg-[var(--color-info-bg,#eff6ff)] px-3 py-2 text-xs text-[var(--color-text-secondary)]">
          <Info size={13} /> 该场景尚未发布：以下为历史日志。发布态场景运行时的规则命中会持续写入这里。
        </div>
      )}
      <div className="flex items-center gap-1.5">
        {LEVEL_FILTERS.filter(item => item.key !== 'all').map(item => (
          <button
            key={item.key}
            type="button"
            aria-pressed={level === item.key}
            onClick={() => { setLevel(item.key); setPage(1) }}
            className={
              'rounded-full border px-2.5 py-0.5 text-xs transition-colors ' +
              (level === item.key
                ? 'border-brand bg-brand-soft text-brand-ink dark:bg-brand-deep dark:text-brand-ink'
                : 'border-[var(--color-border)] text-[var(--color-text-secondary)] hover:border-brand-line')
            }
          >
            {item.label}
          </button>
        ))}
        <button
          type="button"
          aria-pressed={level === 'all'}
          onClick={() => { setLevel('all'); setPage(1) }}
          className={
            'rounded-full border px-2.5 py-0.5 text-xs transition-colors ' +
            (level === 'all'
              ? 'border-brand bg-brand-soft text-brand-ink dark:bg-brand-deep dark:text-brand-ink'
              : 'border-[var(--color-border)] text-[var(--color-text-secondary)] hover:border-brand-line')
          }
        >
          全部
        </button>
      </div>

      {logsQuery.isLoading ? (
        <LoadingState />
      ) : total === 0 ? (
        <div className="rounded-xl border border-[var(--color-border)] bg-card p-10">
          <EmptyState title="暂无运行日志" description="发布态场景在预览时产生的规则命中将出现在这里" />
        </div>
      ) : (
        <div className="overflow-hidden rounded-xl border border-[var(--color-border)] bg-card">
          <table className="w-full text-left text-xs">
            <thead className="bg-muted text-[var(--color-text-tertiary)] dark:bg-accent">
              <tr>
                <th className="px-3 py-2 font-medium">时间</th>
                <th className="px-3 py-2 font-medium">级别</th>
                <th className="px-3 py-2 font-medium">对象</th>
                <th className="px-3 py-2 font-medium">消息</th>
              </tr>
            </thead>
            <tbody>
              {(logsQuery.data?.items ?? []).map(log => (
                <tr key={log.id} className="border-t border-[var(--color-border)]">
                  <td className="whitespace-nowrap px-3 py-2 font-mono text-[11px]">
                    {log.occurred_at ? log.occurred_at.replace('T', ' ').slice(0, 19) : '—'}
                  </td>
                  <td className="px-3 py-2">
                    <span className={'rounded px-1.5 py-0.5 text-[10px] font-medium ' + (LEVEL_BADGES[log.level] ?? LEVEL_BADGES.info)}>
                      {LEVEL_LABELS[log.level] ?? log.level}
                    </span>
                  </td>
                  <td className="px-3 py-2 font-mono text-[11px]">{log.object_id ?? '—'}</td>
                  <td className="px-3 py-2">{log.message}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="flex items-center justify-end gap-2 border-t border-[var(--color-border)] px-3 py-2 text-xs text-[var(--color-text-secondary)]">
            <span>共 {total} 条 · 第 {page}/{totalPages} 页</span>
            <button
              type="button"
              className="rounded border border-[var(--color-border)] px-2 py-0.5 disabled:opacity-40"
              disabled={page <= 1}
              onClick={() => setPage(current => current - 1)}
            >上一页</button>
            <button
              type="button"
              className="rounded border border-[var(--color-border)] px-2 py-0.5 disabled:opacity-40"
              disabled={page >= totalPages}
              onClick={() => setPage(current => current + 1)}
            >下一页</button>
          </div>
        </div>
      )}
    </div>
  )
}
