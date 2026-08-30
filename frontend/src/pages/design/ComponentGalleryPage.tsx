import { useState } from 'react'
import { Play, Sliders } from 'lucide-react'
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
import { Switch } from '@/components/motion-ui/switch'
import { Checkbox } from '@/components/motion-ui/checkbox'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/motion-ui/select'
import { Tooltip } from '@/components/motion-ui/tooltip'
import { IconButton } from '@/components/motion-ui/icon-button'
import { MorphPopover, MorphPopoverContent } from '@/components/motion-ui/popover-morph'
import {
  CenterMorphModal,
  CenterMorphModalContent,
} from '@/components/motion-ui/center-morph-modal'
import { MOTION_UI_CATALOG, UPSTREAM } from '@/components/motion-ui/catalog'

/**
 * 平台共享组件画廊与 beUI 目录（/design/components，不进入导航）。
 *
 * 双职责：① components/ 下已 vendor 组件的活示例（从 main.tsx 可达，满足
 * feature-boundaries 门禁）；② 上游 beUI 的 B 端策展目录渲染（数据源
 * motion-ui/catalog.ts）。引入新组件时按 components/README.md 流程在此补示例。
 */
export default function ComponentGalleryPage() {
  const [week, setWeek] = useState<WeekAvailability>(defaultWeek)
  const [demoSwitch, setDemoSwitch] = useState(true)
  const [demoChecked, setDemoChecked] = useState(false)
  const [demoSelect, setDemoSelect] = useState('statistical')
  const [demoModalOpen, setDemoModalOpen] = useState(false)
  const [tags, setTags] = useState<string[]>(['frontend'])

  const available = MOTION_UI_CATALOG.filter(entry => entry.status === 'available')
  const vendored = MOTION_UI_CATALOG.filter(entry => entry.status === 'vendored')
  const unsuitable = MOTION_UI_CATALOG.filter(entry => entry.status === 'unsuitable')

  return (
    <div className="mx-auto w-full max-w-4xl px-6 py-10">
      <header className="mb-6">
        <p className="font-mono text-xs uppercase tracking-widest text-muted-foreground">
          Platform Components
        </p>
        <h1 className="mt-1 text-2xl font-semibold text-foreground">组件画廊</h1>
        <p className="mt-2 max-w-2xl text-sm text-muted-foreground">
          已 vendor 的 beUI 组件活示例与上游目录速查。选型规则与引入流程见
          components/README.md；目录钉在上游 commit
          <code className="mx-1 rounded bg-muted px-1 font-mono text-xs">{UPSTREAM.commit.slice(0, 12)}</code>
          （{UPSTREAM.license.split(' ©')[0]}）。
        </p>
      </header>

      {/* ── 已引入组件：活示例 ── */}
      <section aria-label="动效原语示例" className="grid grid-cols-1 gap-4 sm:grid-cols-2">
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
            <TiltCard max={10} glare={false} className="rounded-xl border border-border bg-muted p-5">
              <p className="text-sm font-medium text-foreground">鼠标悬停试试</p>
              <p className="mt-1 text-xs text-muted-foreground">指针跟随倾转；光晕层（glare）建议关闭</p>
            </TiltCard>
          </div>
        </div>

        <div className="rounded-xl border border-border bg-card p-6 shadow-sm">
          <p className="text-xs font-medium text-muted-foreground">Switch / Checkbox · 开关与复选</p>
          <div className="mt-3 flex items-center gap-6">
            <Switch checked={demoSwitch} onCheckedChange={setDemoSwitch} label="演示开关" />
            <Checkbox checked={demoChecked} onCheckedChange={setDemoChecked} label="演示复选框" />
          </div>
        </div>

        <div className="rounded-xl border border-border bg-card p-6 shadow-sm">
          <p className="text-xs font-medium text-muted-foreground">Select · 动效下拉</p>
          <div className="mt-3 max-w-56">
            <Select value={demoSelect} onValueChange={setDemoSelect}>
              <SelectTrigger aria-label="演示下拉" className="h-9 rounded-lg">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="statistical">统计预测</SelectItem>
                <SelectItem value="mechanistic">机理仿真</SelectItem>
                <SelectItem value="state_machine">状态机推演</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </div>

        <div className="rounded-xl border border-border bg-card p-6 shadow-sm">
          <p className="text-xs font-medium text-muted-foreground">Tooltip + IconButton · 提示与图标按钮</p>
          <div className="mt-3 flex items-center gap-2">
            <Tooltip content="播放（动效提示替代原生 title）">
              <IconButton label="演示播放" reduce={false} onClick={() => undefined}>
                <Play className="h-4 w-4" />
              </IconButton>
            </Tooltip>
            <Tooltip content="按住有按压回弹">
              <IconButton label="演示调节" reduce={false} onClick={() => undefined}>
                <Sliders className="h-4 w-4" />
              </IconButton>
            </Tooltip>
          </div>
        </div>

        <div className="rounded-xl border border-border bg-card p-6 shadow-sm">
          <p className="text-xs font-medium text-muted-foreground">MorphPopover · 角展开气泡</p>
          <div className="mt-3">
            <MorphPopover>
              <Tooltip content="点击展开">
                <IconButton label="打开演示气泡" reduce={false} onClick={() => undefined} expanded>
                  <Sliders className="h-4 w-4" />
                </IconButton>
              </Tooltip>
              <MorphPopoverContent align="start" className="w-56 p-3">
                <p className="text-xs text-muted-foreground">从触发角展开的气泡面板，外点/Esc 关闭。</p>
              </MorphPopoverContent>
            </MorphPopover>
          </div>
        </div>

        <div className="rounded-xl border border-border bg-card p-6 shadow-sm sm:col-span-2">
          <p className="text-xs font-medium text-muted-foreground">MultiSelect · 多选筛选</p>
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
            ；输入即过滤，键盘 ↑↓ 移动、Enter 勾选、Backspace 逐个回退，chip 可单独移除。
          </p>
        </div>

        <div className="rounded-xl border border-border bg-card p-6 shadow-sm sm:col-span-2">
          <p className="text-xs font-medium text-muted-foreground">CenterMorphModal · 中心展开弹窗</p>
          <div className="mt-3">
            <button
              type="button"
              onClick={() => setDemoModalOpen(true)}
              className="inline-flex h-9 items-center gap-1.5 rounded-lg bg-brand px-4 text-sm font-medium text-white hover:bg-brand-deep"
            >
              打开演示弹窗
            </button>
          </div>
        </div>
      </section>

      {/* ── AvailabilityScheduler：完整组件 ── */}
      <section className="mt-8">
        <p className="mb-2 text-xs font-medium text-muted-foreground">
          AvailabilityScheduler · 每周可用时段编辑器（weekdayLabels / hourCycle / texts 可覆盖）
        </p>
        <div className="rounded-xl border border-border bg-card p-6 shadow-sm">
          <AvailabilityScheduler value={week} onChange={setWeek} />
        </div>
        <p className="mb-2 mt-4 text-xs font-medium text-muted-foreground">当前值（受控）</p>
        <pre
          data-testid="scheduler-value"
          className="max-h-72 overflow-auto rounded-xl border border-border bg-muted p-4 font-mono text-xs leading-relaxed text-foreground"
        >
          {JSON.stringify(week, null, 2)}
        </pre>
      </section>

      {/* ── 上游目录速查（数据源 motion-ui/catalog.ts） ── */}
      <section data-testid="beui-catalog" className="mt-8" aria-label="beUI 上游目录速查">
        <p className="mb-2 text-xs font-medium text-muted-foreground">
          上游目录速查 · 可按需引入（{available.length} 项，按 components/README.md 五步流程 vendor）
        </p>
        <div className="overflow-hidden rounded-xl border border-border bg-card shadow-sm">
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b border-border text-xs text-muted-foreground">
                <th className="px-4 py-2 font-medium">组件</th>
                <th className="px-4 py-2 font-medium">用途</th>
                <th className="px-4 py-2 font-medium">备注</th>
              </tr>
            </thead>
            <tbody>
              {available.map(entry => (
                <tr key={entry.name} className="border-b border-border transition-colors hover:bg-muted">
                  <td className="px-4 py-2 font-mono text-xs text-foreground">{entry.name}</td>
                  <td className="px-4 py-2 text-muted-foreground">{entry.desc}</td>
                  <td className="px-4 py-2 text-xs text-muted-foreground">{entry.note ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <p className="mb-2 mt-4 text-xs font-medium text-muted-foreground">
          已引入（{vendored.length} 项，源码即 API）
        </p>
        <p className="font-mono text-xs leading-6 text-muted-foreground">
          {vendored.map(entry => entry.name).join(' · ')}
        </p>

        <p className="mb-2 mt-4 text-xs font-medium text-muted-foreground">
          不适用 B 端（{unsuitable.length} 项，显式排除）
        </p>
        <p className="font-mono text-xs leading-6 text-muted-foreground/60">
          {unsuitable.map(entry => entry.name).join(' · ')}
        </p>
      </section>

      <CenterMorphModal open={demoModalOpen} onOpenChange={setDemoModalOpen}>
        <CenterMorphModalContent ariaLabel="CenterMorphModal 演示" className="max-w-sm">
          <div className="p-6 pr-12">
            <p className="text-sm font-semibold text-foreground">中心展开 morph 弹窗</p>
            <p className="mt-2 text-xs leading-5 text-muted-foreground">
              面板从画面中心一小片展折为完整弹窗，带背景模糊与焦点圈定；
              适合轻量告知与确认场景，重表单仍用平台 Modal。Esc 或点按背景关闭。
            </p>
          </div>
        </CenterMorphModalContent>
      </CenterMorphModal>
    </div>
  )
}
