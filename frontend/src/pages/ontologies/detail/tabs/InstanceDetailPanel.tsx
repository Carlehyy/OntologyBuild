// 实例详情面板:与实例浏览器左右联动的常驻卡片,回答“这条实例的完整档案与
// 来龙去脉”——完整属性值、来源/外部 ID、创建更新时间、属性级事实历史。
// 只读;事实来自 /instances/{id}/facts( Fact 溯源层,时间倒序)。
// 未选中实例时显示空态引导;大屏下可折叠成窄条给表格让出横向空间。
import { useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { AlertCircle, Loader2, MousePointerClick, PanelRightClose, PanelRightOpen, RefreshCw, X } from 'lucide-react'
import { apiClientV2 } from '@/api/client'
import type { DataColumn, InstanceFact, ObjectRow, ObjectTypeNode } from './instanceBrowserTypes'
import { formatInstanceDateTime, instanceFactKindLabel, instanceSourceLabel } from './instanceValueDisplay'
import { FullValue, SourceChip } from './InstanceValueText'

interface InstanceDetailPanelProps {
  ontologyId: string
  objectType: ObjectTypeNode | null
  columns: DataColumn[]
  row: ObjectRow | null
  collapsed: boolean
  onToggleCollapse: () => void
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

export default function InstanceDetailPanel({
  ontologyId,
  objectType,
  columns,
  row,
  collapsed,
  onToggleCollapse,
  onClose,
}: InstanceDetailPanelProps) {
  useEffect(() => {
    if (!row) return
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', closeOnEscape)
    return () => document.removeEventListener('keydown', closeOnEscape)
  }, [row, onClose])

  const factsQuery = useQuery<InstanceFact[]>({
    queryKey: ['instance-facts', ontologyId, row?.id],
    enabled: Boolean(row),
    queryFn: () => {
      if (!row) throw new Error('请先选择实例')
      return apiClientV2.get(`/formal/ontologies/${ontologyId}/instances/${row.id}/facts`, {
        params: { limit: 50 },
      })
    },
  })

  const label = row ? instanceLabel(row, objectType) : null
  const typeName = objectType ? objectType.displayName || objectType.name : '对象实例'

  // 折叠态:窄条只留展开入口与竖排标题,把横向空间还给表格。
  if (collapsed) {
    return (
      <aside
        data-testid="instance-detail-panel"
        aria-label="实例详情（已折叠）"
        className="flex shrink-0 items-center justify-between gap-2 rounded-xl border border-slate-200 bg-white px-3 py-2 lg:w-11 lg:flex-col lg:self-start lg:px-0 lg:py-3"
      >
        <button
          type="button"
          onClick={onToggleCollapse}
          aria-label="展开实例详情"
          title="展开实例详情"
          className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg text-slate-400 transition hover:bg-slate-100 hover:text-slate-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-400"
        >
          <PanelRightOpen size={14} />
        </button>
        <span className="text-[11px] font-medium tracking-[0.2em] text-slate-400 lg:[writing-mode:vertical-lr]">
          实例详情
        </span>
      </aside>
    )
  }

  return (
    <aside
      data-testid="instance-detail-panel"
      aria-label={label ? `实例 ${label} 详情` : '实例详情'}
      className="flex w-full shrink-0 flex-col self-start rounded-xl border border-slate-200 bg-white lg:sticky lg:top-5 lg:max-h-[calc(100vh-2.5rem)] lg:w-[380px]"
    >
      <div className="flex shrink-0 items-start justify-between gap-2 border-b border-slate-100 px-4 py-3">
        <div className="min-w-0">
          {label ? (
            <>
              <p className="truncate text-sm font-semibold text-slate-900" title={label}>{label}</p>
              <p className="mt-0.5 text-[11px] text-slate-400">{typeName} · 实例详情</p>
            </>
          ) : (
            <p className="text-sm font-semibold text-slate-900">实例详情</p>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <button
            type="button"
            onClick={onToggleCollapse}
            aria-label="折叠实例详情"
            title="折叠实例详情"
            className="flex h-7 w-7 items-center justify-center rounded-lg text-slate-400 transition hover:bg-slate-100 hover:text-slate-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-400"
          >
            <PanelRightClose size={14} />
          </button>
          {row && (
            <button
              type="button"
              onClick={onClose}
              aria-label="关闭实例详情"
              className="flex h-7 w-7 items-center justify-center rounded-lg text-slate-400 transition hover:bg-slate-100 hover:text-slate-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-400"
            >
              <X size={14} />
            </button>
          )}
        </div>
      </div>

      {!row ? (
        <div
          data-testid="instance-detail-empty"
          className="flex flex-col items-center justify-center px-6 py-10 text-center"
        >
          <div className="flex h-11 w-11 items-center justify-center rounded-xl bg-slate-100 text-slate-300">
            <MousePointerClick size={20} />
          </div>
          <p className="mt-3 text-xs font-medium text-slate-500">点击左侧表格中的对象实例行</p>
          <p className="mt-1 text-[11px] leading-5 text-slate-400">这里将展示该实例的基本信息、完整属性值与事实历史</p>
        </div>
      ) : (
      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
        <section aria-label="基本信息">
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
          <dl className="mt-2 space-y-2.5">
            {columns.map(column => (
              <div key={`${column.computed ? 'computed' : 'stored'}:${column.name}`}>
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

        <section aria-label="事实历史" className="mt-5 border-t border-slate-100 pt-3">
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
          ) : !factsQuery.data?.length ? (
            <p className="py-4 text-xs text-slate-400">暂无事实记录</p>
          ) : (
            <ul className="mt-2 space-y-2.5" data-testid="instance-facts-list">
              {factsQuery.data.map(fact => (
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
          )}
        </section>
      </div>
      )}
    </aside>
  )
}
