import type { ReactNode } from 'react'

/**
 * 世界模型三页共用的统计概览卡：图标 + 指标名 + 数值（+ 可选补充说明）。
 * 视觉与调用记录页原 OverviewCard 一致。
 */
export default function StatCard({ icon, label, value, sub, tone }: {
  icon: ReactNode
  label: string
  value: string
  sub?: string
  tone?: 'default' | 'danger'
}) {
  return (
    <div className="flex items-center gap-3 rounded-xl border border-border bg-card px-4 py-3 shadow-sm/50">
      <span className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-lg ${tone === 'danger' ? 'bg-[var(--color-danger-bg)] text-destructive' : 'bg-brand-soft text-brand-ink'}`}>
        {icon}
      </span>
      <div className="min-w-0">
        <p className="text-[11px] text-muted-foreground">{label}</p>
        <p className="text-base font-semibold tabular-nums text-foreground">{value}</p>
        {sub && <p className="truncate text-[11px] text-muted-foreground" title={sub}>{sub}</p>}
      </div>
    </div>
  )
}
