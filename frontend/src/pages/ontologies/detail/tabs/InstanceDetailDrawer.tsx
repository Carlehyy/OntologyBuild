// 实例详情弹窗:行点击后右侧滑出(Sheet),回答“这条实例的完整档案与
// 来龙去脉”——完整属性值、来源/外部 ID、创建更新时间、属性级事实历史。
// 只读;事实来自 /instances/{id}/facts( Fact 溯源层,时间倒序,按页加载)。
import { useInfiniteQuery } from '@tanstack/react-query'
import { AlertCircle, ChevronDown, Loader2, RefreshCw } from 'lucide-react'
import { apiClientV2 } from '@/api/client'
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet'
import type { DataColumn, InstanceFact, ObjectRow, ObjectTypeNode } from './instanceBrowserTypes'
import { formatInstanceDateTime, instanceFactKindLabel, instanceSourceLabel } from './instanceValueDisplay'
import { FullValue, SourceChip } from './InstanceValueText'

const FACTS_PAGE_SIZE = 20

interface InstanceDetailDrawerProps {
  ontologyId: string
  objectType: ObjectTypeNode | null
  columns: DataColumn[]
  row: ObjectRow
  onClose: () => void
}

// 与后端 _instance_summary 同口径:主键属性值 → name/title/label/displayName
// → externalId → 实例 id。
function instanceLabel(row: ObjectRow, objectType?: ObjectTypeNode | null): string {
  const properties = row.properties || {}
  const readable = (value: unknown) => (
    value === null || value === undefined || value === '' ? null : String(value)
  )
  const primary = objectType?.properties.find(
    property => property.id === objectType.primaryKey || property.name === objectType.primaryKey,
  )
  if (primary) {
    const value = readable(properties[primary.name])
    if (value !== null) return value
  }
  for (const key of ['name', 'title', 'label', 'displayName']) {
    const value = readable(properties[key])
    if (value !== null) return value
  }
  return row.externalId || row.id
}

function factValueText(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  if (typeof value === 'string') return value
  if (typeof value === 'number') return value.toLocaleString('zh-CN')
  return JSON.stringify(value)
}

