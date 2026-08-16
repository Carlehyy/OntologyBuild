import { useCallback, useEffect, useState } from 'react'
import {
  Activity,
  Check,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Copy,
  Eye,
  Play,
  Power,
  RefreshCw,
  Rocket,
  Search,
  X,
  XCircle,
} from 'lucide-react'

import { ontologyApi } from '@/api/ontologies'
import {
  apiError,
  worldModelApi,
  type CallRecordItem,
  type ServiceInvokeResult,
  type WorldModelServiceSummary,
} from '@/api/worldModel'
import { Button } from '@/components/ui/Button'
import { Modal } from '@/components/ui/Modal'
import { useToast } from '@/components/ui/Toast'
import { writeTextToClipboard } from '@/utils/clipboard'

const PAGE_SIZE = 20
const DEFAULT_INVOKE_INPUT = JSON.stringify({ context: {}, actions: [], horizon: 3 }, null, 2)

type StatusFilter = '' | 'online' | 'offline'

function formatDateTime(iso?: string | null): string {
  if (!iso) return '-'
  try {
    return new Date(iso).toLocaleString('zh-CN', {
      month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
    })
  } catch {
    return iso
  }
}

function statusBadge(status: string) {
  return status === 'online'
    ? <span className="inline-flex items-center rounded-md bg-teal-50 px-1.5 py-0.5 text-[11px] text-teal-700">在线</span>
    : <span className="inline-flex items-center rounded-md bg-slate-100 px-1.5 py-0.5 text-[11px] text-slate-500">已下线</span>
}

