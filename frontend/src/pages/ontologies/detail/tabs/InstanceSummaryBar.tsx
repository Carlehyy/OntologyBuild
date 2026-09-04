// 实例数据页顶部的轻量汇总条:回答“数据进来了吗、从哪来”——当前发布的
// 对象/关系实例总数(来自实例目录)与来源构成(复用总览页同一 queryKey
// 的 /overview 缓存,不新增请求)。总览请求失败时退化为只显示总数。
import type { FormalOverviewSummary, InstanceCatalog } from './instanceBrowserTypes'
import { instanceSourceLabel } from './instanceValueDisplay'

export default function InstanceSummaryBar({
  catalog,
  overview,
}: {
  catalog: InstanceCatalog
  overview?: FormalOverviewSummary
}) {
  const objectTotal = catalog.objectTypes.reduce((sum, item) => sum + item.instanceCount, 0)
  const linkTotal = catalog.linkTypes.reduce((sum, item) => sum + item.instanceCount, 0)
  const sourceEntries = Object.entries(overview?.data?.instancesBySource || {})
    .filter(([, count]) => count > 0)
    .sort((left, right) => right[1] - left[1])

  return (
    <div
      data-testid="instance-summary-bar"
      className="flex shrink-0 flex-wrap items-center gap-x-4 gap-y-1.5 border-b border-border bg-muted px-5 py-2 text-xs text-muted-foreground"
    >
      <span>
        对象实例 <span className="font-semibold tabular-nums text-foreground">{objectTotal}</span>
      </span>
      <span>
        关系实例 <span className="font-semibold tabular-nums text-foreground">{linkTotal}</span>
      </span>
      {sourceEntries.length > 0 && (
        <span className="flex flex-wrap items-center gap-1.5">
          <span className="text-[var(--color-text-tertiary)]">来源</span>
          {sourceEntries.map(([source, count]) => (
            <span
              key={source}
              className="rounded border border-border bg-card px-1.5 py-0.5 text-[10px] tabular-nums text-muted-foreground"
            >
              {instanceSourceLabel(source)} {count}
            </span>
          ))}
        </span>
      )}
      <span className="ml-auto text-[10px] text-[var(--color-text-tertiary)]">当前发布 {catalog.release.version} 的当前态投影</span>
    </div>
  )
}
