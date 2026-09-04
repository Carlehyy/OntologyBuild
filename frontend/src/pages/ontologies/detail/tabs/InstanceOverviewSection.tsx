// 实例数据页 · 数据概览区：回答"数据进来了吗、分布在哪、从哪来、还是活的吗"。
// 数据全部复用已加载的 catalog / overview（共享 queryKey 缓存），零新增请求。
import { useMemo } from 'react'
import ReactECharts from 'echarts-for-react'
import { Activity, Box, Boxes, Link2 } from 'lucide-react'
import type { FormalOverviewSummary, InstanceCatalog, Selection } from './instanceBrowserTypes.ts'
import {
  buildActivityAreaOption,
  buildSourceDonutOption,
  buildTypeBarOption,
  formatNumber,
} from './instanceStatsFormat.ts'
import { instanceSourceLabel } from './instanceValueDisplay.ts'
import { useCountUp } from './useCountUp.ts'
import { CHART_SERIES_PALETTE } from '@/lib/echartsTheme'
import InstanceChartCard from './InstanceChartCard'

function KpiCard({ icon: Icon, iconCls, label, value, sub, onClick }: {
  icon: any; iconCls: string; label: string; value: number; sub: string
  onClick?: () => void
}) {
  const display = useCountUp(value)
  return (
    <button
      type="button"
      onClick={onClick}
      className="flex items-center gap-3 rounded-xl border border-border bg-card px-4 py-3 text-left transition hover:-translate-y-0.5 hover:border-brand-line focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
    >
      <span className={`inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-lg ${iconCls}`}>
        <Icon size={16} />
      </span>
      <span className="min-w-0 flex-1">
        <span className="flex flex-wrap items-baseline gap-x-2">
          <span className="text-xl font-semibold tabular-nums text-foreground">{formatNumber(display)}</span>
          <span className="whitespace-nowrap text-xs text-[var(--color-text-tertiary)]">{label}</span>
        </span>
        <span className="mt-0.5 block truncate text-[11px] text-[var(--color-text-tertiary)]" title={sub}>{sub}</span>
      </span>
    </button>
  )
}

