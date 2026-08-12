import { useCallback, useEffect, useState } from 'react'
import {
  AlertCircle,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Clock3,
  Gauge,
  RefreshCw,
  Route,
  ScrollText,
  Search,
  TimerReset,
  X,
  XCircle,
} from 'lucide-react'
import {
  apiError,
  worldModelApi,
  type CallRecordDetail,
  type CallRecordItem,
  type CallRecordOverview,
} from '@/api/worldModel'
import { Button } from '@/components/ui/Button'

const PAGE_SIZE = 20

type ResultFilter = 'all' | 'failed'
type DetailTab = 'request' | 'response'

interface AppliedFilters {
  keyword: string
  start: string
  end: string
  result: ResultFilter
}

const EMPTY_FILTERS: AppliedFilters = { keyword: '', start: '', end: '', result: 'all' }

function formatDateTime(iso?: string | null): string {
  if (!iso) return '-'
  try {
    return new Date(iso).toLocaleString('zh-CN', {
      month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit',
    })
  } catch {
    return iso
  }
}

function OverviewCard({ icon, label, value, tone }: {
  icon: React.ReactNode
  label: string
  value: string
  tone?: 'default' | 'danger'
}) {
  return (
    <div className="flex items-center gap-3 rounded-xl border border-slate-200 bg-white px-4 py-3 shadow-sm/50">
      <span className={`flex h-9 w-9 items-center justify-center rounded-lg ${tone === 'danger' ? 'bg-red-50 text-red-500' : 'bg-teal-50 text-teal-600'}`}>
        {icon}
      </span>
      <div>
        <p className="text-[11px] text-slate-400">{label}</p>
        <p className="text-base font-semibold tabular-nums text-slate-700">{value}</p>
      </div>
    </div>
  )
}

