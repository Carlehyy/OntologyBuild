import { useCallback, useEffect, useState } from 'react'
import { Activity, BarChart3, ChevronLeft, ChevronRight, Clock3, Layers3, RefreshCw, Search, Zap } from 'lucide-react'
import { apiError, apiHub, type RunDetail, type RunOverview, type RunSummary } from '@/api/apiHub'
import { Button } from '@/components/ui/Button'
import { Modal } from '@/components/ui/Modal'

const size = 20

export default function RunHistory() {
  const [items, setItems] = useState<RunSummary[]>([])
  const [overview, setOverview] = useState<RunOverview | null>(null)
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
      const [history, summary] = await Promise.all([
        apiHub.listRuns({ page, size, keyword, start: start ? new Date(`${start}T00:00:00`).toISOString() : '', end: end ? new Date(`${end}T23:59:59.999`).toISOString() : '' }),
        apiHub.runOverview(),
      ])
      setItems(history.items); setTotal(history.total); setOverview(summary); setError('')
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
    <div className="flex h-full min-h-0 flex-col gap-4 overflow-hidden p-4">
      <section className="shrink-0 rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-elevated)] p-4 shadow-sm">
        <div className="mb-3 flex items-center justify-between"><div><h1 className="text-base font-semibold">调用总览</h1><p className="text-[11px] text-[var(--color-text-tertiary)]">接口覆盖与近七日调用流量</p></div><Button variant="ghost" size="sm" onClick={load}><RefreshCw size={13} />刷新数据</Button></div>
        <div className="grid grid-cols-[1.35fr_0.55fr_1.1fr] gap-4">
          <div className="grid grid-cols-5 gap-2">
            <OverviewMetric icon={Layers3} label="总接口" value={overview?.total_interfaces ?? 0} />
            <OverviewMetric icon={Activity} label="执行过" value={overview?.executed_interfaces ?? 0} tone="success" />
            <OverviewMetric icon={Clock3} label="未执行" value={overview?.unexecuted_interfaces ?? 0} tone="muted" />
            <OverviewMetric icon={Zap} label="今日流量" value={overview?.today_traffic ?? 0} tone="info" />
            <OverviewMetric icon={BarChart3} label="近七日" value={overview?.seven_day_traffic ?? 0} tone="info" />
          </div>
          <ExecutionDonut overview={overview} />
          <TrafficBars overview={overview} />
        </div>
      </section>

      <section className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-elevated)] shadow-sm">
        <div className="flex shrink-0 items-center gap-3 border-b border-[var(--color-border)] p-4">
          <div className="mr-2"><h2 className="text-sm font-semibold">调用记录</h2><p className="text-[10px] text-[var(--color-text-tertiary)]">筛选记录并查看完整请求响应</p></div>
          <label className="flex h-9 min-w-[230px] items-center gap-2 rounded-md border border-[var(--color-border)] bg-[var(--color-bg-base)] px-3"><Search size={14} className="text-[var(--color-text-tertiary)]" /><input value={keyword} onChange={event => { setPage(1); setKeyword(event.target.value) }} className="min-w-0 flex-1 bg-transparent text-xs outline-none" placeholder="按接口名称搜索" /></label>
          <input aria-label="开始日期" type="date" value={start} onChange={event => { setPage(1); setStart(event.target.value) }} className="h-9 rounded-md border border-[var(--color-border)] bg-[var(--color-bg-base)] px-3 text-xs" />
          <span className="text-xs text-[var(--color-text-tertiary)]">至</span>
          <input aria-label="结束日期" type="date" value={end} onChange={event => { setPage(1); setEnd(event.target.value) }} className="h-9 rounded-md border border-[var(--color-border)] bg-[var(--color-bg-base)] px-3 text-xs" />
          <Button variant="outline" size="sm" onClick={load}><Search size={13} />筛选</Button>
          <span className="ml-auto text-xs text-[var(--color-text-tertiary)]">共 {total} 条</span>
        </div>
        {error && <div className="m-4 rounded-md bg-[var(--color-danger-bg)] px-3 py-2 text-xs text-[var(--color-danger)]">{error}</div>}
        <div className="min-h-0 flex-1 overflow-auto">
          <table className="w-full border-collapse text-left text-xs"><thead className="sticky top-0 z-10 bg-[var(--color-bg-base)] text-[var(--color-text-tertiary)]"><tr>{['时间', '接口', '方法', '状态', '耗时', '登录态', '错误'].map(label => <th key={label} className="border-b border-[var(--color-border)] px-4 py-3 font-medium">{label}</th>)}</tr></thead><tbody>
            {!loading && !items.length && <tr><td colSpan={7}><div className="flex flex-col items-center py-20 text-[var(--color-text-tertiary)]"><Clock3 size={28} className="mb-3 opacity-50" />暂无调用记录</div></td></tr>}
            {items.map(item => <tr key={item.id} onClick={() => open(item)} className="cursor-pointer border-b border-[var(--color-border)] hover:bg-[var(--color-bg-hover)]"><td className="whitespace-nowrap px-4 py-3 text-[var(--color-text-secondary)]">{formatTime(item.created_at)}</td><td className="max-w-[280px] truncate px-4 py-3 font-medium">{item.name}</td><td className="px-4 py-3"><span className="rounded bg-[var(--color-bg-base)] px-2 py-1 font-mono text-[11px] font-semibold">{item.method}</span></td><td className="px-4 py-3"><Status code={item.status_code} ok={Boolean(item.ok)} /></td><td className="px-4 py-3 text-[var(--color-text-secondary)]">{item.elapsed_ms == null ? '—' : `${item.elapsed_ms} ms`}</td><td className="px-4 py-3 text-[var(--color-text-secondary)]">{item.relogin ? '自动重登' : '—'}</td><td className="max-w-[300px] truncate px-4 py-3 text-[var(--color-danger)]">{item.error || '—'}</td></tr>)}
          </tbody></table>
          {loading && <div className="flex h-32 items-center justify-center text-xs text-[var(--color-text-tertiary)]">加载调用历史…</div>}
        </div>
        <div className="flex h-11 shrink-0 items-center justify-end gap-3 border-t border-[var(--color-border)] px-4"><button disabled={page <= 1} onClick={() => setPage(value => value - 1)} className="rounded p-1 disabled:opacity-30"><ChevronLeft size={16} /></button><span className="text-xs text-[var(--color-text-secondary)]">{page} / {pages}</span><button disabled={page >= pages} onClick={() => setPage(value => value + 1)} className="rounded p-1 disabled:opacity-30"><ChevronRight size={16} /></button></div>
      </section>

      <Modal open={Boolean(detail)} onClose={() => setDetail(null)} title={detail ? `${detail.name} · 调用详情` : ''} description={detail ? formatTime(detail.created_at) : ''} size="3xl">{detail && <RunDetailView detail={detail} />}</Modal>
    </div>
  )
}

