import { useState } from 'react'
import {
  AvailabilityScheduler,
  defaultWeek,
  type WeekAvailability,
} from '@/components/availability-scheduler'
import { AnimatedNumber } from '@/components/motion-ui/animated-number'
import {
  MultiSelect,
  MultiSelectContent,
  MultiSelectEmpty,
  MultiSelectInput,
  MultiSelectItem,
  MultiSelectList,
  MultiSelectTrigger,
  MultiSelectValue,
} from '@/components/motion-ui/multi-select'
import { TiltCard } from '@/components/motion-ui/tilt-card'

/**
 * 平台共享组件预览路由（/design/components，不进入导航）。
 *
 * 作用：给 components/ 下的共享组件一个从 main.tsx 可达的真实挂载点
 * （feature-boundaries 门禁要求生产源码可达），同时供开发与验收直观查看。
 * 当前展示：AvailabilityScheduler、MultiSelect（vendored 自 beUI，MIT）。
 */
export default function ComponentGalleryPage() {
  const [week, setWeek] = useState<WeekAvailability>(defaultWeek)
  const [tags, setTags] = useState<string[]>(['frontend'])

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

      <section className="mt-6 grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div className="rounded-xl border border-border bg-card p-6 shadow-sm">
          <p className="text-xs font-medium text-muted-foreground">AnimatedNumber · 数字滚动</p>
          <p className="mt-2 text-3xl font-semibold text-foreground">
            <AnimatedNumber value={42857} duration={1.2} />
          </p>
          <p className="mt-1 text-xs text-muted-foreground">进入视口时从 0 滚入，遵循「减少动态效果」</p>
        </div>
        <div className="rounded-xl border border-border bg-card p-6 shadow-sm">
          <p className="text-xs font-medium text-muted-foreground">TiltCard · 3D 悬停倾转</p>
          <div className="mt-3">
            <TiltCard max={10} className="rounded-xl border border-border bg-muted p-5">
              <p className="text-sm font-medium text-foreground">鼠标悬停试试</p>
              <p className="mt-1 text-xs text-muted-foreground">指针跟随倾转 + 高光，仅桌面悬停设备生效</p>
            </TiltCard>
          </div>
        </div>
      </section>

      <section className="mt-6 rounded-xl border border-border bg-card p-6 shadow-sm">
        <p className="text-xs font-medium text-muted-foreground">MultiSelect · 多选筛选（vendored beUI）</p>
        <div className="mt-3 max-w-sm">
          <MultiSelect value={tags} onValueChange={setTags}>
            <MultiSelectTrigger className="bg-background">
              <MultiSelectValue placeholder="全部方向" />
              <MultiSelectInput aria-label="筛选方向" placeholder="搜索方向…" />
            </MultiSelectTrigger>
            <MultiSelectContent>
              <MultiSelectList ariaLabel="方向">
                <MultiSelectItem value="frontend">前端</MultiSelectItem>
                <MultiSelectItem value="backend">后端</MultiSelectItem>
                <MultiSelectItem value="platform">平台</MultiSelectItem>
                <MultiSelectItem value="algorithm">算法</MultiSelectItem>
              </MultiSelectList>
              <MultiSelectEmpty />
            </MultiSelectContent>
          </MultiSelect>
        </div>
        <p className="mt-3 text-xs text-muted-foreground">
          当前值：<span data-testid="multiselect-value" className="font-mono">{tags.length ? tags.join(', ') : '（空）'}</span>
          ；输入框可搜索过滤，键盘 ↑↓ 移动、Enter 勾选、Backspace 逐个回退。
        </p>
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
