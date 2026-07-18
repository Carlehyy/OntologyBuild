import { useCallback, useEffect, useState } from 'react'
import {
  Check, CheckCircle2, ChevronLeft, ChevronRight, Clock3, Copy, Link2, Loader2,
  Search, ShieldCheck, Trash2, X, XCircle,
} from 'lucide-react'
import manualSharingApi, {
  type ChangeStatus, type ManualChange, type ManualShare, type SharePermission,
} from '@/api/v2/manual-sharing'
import type { DatasetOverviewItem } from '@/api/v2/datasets'

const messageOf = (error: unknown, fallback: string) => {
  const e = error as { detail?: string | { message?: string }; message?: string }
  return typeof e?.detail === 'string' ? e.detail
    : typeof e?.detail === 'object' ? e.detail?.message || fallback
      : e?.message || fallback
}

const fmt = (iso?: string | null) => iso ? new Date(iso).toLocaleString('zh-CN') : '长期有效'
const shareUrl = (token: string) => `${window.location.origin}${window.location.pathname}#/share/manual/${encodeURIComponent(token)}`

/** Clipboard API may be unavailable or denied on HTTP/private-network deployments. */
const copyText = async (value: string) => {
  let clipboardError: unknown
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(value)
      return
    } catch (error) {
      clipboardError = error
    }
  }

  const textarea = document.createElement('textarea')
  textarea.value = value
  textarea.readOnly = true
  textarea.setAttribute('aria-hidden', 'true')
  textarea.style.position = 'fixed'
  textarea.style.left = '-9999px'
  textarea.style.opacity = '0'
  document.body.appendChild(textarea)
  textarea.focus()
  textarea.select()
  textarea.setSelectionRange(0, value.length)
  try {
    if (!document.execCommand('copy')) throw clipboardError || new Error('copy command failed')
  } finally {
    textarea.remove()
  }
}