export default function InstanceOverviewSection({
  catalog,
  overview,
  onSelectType,
  onFilterSource,
  onScrollToBrowser,
}: {
  catalog: InstanceCatalog
  overview?: FormalOverviewSummary
  onSelectType: (selection: Selection) => void
  onFilterSource: (source: string) => void
  onScrollToBrowser: () => void
}) {
  const objectTotal = useMemo(
    () => catalog.objectTypes.reduce((sum, item) => sum + item.instanceCount, 0),
    [catalog.objectTypes],
  )
  const linkTotal = useMemo(
    () => catalog.linkTypes.reduce((sum, item) => sum + item.instanceCount, 0),
    [catalog.linkTypes],
  )
  const typeEntries = useMemo(() => [
    ...catalog.objectTypes.map(item => ({
      id: item.id,
      name: item.displayName || item.name,
      color: item.color,
      count: item.instanceCount,
      kind: 'object' as const,
    })),
    ...catalog.linkTypes.map(item => ({
      id: item.id,
      name: item.displayName || item.name,
      color: item.color,
      count: item.instanceCount,
      kind: 'link' as const,
    })),
  ], [catalog.objectTypes, catalog.linkTypes])
  const sourceEntries = useMemo(
    () => Object.entries(overview?.data?.instancesBySource || {})
      .filter(([, count]) => count > 0)
      .sort((a, b) => b[1] - a[1])
      .map(([source, count]) => ({ source, count })),
    [overview?.data?.instancesBySource],
  )
  const sourceTotal = sourceEntries.reduce((sum, entry) => sum + entry.count, 0)
  // 类型分布图只呈现有实例的类型（全为 0 时给空态，避免几十根零值条的噪声）；
  // 类型过多时取前 8，保持图面可读。
  const chartTypes = useMemo(
    () => typeEntries.filter(item => item.count > 0).slice(0, 8),
    [typeEntries],
  )
  const activityDays = overview?.runtime?.daily7d ?? []
  const activityTotal = activityDays.reduce(
    (sum, day) => sum
      + (day.firings?.fired ?? 0) + (day.firings?.error ?? 0)
      + (day.actionRuns?.success ?? 0) + (day.actionRuns?.failed ?? 0),
    0,
  )

  return (
    <section data-testid="instance-overview-section" className="flex flex-col gap-4">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <KpiCard icon={Box} iconCls="bg-[var(--color-info-bg)] text-[var(--color-info)]" label="对象实例"
          value={objectTotal} sub={`覆盖 ${catalog.objectTypes.length} 类对象实体`}
          onClick={onScrollToBrowser} />
        <KpiCard icon={Link2} iconCls="bg-viz-violet-soft text-viz-violet" label="关系实例"
          value={linkTotal} sub={`覆盖 ${catalog.linkTypes.length} 类实体关系`}
          onClick={onScrollToBrowser} />
        <KpiCard icon={Boxes} iconCls="bg-brand-soft text-brand-ink" label="数据类型"
          value={catalog.objectTypes.length + catalog.linkTypes.length}
          sub="点击类型分布图可直达数据"
          onClick={onScrollToBrowser} />
        <KpiCard icon={Activity} iconCls="bg-[var(--color-warning-bg)] text-[var(--color-warning)]" label="近 7 天活跃"
          value={activityTotal} sub="哨兵命中与动作执行总量"
          onClick={onScrollToBrowser} />
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <InstanceChartCard title="类型分布" sub="点击条形直达该类型数据" testId="overview-type-chart"
          bodyClassName="h-44">
          {chartTypes.length === 0 ? (
            <p className="flex h-full items-center justify-center text-xs text-[var(--color-text-tertiary)]">
              暂无实例数据分布 —— 数据灌入后这里会展示各类型占比
            </p>
          ) : (
            <ReactECharts
              option={buildTypeBarOption(chartTypes)}
              style={{ height: '100%', width: '100%' }}
              opts={{ renderer: 'svg' }}
              notMerge
              onEvents={{
                click: (params: any) => {
                  const data = params?.data
                  if (data?.typeId) onSelectType({ kind: data.kind, id: data.typeId })
                },
              }}
            />
          )}
        </InstanceChartCard>

        <InstanceChartCard title="来源构成" sub="点击来源精确过滤表格" testId="overview-source-chart"
          info="统计当前发布版内对象实例的写入来源分布。点击图例或扇区，可将下方实例表精确过滤到该来源。"
          bodyClassName="h-44">
          {sourceEntries.length === 0 ? (
            <p className="flex h-full items-center justify-center text-xs text-[var(--color-text-tertiary)]">暂无来源数据</p>
          ) : (
            <div className="flex h-full items-center gap-2">
              <div className="relative h-full min-w-0 flex-1">
                <ReactECharts
                  option={buildSourceDonutOption(sourceEntries)}
                  style={{ height: '100%', width: '100%' }}
                  opts={{ renderer: 'svg' }}
                  notMerge
                  onEvents={{
                    click: (params: any) => {
                      if (params?.name) onFilterSource(String(params.name))
                    },
                  }}
                />
                <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
                  <span className="text-lg font-semibold tabular-nums text-foreground">{formatNumber(sourceTotal)}</span>
                  <span className="text-[10px] text-[var(--color-text-tertiary)]">实例总数</span>
                </div>
              </div>
              <div className="flex w-32 shrink-0 flex-col gap-1.5">
                {sourceEntries.map((entry, index) => (
                  <button
                    key={entry.source}
                    type="button"
                    onClick={() => onFilterSource(entry.source)}
                    title={`过滤来源：${instanceSourceLabel(entry.source)}`}
                    className="flex items-center gap-1.5 rounded-md px-1.5 py-1 text-left text-[11px] text-muted-foreground transition hover:bg-brand-soft hover:text-brand-ink"
                  >
                    <span
                      className="h-2 w-2 shrink-0 rounded-full"
                      style={{ backgroundColor: CHART_SERIES_PALETTE[index % CHART_SERIES_PALETTE.length] }}
                    />
                    <span className="min-w-0 flex-1 truncate">{instanceSourceLabel(entry.source)}</span>
                    <span className="tabular-nums text-[var(--color-text-tertiary)]">{formatNumber(entry.count)}</span>
                  </button>
                ))}
              </div>
            </div>
          )}
        </InstanceChartCard>

        <InstanceChartCard title="近 7 天运行活动" sub="哨兵命中 · 动作成功 · 失败" testId="overview-activity-chart"
          info="近 7 天（按自然日）当前发布版的运行遥测：哨兵命中次数、动作执行成功次数，以及两者失败合计。"
          bodyClassName="h-44">
          <ReactECharts
            option={buildActivityAreaOption(activityDays)}
            style={{ height: '100%', width: '100%' }}
            opts={{ renderer: 'svg' }}
            notMerge
          />
        </InstanceChartCard>
      </div>
    </section>
  )
}
