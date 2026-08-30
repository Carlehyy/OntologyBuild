import type { ReactNode } from 'react'
import { AnimatedNumber } from '@/components/motion-ui/animated-number'

/**
 * 世界模型三页共用的统计概览卡：图标 + 指标名 + 数值（beUI AnimatedNumber
 * 滚动入场，遵循"减少动态效果"）+ 可选补充说明。
 * value 为数值，format 决定展示（如百分比/耗时）；无 format 时千分位取整。
 */
export default function StatCard({ icon, label, value, format, sub, tone }: {
  icon: ReactNode
  label: string
  value: number
  format?: (n: number) => string
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
        <p className="text-base font-semibold text-foreground">
          <AnimatedNumber value={value} format={format} duration={0.9} />
        </p>
        {sub && <p className="truncate text-[11px] text-muted-foreground" title={sub}>{sub}</p>}
      </div>
    </div>
  )
}