export function ManualShareModal({ dataset, onClose }: { dataset: DatasetOverviewItem; onClose: () => void }) {
  const [permission, setPermission] = useState<SharePermission>('view')
  const [label, setLabel] = useState('')
  const [days, setDays] = useState('30')
  const [shares, setShares] = useState<ManualShare[]>([])
  const [link, setLink] = useState('')
  const [loading, setLoading] = useState(true)
  const [creating, setCreating] = useState(false)
  const [copied, setCopied] = useState(false)
  const [copiedShareId, setCopiedShareId] = useState('')
  const [error, setError] = useState('')

  const load = useCallback(() => manualSharingApi.list(dataset.id)
    .then(setShares).catch(error => setError(messageOf(error, '分享记录加载失败')))
    .finally(() => setLoading(false)), [dataset.id])
  useEffect(() => { void load() }, [load])

  const create = async () => {
    setCreating(true); setError(''); setLink('')
    try {
      const result = await manualSharingApi.create(dataset.id, {
        permission, label, expires_in_days: days ? Number(days) : null,
      })
      setLink(shareUrl(result.token))
      await load()
    } catch (error) { setError(messageOf(error, '创建分享链接失败')) }
    finally { setCreating(false) }
  }

  const copy = async () => {
    try {
      await copyText(link)
      setError('')
      setCopied(true); window.setTimeout(() => setCopied(false), 1500)
    } catch {
      setError('自动复制失败，请选中链接后使用 Ctrl/Cmd + C 复制')
    }
  }

  const copyExisting = async (share: ManualShare) => {
    if (!share.token) return
    try {
      await copyText(shareUrl(share.token))
      setError('')
      setCopiedShareId(share.id)
      window.setTimeout(() => setCopiedShareId(current => current === share.id ? '' : current), 1500)
    } catch {
      setError('自动复制失败，请选中链接后使用 Ctrl/Cmd + C 复制')
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="flex max-h-[86vh] w-[min(94vw,680px)] flex-col overflow-hidden rounded-2xl bg-white shadow-2xl">
        <div className="flex items-center gap-3 border-b px-5 py-4">
          <span className="grid h-9 w-9 place-items-center rounded-xl bg-emerald-50 text-emerald-700"><Link2 size={17} /></span>
          <div className="min-w-0 flex-1"><h3 className="font-semibold text-slate-900">分享人工数据集</h3><p className="truncate text-xs text-slate-400">{dataset.name} · 链接持有者无需注册</p></div>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-700"><X size={17} /></button>
        </div>

        <div className="overflow-y-auto p-5 space-y-5">
          <div className="rounded-xl border border-emerald-100 bg-emerald-50/50 p-4 space-y-3">
            <div className="grid gap-3 sm:grid-cols-3">
              <label className="text-xs text-slate-600">权限
                <select value={permission} onChange={e => setPermission(e.target.value as SharePermission)} className="mt-1.5 w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm">
                  <option value="view">仅查看</option><option value="edit">可编辑（需审批）</option>
                </select>
              </label>
              <label className="text-xs text-slate-600">有效期
                <select value={days} onChange={e => setDays(e.target.value)} className="mt-1.5 w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm">
                  <option value="7">7 天</option><option value="30">30 天</option><option value="90">90 天</option><option value="">长期有效</option>
                </select>
              </label>
              <label className="text-xs text-slate-600">备注
                <input value={label} onChange={e => setLabel(e.target.value)} placeholder="如：供应商维护" className="mt-1.5 w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm" />
              </label>
            </div>
            <p className="text-[11px] leading-5 text-emerald-800">可编辑链接提交时会先做主键与字段类型校验，校验通过后只生成待审批任务，不会直接改动正式数据。</p>
            <button onClick={create} disabled={creating} className="inline-flex items-center gap-1.5 rounded-lg bg-emerald-600 px-3.5 py-2 text-xs font-medium text-white hover:bg-emerald-700 disabled:opacity-50">
              {creating ? <Loader2 size={13} className="animate-spin" /> : <Link2 size={13} />} 生成分享链接
            </button>
          </div>

          {link && <div className="rounded-xl border border-emerald-200 bg-white p-3">
            <p className="mb-2 text-xs font-medium text-emerald-700">链接已生成，并会保存在下方的分享列表中</p>
            <div className="flex gap-2"><input readOnly value={link} className="min-w-0 flex-1 rounded-lg border bg-slate-50 px-3 py-2 font-mono text-xs" /><button onClick={copy} className="inline-flex items-center gap-1 rounded-lg border px-3 text-xs text-slate-600 hover:bg-slate-50">{copied ? <Check size={13} /> : <Copy size={13} />}{copied ? '已复制' : '复制'}</button></div>
          </div>}
          {error && <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">{error}</div>}

          <div><h4 className="mb-2 text-xs font-semibold text-slate-600">已有分享</h4>
            {loading ? <p className="text-xs text-slate-400">加载中...</p> : shares.length === 0 ? <p className="rounded-lg border border-dashed p-4 text-center text-xs text-slate-400">暂无分享链接</p> : <div className="space-y-2">
              {shares.map(share => <div key={share.id} className="rounded-lg border px-3 py-2.5 text-xs">
                <div className="flex items-center gap-3">
                  <span className={`rounded-full px-2 py-0.5 ${share.permission === 'edit' ? 'bg-amber-50 text-amber-700' : 'bg-blue-50 text-blue-700'}`}>{share.permission === 'edit' ? '可编辑' : '仅查看'}</span>
                  <span className="min-w-0 flex-1 truncate text-slate-600">{share.label || '未命名分享'} · {fmt(share.expires_at)}</span>
                  {share.revoked_at ? <span className="text-slate-400">已停用</span> : <button onClick={async () => { await manualSharingApi.revoke(share.id); await load() }} title="停用链接" className="text-slate-400 hover:text-red-500"><Trash2 size={13} /></button>}
                </div>
                {!share.revoked_at && (share.token ? <div className="mt-2 flex gap-2">
                  <input readOnly value={shareUrl(share.token)} className="min-w-0 flex-1 rounded-lg border bg-slate-50 px-3 py-2 font-mono text-[11px]" />
                  <button onClick={() => void copyExisting(share)} className="inline-flex items-center gap-1 rounded-lg border px-3 text-xs text-slate-600 hover:bg-slate-50">{copiedShareId === share.id ? <Check size={13} /> : <Copy size={13} />}{copiedShareId === share.id ? '已复制' : '复制'}</button>
                </div> : <p className="mt-2 rounded-lg bg-amber-50 px-3 py-2 text-[11px] text-amber-700">此链接创建于令牌持久化功能上线前，无法恢复原地址；请停用后重新生成。</p>)}
              </div>)}
            </div>}
          </div>
        </div>
      </div>
    </div>
  )
}

export function ManualApprovalModal({ onClose, onChanged }: { onClose: () => void; onChanged: () => void }) {
  const [items, setItems] = useState<ManualChange[]>([])
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState('')
  const [notes, setNotes] = useState<Record<string, string>>({})
  const [error, setError] = useState('')
  const [statusFilter, setStatusFilter] = useState<ChangeStatus | 'all'>('pending')
  const [search, setSearch] = useState('')
  const [searchQuery, setSearchQuery] = useState('')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(10)
  const [total, setTotal] = useState(0)

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const result = await manualSharingApi.changes({
        status: statusFilter === 'all' ? undefined : statusFilter,
        search: searchQuery || undefined,
        page,
        page_size: pageSize,
      })
      setItems(result.items)
      setTotal(result.total)
      if (page > 1 && result.items.length === 0 && result.total > 0) {
        setPage(current => Math.max(1, current - 1))
      }
    } catch (loadError) {
      setItems([])
      setTotal(0)
      setError(messageOf(loadError, '审批任务加载失败'))
    } finally {
      setLoading(false)
    }
  }, [page, pageSize, searchQuery, statusFilter])
  useEffect(() => { void load() }, [load])
  useEffect(() => {
    const timer = window.setTimeout(() => {
      setSearchQuery(search.trim())
      setPage(1)
    }, 250)
    return () => window.clearTimeout(timer)
  }, [search])

  const review = async (item: ManualChange, decision: 'approve' | 'reject') => {
    const comment = notes[item.id] || ''
    if (decision === 'reject' && !comment.trim()) { setError('驳回时请填写具体原因，外部维护者会在进度中看到'); return }
    setBusy(item.id); setError('')
    try {
      await manualSharingApi.review(item.id, decision, comment)
      setNotes(current => {
        const next = { ...current }
        delete next[item.id]
        return next
      })
      await load()
      onChanged()
    }
    catch (e) { setError(messageOf(e, '审批失败')) }
    finally { setBusy('') }
  }

  const totalPages = Math.max(1, Math.ceil(total / pageSize))
  const rangeStart = total ? (page - 1) * pageSize + 1 : 0
  const rangeEnd = Math.min(page * pageSize, total)

  return <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/40 p-4 backdrop-blur-[2px]">
    <div className="flex h-[min(88vh,820px)] w-[min(96vw,1040px)] flex-col overflow-hidden rounded-2xl border border-white/70 bg-white shadow-[0_24px_80px_rgba(15,23,42,0.22)]" role="dialog" aria-modal="true" aria-labelledby="manual-approval-title">
      <div className="flex items-center gap-3 border-b border-slate-100 px-5 py-4">
        <span className="grid h-9 w-9 place-items-center rounded-xl bg-emerald-50 text-emerald-700"><ShieldCheck size={17} /></span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2"><h3 id="manual-approval-title" className="font-semibold text-slate-900">人工数据集审批任务</h3><span className="rounded-md bg-slate-100 px-2 py-0.5 text-[10px] tabular-nums text-slate-500">{total} 项</span></div>
          <p className="text-xs text-slate-400">核对逐行明细并批准或驳回；只有批准后修改才会生成正式新版本</p>
        </div>
        <button type="button" onClick={onClose} aria-label="关闭审批任务" className="grid h-8 w-8 place-items-center rounded-lg text-slate-400 transition hover:bg-slate-100 hover:text-slate-700"><X size={17} /></button>
      </div>

      <div className="flex flex-wrap items-center gap-3 border-b border-slate-100 bg-slate-50/70 px-5 py-3">
        <label className="flex items-center gap-2 text-xs text-slate-500">
          审批状态
          <select value={statusFilter} onChange={event => { setStatusFilter(event.target.value as ChangeStatus | 'all'); setPage(1) }} className="h-8 rounded-lg border border-slate-200 bg-white px-2.5 text-xs text-slate-700 outline-none focus:border-emerald-500">
            <option value="pending">待审批</option>
            <option value="approved">已批准</option>
            <option value="rejected">已驳回</option>
            <option value="all">全部状态</option>
          </select>
        </label>
        <label className="relative min-w-56 flex-1 sm:max-w-sm">
          <Search size={13} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
          <input value={search} onChange={event => setSearch(event.target.value)} placeholder="按数据集或分享备注筛选" aria-label="筛选审批任务" className="h-8 w-full rounded-lg border border-slate-200 bg-white pl-8 pr-8 text-xs text-slate-700 outline-none transition focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500/10" />
          {search && <button type="button" onClick={() => setSearch('')} aria-label="清除审批任务筛选" className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-0.5 text-slate-400 hover:text-slate-700"><X size={12} /></button>}
        </label>
        <span className="ml-auto text-[11px] tabular-nums text-slate-400">显示 {rangeStart}–{rangeEnd} / {total}</span>
      </div>

      {error && <div className="mx-5 mt-3 flex items-center gap-2 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700"><XCircle size={13} className="shrink-0" /><span className="flex-1">{error}</span><button type="button" onClick={() => setError('')}><X size={12} /></button></div>}

      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-5">
        {loading ? <div className="flex h-full min-h-48 items-center justify-center text-sm text-slate-400"><Loader2 size={15} className="mr-2 animate-spin" />正在分页查询审批任务…</div> : items.length === 0 ? <div className="grid min-h-56 place-items-center rounded-xl border border-dashed border-slate-200 bg-slate-50/40 text-center"><div><ShieldCheck size={28} className="mx-auto mb-2 text-slate-300" /><p className="text-sm font-medium text-slate-500">当前筛选条件下暂无审批任务</p><p className="mt-1 text-xs text-slate-400">可切换状态或清除搜索条件查看其他任务</p></div></div> : items.map(item => {
          const pending = item.status === 'pending'
          const summary = item.summary || { updated: 0, inserted: 0, deleted: 0, result_rows: 0 }
          const statusLabel = pending ? '待审批' : item.status === 'approved' ? '已批准' : '已驳回'
          return <article key={item.id} className={`rounded-xl border p-4 transition ${pending ? 'border-amber-200 bg-amber-50/30' : 'border-slate-200 bg-white'}`}>
            <div className="flex items-start gap-3">
              <span className={`mt-0.5 grid h-8 w-8 shrink-0 place-items-center rounded-lg ${pending ? 'bg-amber-100 text-amber-700' : item.status === 'approved' ? 'bg-emerald-100 text-emerald-700' : 'bg-red-100 text-red-700'}`}>{pending ? <Clock3 size={15} /> : item.status === 'approved' ? <CheckCircle2 size={15} /> : <XCircle size={15} />}</span>
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <b className="text-sm text-slate-900">{item.dataset_name}</b>
                  <span className="text-xs text-slate-400">基于 v{item.base_version_no}</span>
                  <span className="rounded-md bg-white px-2 py-0.5 text-[10px] text-slate-500 ring-1 ring-inset ring-slate-200">{item.share_label || '外部维护链接'}</span>
                  <span className={`ml-auto rounded-md px-2 py-0.5 text-[10px] font-medium ${pending ? 'bg-amber-100 text-amber-700' : item.status === 'approved' ? 'bg-emerald-50 text-emerald-700' : 'bg-red-50 text-red-700'}`}>{statusLabel}</span>
                </div>
                <p className="mt-1.5 text-xs text-slate-600">修改 {summary.updated || 0} 行 · 新增 {summary.inserted || 0} 行 · 删除 {summary.deleted || 0} 行 · 审批后共 {summary.result_rows || 0} 行</p>
                <p className="mt-1 text-[11px] text-slate-400">提交于 {fmt(item.submitted_at)}{item.applied_version_no ? ` · 已生效为 v${item.applied_version_no}` : ''}</p>
                <details className="mt-2 rounded-lg border border-slate-200 bg-white px-3 py-2">
                  <summary className="cursor-pointer text-[11px] font-medium text-slate-500">查看逐行修改明细</summary>
                  <div className="mt-2 max-h-48 space-y-1.5 overflow-auto font-mono text-[10px] text-slate-600">
                    {(item.edits?.updates || []).map((edit, index) => <p key={`u-${index}`} className="rounded bg-amber-50 px-2 py-1">修改 {JSON.stringify(edit.key)} → {JSON.stringify(edit.values)}</p>)}
                    {(item.edits?.inserts || []).map((edit, index) => <p key={`i-${index}`} className="rounded bg-emerald-50 px-2 py-1">新增 {JSON.stringify(edit.values)}</p>)}
                    {(item.edits?.deletes || []).map((edit, index) => <p key={`d-${index}`} className="rounded bg-red-50 px-2 py-1">删除 {JSON.stringify(edit.key)}</p>)}
                  </div>
                </details>
                {item.review_comment && <p className="mt-2 rounded-lg bg-white px-3 py-2 text-xs text-slate-600 ring-1 ring-inset ring-slate-100">审批意见：{item.review_comment}</p>}
              </div>
            </div>
            {pending && <div className="mt-3 flex flex-wrap items-center gap-2 pl-0 sm:pl-11"><input value={notes[item.id] || ''} onChange={event => setNotes(current => ({ ...current, [item.id]: event.target.value }))} placeholder="审批意见（驳回时必填）" aria-label={`${item.dataset_name} 的审批意见`} className="h-9 min-w-56 flex-1 rounded-lg border border-slate-200 bg-white px-3 text-xs outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500/10" /><button type="button" disabled={busy === item.id} onClick={() => void review(item, 'reject')} className="h-9 rounded-lg border border-red-200 bg-white px-3 text-xs font-medium text-red-600 transition hover:bg-red-50 disabled:opacity-50">驳回</button><button type="button" disabled={busy === item.id} onClick={() => void review(item, 'approve')} className="inline-flex h-9 items-center gap-1 rounded-lg bg-emerald-600 px-3 text-xs font-medium text-white transition hover:bg-emerald-700 disabled:opacity-50">{busy === item.id && <Loader2 size={12} className="animate-spin" />}批准生效</button></div>}
          </article>
        })}
      </div>

      <div className="flex flex-wrap items-center justify-end gap-3 border-t border-slate-100 bg-slate-50/70 px-5 py-3">
        <label className="flex items-center gap-1.5 text-xs text-slate-500">每页<select value={pageSize} onChange={event => { setPageSize(Number(event.target.value)); setPage(1) }} className="h-8 rounded-lg border border-slate-200 bg-white px-2 text-xs outline-none focus:border-emerald-500" aria-label="审批任务每页显示条数">{[5, 10, 20, 50].map(size => <option key={size} value={size}>{size}</option>)}</select>条</label>
        <span className="min-w-24 text-center text-xs tabular-nums text-slate-500">第 {page} / {totalPages} 页</span>
        <button type="button" onClick={() => setPage(current => Math.max(1, current - 1))} disabled={page <= 1 || loading} aria-label="审批任务上一页" className="grid h-8 w-8 place-items-center rounded-lg border border-slate-200 bg-white text-slate-500 transition hover:border-emerald-200 hover:text-emerald-700 disabled:opacity-35"><ChevronLeft size={13} /></button>
        <button type="button" onClick={() => setPage(current => Math.min(totalPages, current + 1))} disabled={page >= totalPages || loading} aria-label="审批任务下一页" className="grid h-8 w-8 place-items-center rounded-lg border border-slate-200 bg-white text-slate-500 transition hover:border-emerald-200 hover:text-emerald-700 disabled:opacity-35"><ChevronRight size={13} /></button>
      </div>
    </div>
  </div>
}
