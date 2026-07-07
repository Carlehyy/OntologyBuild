import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  ClipboardList, Plus, Plug, Search, User, Paperclip, X,
  ChevronLeft, ChevronRight, Layers, CalendarDays,
} from 'lucide-react'
import { eventsApi, SEVERITY_META, type EventListParams } from '@/api/events'
import EventFormModal from './EventFormModal'
import EventDetailDrawer from './EventDetailDrawer'
import IngestKeysDrawer from './IngestKeysDrawer'

const PAGE_SIZE = 20

function fmt(iso: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (isNaN(d.getTime())) return '—'
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

function Metric({ icon, label, value, tint }: {
  icon: React.ReactNode; label: string; value: number; tint: string
}) {
  return (
    <div className="flex items-center gap-3 px-5 py-4">
      <div className={`w-10 h-10 rounded-lg flex items-center justify-center shrink-0 ${tint}`}>{icon}</div>
      <div className="min-w-0">
        <div className="text-2xl font-semibold text-gray-900 leading-none tabular-nums">{value}</div>
        <div className="text-xs text-gray-500 mt-1.5">{label}</div>
      </div>
    </div>
  )
}

export default function EventRegistryPage() {
  const [q, setQ] = useState('')
  const [sourceType, setSourceType] = useState('')
  const [severity, setSeverity] = useState('')
  const [status, setStatus] = useState('active')
  const [page, setPage] = useState(1)

  const [formOpen, setFormOpen] = useState(false)
  const [editing, setEditing] = useState<any>(null)
  const [detailId, setDetailId] = useState<string | null>(null)
  const [keysOpen, setKeysOpen] = useState(false)

  const params: EventListParams = {
    q: q.trim() || undefined,
    sourceType: sourceType || undefined,
    severity: severity || undefined,
    status,
    page,
    pageSize: PAGE_SIZE,
  }

  const { data, isLoading } = useQuery({ queryKey: ['events', params], queryFn: () => eventsApi.list(params) })
  const { data: stats } = useQuery({ queryKey: ['event-stats'], queryFn: eventsApi.stats })

  const items = data?.items || []
  const total = data?.total || 0
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE))
  const hasFilter = !!(q || sourceType || severity || status !== 'active')

  const setF = (fn: () => void) => { fn(); setPage(1) }
  const clearFilters = () => setF(() => { setQ(''); setSourceType(''); setSeverity(''); setStatus('active') })

  return (
    <div className="space-y-5">
      {/* 页头 */}
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="flex items-center gap-2 text-xl font-semibold text-gray-900">
            <ClipboardList size={20} className="text-[var(--color-nav-bg)]" /> 事件登记
          </h1>
          <p className="text-sm text-gray-500 mt-1">
            采集平台录入与第三方上传的业务事件，可审计、可溯源，作为本体优化的原始素材。
          </p>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <button onClick={() => setKeysOpen(true)}
            className="flex items-center gap-1.5 px-3.5 py-2 border border-[var(--color-nav-bg)]/30 bg-[var(--color-nav-light)] text-[var(--color-nav-bg)] text-sm font-medium rounded-lg hover:bg-[var(--color-nav-light)]/70 transition-colors">
            <Plug size={15} /> API 接入
          </button>
          <button onClick={() => { setEditing(null); setFormOpen(true) }}
            className="flex items-center gap-1.5 px-3.5 py-2 bg-[var(--color-nav-bg)] text-white text-sm font-medium rounded-lg hover:opacity-90 transition-opacity">
            <Plus size={15} /> 登记事件
          </button>
        </div>
      </div>

      {/* 指标条 */}
      <div className="grid grid-cols-2 md:grid-cols-4 rounded-xl border border-gray-200 bg-white divide-x divide-gray-100 overflow-hidden shadow-sm">
        <Metric icon={<Layers size={18} />} label="事件总数" value={stats?.total ?? 0}
          tint="bg-slate-100 text-slate-600" />
        <Metric icon={<User size={18} />} label="平台录入" value={stats?.platform ?? 0}
          tint="bg-gray-100 text-gray-500" />
        <Metric icon={<Plug size={18} />} label="第三方接口" value={stats?.api ?? 0}
          tint="bg-teal-50 text-teal-600" />
        <Metric icon={<CalendarDays size={18} />} label="今日新增" value={stats?.today ?? 0}
          tint="bg-amber-50 text-amber-600" />
      </div>

      {/* 搜索与筛选 */}
      <div className="flex items-center gap-3 bg-white rounded-xl border border-gray-200 px-4 py-3 flex-wrap shadow-sm">
        <div className="relative w-72">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
          <input value={q} onChange={e => setF(() => setQ(e.target.value))}
            placeholder="搜索标题 / 描述 / 编号"
            className="w-full pl-8 pr-8 py-1.5 border border-gray-200 rounded-lg text-sm focus:outline-none focus:ring-1 focus:ring-[var(--color-nav-bg)] focus:border-[var(--color-nav-bg)]" />
          {q && (
            <button onClick={() => setF(() => setQ(''))} className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-700">
              <X size={12} />
            </button>
          )}
        </div>
        <select value={sourceType} onChange={e => setF(() => setSourceType(e.target.value))}
          className="border border-gray-200 rounded-lg px-3 py-1.5 text-sm text-gray-700 focus:outline-none focus:ring-1 focus:ring-[var(--color-nav-bg)]">
          <option value="">全部来源</option>
          <option value="platform">平台录入</option>
          <option value="api">第三方接口</option>
        </select>
        <select value={severity} onChange={e => setF(() => setSeverity(e.target.value))}
          className="border border-gray-200 rounded-lg px-3 py-1.5 text-sm text-gray-700 focus:outline-none focus:ring-1 focus:ring-[var(--color-nav-bg)]">
          <option value="">全部级别</option>
          <option value="critical">严重</option>
          <option value="high">高</option>
          <option value="medium">中</option>
          <option value="low">低</option>
          <option value="info">信息</option>
        </select>
        <select value={status} onChange={e => setF(() => setStatus(e.target.value))}
          className="border border-gray-200 rounded-lg px-3 py-1.5 text-sm text-gray-700 focus:outline-none focus:ring-1 focus:ring-[var(--color-nav-bg)]">
          <option value="active">活跃</option>
          <option value="archived">已归档</option>
          <option value="all">全部状态</option>
        </select>
        {hasFilter && (
          <button onClick={clearFilters} className="text-xs text-gray-500 hover:text-gray-900 px-2 py-1">
            清除筛选
          </button>
        )}
        <span className="ml-auto text-xs text-gray-400">{total} 条事件</span>
      </div>

      {/* 列表 */}
      {isLoading ? (
        <div className="text-gray-400 text-sm p-12 text-center">加载中…</div>
      ) : items.length === 0 ? (
        <div className="border-2 border-dashed border-gray-200 rounded-xl p-14 text-center space-y-3">
          <div className="w-14 h-14 rounded-2xl bg-gray-50 flex items-center justify-center mx-auto">
            <ClipboardList size={26} className="text-gray-300" />
          </div>
          <p className="text-sm font-medium text-gray-600">{hasFilter ? '没有匹配的事件' : '还没有登记任何事件'}</p>
          <p className="text-xs text-gray-400 max-w-sm mx-auto">
            {hasFilter ? '试试调整或清除筛选条件。' : '点击「登记事件」手动录入，或用「API 接入」让第三方系统上传。'}
          </p>
          {!hasFilter && (
            <button onClick={() => { setEditing(null); setFormOpen(true) }}
              className="inline-flex items-center gap-1.5 mt-1 px-3.5 py-2 bg-[var(--color-nav-bg)] text-white text-sm font-medium rounded-lg hover:opacity-90">
              <Plus size={15} /> 登记事件
            </button>
          )}
        </div>
      ) : (
        <div className="border border-gray-200 rounded-xl overflow-hidden bg-white shadow-sm">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 border-b border-gray-200">
              <tr>
                {['事件', '类型', '级别', '来源', '发生时间', '上报人', '附件'].map((h, i) => (
                  <th key={h} className={`px-4 py-2.5 font-medium text-gray-500 text-xs ${i === 6 ? 'text-center' : 'text-left'}`}>{h}</th>
                ))}
                <th className="w-8" />
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {items.map(ev => {
                const sev = SEVERITY_META[ev.severity] || SEVERITY_META.info
                const isApi = ev.sourceType === 'api'
                return (
                  <tr key={ev.id} className="group hover:bg-gray-50/70 transition-colors cursor-pointer"
                    onClick={() => setDetailId(ev.id)}>
                    <td className="px-4 py-3">
                      <p className="font-medium text-gray-900 truncate max-w-[280px]">{ev.title}</p>
                      <p className="text-xs text-gray-400 font-mono mt-0.5">{ev.eventNo}</p>
                    </td>
                    <td className="px-4 py-3 text-xs text-gray-500 whitespace-nowrap">{ev.eventType || '—'}</td>
                    <td className="px-4 py-3">
                      <span className={`inline-flex items-center gap-1.5 text-xs px-2 py-0.5 rounded-full font-medium ${sev.cls}`}>
                        <span className={`w-1.5 h-1.5 rounded-full ${sev.dot}`} />{sev.label}
                      </span>
                    </td>
                    <td className="px-4 py-3">
                      <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-xs font-medium whitespace-nowrap ${
                        isApi ? 'bg-teal-50 text-teal-700 border border-teal-100' : 'bg-gray-100 text-gray-600'
                      }`}>
                        {isApi ? <Plug size={11} /> : <User size={11} />}{ev.sourceLabel}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-xs text-gray-500 whitespace-nowrap">{fmt(ev.occurredAt || ev.recordedAt)}</td>
                    <td className="px-4 py-3 text-xs text-gray-500 whitespace-nowrap">{ev.reporterName || '—'}</td>
                    <td className="px-4 py-3 text-center">
                      {ev.attachmentCount ? (
                        <span className="inline-flex items-center gap-0.5 text-xs text-gray-500">
                          <Paperclip size={12} />{ev.attachmentCount}
                        </span>
                      ) : <span className="text-gray-300">—</span>}
                    </td>
                    <td className="pr-3 text-right">
                      <ChevronRight size={15} className="text-gray-300 group-hover:text-gray-500 transition-colors inline" />
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* 分页 */}
      {total > PAGE_SIZE && (
        <div className="flex items-center justify-between text-sm text-gray-500">
          <span>共 {total} 条 · 第 {page}/{pages} 页</span>
          <div className="flex items-center gap-2">
            <button disabled={page <= 1} onClick={() => setPage(p => p - 1)}
              className="inline-flex items-center gap-1 px-3 py-1.5 border border-gray-200 rounded-lg text-sm hover:bg-gray-50 disabled:opacity-40 disabled:hover:bg-transparent">
              <ChevronLeft size={14} /> 上一页
            </button>
            <button disabled={page >= pages} onClick={() => setPage(p => p + 1)}
              className="inline-flex items-center gap-1 px-3 py-1.5 border border-gray-200 rounded-lg text-sm hover:bg-gray-50 disabled:opacity-40 disabled:hover:bg-transparent">
              下一页 <ChevronRight size={14} />
            </button>
          </div>
        </div>
      )}

      {/* 弹层 */}
      <EventFormModal open={formOpen} editing={editing} onClose={() => setFormOpen(false)} />
      <EventDetailDrawer open={!!detailId} eventId={detailId} onClose={() => setDetailId(null)}
        onEdit={(ev) => { setDetailId(null); setEditing(ev); setFormOpen(true) }} />
      <IngestKeysDrawer open={keysOpen} onClose={() => setKeysOpen(false)} />
    </div>
  )
}
