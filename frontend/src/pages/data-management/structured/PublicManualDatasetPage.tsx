import { useCallback, useEffect, useMemo, useState } from 'react'
import { AlertCircle, CheckCircle2, ChevronLeft, ChevronRight, Clock3, Database, ExternalLink, Eye, KeyRound, Loader2, Plus, RefreshCw, Save, Trash2, Undo2, XCircle } from 'lucide-react'
import { useParams } from 'react-router-dom'
import { publicManualSharingApi, type PublicManualDataset } from '@/api/public-manual-sharing'

const PAGE_SIZE = 50
type CellMap = Record<string, string>
type EditRow = { orig: CellMap; cur: CellMap; deleted: boolean }

const str = (value: unknown) => value == null ? '' : typeof value === 'object' ? JSON.stringify(value) : String(value)
const fmt = (iso?: string | null) => iso ? new Date(iso).toLocaleString('zh-CN') : '—'

function validateValue(column: string, type: string, value: string): string | null {
  if (value === '') return null
  if (type === 'integer' && !/^[+-]?\d+$/.test(value)) return `「${column}」必须是整数`
  if (type === 'float' && !Number.isFinite(Number(value))) return `「${column}」必须是数字`
  if (type === 'boolean' && !['true', 'false', '1', '0', '是', '否'].includes(value.toLowerCase())) return `「${column}」必须是布尔值（true/false、1/0 或 是/否）`
  if (type === 'timestamp' && Number.isNaN(Date.parse(value))) return `「${column}」必须是合法日期时间`
  if (type === 'json') { try { JSON.parse(value) } catch { return `「${column}」必须是合法 JSON` } }
  return null
}