function OverviewMetric({ icon: Icon, label, value, tone }: { icon: React.ElementType; label: string; value: number; tone?: 'success' | 'info' | 'muted' }) { const color = tone === 'success' ? 'text-emerald-600 bg-emerald-50' : tone === 'info' ? 'text-blue-600 bg-blue-50' : tone === 'muted' ? 'text-slate-500 bg-slate-100' : 'text-teal-600 bg-teal-50'; return <div className="rounded-lg border border-[var(--color-border)] bg-white p-3"><div className={`mb-3 flex h-7 w-7 items-center justify-center rounded-md ${color}`}><Icon size={14} /></div><div className="text-xl font-semibold">{value}</div><div className="mt-0.5 text-[10px] text-[var(--color-text-tertiary)]">{label}</div></div> }

function ExecutionDonut({ overview }: { overview: RunOverview | null }) { const total = overview?.total_interfaces || 0; const executed = overview?.executed_interfaces || 0; const ratio = total ? Math.round(executed * 100 / total) : 0; return <div className="flex items-center justify-center rounded-lg border border-[var(--color-border)] bg-white px-3"><div className="relative flex h-24 w-24 items-center justify-center rounded-full" style={{ background: `conic-gradient(var(--color-nav-bg) ${ratio}%, #e5e7eb 0)` }}><div className="flex h-16 w-16 flex-col items-center justify-center rounded-full bg-white"><span className="text-lg font-semibold">{ratio}%</span><span className="text-[9px] text-[var(--color-text-tertiary)]">执行覆盖</span></div></div></div> }

