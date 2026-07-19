import { useEffect } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangle,
  Archive,
  CheckCircle2,
  ChevronRight,
  Inbox,
  Loader2,
  RefreshCw,
} from 'lucide-react'

import { inboxApi, type InboxDelivery } from '@/api/inbox'


function formatRelativeTime(value: string): string {
  const time = new Date(value).getTime()
  if (!Number.isFinite(time)) return value
  const diff = Math.max(0, Date.now() - time)
  if (diff < 60_000) return '刚刚'
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)} 分钟前`
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)} 小时前`
  if (diff < 604_800_000) return `${Math.floor(diff / 86_400_000)} 天前`
  return new Date(value).toLocaleDateString('zh-CN')
}

export default function InboxPopover({
  open,
  onOpenChange,
  onNavigate,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  onNavigate: (href: string) => void
}) {
  const queryClient = useQueryClient()
  const summary = useQuery({
    queryKey: ['inbox', 'summary'],
    queryFn: inboxApi.summary,
    refetchInterval: 15_000,
    refetchOnWindowFocus: true,
  })
  const list = useQuery({
    queryKey: ['inbox', 'popover'],
    queryFn: () => inboxApi.list({ tab: 'all', limit: 8 }),
    enabled: open,
    refetchOnMount: 'always',
  })
  const stateMutation = useMutation({
    mutationFn: ({ id, state }: { id: string; state: 'read' | 'archived' }) =>
      inboxApi.updateState(id, state),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['inbox'] })
    },
  })

  useEffect(() => {
    if (!open) return
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onOpenChange(false)
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [onOpenChange, open])

  const handleOpen = async (item: InboxDelivery) => {
    if (item.deliveryState === 'unread') {
      try { await inboxApi.updateState(item.id, 'read') } catch { /* navigation remains available */ }
      void queryClient.invalidateQueries({ queryKey: ['inbox'] })
    }
    onOpenChange(false)
    onNavigate(item.actions[0]?.href || item.resource.href)
  }

  const openCount = summary.data?.openAlertCount ?? 0
  const items = list.data?.items ?? []

  return (
    <>
      <button
        type="button"
        onClick={() => onOpenChange(!open)}
        className={`relative grid h-11 w-11 place-items-center rounded-lg transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-500/40 ${
          open
            ? 'bg-[var(--color-bg-hover)] text-[var(--color-text-primary)]'
            : 'text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text-primary)]'
        }`}
        aria-label={`收件箱${openCount ? `，${openCount} 个未恢复告警` : ''}`}
        aria-expanded={open}
        aria-controls="global-inbox-popover"
      >
        <Inbox size={21} strokeWidth={1.8} />
        {openCount > 0 && (
          <span className="absolute right-0 top-0 flex h-[18px] min-w-[18px] items-center justify-center rounded-full bg-rose-600 px-1 text-[10px] font-semibold tabular-nums text-white shadow-sm">
            {openCount > 99 ? '99+' : openCount}
          </span>
        )}
      </button>

      {open && (
        <section
          id="global-inbox-popover"
          aria-label="收件箱消息"
          className="fixed left-3 right-3 top-[58px] z-50 overflow-hidden rounded-xl border border-slate-200 bg-white shadow-[0_18px_50px_rgba(15,23,42,0.16)] sm:absolute sm:left-auto sm:right-0 sm:top-auto sm:mt-2 sm:w-[420px]"
        >
          <header className="flex items-center gap-3 border-b border-slate-100 px-4 py-3">
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <h2 className="text-sm font-semibold text-slate-800">收件箱</h2>
                {openCount > 0 && (
                  <span className="inline-flex items-center gap-1 rounded-full bg-rose-50 px-2 py-0.5 text-[10px] font-medium text-rose-700">
                    <span className="h-1.5 w-1.5 rounded-full bg-rose-500" />
                    {openCount} 个任务故障待恢复
                  </span>
                )}
              </div>
              <p className="mt-0.5 text-[11px] text-slate-400">
                阅读不会关闭告警，任务恢复后会自动解决
              </p>
            </div>
            <button
              type="button"
              onClick={() => void list.refetch()}
              disabled={list.isFetching}
              className="grid h-9 w-9 place-items-center rounded-lg text-slate-400 transition hover:bg-slate-100 hover:text-slate-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-500/30 disabled:opacity-50"
              aria-label="刷新收件箱"
            >
              <RefreshCw size={15} className={list.isFetching ? 'animate-spin' : ''} />
            </button>
          </header>

          <div className="max-h-[min(520px,calc(100vh-150px))] overflow-y-auto">
            {list.isLoading ? (
              <div className="space-y-3 p-4" aria-label="正在加载收件箱">
                {[0, 1, 2].map(index => (
                  <div key={index} className="h-[76px] animate-pulse rounded-lg bg-slate-100" />
                ))}
              </div>
            ) : list.isError ? (
              <div role="alert" className="flex flex-col items-center px-5 py-10 text-center">
                <AlertTriangle size={24} className="mb-2 text-rose-500" />
                <p className="text-sm font-medium text-slate-700">收件箱加载失败</p>
                <button type="button" onClick={() => void list.refetch()} className="mt-3 inline-flex h-9 items-center gap-1.5 rounded-lg bg-slate-100 px-3 text-xs text-slate-700 hover:bg-slate-200">
                  <RefreshCw size={13} />重新加载
                </button>
              </div>
            ) : items.length === 0 ? (
              <div className="flex flex-col items-center px-5 py-12 text-center">
                <span className="mb-3 grid h-11 w-11 place-items-center rounded-xl bg-emerald-50 text-emerald-600">
                  <CheckCircle2 size={20} />
                </span>
                <p className="text-sm font-medium text-slate-700">任务运行平稳</p>
                <p className="mt-1 text-xs text-slate-400">数据任务执行失败时会在这里提醒你</p>
              </div>
            ) : (
              <div className="divide-y divide-slate-100">
                {items.map(item => (
                  <article key={item.id} className={`group px-4 py-3 transition-colors hover:bg-slate-50 ${item.deliveryState === 'unread' ? 'bg-rose-50/25' : ''}`}>
                    <div className="flex items-start gap-3">
                      <span className={`relative mt-0.5 grid h-8 w-8 shrink-0 place-items-center rounded-lg ${item.businessState === 'open' ? 'bg-rose-50 text-rose-600' : 'bg-emerald-50 text-emerald-600'}`}>
                        {item.businessState === 'open' ? <AlertTriangle size={15} /> : <CheckCircle2 size={15} />}
                        {item.deliveryState === 'unread' && <span className="absolute -right-0.5 -top-0.5 h-2 w-2 rounded-full border-2 border-white bg-rose-500" />}
                      </span>
                      <button type="button" onClick={() => void handleOpen(item)} className="min-w-0 flex-1 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-500/30">
                        <div className="flex items-center gap-2">
                          <p className="min-w-0 flex-1 truncate text-sm font-medium text-slate-800" title={item.title}>{item.title}</p>
                          <time className="shrink-0 text-[10px] text-slate-400">{formatRelativeTime(item.lastOccurredAt)}</time>
                        </div>
                        <p className="mt-1 line-clamp-2 text-xs leading-5 text-slate-500">{item.summary}</p>
                        <div className="mt-1.5 flex items-center gap-2 text-[10px] text-slate-400">
                          <span>{item.safeContext.pipelineName || '数据任务池'}</span>
                          {item.occurrenceCount > 1 && <span className="rounded bg-rose-50 px-1.5 py-0.5 text-rose-600">连续失败 {item.occurrenceCount} 次</span>}
                          {item.businessState === 'resolved' && <span className="rounded bg-emerald-50 px-1.5 py-0.5 text-emerald-700">已恢复</span>}
                        </div>
                      </button>
                      <ChevronRight size={15} className="mt-2 shrink-0 text-slate-300 transition-transform group-hover:translate-x-0.5 group-hover:text-slate-500" />
                    </div>
                    <div className="mt-2 flex min-h-8 items-center justify-end gap-2 pl-11">
                      {item.deliveryState === 'unread' && (
                        <button
                          type="button"
                          disabled={stateMutation.isPending}
                          onClick={() => stateMutation.mutate({ id: item.id, state: 'read' })}
                          className="h-8 rounded-lg px-2.5 text-[11px] text-slate-500 transition hover:bg-slate-100 hover:text-slate-700 disabled:opacity-50"
                        >
                          标为已读
                        </button>
                      )}
                      {item.canArchive && (
                        <button
                          type="button"
                          disabled={stateMutation.isPending}
                          onClick={() => stateMutation.mutate({ id: item.id, state: 'archived' })}
                          className="inline-flex h-8 items-center gap-1 rounded-lg px-2.5 text-[11px] text-slate-500 transition hover:bg-slate-100 hover:text-slate-700 disabled:opacity-50"
                        >
                          <Archive size={12} />归档
                        </button>
                      )}
                    </div>
                  </article>
                ))}
              </div>
            )}
          </div>

          <button
            type="button"
            onClick={() => { onOpenChange(false); onNavigate('/inbox') }}
            className="flex h-11 w-full items-center justify-center gap-1 border-t border-slate-100 text-xs font-medium text-teal-700 transition hover:bg-teal-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-teal-500/30"
          >
            查看全部消息 <ChevronRight size={13} />
          </button>
          {stateMutation.isPending && (
            <div className="absolute inset-x-0 bottom-11 flex justify-center" aria-live="polite">
              <span className="inline-flex items-center gap-1 rounded-full bg-slate-800 px-2.5 py-1 text-[10px] text-white shadow-lg"><Loader2 size={10} className="animate-spin" />正在更新</span>
            </div>
          )}
        </section>
      )}
    </>
  )
}