export default function PublicManualDatasetPage() {
  const { token = '' } = useParams()
  const [data, setData] = useState<PublicManualDataset | null>(null)
  const [rows, setRows] = useState<EditRow[]>([])
  const [inserts, setInserts] = useState<CellMap[]>([])
  const [offset, setOffset] = useState(0)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [success, setSuccess] = useState('')

  const columns = data?.dataset.columns || []
  const pkCols = useMemo(() => (data?.dataset.primary_key || '').split(',').map(v => v.trim()).filter(Boolean), [data?.dataset.primary_key])
  const pending = data?.changes.some(item => item.status === 'pending') || false
  const editable = data?.share.permission === 'edit' && !pending
  const dirty = inserts.length > 0 || rows.some(row => row.deleted || columns.some(col => row.cur[col] !== row.orig[col]))

  const hydrate = useCallback((result: PublicManualDataset, off: number) => {
    const cols = result.dataset.columns
    setRows(result.dataset.rows.map(raw => {
      const map = Object.fromEntries(cols.map(col => [col, str(raw[col])])) as CellMap
      return { orig: { ...map }, cur: { ...map }, deleted: false }
    }))
    setInserts([]); setData(result); setOffset(off)
  }, [])

  const load = useCallback(async (off: number) => {
    setLoading(true); setError('')
    try { hydrate(await publicManualSharingApi.get(token, off, PAGE_SIZE), off) }
    catch (e) { setError(e instanceof Error ? e.message : '分享数据加载失败') }
    finally { setLoading(false) }
  }, [hydrate, token])
  useEffect(() => { void Promise.resolve().then(() => load(0)) }, [load])

  const switchPage = (next: number) => {
    if (dirty) { setError('当前页有未提交修改，请先保存或刷新放弃后再翻页'); return }
    void load(Math.max(0, next))
  }

  const validate = (): string | null => {
    const activeRows = [...rows.filter(row => !row.deleted).map(row => row.cur), ...inserts]
    for (const row of activeRows) {
      for (const pk of pkCols) if (!String(row[pk] || '').trim()) return `主键列「${pk}」不能为空`
      for (const col of columns) {
        const issue = validateValue(col, data?.dataset.column_types[col] || 'string', row[col] || '')
        if (issue) return issue
      }
    }
    if (pkCols.length) {
      const keys = activeRows.map(row => pkCols.map(col => String(row[col] || '').trim()).join('\u001f'))
      if (new Set(keys).size !== keys.length) return `当前页存在重复主键（${pkCols.join(' + ')}）`
    }
    return null
  }

  const save = async () => {
    if (!data) return
    const issue = validate()
    if (issue) { setError(issue); return }
    const keyOf = (row: CellMap) => Object.fromEntries(pkCols.map(col => [col, row[col] || '']))
    const updates = rows.filter(row => !row.deleted && columns.some(col => row.cur[col] !== row.orig[col])).map(row => ({
      key: keyOf(row.orig), values: Object.fromEntries(columns.filter(col => row.cur[col] !== row.orig[col]).map(col => [col, row.cur[col]])),
    }))
    const deletes = rows.filter(row => row.deleted).map(row => ({ key: keyOf(row.orig) }))
    const insertOps = inserts.filter(row => Object.values(row).some(Boolean)).map(values => ({ values }))
    if (!updates.length && !deletes.length && !insertOps.length) { setError('没有需要提交的修改'); return }
    setSaving(true); setError(''); setSuccess('')
    try {
      await publicManualSharingApi.submit(token, { base_version_no: data.dataset.version_no, updates, deletes, inserts: insertOps })
      setSuccess('修改已通过数据合法性校验并提交审批。平台批准后才会正式生效。')
      await load(offset)
    } catch (e) { setError(e instanceof Error ? e.message : '提交失败') }
    finally { setSaving(false) }
  }

  if (loading && !data) return <div className="grid min-h-screen place-items-center bg-slate-50 text-sm text-slate-500"><span className="flex items-center gap-2"><Loader2 size={16} className="animate-spin" />正在打开分享数据...</span></div>
  if (!data) return <div className="grid min-h-screen place-items-center bg-slate-50 p-6"><div className="max-w-md rounded-2xl border bg-white p-8 text-center shadow-sm"><XCircle className="mx-auto mb-3 text-red-400" /><h1 className="font-semibold">无法访问该分享</h1><p className="mt-2 text-sm text-slate-500">{error || '链接不存在或已失效'}</p><a href={`${window.location.pathname}#/overview`} className="mt-5 inline-flex items-center gap-1 text-sm text-emerald-700">访问平台主页 <ExternalLink size={13} /></a></div></div>

  const end = Math.min(offset + rows.length, data.dataset.total_rows)
  return <div className="min-h-screen bg-slate-50 text-slate-800">
    <header className="border-b bg-white"><div className="mx-auto flex max-w-[1500px] items-center gap-3 px-5 py-3"><span className="grid h-9 w-9 place-items-center rounded-xl bg-emerald-600 text-white"><Database size={17} /></span><div className="min-w-0 flex-1"><h1 className="truncate text-sm font-semibold">{data.dataset.name}</h1><p className="text-xs text-slate-400">人工数据集在线{data.share.permission === 'edit' ? '维护' : '查看'} · v{data.dataset.version_no} · {data.dataset.total_rows} 行</p></div><span className={`inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-xs ${data.share.permission === 'edit' ? 'bg-amber-50 text-amber-700' : 'bg-blue-50 text-blue-700'}`}>{data.share.permission === 'edit' ? <Save size={12} /> : <Eye size={12} />}{data.share.permission === 'edit' ? '可编辑，审批后生效' : '仅查看'}</span><a href={`${window.location.pathname}#/overview`} className="ml-2 inline-flex items-center gap-1 text-xs text-emerald-700 hover:underline">访问平台主页 <ExternalLink size={12} /></a></div></header>

    <main className="mx-auto max-w-[1500px] space-y-4 p-5">
      {data.share.label && <p className="rounded-xl border border-emerald-100 bg-emerald-50 px-4 py-2.5 text-xs text-emerald-800">分享说明：{data.share.label}</p>}
      {pending && <div className="flex items-center gap-2 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800"><Clock3 size={15} />本次修改正在审批中，审批完成前暂不可继续编辑。可在下方查看进度与意见。</div>}
      {error && <div className="flex items-center gap-2 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700"><AlertCircle size={15} /><span className="flex-1">{error}</span><button onClick={() => setError('')}><XCircle size={14} /></button></div>}
      {success && <div className="flex items-center gap-2 rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-700"><CheckCircle2 size={15} />{success}</div>}

      <section className="overflow-hidden rounded-2xl border bg-white shadow-sm">
        <div className="flex items-center gap-3 border-b px-4 py-3"><div className="flex-1 text-xs text-slate-500">{pkCols.length ? <span className="inline-flex items-center gap-1 rounded-md border border-amber-200 bg-amber-50 px-2 py-1 text-amber-700"><KeyRound size={10} />主键：{pkCols.join(' + ')}</span> : '未声明主键：仅支持新增行'}</div><button onClick={() => { if (!dirty || window.confirm('放弃当前未提交的修改并刷新？')) void load(offset) }} className="inline-flex items-center gap-1 text-xs text-slate-500 hover:text-slate-800"><RefreshCw size={12} />刷新</button></div>
        <div className="overflow-auto p-4">
          <table className="w-full min-w-max border-separate border-spacing-0 text-xs"><thead className="sticky top-0"><tr><th className="border-b border-r bg-slate-50 px-2 py-2 font-normal text-slate-400">#</th>{columns.map(col => <th key={col} className="border-b bg-slate-50 px-3 py-2 text-left font-medium text-slate-600">{col}{pkCols.includes(col) && <KeyRound size={9} className="ml-1 inline text-amber-500" />}<small className="ml-1 font-normal text-slate-400">{data.dataset.column_types[col] && data.dataset.column_types[col] !== 'string' ? data.dataset.column_types[col] : ''}</small></th>)}<th className="border-b bg-slate-50" /></tr></thead>
            <tbody>{rows.map((row, index) => <tr key={index} className={row.deleted ? 'opacity-40' : ''}><td className="border-b border-r px-2 text-center text-slate-300">{offset + index + 1}</td>{columns.map(col => <td key={col} className="border-b p-0"><input value={row.cur[col]} disabled={!editable || row.deleted || (!pkCols.length && true)} onChange={e => setRows(list => list.map((item, i) => i === index ? { ...item, cur: { ...item.cur, [col]: e.target.value } } : item))} className={`min-w-[8rem] bg-transparent px-3 py-2 outline-none focus:bg-emerald-50 disabled:cursor-not-allowed ${row.cur[col] !== row.orig[col] ? 'bg-amber-50 text-amber-900' : ''}`} /></td>)}<td className="border-b px-2">{editable && pkCols.length > 0 && <button onClick={() => setRows(list => list.map((item, i) => i === index ? { ...item, deleted: !item.deleted } : item))} className="text-slate-300 hover:text-red-500">{row.deleted ? <Undo2 size={13} /> : <Trash2 size={13} />}</button>}</td></tr>)}
              {inserts.map((row, index) => <tr key={`new-${index}`} className="bg-emerald-50/40"><td className="border-b border-r px-2 text-center text-emerald-500">新</td>{columns.map(col => <td key={col} className="border-b p-0"><input value={row[col]} placeholder={pkCols.includes(col) ? '主键，必填' : ''} onChange={e => setInserts(list => list.map((item, i) => i === index ? { ...item, [col]: e.target.value } : item))} className="min-w-[8rem] bg-transparent px-3 py-2 outline-none focus:bg-emerald-50" /></td>)}<td className="border-b px-2"><button onClick={() => setInserts(list => list.filter((_, i) => i !== index))} className="text-slate-300 hover:text-red-500"><Trash2 size={13} /></button></td></tr>)}</tbody>
          </table>
        </div>
        <div className="flex items-center gap-3 border-t bg-slate-50/70 px-4 py-3"><button onClick={() => setInserts(list => [...list, Object.fromEntries(columns.map(col => [col, '']))])} disabled={!editable} className="inline-flex items-center gap-1 rounded-lg border bg-white px-3 py-1.5 text-xs text-slate-600 disabled:opacity-40"><Plus size={12} />新增行</button><div className="flex items-center gap-1 text-xs text-slate-400"><button onClick={() => switchPage(offset - PAGE_SIZE)} disabled={offset === 0}><ChevronLeft size={14} /></button><span>{data.dataset.total_rows === 0 ? '0' : `${offset + 1}–${end}`} / {data.dataset.total_rows} 行</span><button onClick={() => switchPage(offset + PAGE_SIZE)} disabled={end >= data.dataset.total_rows}><ChevronRight size={14} /></button></div>{dirty && <span className="text-xs text-amber-600">有未提交修改</span>}<button onClick={save} disabled={!editable || !dirty || saving} className="ml-auto inline-flex items-center gap-1.5 rounded-lg bg-emerald-600 px-4 py-2 text-xs font-medium text-white hover:bg-emerald-700 disabled:opacity-40">{saving ? <Loader2 size={13} className="animate-spin" /> : <Save size={13} />}保存并提交审批</button></div>
      </section>

      <section className="rounded-2xl border bg-white p-4 shadow-sm"><h2 className="mb-3 text-sm font-semibold">审批进度</h2>{data.changes.length === 0 ? <p className="text-xs text-slate-400">尚未提交过修改</p> : <div className="space-y-2">{data.changes.map(change => <div key={change.id} className="flex items-start gap-3 rounded-xl border px-3 py-3"><span className={`grid h-8 w-8 place-items-center rounded-lg ${change.status === 'pending' ? 'bg-amber-50 text-amber-600' : change.status === 'approved' ? 'bg-emerald-50 text-emerald-600' : 'bg-red-50 text-red-600'}`}>{change.status === 'pending' ? <Clock3 size={14} /> : change.status === 'approved' ? <CheckCircle2 size={14} /> : <XCircle size={14} />}</span><div><p className="text-xs font-medium">{change.status === 'pending' ? '等待平台审批' : change.status === 'approved' ? `已批准${change.applied_version_no ? `，生效为 v${change.applied_version_no}` : ''}` : '已驳回'}</p><p className="mt-1 text-[11px] text-slate-400">{fmt(change.submitted_at)} · 修改 {change.summary.updated || 0} / 新增 {change.summary.inserted || 0} / 删除 {change.summary.deleted || 0}</p>{change.review_comment && <p className="mt-1.5 text-xs text-slate-600">审批意见：{change.review_comment}</p>}</div></div>)}</div>}</section>
    </main>
  </div>
}