export default function WorldModelCallsTab() {
  const [items, setItems] = useState<CallRecordItem[]>([])
  const [overview, setOverview] = useState<CallRecordOverview | null>(null)
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [draftKeyword, setDraftKeyword] = useState('')
  const [draftStart, setDraftStart] = useState('')
  const [draftEnd, setDraftEnd] = useState('')
  const [draftResult, setDraftResult] = useState<ResultFilter>('all')
  const [filters, setFilters] = useState<AppliedFilters>(EMPTY_FILTERS)
  const [historyLoading, setHistoryLoading] = useState(true)
  const [historyError, setHistoryError] = useState('')
  const [selected, setSelected] = useState<CallRecordItem | null>(null)
  const [detail, setDetail] = useState<CallRecordDetail | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [detailTab, setDetailTab] = useState<DetailTab>('request')
  const [refreshing, setRefreshing] = useState(false)

  const loadHistory = useCallback(async (silent = false) => {
    if (!silent) setHistoryLoading(true)
    setHistoryError('')
    try {
      const history = await worldModelApi.listCalls({
        page,
        size: PAGE_SIZE,
        keyword: filters.keyword,
        start: filters.start ? new Date(`${filters.start}T00:00:00`).toISOString() : '',
        end: filters.end ? new Date(`${filters.end}T23:59:59.999`).toISOString() : '',
        result: filters.result,
      })
      setItems(history.items)
      setTotal(history.total)
    } catch (error) {
      setHistoryError(apiError(error))
    } finally {
      if (!silent) setHistoryLoading(false)
    }
  }, [filters, page])

  const loadOverview = useCallback(async () => {
    try {
      setOverview(await worldModelApi.callsOverview())
    } catch {
      setOverview(null)
    }
  }, [])

  useEffect(() => { void loadHistory() }, [loadHistory])
  useEffect(() => { void loadOverview() }, [loadOverview])

  const refreshAll = async () => {
    setRefreshing(true)
    try {
      await Promise.all([loadHistory(true), loadOverview()])
    } finally {
      setRefreshing(false)
    }
  }

  const applyFilters = () => {
    setPage(1)
    setFilters({ keyword: draftKeyword.trim(), start: draftStart, end: draftEnd, result: draftResult })
  }

  const resetFilters = () => {
    setDraftKeyword('')
    setDraftStart('')
    setDraftEnd('')
    setDraftResult('all')
    setPage(1)
    setFilters(EMPTY_FILTERS)
  }

  const openDetail = async (item: CallRecordItem) => {
    setSelected(item)
    setDetail(null)
    setDetailTab('request')
    setDetailLoading(true)
    try {
      setDetail(await worldModelApi.getCall(item.id))
    } catch {
      setDetail(null)
    } finally {
      setDetailLoading(false)
    }
  }

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  return (
    <div className="space-y-4">
      {/* 概览统计 */}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <OverviewCard icon={<Route size={17} />} label="总调用次数" value={String(overview?.total ?? 0)} />
        <OverviewCard icon={<AlertCircle size={17} />} label="失败次数" value={String(overview?.failed ?? 0)} tone="danger" />
        <OverviewCard icon={<Gauge size={17} />} label="平均耗时" value={`${overview?.avg_duration_ms ?? 0} ms`} />
        <OverviewCard icon={<TimerReset size={17} />} label="数据说明" value="发布后产生" />
      </div>

      {/* 筛选栏 */}
      <section className="flex flex-wrap items-center gap-2 rounded-xl border border-slate-200 bg-white px-4 py-3 shadow-sm/50" aria-label="调用记录筛选">
        <div className="relative w-full sm:w-64">
          <Search size={14} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
          <input
            value={draftKeyword}
            onChange={event => setDraftKeyword(event.target.value)}
            onKeyDown={event => { if (event.key === 'Enter') applyFilters() }}
            placeholder="搜索服务名或调用方"
            aria-label="按服务名或调用方筛选"
            className="h-9 w-full rounded-lg border border-slate-200 bg-white pl-8 pr-3 text-sm text-slate-700 placeholder:text-slate-400 focus:border-teal-400 focus:outline-none focus:ring-2 focus:ring-teal-500/20"
          />
        </div>
        <input
          type="date"
          value={draftStart}
          onChange={event => setDraftStart(event.target.value)}
          aria-label="开始日期"
          className="h-9 rounded-lg border border-slate-200 bg-white px-3 text-sm text-slate-600 focus:border-teal-400 focus:outline-none focus:ring-2 focus:ring-teal-500/20"
        />
        <span className="text-xs text-slate-400">至</span>
        <input
          type="date"
          value={draftEnd}
          onChange={event => setDraftEnd(event.target.value)}
          aria-label="结束日期"
          className="h-9 rounded-lg border border-slate-200 bg-white px-3 text-sm text-slate-600 focus:border-teal-400 focus:outline-none focus:ring-2 focus:ring-teal-500/20"
        />
        <select
          value={draftResult}
          onChange={event => setDraftResult(event.target.value as ResultFilter)}
          aria-label="按调用结果筛选"
          className="h-9 rounded-lg border border-slate-200 bg-white px-3 text-sm text-slate-600 focus:border-teal-400 focus:outline-none focus:ring-2 focus:ring-teal-500/20"
        >
          <option value="all">全部结果</option>
          <option value="failed">仅失败</option>
        </select>
        <Button onClick={applyFilters} className="h-9 bg-teal-600 text-white hover:bg-teal-700">查询</Button>
        <button
          type="button"
          onClick={resetFilters}
          className="inline-flex h-9 items-center gap-1 rounded-lg px-2.5 text-xs text-slate-400 hover:bg-slate-50 hover:text-slate-600"
        >
          <X size={13} /> 重置
        </button>
        <button
          type="button"
          onClick={() => void refreshAll()}
          className="ml-auto inline-flex h-9 items-center gap-1.5 rounded-lg border border-slate-200 px-3 text-xs text-slate-600 hover:bg-slate-50"
        >
          <RefreshCw size={13} className={refreshing ? 'animate-spin' : ''} /> 刷新
        </button>
      </section>

      {/* 记录表格 */}
      <section className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm/50">
        {historyLoading ? (
          <p className="py-16 text-center text-sm text-slate-400">加载调用记录…</p>
        ) : historyError ? (
          <div className="flex flex-col items-center gap-3 py-16" role="alert">
            <p className="text-sm text-red-600">{historyError}</p>
            <button
              type="button"
              onClick={() => void loadHistory()}
              className="rounded-lg border border-red-200 bg-white px-3 py-1.5 text-xs font-medium text-red-600 hover:bg-red-50"
            >
              重新加载
            </button>
          </div>
        ) : items.length === 0 ? (
          <div className="flex flex-col items-center justify-center px-6 py-16 text-center">
            <ScrollText size={28} className="text-slate-300" />
            <p className="mt-3 text-sm font-medium text-slate-500">暂无调用记录</p>
            <p className="mt-1 max-w-md text-xs leading-5 text-slate-400">
              调用记录随「发布为推演服务」能力（规划中）产生：模型发布后，每次经 HTTP 接口或 Agent 调用都会在此留下含输入快照、耗时与结果的审计记录。
            </p>
          </div>
        ) : (
          <>
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-slate-100 text-xs text-slate-400">
                  <th className="px-4 py-2.5 font-medium">时间</th>
                  <th className="px-4 py-2.5 font-medium">推演服务</th>
                  <th className="px-4 py-2.5 font-medium">调用方</th>
                  <th className="px-4 py-2.5 font-medium">结果</th>
                  <th className="px-4 py-2.5 text-right font-medium">耗时</th>
                </tr>
              </thead>
              <tbody>
                {items.map(item => (
                  <tr
                    key={item.id}
                    onClick={() => void openDetail(item)}
                    className="cursor-pointer border-b border-slate-50 transition-colors hover:bg-slate-50/60"
                  >
                    <td className="px-4 py-2.5 tabular-nums text-slate-500">{formatDateTime(item.created_at)}</td>
                    <td className="px-4 py-2.5 text-slate-700">{item.service_name || '—'}</td>
                    <td className="px-4 py-2.5 text-slate-500">{item.caller || '—'}</td>
                    <td className="px-4 py-2.5">
                      {item.ok
                        ? <span className="inline-flex items-center gap-1 text-xs text-teal-600"><CheckCircle2 size={13} /> 成功</span>
                        : <span className="inline-flex items-center gap-1 text-xs text-red-600"><XCircle size={13} /> 失败</span>}
                    </td>
                    <td className="px-4 py-2.5 text-right tabular-nums text-slate-500">{item.duration_ms} ms</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="flex items-center justify-between border-t border-slate-100 px-4 py-2.5 text-xs text-slate-400">
              <span>共 {total} 条</span>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => setPage(p => Math.max(1, p - 1))}
                  disabled={page <= 1}
                  aria-label="上一页"
                  className="inline-flex h-7 w-7 items-center justify-center rounded-md border border-slate-200 disabled:opacity-40"
                >
                  <ChevronLeft size={14} />
                </button>
                <span className="tabular-nums">{page} / {totalPages}</span>
                <button
                  type="button"
                  onClick={() => setPage(p => Math.min(totalPages, p + 1))}
                  disabled={page >= totalPages}
                  aria-label="下一页"
                  className="inline-flex h-7 w-7 items-center justify-center rounded-md border border-slate-200 disabled:opacity-40"
                >
                  <ChevronRight size={14} />
                </button>
              </div>
            </div>
          </>
        )}
      </section>

      {/* 详情抽屉 */}
      {selected && (
        <div className="fixed inset-0 z-40 flex justify-end" role="dialog" aria-label="调用详情">
          <div className="absolute inset-0 bg-slate-900/30" onClick={() => setSelected(null)} />
          <aside className="relative z-10 flex h-full w-[min(560px,100%)] flex-col bg-white shadow-2xl">
            <header className="flex items-center justify-between border-b border-slate-100 px-5 py-3.5">
              <div>
                <h2 className="text-sm font-semibold text-slate-800">调用详情</h2>
                <p className="mt-0.5 text-[11px] text-slate-400">
                  {selected.service_name || '—'} · {formatDateTime(selected.created_at)}
                </p>
              </div>
              <button
                type="button"
                onClick={() => setSelected(null)}
                aria-label="关闭详情"
                className="inline-flex h-8 w-8 items-center justify-center rounded-lg text-slate-400 hover:bg-slate-100 hover:text-slate-600"
              >
                <X size={16} />
              </button>
            </header>

            <div className="flex items-center gap-4 border-b border-slate-100 px-5 py-3 text-xs text-slate-500">
              <span className="inline-flex items-center gap-1">
                {selected.ok
                  ? <><CheckCircle2 size={13} className="text-teal-600" /> 成功</>
                  : <><XCircle size={13} className="text-red-500" /> 失败</>}
              </span>
              <span className="inline-flex items-center gap-1"><Clock3 size={13} /> {selected.duration_ms} ms</span>
              <span className="ml-auto">调用方：{selected.caller || '—'}</span>
            </div>

            <nav className="flex gap-1 border-b border-slate-100 px-5 pt-2" aria-label="详情内容">
              {(['request', 'response'] as DetailTab[]).map(tabKey => (
                <button
                  key={tabKey}
                  type="button"
                  onClick={() => setDetailTab(tabKey)}
                  className={`-mb-px border-b-2 px-3 pb-2 text-xs transition-colors ${
                    detailTab === tabKey
                      ? 'border-teal-600 font-medium text-teal-700'
                      : 'border-transparent text-slate-400 hover:text-slate-600'
                  }`}
                >
                  {tabKey === 'request' ? '请求' : '响应'}
                </button>
              ))}
            </nav>

            <div className="min-h-0 flex-1 overflow-auto px-5 py-4">
              {detailLoading ? (
                <p className="py-10 text-center text-xs text-slate-400">加载详情…</p>
              ) : (
                <pre className="overflow-auto rounded-lg bg-slate-50 p-3 text-xs leading-5 text-slate-700">
                  {JSON.stringify(
                    detailTab === 'request' ? detail?.request_payload : (detail?.response_payload ?? { error: detail?.error ?? selected.error }),
                    null,
                    2,
                  ) ?? '—'}
                </pre>
              )}
            </div>
          </aside>
        </div>
      )}
    </div>
  )
}