function TrafficBars({ overview }: { overview: RunOverview | null }) { const daily = overview?.daily || []; const max = Math.max(1, ...daily.map(item => item.count)); return <div className="rounded-lg border border-[var(--color-border)] bg-white p-3"><div className="mb-2 text-[10px] font-medium text-[var(--color-text-secondary)]">近七日调用趋势</div><div className="flex h-20 items-end gap-2">{daily.map(item => <div key={item.date} className="flex min-w-0 flex-1 flex-col items-center justify-end gap-1"><span className="text-[9px] text-[var(--color-text-tertiary)]">{item.count}</span><div title={`${item.date} · ${item.count} 次`} className="w-full rounded-t bg-teal-500/80" style={{ height: `${Math.max(4, item.count * 48 / max)}px` }} /><span className="text-[8px] text-[var(--color-text-tertiary)]">{item.date.slice(5)}</span></div>)}</div></div> }

function Status({ code, ok }: { code: number | null; ok: boolean }) { return <span className={`rounded-full px-2.5 py-1 text-[11px] font-semibold ${ok ? 'bg-[var(--color-success-bg)] text-[var(--color-success)]' : 'bg-[var(--color-danger-bg)] text-[var(--color-danger)]'}`}>{code ?? 'ERR'}</span> }
function RunDetailView({ detail }: { detail: RunDetail }) { const response = pretty(detail.response_body); return <div className="max-h-[65vh] space-y-4 overflow-y-auto pr-1 text-xs"><div className="grid grid-cols-4 gap-3"><Info label="状态" value={String(detail.status_code ?? 'ERR')} /><Info label="耗时" value={detail.elapsed_ms == null ? '—' : `${detail.elapsed_ms} ms`} /><Info label="方法" value={detail.method} /><Info label="自动重登" value={detail.relogin ? '是' : '否'} /></div>{detail.error && <div className="rounded-md bg-[var(--color-danger-bg)] px-3 py-2 text-[var(--color-danger)]">{detail.error}</div>}<CodeBlock title="请求快照" value={JSON.stringify(detail.request_snapshot, null, 2)} /><CodeBlock title="响应头" value={JSON.stringify(detail.response_headers, null, 2)} /><CodeBlock title="响应体" value={response || '(空响应体)'} /></div> }
function Info({ label, value }: { label: string; value: string }) { return <div className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg-base)] p-3"><div className="text-[10px] text-[var(--color-text-tertiary)]">{label}</div><div className="mt-1 font-semibold">{value}</div></div> }
function CodeBlock({ title, value }: { title: string; value: string }) { return <div><div className="mb-1.5 font-medium text-[var(--color-text-secondary)]">{title}</div><pre className="max-h-60 overflow-auto whitespace-pre-wrap break-all rounded-md bg-[#111827] p-3 font-mono leading-5 text-slate-100">{value}</pre></div> }
function pretty(text: string) { try { return JSON.stringify(JSON.parse(text), null, 2) } catch { return text } }
function formatTime(iso?: string | null) { return iso ? new Date(iso).toLocaleString('zh-CN', { hour12: false }) : '—' }
