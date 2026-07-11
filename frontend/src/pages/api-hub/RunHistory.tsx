import { useCallback, useEffect, useState } from 'react'
import { ChevronLeft, ChevronRight, Clock3, RefreshCw, Search } from 'lucide-react'
import { apiError, apiHub, type RunDetail, type RunSummary } from '@/api/apiHub'
import { Button } from '@/components/ui/Button'
import { Modal } from '@/components/ui/Modal'

const size = 20

export default function RunHistory() {
  const [items, setItems] = useState<RunSummary[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [keyword, setKeyword] = useState('')
  const [start, setStart] = useState('')
  const [end, setEnd] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [detail, setDetail] = useState<RunDetail | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const result = await apiHub.listRuns({
        page, size, keyword,
        start: start ? new Date(`${start}T00:00:00`).toISOString() : '',
        end: end ? new Date(`${end}T23:59:59.999`).toISOString() : '',
      })
      setItems(result.items)
      setTotal(result.total)
      setError('')
    } catch (error) { setError(apiError(error)) }
    finally { setLoading(false) }
  }, [end, keyword, page, start])

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- query changes hydrate server results
    void load()
  }, [load])

  const open = async (item: RunSummary) => {
    try { setDetail(await apiHub.getRun(item.interface_id, item.id)) }
    catch (error) { setError(apiError(error)) }
  }
  const pages = Math.max(1, Math.ceil(total / size))

  return (
    <div className="h-full min-h-0 p-4">
      <section className="flex h-full min-h-0 flex-col overflow-hidden rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-elevated)]">
        <div className="flex shrink-0 items-center gap-3 border-b border-[var(--color-border)] p-4">
          <label className="flex h-9 min-w-[260px] items-center gap-2 rounded-md border border-[var(--color-border)] bg-[var(--color-bg-base)] px-3">
            <Search size={14} className="text-[var(--color-text-tertiary)]" />
            <input value={keyword} onChange={event => { setPage(1); setKeyword(event.target.value) }} className="min-w-0 flex-1 bg-transparent text-xs outline-none" placeholder="按接口名称搜索" />
          </label>
          <input type="date" value={start} onChange={event => { setPage(1); setStart(event.target.value) }} className="h-9 rounded-md border border-[var(--color-border)] bg-[var(--color-bg-base)] px-3 text-xs" />
          <span className="text-xs text-[var(--color-text-tertiary)]">至</span>
          <input type="date" value={end} onChange={event => { setPage(1); setEnd(event.target.value) }} className="h-9 rounded-md border border-[var(--color-border)] bg-[var(--color-bg-base)] px-3 text-xs" />
          <Button variant="outline" size="sm" onClick={load}><RefreshCw size={13} />刷新</Button>
          <span className="ml-auto text-xs text-[var(--color-text-tertiary)]">共 {total} 条记录</span>
        </div>
        {error && <div className="m-4 rounded-md bg-[var(--color-danger-bg)] px-3 py-2 text-xs text-[var(--color-danger)]">{error}</div>}
        <div className="min-h-0 flex-1 overflow-auto">
          <table className="w-full border-collapse text-left text-xs">
            <thead className="sticky top-0 z-10 bg-[var(--color-bg-base)] text-[var(--color-text-tertiary)]">
              <tr>{['时间', '接口', '方法', '状态', '耗时', '登录态', '错误'].map(label => <th key={label} className="border-b border-[var(--color-border)] px-4 py-3 font-medium">{label}</th>)}</tr>
            </thead>
            <tbody>
              {!loading && !items.length && <tr><td colSpan={7}><div className="flex flex-col items-center py-24 text-[var(--color-text-tertiary)]"><Clock3 size={28} className="mb-3 opacity-50" />暂无调用记录</div></td></tr>}
              {items.map(item => (
                <tr key={item.id} onClick={() => open(item)} className="cursor-pointer border-b border-[var(--color-border)] hover:bg-[var(--color-bg-hover)]">
                  <td className="whitespace-nowrap px-4 py-3 text-[var(--color-text-secondary)]">{formatTime(item.created_at)}</td>
                  <td className="max-w-[280px] truncate px-4 py-3 font-medium text-[var(--color-text-primary)]">{item.name}</td>
                  <td className="px-4 py-3"><span className="rounded bg-[var(--color-bg-base)] px-2 py-1 font-mono text-[11px] font-semibold">{item.method}</span></td>
                  <td className="px-4 py-3"><Status code={item.status_code} ok={Boolean(item.ok)} /></td>
                  <td className="px-4 py-3 text-[var(--color-text-secondary)]">{item.elapsed_ms == null ? '—' : `${item.elapsed_ms} ms`}</td>
                  <td className="px-4 py-3 text-[var(--color-text-secondary)]">{item.relogin ? '自动重登' : '—'}</td>
                  <td className="max-w-[300px] truncate px-4 py-3 text-[var(--color-danger)]">{item.error || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {loading && <div className="flex h-40 items-center justify-center text-xs text-[var(--color-text-tertiary)]">加载调用历史…</div>}
        </div>
        <div className="flex h-12 shrink-0 items-center justify-end gap-3 border-t border-[var(--color-border)] px-4">
          <button disabled={page <= 1} onClick={() => setPage(value => value - 1)} className="rounded p-1 text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)] disabled:opacity-30"><ChevronLeft size={16} /></button>
          <span className="text-xs text-[var(--color-text-secondary)]">{page} / {pages}</span>
          <button disabled={page >= pages} onClick={() => setPage(value => value + 1)} className="rounded p-1 text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)] disabled:opacity-30"><ChevronRight size={16} /></button>
        </div>
      </section>

      <Modal open={Boolean(detail)} onClose={() => setDetail(null)} title={detail ? `${detail.name} · 调用详情` : ''} description={detail ? formatTime(detail.created_at) : ''} size="3xl">
        {detail && <RunDetailView detail={detail} />}
      </Modal>
    </div>
  )
}

function Status({ code, ok }: { code: number | null; ok: boolean }) {
  return <span className={`rounded-full px-2.5 py-1 text-[11px] font-semibold ${ok ? 'bg-[var(--color-success-bg)] text-[var(--color-success)]' : 'bg-[var(--color-danger-bg)] text-[var(--color-danger)]'}`}>{code ?? 'ERR'}</span>
}

function RunDetailView({ detail }: { detail: RunDetail }) {
  const response = pretty(detail.response_body)
  return <div className="max-h-[65vh] space-y-4 overflow-y-auto pr-1 text-xs">
    <div className="grid grid-cols-4 gap-3">
      <Info label="状态" value={String(detail.status_code ?? 'ERR')} />
      <Info label="耗时" value={detail.elapsed_ms == null ? '—' : `${detail.elapsed_ms} ms`} />
      <Info label="方法" value={detail.method} />
      <Info label="自动重登" value={detail.relogin ? '是' : '否'} />
    </div>
    {detail.error && <div className="rounded-md bg-[var(--color-danger-bg)] px-3 py-2 text-[var(--color-danger)]">{detail.error}</div>}
    <CodeBlock title="请求快照" value={JSON.stringify(detail.request_snapshot, null, 2)} />
    <CodeBlock title="响应头" value={JSON.stringify(detail.response_headers, null, 2)} />
    <CodeBlock title="响应体" value={response || '(空响应体)'} />
  </div>
}

function Info({ label, value }: { label: string; value: string }) {
  return <div className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg-base)] p-3"><div className="text-[10px] text-[var(--color-text-tertiary)]">{label}</div><div className="mt-1 font-semibold text-[var(--color-text-primary)]">{value}</div></div>
}
function CodeBlock({ title, value }: { title: string; value: string }) {
  return <div><div className="mb-1.5 font-medium text-[var(--color-text-secondary)]">{title}</div><pre className="max-h-60 overflow-auto whitespace-pre-wrap break-all rounded-md bg-[#111827] p-3 font-mono leading-5 text-slate-100">{value}</pre></div>
}
function pretty(text: string) { try { return JSON.stringify(JSON.parse(text), null, 2) } catch { return text } }
function formatTime(iso: string) { return new Date(iso).toLocaleString('zh-CN', { hour12: false }) }