export default function InstanceDetailDrawer({
  ontologyId,
  objectType,
  columns,
  row,
  onClose,
}: InstanceDetailDrawerProps) {
  const factsQuery = useInfiniteQuery({
    queryKey: ['instance-facts', ontologyId, row.id],
    queryFn: ({ pageParam }) => apiClientV2.get(`/formal/ontologies/${ontologyId}/instances/${row.id}/facts`, {
      params: { limit: FACTS_PAGE_SIZE, offset: pageParam },
    }) as Promise<InstanceFact[]>,
    initialPageParam: 0,
    // 服务端按时间倒序稳定分页:返回不足一页即没有更多
    getNextPageParam: (lastPage, allPages) => (
      lastPage.length === FACTS_PAGE_SIZE ? allPages.length * FACTS_PAGE_SIZE : undefined
    ),
  })
  const facts = factsQuery.data?.pages.flat() ?? []

  const label = instanceLabel(row, objectType)
  const typeName = objectType ? objectType.displayName || objectType.name : '对象实例'

  return (
    <Sheet open onOpenChange={nextOpen => { if (!nextOpen) onClose() }}>
      <SheetContent
        data-testid="instance-detail-drawer"
        aria-label={`实例 ${label} 详情`}
        className="w-[min(480px,94%)]"
      >
        <SheetHeader>
          <SheetTitle className="truncate" title={label}>{label}</SheetTitle>
          <SheetDescription>{typeName} · 实例详情</SheetDescription>
        </SheetHeader>

        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
          <section aria-label="基本信息" className="rounded-xl border border-slate-100 bg-slate-50/60 px-4 py-3">
            <dl className="grid grid-cols-[5.5rem_minmax(0,1fr)] gap-y-2 text-xs">
              <dt className="text-slate-400">外部 ID</dt>
              <dd className="truncate font-mono text-[11px] text-slate-600" title={row.externalId || undefined}>
                {row.externalId || '—'}
              </dd>
              <dt className="text-slate-400">来源</dt>
              <dd><SourceChip source={row.source} /></dd>
              <dt className="text-slate-400">创建时间</dt>
              <dd className="tabular-nums text-slate-600">{formatInstanceDateTime(row.createdAt)}</dd>
              <dt className="text-slate-400">更新时间</dt>
              <dd className="tabular-nums text-slate-600">{formatInstanceDateTime(row.updatedAt)}</dd>
            </dl>
          </section>

          <section aria-label="属性值" className="mt-4">
            <h3 className="text-[11px] font-semibold uppercase tracking-[0.06em] text-slate-400">属性值</h3>
            <dl className="mt-2 divide-y divide-slate-100 rounded-xl border border-slate-100">
              {columns.map(column => (
                <div
                  key={`${column.computed ? 'computed' : 'stored'}:${column.name}`}
                  className="px-4 py-2.5"
                >
                  <dt className="flex items-center gap-1.5 text-[11px] text-slate-400">
                    <span>{column.label}</span>
                    {column.computed && (
                      <span className="rounded bg-violet-50 px-1 py-0.5 text-[9px] text-violet-600">派生</span>
                    )}
                  </dt>
                  <dd className="mt-0.5 overflow-x-auto text-xs">
                    <FullValue
                      type={column.type}
                      value={column.computed
                        ? row.computed?.[column.name] ?? row.properties?.[column.name]
                        : row.properties?.[column.name]}
                    />
                  </dd>
                </div>
              ))}
            </dl>
          </section>

          <section aria-label="事实历史" className="mt-5">
            <h3 className="text-[11px] font-semibold uppercase tracking-[0.06em] text-slate-400">
              事实历史
              <span className="ml-1.5 font-normal normal-case tracking-normal text-slate-300">属性级变更,时间倒序</span>
            </h3>
            {factsQuery.isLoading ? (
              <p className="flex items-center gap-2 py-4 text-xs text-slate-400">
                <Loader2 size={13} className="animate-spin text-teal-600" /> 正在加载事实历史…
              </p>
            ) : factsQuery.isError ? (
              <p className="flex items-center gap-2 py-4 text-xs text-red-600" role="alert">
                <AlertCircle size={13} /> 事实历史加载失败
                <button
                  type="button"
                  onClick={() => void factsQuery.refetch()}
                  className="inline-flex items-center gap-1 font-medium text-teal-700 hover:underline"
                >
                  <RefreshCw size={11} /> 重试
                </button>
              </p>
            ) : !facts.length ? (
              <p className="py-4 text-xs text-slate-400">暂无事实记录</p>
            ) : (
              <>
                <ul className="mt-2 space-y-2.5" data-testid="instance-facts-list">
                  {facts.map(fact => (
                    <li key={fact.id} className="rounded-lg border border-slate-100 bg-slate-50/60 px-2.5 py-2">
                      <div className="flex items-center justify-between gap-2">
                        <span className="rounded bg-white px-1.5 py-0.5 text-[10px] font-medium text-slate-500 ring-1 ring-slate-200">
                          {instanceFactKindLabel(fact.kind)}
                        </span>
                        <time className="text-[10px] tabular-nums text-slate-400">
                          {fact.recordedAt ? formatInstanceDateTime(fact.recordedAt) : '—'}
                        </time>
                      </div>
                      <p className="mt-1.5 break-all text-xs leading-5 text-slate-700">
                        <span className="font-mono text-[11px] text-slate-500">{fact.propertyName}</span>
                        {' → '}
                        {fact.present === false ? '—（已删除）' : factValueText(fact.value)}
                      </p>
                      <p className="mt-1 text-[10px] text-slate-400">来源:{instanceSourceLabel(fact.source)}</p>
                    </li>
                  ))}
                </ul>
                {factsQuery.hasNextPage && (
                  <button
                    type="button"
                    data-testid="instance-facts-load-more"
                    onClick={() => void factsQuery.fetchNextPage()}
                    disabled={factsQuery.isFetchingNextPage}
                    className="mt-3 inline-flex h-8 w-full items-center justify-center gap-1.5 rounded-lg border border-slate-200 bg-white text-xs font-medium text-slate-600 transition hover:border-teal-200 hover:bg-teal-50 hover:text-teal-700 disabled:cursor-wait disabled:opacity-60"
                  >
                    {factsQuery.isFetchingNextPage
                      ? <Loader2 size={12} className="animate-spin" />
                      : <ChevronDown size={12} />}
                    {factsQuery.isFetchingNextPage ? '正在加载…' : `加载更多（已显示 ${facts.length} 条）`}
                  </button>
                )}
              </>
            )}
          </section>
        </div>
      </SheetContent>
    </Sheet>
  )
}
