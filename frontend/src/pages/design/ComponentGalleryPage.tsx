import { useState } from 'react'
import {
  AvailabilityScheduler,
  defaultWeek,
  type WeekAvailability,
} from '@/components/availability-scheduler'

/**
 * 平台共享组件预览路由（/design/components，不进入导航）。
 *
 * 作用：给 components/ 下的共享组件一个从 main.tsx 可达的真实挂载点
 * （feature-boundaries 门禁要求生产源码可达），同时供开发与验收直观查看。
 * 当前展示：AvailabilityScheduler（vendored 自 beUI，MIT）。
 */
export default function ComponentGalleryPage() {
  const [week, setWeek] = useState<WeekAvailability>(defaultWeek)

  return (
    <div className="mx-auto w-full max-w-4xl px-6 py-10">
      <header className="mb-6">
        <p className="font-mono text-xs uppercase tracking-widest text-muted-foreground">
          Platform Components
        </p>
        <h1 className="mt-1 text-2xl font-semibold text-foreground">
          组件预览 · AvailabilityScheduler
        </h1>
        <p className="mt-2 max-w-2xl text-sm text-muted-foreground">
          每周可用时段编辑器：星期开关、多段起止时间（24 小时制）、
          「复制到其他天」。来源 beUI（MIT），已映射平台设计令牌；
          动效基于 motion，并遵循系统「减少动态效果」偏好。
        </p>
      </header>

      <section className="rounded-xl border border-border bg-card p-6 shadow-sm">
        <AvailabilityScheduler value={week} onChange={setWeek} />
      </section>

      <section className="mt-6">
        <p className="mb-2 text-xs font-medium text-muted-foreground">当前值（受控）</p>
        <pre
          data-testid="scheduler-value"
          className="max-h-72 overflow-auto rounded-xl border border-border bg-muted p-4 font-mono text-xs leading-relaxed text-foreground"
        >
          {JSON.stringify(week, null, 2)}
        </pre>
      </section>
    </div>
  )
}
