import { useDeferredValue, useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangle, Ban, Check, ChevronLeft, ChevronRight, Copy, KeyRound, Loader2,
  Plus, Plug, RotateCcw, Search, ShieldCheck, Terminal, X,
} from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { LoadingState } from '@/components/ui/LoadingState'
import { eventsApi, type IngestKey, type IngestKeyListResp } from '@/api/events'

const KEY_PAGE_SIZE = 5

function fmt(iso: string | null): string {
  if (!iso) return '从未'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return '时间未知'
  const pad = (value: number) => String(value).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

function CopyBtn({ text, small }: { text: string; small?: boolean }) {
  const [done, setDone] = useState(false)
  return (
    <button
      type="button"
      onClick={() => {
        void navigator.clipboard?.writeText(text)
        setDone(true)
        window.setTimeout(() => setDone(false), 1500)
      }}
      className={`inline-flex items-center gap-1 rounded-md text-emerald-700 transition-colors hover:text-emerald-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-500/30 ${small ? 'text-xs' : 'text-sm'}`}
    >
      {done ? <Check size={13} /> : <Copy size={13} />}{done ? '已复制' : '复制'}
    </button>
  )
}

export default function IngestKeysDrawer({ open, onClose }: { open: boolean; onClose: () => void }) {
  const queryClient = useQueryClient()
  const [name, setName] = useState('')
  const [scope, setScope] = useState('')
  const [newKey, setNewKey] = useState<IngestKey | null>(null)
  const [error, setError] = useState('')
  const [search, setSearch] = useState('')
  const [status, setStatus] = useState<'all' | 'active' | 'revoked'>('all')
  const [sourceSystem, setSourceSystem] = useState('')
  const [page, setPage] = useState(1)
  const deferredSearch = useDeferredValue(search.trim())
  const deferredSourceSystem = useDeferredValue(sourceSystem.trim())

  const endpoint = `${window.location.origin}/api/v2/ingest/events`
  const keyQuery = useQuery<IngestKeyListResp>({
    queryKey: ['ingest-keys', { q: deferredSearch, status, sourceSystem: deferredSourceSystem, page }],
    queryFn: () => eventsApi.listKeys({
      q: deferredSearch || undefined,
      status,
      sourceSystem: deferredSourceSystem || undefined,
      page,
      pageSize: KEY_PAGE_SIZE,
    }),
    enabled: open,
    placeholderData: previous => previous,
  })

  const keys = keyQuery.data?.items || []
  const total = keyQuery.data?.total || 0
  const totalPages = Math.max(1, Math.ceil(total / KEY_PAGE_SIZE))
  const hasFilters = Boolean(search.trim() || sourceSystem.trim() || status !== 'all')

  useEffect(() => setPage(1), [deferredSearch, deferredSourceSystem, status])
  useEffect(() => {
    if (page > totalPages) setPage(totalPages)
  }, [page, totalPages])

  const createMutation = useMutation({
    mutationFn: () => eventsApi.createKey(name.trim(), scope.trim() || undefined),
    onSuccess: key => {
      setNewKey(key)
      setName('')
      setScope('')
      setError('')
      setPage(1)
      queryClient.invalidateQueries({ queryKey: ['ingest-keys'] })
    },
    onError: (cause: any) => setError(cause?.detail || cause?.message || '创建失败（需要管理员权限）'),
  })

  const revokeMutation = useMutation({
    mutationFn: (id: string) => eventsApi.revokeKey(id),
    onSuccess: () => {
      setError('')
      queryClient.invalidateQueries({ queryKey: ['ingest-keys'] })
    },
    onError: (cause: any) => setError(cause?.detail || cause?.message || '密钥吊销失败'),
  })

  const clearFilters = () => {
    setSearch('')
    setStatus('all')
    setSourceSystem('')
    setPage(1)
  }

  if (!open) return null

  const curl = `curl -X POST "${endpoint}" \\
  -H "X-API-Key: <你的密钥>" \\
  -H "Content-Type: application/json" \\
  -d '{
    "title": "产线停机",
    "event_type": "设备异常",
    "severity": "high",
    "source_system": "MES",
    "source_ref": "WO-2026-0007",
    "occurred_at": "2026-07-07T09:12:00Z",
    "payload": { "line": "A3", "code": "E502" }
  }'`

  return createPortal(
    <div className="fixed inset-0 z-[80] flex justify-end">
      <div className="absolute inset-0 bg-[var(--color-bg-overlay)]" onClick={onClose} />
      <aside className="anim-drawer-in relative flex w-full max-w-2xl flex-col border-l border-slate-200 bg-white shadow-xl">
        <header className="flex shrink-0 items-center justify-between border-b border-slate-200 px-6 py-4">
          <div className="flex items-center gap-2">
            <Plug size={18} className="text-emerald-600" />
            <h2 className="text-lg font-semibold text-slate-900">API 接入 · 第三方上传</h2>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="flex h-9 w-9 items-center justify-center rounded-lg text-slate-400 transition-colors hover:bg-slate-100 hover:text-slate-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-500/30"
            aria-label="关闭接入管理"
          >
            <X size={20} />
          </button>
        </header>

        <div className="flex-1 space-y-6 overflow-y-auto px-6 py-5">
          <section>
            <h3 className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-slate-500">
              <Terminal size={13} /> 上传端点
            </h3>
            <div className="mb-2 flex items-center gap-2 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2">
              <code className="flex-1 break-all text-xs text-slate-700">POST {endpoint}</code>
              <CopyBtn text={endpoint} small />
            </div>
            <div className="relative rounded-lg border border-slate-200 bg-slate-50 p-3">
              <div className="absolute right-2 top-2"><CopyBtn text={curl.replace(/\\\n\s*/g, ' ')} small /></div>
              <pre className="overflow-x-auto whitespace-pre font-mono text-xs text-slate-600">{curl}</pre>
            </div>
            <p className="mt-2 text-xs leading-relaxed text-slate-400">
              批量上传时，请求体传 <code>{'{ "events": [ ... ] }'}</code>。字段 <code>source_system</code> 与 <code>source_ref</code>
              构成幂等键，重复投递同一 <code>source_ref</code> 不会重复登记。
            </p>
          </section>

          <section className="border-t border-slate-200 pt-5">
            <h3 className="mb-3 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-slate-500">
              <KeyRound size={13} /> 生成密钥（管理员）
            </h3>
            {newKey?.plaintextKey && (
              <div className="mb-3 rounded-lg border border-emerald-200 bg-emerald-50 p-3">
                <div className="mb-1.5 flex items-center gap-1.5 text-xs font-medium text-emerald-700">
                  <ShieldCheck size={13} /> 密钥“{newKey.name}”已生成，可在下方列表继续复制
                </div>
                <div className="flex items-center gap-2 rounded-md bg-white px-2 py-1.5">
                  <code className="flex-1 break-all text-xs text-slate-700">{newKey.plaintextKey}</code>
                  <CopyBtn text={newKey.plaintextKey} small />
                </div>
              </div>
            )}
            {error && (
              <div className="mb-3 flex items-center gap-2 rounded-lg bg-red-50 px-3 py-2 text-xs text-red-600">
                <AlertTriangle size={13} /> {error}
              </div>
            )}
            <div className="flex items-end gap-2">
              <Input label="密钥名（= 第三方来源标识）" value={name} onChange={event => setName(event.target.value)}
                placeholder="如：MES产线网关" className="flex-1" />
              <Input label="限定来源系统（可选）" value={scope} onChange={event => setScope(event.target.value)}
                placeholder="如：MES" className="flex-1" />
              <Button onClick={() => createMutation.mutate()} loading={createMutation.isPending} disabled={!name.trim()}>
                <Plus size={14} /> 生成
              </Button>
            </div>
          </section>

          <section className="border-t border-slate-200 pt-5">
            <div className="mb-3 flex items-center justify-between gap-3">
              <div>
                <h3 className="text-sm font-semibold text-slate-800">已有密钥</h3>
                <p className="mt-0.5 text-xs text-slate-400">按名称、前缀、状态或来源系统查询</p>
              </div>
              <div className="flex items-center gap-2 text-xs tabular-nums text-slate-400">
                {keyQuery.isFetching && !keyQuery.isLoading && <Loader2 size={13} className="animate-spin text-emerald-600" />}
                共 {total} 把
              </div>
            </div>

            <div className="mb-3 grid grid-cols-1 gap-2 sm:grid-cols-[minmax(0,1.5fr)_120px_minmax(0,1fr)_36px]">
              <label className="relative">
                <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                <input
                  value={search}
                  onChange={event => setSearch(event.target.value)}
                  placeholder="搜索名称、前缀或来源"
                  className="h-9 w-full rounded-lg border border-slate-200 bg-white pl-8 pr-3 text-xs text-slate-700 outline-none transition focus:border-emerald-400 focus:ring-2 focus:ring-emerald-100"
                />
              </label>
              <select
                value={status}
                onChange={event => setStatus(event.target.value as typeof status)}
                className="h-9 rounded-lg border border-slate-200 bg-white px-2.5 text-xs text-slate-600 outline-none transition focus:border-emerald-400 focus:ring-2 focus:ring-emerald-100"
                aria-label="密钥状态"
              >
                <option value="all">全部状态</option>
                <option value="active">有效</option>
                <option value="revoked">已吊销</option>
              </select>
              <input
                value={sourceSystem}
                onChange={event => setSourceSystem(event.target.value)}
                placeholder="来源系统，如 MES"
                className="h-9 rounded-lg border border-slate-200 bg-white px-3 text-xs text-slate-700 outline-none transition focus:border-emerald-400 focus:ring-2 focus:ring-emerald-100"
              />
              <button
                type="button"
                onClick={clearFilters}
                disabled={!hasFilters}
                className="flex h-9 w-9 items-center justify-center rounded-lg border border-slate-200 bg-white text-slate-400 transition-colors hover:bg-slate-50 hover:text-slate-700 disabled:cursor-not-allowed disabled:opacity-40"
                title="清除筛选"
                aria-label="清除密钥筛选"
              >
                <RotateCcw size={14} />
              </button>
            </div>

            {keyQuery.isLoading ? (
              <LoadingState />
            ) : keyQuery.isError ? (
              <div className="rounded-xl border border-red-100 bg-red-50 px-4 py-10 text-center text-sm text-red-600">
                密钥列表加载失败，请确认当前账号具备管理员权限
              </div>
            ) : keys.length ? (
              <div className="space-y-2">
                {keys.map(key => {
                  const revoked = !key.enabled || Boolean(key.revokedAt)
                  return (
                    <article key={key.id} className={`rounded-xl border px-3 py-3 transition-colors ${revoked ? 'border-slate-200 bg-slate-50/60' : 'border-slate-200 bg-white hover:border-emerald-200'}`}>
                      <div className="flex items-center gap-3">
                        <span className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-lg ${revoked ? 'bg-slate-100 text-slate-400' : 'bg-emerald-50 text-emerald-700'}`}>
                          <KeyRound size={15} />
                        </span>
                        <div className="min-w-0 flex-1">
                          <div className="flex items-center gap-2">
                            <span className="truncate text-sm font-medium text-slate-800">{key.name}</span>
                            {key.allowedSourceSystem && (
                              <span className="shrink-0 rounded-md bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-600">
                                限 {key.allowedSourceSystem}
                              </span>
                            )}
                            <span className={`shrink-0 text-[10px] font-medium ${revoked ? 'text-red-500' : 'text-emerald-600'}`}>
                              {revoked ? '已吊销' : '有效'}
                            </span>
                          </div>
                          <div className="mt-0.5 text-xs text-slate-400">
                            创建 {fmt(key.createdAt)} · 最近使用 {fmt(key.lastUsedAt)}
                          </div>
                        </div>
                        {!revoked && (
                          <button
                            type="button"
                            onClick={() => revokeMutation.mutate(key.id)}
                            disabled={revokeMutation.isPending}
                            className="inline-flex shrink-0 items-center gap-1 rounded-lg px-2 py-1.5 text-xs text-slate-400 transition-colors hover:bg-red-50 hover:text-red-600 disabled:opacity-40"
                          >
                            <Ban size={13} /> 吊销
                          </button>
                        )}
                      </div>
                      {!revoked && (
                        key.plaintextKey ? (
                          <div className="mt-2 flex items-center gap-2 rounded-lg border border-slate-100 bg-slate-50 px-2 py-1.5">
                            <code className="flex-1 break-all text-xs text-slate-600">{key.plaintextKey}</code>
                            <CopyBtn text={key.plaintextKey} small />
                          </div>
                        ) : (
                          <div className="mt-2 font-mono text-[11px] text-slate-400">
                            {key.keyPrefix}…（旧密钥未留存明文，如需复制请重新生成）
                          </div>
                        )
                      )}
                    </article>
                  )
                })}
              </div>
            ) : (
              <div className="rounded-xl border border-dashed border-slate-200 px-4 py-10 text-center">
                <KeyRound size={22} className="mx-auto text-slate-300" />
                <p className="mt-2 text-sm font-medium text-slate-600">{hasFilters ? '没有匹配的密钥' : '还没有密钥'}</p>
                <p className="mt-1 text-xs text-slate-400">{hasFilters ? '请调整筛选条件后重试' : '可在上方生成一把供第三方系统使用'}</p>
                {hasFilters && (
                  <button type="button" onClick={clearFilters} className="mt-3 text-xs font-medium text-emerald-700 hover:underline">
                    清除筛选
                  </button>
                )}
              </div>
            )}

            {!keyQuery.isLoading && !keyQuery.isError && total > 0 && (
              <div className="mt-3 flex items-center justify-between border-t border-slate-100 pt-3">
                <span className="text-xs tabular-nums text-slate-400">
                  显示 {(page - 1) * KEY_PAGE_SIZE + 1}–{Math.min(page * KEY_PAGE_SIZE, total)} / {total}
                </span>
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    onClick={() => setPage(current => Math.max(1, current - 1))}
                    disabled={page <= 1 || keyQuery.isFetching}
                    className="flex h-8 w-8 items-center justify-center rounded-lg border border-slate-200 bg-white text-slate-500 transition-colors hover:bg-slate-50 disabled:opacity-40"
                    aria-label="上一页密钥"
                  >
                    <ChevronLeft size={15} />
                  </button>
                  <span className="min-w-16 text-center text-xs tabular-nums text-slate-500">{page} / {totalPages}</span>
                  <button
                    type="button"
                    onClick={() => setPage(current => Math.min(totalPages, current + 1))}
                    disabled={page >= totalPages || keyQuery.isFetching}
                    className="flex h-8 w-8 items-center justify-center rounded-lg border border-slate-200 bg-white text-slate-500 transition-colors hover:bg-slate-50 disabled:opacity-40"
                    aria-label="下一页密钥"
                  >
                    <ChevronRight size={15} />
                  </button>
                </div>
              </div>
            )}
          </section>
        </div>
      </aside>
    </div>,
    document.body,
  )
}
