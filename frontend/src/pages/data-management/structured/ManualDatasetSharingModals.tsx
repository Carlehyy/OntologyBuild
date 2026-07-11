import { useCallback, useEffect, useState } from 'react'
import { Check, CheckCircle2, Clock3, Copy, Link2, Loader2, ShieldCheck, Trash2, X, XCircle } from 'lucide-react'
import manualSharingApi, { type ManualChange, type ManualShare, type SharePermission } from '@/api/v2/manual-sharing'
import type { DatasetOverviewItem } from '@/api/v2/datasets'

const messageOf = (error: unknown, fallback: string) => {
  const e = error as { detail?: string | { message?: string }; message?: string }
  return typeof e?.detail === 'string' ? e.detail
    : typeof e?.detail === 'object' ? e.detail?.message || fallback
      : e?.message || fallback
}

const fmt = (iso?: string | null) => iso ? new Date(iso).toLocaleString('zh-CN') : '长期有效'

export function ManualShareModal({ dataset, onClose }: { dataset: DatasetOverviewItem; onClose: () => void }) {
  const [permission, setPermission] = useState<SharePermission>('view')
  const [label, setLabel] = useState('')
  const [days, setDays] = useState('30')
  const [shares, setShares] = useState<ManualShare[]>([])
  const [link, setLink] = useState('')
  const [loading, setLoading] = useState(true)
  const [creating, setCreating] = useState(false)
  const [copied, setCopied] = useState(false)
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
      const url = `${window.location.origin}${window.location.pathname}#/share/manual/${result.token}`
      setLink(url)
      await load()
    } catch (error) { setError(messageOf(error, '创建分享链接失败')) }
    finally { setCreating(false) }
  }

  const copy = async () => {
    await navigator.clipboard.writeText(link)
    setCopied(true); window.setTimeout(() => setCopied(false), 1500)
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
            <p className="mb-2 text-xs font-medium text-emerald-700">链接已生成（原始访问令牌仅本次展示）</p>
            <div className="flex gap-2"><input readOnly value={link} className="min-w-0 flex-1 rounded-lg border bg-slate-50 px-3 py-2 font-mono text-xs" /><button onClick={copy} className="inline-flex items-center gap-1 rounded-lg border px-3 text-xs text-slate-600 hover:bg-slate-50">{copied ? <Check size={13} /> : <Copy size={13} />}{copied ? '已复制' : '复制'}</button></div>
          </div>}
          {error && <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">{error}</div>}

          <div><h4 className="mb-2 text-xs font-semibold text-slate-600">已有分享</h4>
            {loading ? <p className="text-xs text-slate-400">加载中...</p> : shares.length === 0 ? <p className="rounded-lg border border-dashed p-4 text-center text-xs text-slate-400">暂无分享链接</p> : <div className="space-y-2">
              {shares.map(share => <div key={share.id} className="flex items-center gap-3 rounded-lg border px-3 py-2.5 text-xs">
                <span className={`rounded-full px-2 py-0.5 ${share.permission === 'edit' ? 'bg-amber-50 text-amber-700' : 'bg-blue-50 text-blue-700'}`}>{share.permission === 'edit' ? '可编辑' : '仅查看'}</span>
                <span className="min-w-0 flex-1 truncate text-slate-600">{share.label || '未命名分享'} · {fmt(share.expires_at)}</span>
                {share.revoked_at ? <span className="text-slate-400">已停用</span> : <button onClick={async () => { await manualSharingApi.revoke(share.id); await load() }} title="停用链接" className="text-slate-400 hover:text-red-500"><Trash2 size={13} /></button>}
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
  const load = useCallback(() => manualSharingApi.changes().then(setItems).catch(e => setError(messageOf(e, '审批任务加载失败'))).finally(() => setLoading(false)), [])
  useEffect(() => { void load() }, [load])

  const review = async (item: ManualChange, decision: 'approve' | 'reject') => {
    const comment = notes[item.id] || ''
    if (decision === 'reject' && !comment.trim()) { setError('驳回时请填写具体原因，外部维护者会在进度中看到'); return }
    setBusy(item.id); setError('')
    try { await manualSharingApi.review(item.id, decision, comment); await load(); onChanged() }
    catch (e) { setError(messageOf(e, '审批失败')) }
    finally { setBusy('') }
  }

  return <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
    <div className="flex max-h-[88vh] w-[min(96vw,900px)] flex-col overflow-hidden rounded-2xl bg-white shadow-2xl">
      <div className="flex items-center gap-3 border-b px-5 py-4"><ShieldCheck size={18} className="text-emerald-700" /><div className="flex-1"><h3 className="font-semibold">人工数据集审批任务</h3><p className="text-xs text-slate-400">只有批准后，外部维护者提交的修改才会成为正式新版本</p></div><button onClick={onClose}><X size={17} className="text-slate-400" /></button></div>
      <div className="overflow-y-auto p-5 space-y-3">
        {error && <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">{error}</div>}
        {loading ? <p className="p-8 text-center text-sm text-slate-400">加载中...</p> : items.length === 0 ? <p className="rounded-xl border border-dashed p-10 text-center text-sm text-slate-400">暂无审批任务</p> : items.map(item => {
          const pending = item.status === 'pending'; const s = item.summary || { updated: 0, inserted: 0, deleted: 0, result_rows: 0 }
          return <div key={item.id} className={`rounded-xl border p-4 ${pending ? 'border-amber-200 bg-amber-50/30' : 'border-slate-200'}`}>
            <div className="flex items-start gap-3"><span className={`mt-0.5 grid h-8 w-8 place-items-center rounded-lg ${pending ? 'bg-amber-100 text-amber-700' : item.status === 'approved' ? 'bg-emerald-100 text-emerald-700' : 'bg-red-100 text-red-700'}`}>{pending ? <Clock3 size={15} /> : item.status === 'approved' ? <CheckCircle2 size={15} /> : <XCircle size={15} />}</span>
              <div className="min-w-0 flex-1"><div className="flex flex-wrap items-center gap-2"><b className="text-sm">{item.dataset_name}</b><span className="text-xs text-slate-400">基于 v{item.base_version_no}</span><span className="rounded-full bg-white px-2 py-0.5 text-[10px] text-slate-500">{item.share_label || '外部维护链接'}</span></div><p className="mt-1 text-xs text-slate-600">修改 {s.updated || 0} 行 · 新增 {s.inserted || 0} 行 · 删除 {s.deleted || 0} 行 · 审批后共 {s.result_rows || 0} 行</p><p className="mt-1 text-[11px] text-slate-400">提交于 {fmt(item.submitted_at)}{item.applied_version_no ? ` · 已生效为 v${item.applied_version_no}` : ''}</p>
                <details className="mt-2 rounded-lg border border-slate-200 bg-white px-3 py-2"><summary className="cursor-pointer text-[11px] font-medium text-slate-500">查看逐行修改明细</summary><div className="mt-2 max-h-48 space-y-1.5 overflow-auto font-mono text-[10px] text-slate-600">{(item.edits?.updates || []).map((edit, index) => <p key={`u-${index}`} className="rounded bg-amber-50 px-2 py-1">修改 {JSON.stringify(edit.key)} → {JSON.stringify(edit.values)}</p>)}{(item.edits?.inserts || []).map((edit, index) => <p key={`i-${index}`} className="rounded bg-emerald-50 px-2 py-1">新增 {JSON.stringify(edit.values)}</p>)}{(item.edits?.deletes || []).map((edit, index) => <p key={`d-${index}`} className="rounded bg-red-50 px-2 py-1">删除 {JSON.stringify(edit.key)}</p>)}</div></details>
                {item.review_comment && <p className="mt-2 rounded-lg bg-white px-3 py-2 text-xs text-slate-600">审批意见：{item.review_comment}</p>}</div>
            </div>
            {pending && <div className="mt-3 flex items-center gap-2 pl-11"><input value={notes[item.id] || ''} onChange={e => setNotes(n => ({ ...n, [item.id]: e.target.value }))} placeholder="审批意见（驳回时必填）" className="min-w-0 flex-1 rounded-lg border bg-white px-3 py-2 text-xs" /><button disabled={busy === item.id} onClick={() => review(item, 'reject')} className="rounded-lg border border-red-200 bg-white px-3 py-2 text-xs text-red-600 hover:bg-red-50">驳回</button><button disabled={busy === item.id} onClick={() => review(item, 'approve')} className="inline-flex items-center gap-1 rounded-lg bg-emerald-600 px-3 py-2 text-xs text-white hover:bg-emerald-700">{busy === item.id && <Loader2 size={12} className="animate-spin" />}批准生效</button></div>}
          </div>
        })}
      </div>
    </div>
  </div>
}
