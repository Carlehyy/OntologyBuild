// 实例数据页 · 类型数据画像区：回答"这个类型的数据内容长什么样"。
// 数据来自只读接口 instance-browser/stats（发布版作用域）；字段值分布条
// 点击即对下方实例表施加精确属性过滤。
import { useQuery } from '@tanstack/react-query'
import ReactECharts from 'echarts-for-react'
import { AlertCircle, CalendarRange, Hash, Loader2 } from 'lucide-react'
import { apiClientV2 } from '@/api/client'
import type {
  LinkTypeNode, ObjectTypeNode, Selection,
} from './instanceBrowserTypes.ts'
import {
  buildCategoryBarOption,
  buildHistogramOption,
  buildTrendOption,
  formatFilterValue,
  formatNumber,
  normalizeInstanceTypeStats,
  type ActiveFilters,
  type FilterValue,
  type InstanceTypeStats,
} from './instanceStatsFormat.ts'
import { instanceSourceLabel } from './instanceValueDisplay.ts'
import InstanceChartCard from './InstanceChartCard'

function typeLabel(item: { name: string; displayName?: string }) {
  return item.displayName || item.name
}

export default function InstanceTypeProfileSection({
  ontologyId,
  selection,
  typeNode,
  activeFilters,
  onFilterProp,
}: {
  ontologyId: string
  selection: Selection
  typeNode: ObjectTypeNode | LinkTypeNode | null
  activeFilters: ActiveFilters
  onFilterProp: (name: string, value: FilterValue) => void
}) {
  const statsQuery = useQuery<InstanceTypeStats>({
    queryKey: ['instance-browser-stats', ontologyId, selection.kind, selection.id],
    queryFn: () => apiClientV2.get(
      `/formal/ontologies/${ontologyId}/instance-browser/stats`,
      {
        params: selection.kind === 'object'
          ? { object_type_id: selection.id }
          : { link_type_id: selection.id },
      },
    ) as Promise<InstanceTypeStats>,
    staleTime: 30_000,
  })
  // 运行时形状兜底：旧后端/异常响应一律降级为错误提示，不拖垮整页。
  const stats = normalizeInstanceTypeStats(statsQuery.data)
  const statsFailed = statsQuery.isError
    || (statsQuery.isSuccess && statsQuery.data !== undefined && !stats)
  const color = typeNode?.color || '#059669'
  const fields = stats?.fields ?? []
  const categoryFields = fields.filter(field => field.kind === 'category' && (field.values?.length ?? 0) > 0)
  const numberFields = fields.filter(field => field.kind === 'number' && field.histogram)
  const dateFields = fields.filter(field => field.kind === 'date' && field.min)

  return (
    <section
      key={`${selection.kind}:${selection.id}`}
      data-testid="instance-type-profile"
      className="anim-fade-in-up flex flex-col gap-4"
    >
      {/* 类型名片 */}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-xl border border-slate-200 bg-white px-4 py-3">
        <span
          className="inline-flex h-9 w-9 items-center justify-center rounded-lg text-lg"
          style={{ backgroundColor: `${color}1A`, color }}
        >
          {typeNode?.icon || <Hash size={16} />}
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold text-slate-800" title={typeNode?.description || undefined}>
            {typeNode ? typeLabel(typeNode) : '数据画像'}
            <span className="ml-2 rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[10px] font-normal text-slate-500">
              {typeNode?.name}
            </span>
          </p>
        </div>
        {stats && (
          <span className="text-xs text-slate-500">
            共 <span className="font-semibold tabular-nums text-slate-800">{formatNumber(stats.total)}</span> 条
          </span>
        )}
        {stats?.truncated && (
          <span className="rounded bg-amber-50 px-1.5 py-0.5 text-[10px] text-amber-600"
            title="数据量较大，分布统计基于前 20000 条采样">
            分布基于 2 万条采样
          </span>
        )}
        {stats?.bySource && stats.bySource.length > 0 && (
          <span className="flex flex-wrap items-center gap-1">
            {stats.bySource.map(entry => (
              <span key={entry.source}
                className="rounded border border-slate-200 bg-slate-50 px-1.5 py-0.5 text-[10px] tabular-nums text-slate-500">
                {instanceSourceLabel(entry.source)} {formatNumber(entry.count)}
              </span>
            ))}
          </span>
        )}
      </div>

      {statsQuery.isLoading && (
        <div className="flex items-center justify-center gap-2 rounded-xl border border-slate-200 bg-white py-10 text-xs text-slate-400">
          <Loader2 size={14} className="animate-spin text-teal-600" /> 正在分析该类型的数据画像…
        </div>
      )}

      {statsFailed && (
        <div className="flex items-center gap-2 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-xs text-red-700">
          <AlertCircle size={14} />
          <span className="flex-1">数据画像加载失败</span>
          <button type="button" onClick={() => void statsQuery.refetch()} className="font-medium hover:underline">重试</button>
        </div>
      )}

      {stats && (
        <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
          <InstanceChartCard
            title={stats.kind === 'link' ? '近 30 天新增趋势' : '近 30 天新增 / 更新趋势'}
            sub="按自然日（UTC）"
            testId="profile-trend-chart"
            bodyClassName="h-44"
          >
            <ReactECharts
              option={buildTrendOption(stats)}
              style={{ height: '100%', width: '100%' }}
              opts={{ renderer: 'svg' }}
              notMerge
            />
          </InstanceChartCard>

          {categoryFields.length === 0 && numberFields.length === 0 && dateFields.length === 0 && (
            <div className="flex items-center justify-center rounded-xl border border-dashed border-slate-200 bg-white text-xs text-slate-400">
              该类型暂无可画像的字段取值
            </div>
          )}

          {categoryFields.map(field => (
            <InstanceChartCard
              key={field.name}
              title={`${field.label} · 值分布`}
              sub={`覆盖率 ${Math.round(field.coverage * 100)}% · 点击条形筛选`}
              info={`字段 ${field.name} 的取值分布（去重 ${field.distinct ?? 0} 种）。覆盖率 = 非空实例占比；点击条形对下方实例表施加「${field.label} = 取值」的精确过滤，再次点击取消。`}
              testId={`profile-field-${field.name}`}
              bodyClassName="min-h-24"
            >
              <ReactECharts
                option={buildCategoryBarOption(field)}
                style={{
                  height: `${Math.max(96, ((field.values?.length ?? 0) + (field.otherCount ? 1 : 0)) * 30 + 16)}px`,
                  width: '100%',
                }}
                opts={{ renderer: 'svg' }}
                notMerge
                onEvents={{
                  click: (params: any) => {
                    const value = params?.data?.filterValue
                    if (value !== null && value !== undefined) onFilterProp(field.name, value as FilterValue)
                  },
                }}
              />
              {(activeFilters[field.name]?.length ?? 0) > 0 && (
                <p className="mt-1 text-[11px] text-teal-600">
                  已过滤：{activeFilters[field.name].map(formatFilterValue).join('、')}（再次点击条形可取消）
                </p>
              )}
            </InstanceChartCard>
          ))}

          {numberFields.map(field => (
            <InstanceChartCard
              key={field.name}
              title={`${field.label} · 数值范围`}
              sub={`覆盖率 ${Math.round(field.coverage * 100)}%`}
              info={`字段 ${field.name} 的最小/平均/最大值与取值直方图（等宽分桶）。`}
              testId={`profile-field-${field.name}`}
              bodyClassName="flex h-44 flex-col"
            >
              <div className="mb-1 grid grid-cols-3 gap-2 text-center">
                {([
                  ['最小', field.min],
                  ['平均', field.avg],
                  ['最大', field.max],
                ] as const).map(([label, value]) => (
                  <div key={label} className="rounded-lg bg-slate-50 px-2 py-1.5">
                    <p className="text-[10px] text-slate-400">{label}</p>
                    <p className="text-sm font-semibold tabular-nums text-slate-800">
                      {typeof value === 'number' ? formatNumber(value) : '—'}
                    </p>
                  </div>
                ))}
              </div>
              <div className="min-h-0 flex-1">
                <ReactECharts
                  option={buildHistogramOption(field)}
                  style={{ height: '100%', width: '100%' }}
                  opts={{ renderer: 'svg' }}
                  notMerge
                />
              </div>
            </InstanceChartCard>
          ))}

          {dateFields.map(field => (
            <div
              key={field.name}
              data-testid={`profile-field-${field.name}`}
              className="flex items-center gap-3 rounded-xl border border-slate-200 bg-white px-4 py-3 transition hover:border-teal-200"
            >
              <CalendarRange size={16} className="shrink-0 text-teal-600" />
              <div className="min-w-0">
                <p className="text-[13px] font-semibold text-slate-800">{field.label} · 时间范围</p>
                <p className="mt-0.5 text-xs tabular-nums text-slate-500">
                  {String(field.min)} <span className="text-slate-300">→</span> {String(field.max)}
                </p>
              </div>
              <span className="ml-auto text-[11px] text-slate-400">
                覆盖率 {Math.round(field.coverage * 100)}%
              </span>
            </div>
          ))}
        </div>
      )}
    </section>
  )
}