export default function WorldModelServicesPage() {
  const { toast } = useToast()
  const [items, setItems] = useState<WorldModelServiceSummary[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [refreshing, setRefreshing] = useState(false)
  const [draftKeyword, setDraftKeyword] = useState('')
  const [draftStatus, setDraftStatus] = useState<StatusFilter>('')
  const [keyword, setKeyword] = useState('')
  const [status, setStatus] = useState<StatusFilter>('')

  const [invokeTarget, setInvokeTarget] = useState<WorldModelServiceSummary | null>(null)
  const [invokeInput, setInvokeInput] = useState(DEFAULT_INVOKE_INPUT)
  const [invoking, setInvoking] = useState(false)
  const [invokeResult, setInvokeResult] = useState<ServiceInvokeResult | null>(null)

  const [detailTarget, setDetailTarget] = useState<WorldModelServiceSummary | null>(null)
  const [ontologyName, setOntologyName] = useState('')
  const [objectTypeLabels, setObjectTypeLabels] = useState<Record<string, string>>({})
  const [serviceCalls, setServiceCalls] = useState<CallRecordItem[]>([])
  const [serviceCallsTotal, setServiceCallsTotal] = useState(0)
  const [serviceCallsLoading, setServiceCallsLoading] = useState(false)

  const [copiedId, setCopiedId] = useState<string | null>(null)
  const [togglingId, setTogglingId] = useState<string | null>(null)

  const loadServices = useCallback(async (silent = false) => {
    if (!silent) setLoading(true)
    setError('')
    try {
      const result = await worldModelApi.listServices({
        page, size: PAGE_SIZE, keyword, status: status || undefined,
      })
      setItems(result.items)
      setTotal(result.total)
    } catch (err) {
      setError(apiError(err))
    } finally {
      if (!silent) setLoading(false)
    }
  }, [page, keyword, status])

  useEffect(() => { void loadServices() }, [loadServices])

  const applyFilters = () => {
    setPage(1)
    setKeyword(draftKeyword.trim())
    setStatus(draftStatus)
  }

  const resetFilters = () => {
    setDraftKeyword('')
    setDraftStatus('')
    setPage(1)
    setKeyword('')
    setStatus('')
  }

  const refreshAll = async () => {
    setRefreshing(true)
    try {
      await loadServices(true)
    } finally {
      setRefreshing(false)
    }
  }

  const toggleStatus = async (item: WorldModelServiceSummary) => {
    if (togglingId) return
    setTogglingId(item.id)
    const next = item.status === 'online' ? 'offline' : 'online'
    try {
      const updated = await worldModelApi.setServiceStatusById(item.id, next)
      setItems(current => current.map(row => row.id === updated.id ? { ...row, ...updated } : row))
      toast({ tone: 'success', title: next === 'online' ? '服务已上线' : '服务已下线' })
    } catch (err) {
      toast({ tone: 'error', title: '状态切换失败', description: apiError(err) })
    } finally {
      setTogglingId(null)
    }
  }

  const copyEndpoint = (item: WorldModelServiceSummary) => {
    if (!item.endpoint_path) return
    writeTextToClipboard(item.endpoint_path).then(() => {
      setCopiedId(item.id)
      window.setTimeout(() => setCopiedId(null), 1400)
    }).catch(() => undefined)
  }

  const openInvoke = (item: WorldModelServiceSummary) => {
    setInvokeTarget(item)
    setInvokeInput(DEFAULT_INVOKE_INPUT)
    setInvokeResult(null)
  }

  const submitInvoke = async () => {
    if (!invokeTarget) return
    let body: { context: Record<string, unknown>; actions: unknown[]; horizon: number }
    try {
      const parsed = JSON.parse(invokeInput || '{}')
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
        toast({ tone: 'error', title: '测试入参必须是 JSON 对象' })
        return
      }
      body = {
        context: parsed.context ?? {},
        actions: parsed.actions ?? [],
        horizon: parsed.horizon ?? 1,
      }
    } catch {
      toast({ tone: 'error', title: '测试入参不是有效 JSON' })
      return
    }
    setInvoking(true)
    setInvokeResult(null)
    try {
      const result = await worldModelApi.invokeService(invokeTarget.id, body)
      setInvokeResult(result)
      if (result.ok) {
        toast({ tone: 'success', title: '调用成功', description: '耗时 ' + result.duration_ms + ' ms' })
        setItems(current => current.map(row => row.id === invokeTarget.id
          ? { ...row, call_count: row.call_count + 1 }
          : row))
      }
    } catch (err) {
      setInvokeResult({ ok: false, payload: null, error: apiError(err), duration_ms: 0, call_id: null })
    } finally {
      setInvoking(false)
    }
  }

  const openDetail = (item: WorldModelServiceSummary) => {
    setDetailTarget(item)
    setOntologyName('')
    setObjectTypeLabels({})
    setServiceCalls([])
    setServiceCallsTotal(0)
    const ontologyId = item.applicable_object_types?.ontology_id
    if (ontologyId) {
      ontologyApi.list({ page_size: 200 })
        .then(result => {
          const found = result.items.find(entry => entry.id === ontologyId)
          if (found) setOntologyName(found.name)
        })
        .catch(() => undefined)
      ontologyApi.listEntities(ontologyId)
        .then(entities => {
          const labels: Record<string, string> = {}
          entities.forEach(entity => {
            labels[entity.id] = entity.name_cn || entity.name_en || entity.id
          })
          setObjectTypeLabels(labels)
        })
        .catch(() => undefined)
    }
    setServiceCallsLoading(true)
    worldModelApi.listCalls({ service_id: item.id, page: 1, size: 10 })
      .then(result => {
        setServiceCalls(result.items)
        setServiceCallsTotal(result.total)
      })
      .catch(() => { setServiceCalls([]); setServiceCallsTotal(0) })
      .finally(() => setServiceCallsLoading(false))
  }

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  return (
    <div className="space-y-4">
      {/* 筛选栏 */}
      <section className="flex flex-wrap items-center gap-2 rounded-xl border border-slate-200 bg-white px-4 py-3 shadow-sm/50" aria-label="推演服务筛选">
        <div className="relative w-full sm:w-72">
          <Search size={14} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
          <input
            value={draftKeyword}
            onChange={event => setDraftKeyword(event.target.value)}
            onKeyDown={event => { if (event.key === 'Enter') applyFilters() }}
            placeholder="搜索服务名称或描述"
            aria-label="按服务名称或描述筛选"
            className="h-9 w-full rounded-lg border border-slate-200 bg-white pl-8 pr-3 text-sm text-slate-700 placeholder:text-slate-400 focus:border-teal-400 focus:outline-none focus:ring-2 focus:ring-teal-500/20"
          />
        </div>
        <select
          value={draftStatus}
          onChange={event => setDraftStatus(event.target.value as StatusFilter)}
          aria-label="按服务状态筛选"
          className="h-9 rounded-lg border border-slate-200 bg-white px-3 text-sm text-slate-600 focus:border-teal-400 focus:outline-none focus:ring-2 focus:ring-teal-500/20"
        >
          <option value="">全部状态</option>
          <option value="online">在线</option>
          <option value="offline">已下线</option>
        </select>
        <Button onClick={applyFilters} className="h-9 bg-teal-600 text-white hover:bg-teal-700">查询</Button>
        <button
          type="button"
          onClick={resetFilters}
          className="inline-flex h-9 items-center gap-1 rounded-lg px-2.5 text-xs text-slate-400 hover:bg-slate-50 hover:text-slate-600"
        >
          <X size={13} /> 重置
        </button>
        <span className="ml-auto hidden text-xs tabular-nums text-slate-400 sm:inline" aria-live="polite">
          共 {total} 个推演服务
        </span>
        <button
          type="button"
          onClick={() => void refreshAll()}
          className="inline-flex h-9 items-center gap-1.5 rounded-lg border border-slate-200 px-3 text-xs text-slate-600 hover:bg-slate-50"
        >
          <RefreshCw size={13} className={refreshing ? 'animate-spin' : ''} /> 刷新
        </button>
      </section>

      {/* 服务注册表 */}
      <section className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm/50">
        {loading ? (
          <p className="py-16 text-center text-sm text-slate-400">加载推演服务…</p>
        ) : error ? (
          <div className="flex flex-col items-center gap-3 py-16" role="alert">
            <p className="text-sm text-red-600">{error}</p>
            <button
              type="button"
              onClick={() => void loadServices()}
              className="rounded-lg border border-red-200 bg-white px-3 py-1.5 text-xs font-medium text-red-600 hover:bg-red-50"
            >
              重新加载
            </button>
          </div>
        ) : items.length === 0 ? (
          <div className="flex flex-col items-center justify-center px-6 py-16 text-center">
            <Rocket size={28} className="text-slate-300" />
            <p className="mt-3 text-sm font-medium text-slate-500">{keyword || status ? '没有符合条件的推演服务' : '暂无推演服务'}</p>
            <p className="mt-1 max-w-md text-xs leading-5 text-slate-400">
              {keyword || status
                ? '请调整名称或状态筛选条件'
                : '在「推演模型」开发页执行通过、保存版本并发布后，服务会作为一等实体出现在这里，对外提供统一调用端点。'}
            </p>
          </div>
        ) : (
          <>
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-slate-100 text-xs text-slate-400">
                  <th className="px-4 py-2.5 font-medium">推演服务</th>
                  <th className="px-4 py-2.5 font-medium">所属模型</th>
                  <th className="px-4 py-2.5 font-medium">版本</th>
                  <th className="px-4 py-2.5 font-medium">状态</th>
                  <th className="px-4 py-2.5 font-medium">调用</th>
                  <th className="px-4 py-2.5 font-medium">更新时间</th>
                  <th className="px-4 py-2.5 text-right font-medium">操作</th>
                </tr>
              </thead>
              <tbody>
                {items.map(item => (
                  <tr key={item.id} className="border-b border-slate-50 transition-colors hover:bg-slate-50/60">
                    <td className="max-w-[240px] px-4 py-2.5">
                      <p className="truncate font-medium text-slate-700" title={item.name}>{item.name}</p>
                      {item.description && (
                        <p className="truncate text-[11px] text-slate-400" title={item.description}>{item.description}</p>
                      )}
                    </td>
                    <td className="px-4 py-2.5">
                      <button
                        type="button"
                        onClick={() => { window.location.hash = '#/world-model/models/' + item.project_id + '/develop' }}
                        className="text-teal-700 underline-offset-2 hover:underline"
                        title={'进入「' + item.project_name + '」开发页'}
                      >
                        {item.project_name || '—'}
                      </button>
                    </td>
                    <td className="px-4 py-2.5 tabular-nums text-slate-500">{item.version_no !== null ? 'v' + item.version_no : '—'}</td>
                    <td className="px-4 py-2.5">{statusBadge(item.status)}</td>
                    <td className="px-4 py-2.5 tabular-nums text-slate-500">
                      <span className={item.failed_count > 0 ? 'text-red-500' : 'text-teal-600'}>
                        {item.call_count - item.failed_count}
                      </span>
                      <span className="text-slate-300"> / </span>
                      {item.call_count}
                    </td>
                    <td className="px-4 py-2.5 tabular-nums text-slate-500">{formatDateTime(item.updated_at)}</td>
                    <td className="px-4 py-2.5">
                      <span className="flex items-center justify-end gap-1">
                        <button
                          type="button"
                          onClick={() => openInvoke(item)}
                          disabled={item.status !== 'online'}
                          title={item.status === 'online' ? '试调用该推演服务' : '服务已下线，无法调用'}
                          aria-label={'试调用 ' + item.name}
                          className="inline-flex h-7 items-center gap-1 rounded-md border border-slate-200 px-2 text-[11px] text-slate-600 transition-colors hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40"
                        >
                          <Play size={12} /> 试调用
                        </button>
                        <button
                          type="button"
                          onClick={() => openDetail(item)}
                          aria-label={'查看 ' + item.name + ' 详情'}
                          title="服务详情与语义注册"
                          className="inline-flex h-7 w-7 items-center justify-center rounded-md border border-slate-200 text-slate-500 transition-colors hover:bg-slate-50"
                        >
                          <Eye size={13} />
                        </button>
                        <button
                          type="button"
                          onClick={() => void toggleStatus(item)}
                          disabled={togglingId === item.id}
                          aria-label={item.status === 'online' ? '下线 ' + item.name : '上线 ' + item.name}
                          title={item.status === 'online' ? '下线' : '上线'}
                          className="inline-flex h-7 w-7 items-center justify-center rounded-md border border-slate-200 text-slate-500 transition-colors hover:bg-slate-50 disabled:opacity-40"
                        >
                          <Power size={13} className={item.status === 'online' ? 'text-teal-600' : ''} />
                        </button>
                        <button
                          type="button"
                          onClick={() => copyEndpoint(item)}
                          aria-label={'复制 ' + item.name + ' 调用端点'}
                          title="复制调用端点"
                          className="inline-flex h-7 w-7 items-center justify-center rounded-md border border-slate-200 text-slate-500 transition-colors hover:bg-slate-50"
                        >
                          {copiedId === item.id ? <Check size={13} className="text-teal-600" /> : <Copy size={13} />}
                        </button>
                      </span>
                    </td>
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

      {/* 试调用弹窗 */}
      <Modal
        open={!!invokeTarget}
        onClose={() => !invoking && setInvokeTarget(null)}
        title="试调用推演服务"
        description={invokeTarget ? 'POST ' + (invokeTarget.endpoint_path ?? '') : ''}
        headerIcon={<Play size={17} />}
        size="lg"
        footer={(
          <>
            <Button variant="ghost" onClick={() => setInvokeTarget(null)} disabled={invoking}>关闭</Button>
            <Button
              onClick={() => void submitInvoke()}
              loading={invoking}
              disabled={!invokeTarget || invokeTarget.status !== 'online'}
              className="bg-teal-600 text-white hover:bg-teal-700"
            >
              调用
            </Button>
          </>
        )}
      >
        <div className="space-y-4">
          <div>
            <p className="mb-1.5 text-sm font-medium text-slate-700">测试入参（context / actions / horizon）</p>
            <textarea
              value={invokeInput}
              onChange={event => setInvokeInput(event.target.value)}
              spellCheck={false}
              rows={8}
              className="w-full resize-none rounded-lg border border-slate-200 bg-white px-3 py-2 font-mono text-xs leading-5 text-slate-700 focus:border-teal-500 focus:outline-none focus:ring-1 focus:ring-teal-500/30"
              aria-label="试调用测试入参 JSON"
            />
          </div>
          {invokeResult && (
            <div aria-live="polite">
              {invokeResult.ok ? (
                <div>
                  <p className="mb-1 flex items-center gap-1 text-[11px] font-medium text-teal-600">
                    <CheckCircle2 size={12} /> 调用成功 · {invokeResult.duration_ms} ms
                  </p>
                  <pre className="max-h-64 overflow-auto rounded-lg bg-slate-50 p-2.5 text-xs leading-5 text-slate-700">
                    {JSON.stringify(invokeResult.payload, null, 2)}
                  </pre>
                </div>
              ) : (
                <div>
                  <p className="mb-1 flex items-center gap-1 text-[11px] font-medium text-red-600">
                    <XCircle size={12} /> 调用失败
                  </p>
                  <p className="text-xs text-red-600">{invokeResult.error}</p>
                </div>
              )}
            </div>
          )}
        </div>
      </Modal>

      {/* 详情抽屉 */}
      {detailTarget && (
        <div className="fixed inset-0 z-40 flex justify-end" role="dialog" aria-label="推演服务详情">
          <div className="absolute inset-0 bg-slate-900/30" onClick={() => setDetailTarget(null)} />
          <aside className="relative z-10 flex h-full w-[min(560px,100%)] flex-col bg-white shadow-2xl">
            <header className="flex items-center justify-between border-b border-slate-100 px-5 py-3.5">
              <div>
                <h2 className="text-sm font-semibold text-slate-800">{detailTarget.name}</h2>
                <p className="mt-0.5 text-[11px] text-slate-400">
                  {detailTarget.project_name} · v{detailTarget.version_no ?? '-'} · 更新于 {formatDateTime(detailTarget.updated_at)}
                </p>
              </div>
              <button
                type="button"
                onClick={() => setDetailTarget(null)}
                aria-label="关闭详情"
                className="inline-flex h-8 w-8 items-center justify-center rounded-lg text-slate-400 hover:bg-slate-100 hover:text-slate-600"
              >
                <X size={16} />
              </button>
            </header>

            <div className="min-h-0 flex-1 overflow-auto px-5 py-4">
              <div className="space-y-4 text-xs leading-6 text-slate-600">
                <section>
                  <p className="mb-1.5 flex items-center justify-between text-xs font-semibold text-slate-800">
                    <span>基本状态</span>{statusBadge(detailTarget.status)}
                  </p>
                  <dl className="space-y-1.5 rounded-lg border border-slate-100 bg-slate-50/50 p-3">
                    <div className="flex justify-between"><dt className="text-slate-400">调用端点</dt><dd className="max-w-[320px] truncate font-mono text-[11px] text-slate-700">{detailTarget.endpoint_path ?? '—'}</dd></div>
                    <div className="flex justify-between"><dt className="text-slate-400">服务描述</dt><dd className="max-w-[320px] text-right text-slate-700">{detailTarget.description || '—'}</dd></div>
                    <div className="flex justify-between"><dt className="text-slate-400">调用统计</dt><dd className="tabular-nums text-slate-700">成功 {detailTarget.call_count - detailTarget.failed_count} / 共 {detailTarget.call_count}</dd></div>
                  </dl>
                </section>

                <section>
                  <p className="mb-1.5 text-xs font-semibold text-slate-800">本体语义注册</p>
                  <dl className="space-y-1.5 rounded-lg border border-slate-100 bg-slate-50/50 p-3">
                    <div className="flex justify-between">
                      <dt className="text-slate-400">所属本体</dt>
                      <dd className="max-w-[320px] text-right text-slate-700">{ontologyName || detailTarget.applicable_object_types?.ontology_id || '—'}</dd>
                    </div>
                    <div className="flex justify-between">
                      <dt className="text-slate-400">适用对象类型</dt>
                      <dd className="max-w-[320px] text-right text-slate-700">
                        {(detailTarget.applicable_object_types?.object_type_ids ?? [])
                          .map(id => objectTypeLabels[id] || id)
                          .join('、') || '—'}
                      </dd>
                    </div>
                    <div className="flex justify-between">
                      <dt className="text-slate-400">前置条件</dt>
                      <dd className="max-w-[320px] text-right text-slate-700">
                        {(detailTarget.preconditions ?? [])
                          .map(item => (objectTypeLabels[item.object_type_id] || item.object_type_id) + ' ≥ ' + item.min_count)
                          .join('；') || '无'}
                      </dd>
                    </div>
                  </dl>
                </section>

                <section>
                  <p className="mb-1.5 flex items-center gap-1.5 text-xs font-semibold text-slate-800">
                    <Activity size={13} /> 最近调用（共 {serviceCallsTotal} 条）
                  </p>
                  {serviceCallsLoading ? (
                    <p className="py-6 text-center text-xs text-slate-400">加载调用记录…</p>
                  ) : serviceCalls.length === 0 ? (
                    <p className="py-6 text-center text-xs text-slate-400">该服务暂无调用记录</p>
                  ) : (
                    <div className="overflow-hidden rounded-lg border border-slate-100">
                      <table className="w-full text-left text-xs">
                        <thead>
                          <tr className="border-b border-slate-100 text-[11px] text-slate-400">
                            <th className="px-3 py-2 font-medium">时间</th>
                            <th className="px-3 py-2 font-medium">调用方</th>
                            <th className="px-3 py-2 font-medium">结果</th>
                            <th className="px-3 py-2 text-right font-medium">耗时</th>
                          </tr>
                        </thead>
                        <tbody>
                          {serviceCalls.map(call => (
                            <tr key={call.id} className="border-b border-slate-50">
                              <td className="px-3 py-2 tabular-nums text-slate-500">{formatDateTime(call.created_at)}</td>
                              <td className="px-3 py-2 text-slate-600">{call.caller || '—'}</td>
                              <td className="px-3 py-2">
                                {call.ok
                                  ? <span className="inline-flex items-center gap-1 text-teal-600"><CheckCircle2 size={12} /> 成功</span>
                                  : <span className="inline-flex items-center gap-1 text-red-600"><XCircle size={12} /> 失败</span>}
                              </td>
                              <td className="px-3 py-2 text-right tabular-nums text-slate-500">{call.duration_ms} ms</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </section>
              </div>
            </div>
          </aside>
        </div>
      )}
    </div>
  )
}
