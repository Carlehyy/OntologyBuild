/**
 * 业务画布元素详情弹窗
 *
 * 详情页的首要任务是帮助用户理解“这个模型如何运转”，而不是复述字段。
 * 七类模型各自使用不同的逻辑表达：
 *   · 对象：识别 → 描述 → 连接
 *   · 主体：身份 → 职责 → 参与
 *   · 行为：触发 → 执行 → 结果
 *   · 事件：来源 → 事实 → 影响
 *   · 规则：作用对象 → 判定 → 执行结果
 *   · 流程：目标 → 步骤/分支 → 产出度量
 *   · 场景：目标 → 主流程/分支 → 业务结果
 *
 * 结构缺口只做简短提示；具体澄清问题仍统一留在画布的“澄清账本”中处理。
 */
import { useEffect, useMemo, type ElementType, type ReactNode } from 'react'
import {
  ArrowLeft, ArrowRight, Box, Building2, Calendar, CircleAlert, CircleCheck, Coins,
  CornerDownRight, Flag, GitBranch, Hash, Key, List, ListChecks, Map as MapIcon,
  Package, Play, Route, Rows3, Scale, Server, Share2, ShieldCheck, Target,
  ToggleLeft, Type, User, UserCog, Users, X, Zap,
} from 'lucide-react'
import type { BusinessCanvas, CanvasElement } from '@/api/exploration'

type CanvasKey = 'objects' | 'actors' | 'behaviors' | 'events' | 'rules' | 'processes' | 'scenarios'

interface KindStyle {
  label: string
  Icon: ElementType
  accent: string
  soft: string
  text: string
  border: string
}

const KIND_STYLE: Record<CanvasKey, KindStyle> = {
  objects: {
    label: '对象模型', Icon: Box, accent: 'bg-sky-600', soft: 'bg-sky-50',
    text: 'text-sky-700', border: 'border-sky-200',
  },
  actors: {
    label: '主体模型', Icon: Users, accent: 'bg-violet-600', soft: 'bg-violet-50',
    text: 'text-violet-700', border: 'border-violet-200',
  },
  behaviors: {
    label: '行为模型', Icon: Play, accent: 'bg-teal-600', soft: 'bg-teal-50',
    text: 'text-teal-700', border: 'border-teal-200',
  },
  events: {
    label: '事件模型', Icon: Zap, accent: 'bg-amber-500', soft: 'bg-amber-50',
    text: 'text-amber-700', border: 'border-amber-200',
  },
  rules: {
    label: '规则模型', Icon: Scale, accent: 'bg-rose-600', soft: 'bg-rose-50',
    text: 'text-rose-700', border: 'border-rose-200',
  },
  processes: {
    label: '流程模型', Icon: GitBranch, accent: 'bg-indigo-600', soft: 'bg-indigo-50',
    text: 'text-indigo-700', border: 'border-indigo-200',
  },
  scenarios: {
    label: '场景模型', Icon: MapIcon, accent: 'bg-emerald-600', soft: 'bg-emerald-50',
    text: 'text-emerald-700', border: 'border-emerald-200',
  },
}

const KIND_GUIDE: Record<CanvasKey, { purpose: string; reading: string }> = {
  objects: {
    purpose: '定义业务中需要被持续识别、记录和关联的实体。',
    reading: '先看主键如何识别对象，再看属性如何描述对象，最后看它与哪些对象发生关系。',
  },
  actors: {
    purpose: '定义谁在业务中承担责任、作出决策或操作系统。',
    reading: '先确认主体身份，再确认职责边界；人员和组织还需要作为数据实体被识别。',
  },
  behaviors: {
    purpose: '定义业务变化是如何被触发和执行的。',
    reading: '沿着“触发条件 → 执行主体对对象采取动作 → 产生业务结果”阅读。',
  },
  events: {
    purpose: '定义一个值得被记录、响应或继续驱动流程的业务事实。',
    reading: '沿着“事件来源 → 记录的事实及载荷 → 后续影响”阅读。',
  },
  rules: {
    purpose: '定义业务约束、判断、审批、派生或告警的执行口径。',
    reading: '先看规则作用于谁，再看判定表达，最后看命中或不满足时系统做什么。',
  },
  processes: {
    purpose: '定义业务的标准骨架：有序步骤、条件分支、异常路径与产出度量。',
    reading: '先看目标与触发，再沿步骤顺序看每步由谁执行、绑定什么行为，最后看分支类型与度量口径。',
  },
  scenarios: {
    purpose: '挂接流程的情境变体：在特定上下文里走哪条路径、怎么决策。',
    reading: '从业务目标出发，顺着主流程和条件分支走到预期结果。',
  },
}

const ACTOR_KIND: Record<string, { label: string; Icon: ElementType }> = {
  person: { label: '人员', Icon: User },
  org: { label: '组织', Icon: Building2 },
  system: { label: '系统', Icon: Server },
  role: { label: '角色', Icon: UserCog },
}

const RULE_KIND: Record<string, { label: string; cls: string }> = {
  constraint: { label: '约束', cls: 'bg-slate-100 text-slate-700' },
  validation: { label: '校验', cls: 'bg-blue-50 text-blue-700' },
  derivation: { label: '派生', cls: 'bg-violet-50 text-violet-700' },
  approval: { label: '审批', cls: 'bg-amber-50 text-amber-700' },
  alert: { label: '告警', cls: 'bg-rose-50 text-rose-700' },
}

const CARDINALITY: Record<string, [string, string]> = {
  'one-to-one': ['一对一', '1:1'],
  'one-to-many': ['一对多', '1:N'],
  'many-to-one': ['多对一', 'N:1'],
  'many-to-many': ['多对多', 'N:N'],
}

interface AttrRow {
  name?: string
  display_name?: string
  type_hint?: string
  required?: boolean
  enum?: string[]
  notes?: string
}

interface RelRow {
  target?: string
  name?: string
  display_name?: string
  cardinality?: string
  description?: string
}

interface ScenarioBranch {
  from_step?: number
  to_step?: number | null
  condition?: string
}

interface ProcessStep {
  id?: string | null
  seq?: number
  name?: string
  actor?: string | null
  behavior?: string | null
  inputs?: string[]
  outputs?: string[]
  description?: string | null
}

interface ProcessBranch {
  from_step?: number
  to_step?: number | null
  condition?: string
  kind?: string
}

interface MetricRow {
  name?: string
  display_name?: string
  formula?: string | null
  source_objects?: string[]
  target?: string | null
  description?: string | null
}

interface Hit {
  key: CanvasKey
  el: CanvasElement
}

interface RefSpec {
  name: string
  preferred?: CanvasKey
}

interface Ctx {
  resolve: (name: string, preferred?: CanvasKey) => Hit | null
  onNavigate?: (key: CanvasKey, el: CanvasElement) => void
}

const asArr = <T,>(value: unknown): T[] => (Array.isArray(value) ? (value as T[]) : [])
const str = (value: unknown): string => (typeof value === 'string' ? value : '')
const norm = (value: unknown): string =>
  String(value ?? '').trim().toLowerCase().replace(/[\s_-]+/g, '')
const displayName = (el: CanvasElement): string => str(el.display_name) || el.name

function typeCategory(hint?: string): { Icon: ElementType; cls: string } {
  const value = (hint || '').toLowerCase()
  if (!value) return { Icon: Type, cls: 'bg-slate-100 text-slate-500' }
  if (/金额|价格|费用|money|price|amount|currency|decimal/.test(value)) {
    return { Icon: Coins, cls: 'bg-emerald-50 text-emerald-700' }
  }
  if (/日期|时间|date|time/.test(value)) {
    return { Icon: Calendar, cls: 'bg-amber-50 text-amber-700' }
  }
  if (/是否|布尔|bool|标志/.test(value)) {
    return { Icon: ToggleLeft, cls: 'bg-violet-50 text-violet-700' }
  }
  if (/枚举|enum|选项|类别/.test(value)) {
    return { Icon: List, cls: 'bg-rose-50 text-rose-700' }
  }
  if (/数字|数值|整数|数量|个数|number|int|float|count|qty/.test(value)) {
    return { Icon: Hash, cls: 'bg-blue-50 text-blue-700' }
  }
  return { Icon: Type, cls: 'bg-slate-100 text-slate-600' }
}

function EntityRef({ name, preferred, ctx }: { name?: string; preferred?: CanvasKey; ctx: Ctx }) {
  if (!name) return null
  const hit = ctx.resolve(name, preferred)
  if (!hit) {
    return (
      <span
        title="尚未在画布中定义"
        className="inline-flex items-center gap-1.5 rounded-md border border-dashed border-[var(--color-border-hover)] bg-white px-2 py-1 text-xs text-[var(--color-text-tertiary)]"
      >
        <CircleAlert size={12} />
        {name}
      </span>
    )
  }

  const style = KIND_STYLE[hit.key]
  const Icon = style.Icon
  const className = `inline-flex items-center gap-1.5 rounded-md border px-2 py-1 text-xs font-medium ${style.soft} ${style.text} ${style.border}`
  return ctx.onNavigate ? (
    <button
      type="button"
      onClick={() => ctx.onNavigate?.(hit.key, hit.el)}
      title="打开关联模型"
      className={`${className} transition hover:-translate-y-px hover:brightness-[0.98] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-500/30`}
    >
      <Icon size={12} />
      {displayName(hit.el)}
      <ArrowRight size={11} className="opacity-55" />
    </button>
  ) : (
    <span className={className}>
      <Icon size={12} />
      {displayName(hit.el)}
    </span>
  )
}

function Cardinality({ value }: { value?: string }) {
  if (!value) {
    return (
      <span className="rounded-md border border-dashed border-amber-300 bg-amber-50 px-1.5 py-0.5 text-[10px] text-amber-700">
        基数待补
      </span>
    )
  }
  const cardinality = CARDINALITY[value]
  return (
    <span
      title={cardinality ? cardinality[0] : value}
      className="rounded-md border border-[var(--color-border)] bg-white px-1.5 py-0.5 font-mono text-[10px] text-[var(--color-text-secondary)]"
    >
      {cardinality ? cardinality[1] : value}
    </span>
  )
}

function Section({ icon: Icon, title, count, children }: {
  icon: ElementType
  title: string
  count?: number
  children: ReactNode
}) {
  return (
    <section className="mt-6 border-t border-[var(--color-border)] pt-5 first:mt-0 first:border-t-0 first:pt-0">
      <div className="mb-3 flex items-center gap-2">
        <Icon size={14} className="text-[var(--color-text-tertiary)]" />
        <h4 className="text-[13px] font-semibold text-[var(--color-text-primary)]">{title}</h4>
        {count != null && (
          <span className="font-mono text-[10px] text-[var(--color-text-tertiary)]">{count}</span>
        )}
      </div>
      {children}
    </section>
  )
}

function Empty({ text }: { text: string }) {
  return (
    <div className="rounded-xl border border-dashed border-[var(--color-border)] bg-[#fafbfc] px-4 py-5 text-center text-xs leading-relaxed text-[var(--color-text-tertiary)] dark:bg-[#121820]">
      {text}
    </div>
  )
}

interface LogicItem {
  eyebrow: string
  value: ReactNode
  meta?: ReactNode
  Icon: ElementType
  emphasized?: boolean
  warning?: boolean
}

function LogicChain({ title, description, items }: {
  title: string
  description: string
  items: LogicItem[]
}) {
  return (
    <section className="rounded-2xl border border-slate-200 bg-[#f7f9fb] dark:bg-[#121820] p-4">
      <div className="mb-4">
        <div className="text-[11px] font-semibold tracking-[0.12em] text-teal-700">核心逻辑</div>
        <h4 className="mt-1 text-base font-semibold tracking-[-0.02em] text-[var(--color-text-primary)]">{title}</h4>
        <p className="mt-1 max-w-[66ch] text-xs leading-relaxed text-[var(--color-text-tertiary)]">{description}</p>
      </div>
      <div className="flex flex-col items-stretch gap-2 md:flex-row md:items-center">
        {items.map((item, index) => {
          const Icon = item.Icon
          return (
            <div key={index} className="contents">
              <div className={`min-w-0 flex-1 rounded-xl border px-3 py-3 ${item.warning
                ? 'border-amber-200 bg-amber-50/70'
                : item.emphasized
                  ? 'border-teal-200 bg-white shadow-[0_8px_24px_rgba(15,118,110,0.08)]'
                  : 'border-slate-200 bg-white'}`}>
                <div className="flex items-center gap-1.5 text-[10px] font-medium tracking-[0.08em] text-[var(--color-text-tertiary)]">
                  <Icon size={12} />
                  {item.eyebrow}
                </div>
                <div className="mt-2 min-h-5 text-sm font-semibold leading-relaxed text-[var(--color-text-primary)]">
                  {item.value}
                </div>
                {item.meta && (
                  <div className="mt-1 text-[11px] leading-relaxed text-[var(--color-text-tertiary)]">{item.meta}</div>
                )}
              </div>
              {index < items.length - 1 && (
                <ArrowRight
                  size={15}
                  className="mx-auto shrink-0 rotate-90 text-slate-300 md:mx-0 md:rotate-0"
                />
              )}
            </div>
          )
        })}
      </div>
    </section>
  )
}

function AttributeTable({ attributes, keyName }: { attributes: AttrRow[]; keyName?: string }) {
  if (!attributes.length) return <Empty text="还没有沉淀属性；模型详情暂时只保留已确认结构。" />
  return (
    <div className="overflow-hidden rounded-xl border border-[var(--color-border)]">
      <div className="hidden grid-cols-[minmax(130px,1fr)_90px_minmax(150px,1.25fr)] gap-3 bg-[#f7f9fb] dark:bg-[#121820] px-3 py-2 text-[10px] font-medium text-[var(--color-text-tertiary)] sm:grid">
        <span>字段</span>
        <span>类型</span>
        <span>业务约束</span>
      </div>
      <div className="divide-y divide-[var(--color-border)] bg-white">
        {attributes.map((attribute, index) => {
          const type = typeCategory(attribute.type_hint)
          const TypeIcon = type.Icon
          const isKey = !!keyName && norm(attribute.name) === norm(keyName)
          const alias = attribute.display_name && attribute.name && attribute.display_name !== attribute.name
          return (
            <div
              key={`${attribute.name || 'attribute'}-${index}`}
              className={`grid gap-2 px-3 py-3 sm:grid-cols-[minmax(130px,1fr)_90px_minmax(150px,1.25fr)] sm:items-start sm:gap-3 ${isKey ? 'bg-amber-50/35' : ''}`}
            >
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-1.5">
                  {isKey && <Key size={12} className="text-amber-600" />}
                  <span className="text-sm font-medium text-[var(--color-text-primary)]">
                    {attribute.display_name || attribute.name}
                  </span>
                  {attribute.required && (
                    <span className="rounded bg-rose-50 px-1 py-px text-[9px] font-medium text-rose-600">必填</span>
                  )}
                </div>
                {alias && (
                  <code className="mt-0.5 block truncate font-mono text-[10px] text-[var(--color-text-tertiary)]">
                    {attribute.name}
                  </code>
                )}
              </div>
              <div>
                <span className={`inline-flex items-center gap-1 rounded-md px-1.5 py-1 text-[10px] ${type.cls}`}>
                  <TypeIcon size={11} />
                  {attribute.type_hint || '未指定'}
                </span>
              </div>
              <div className="min-w-0 text-[11px] leading-relaxed text-[var(--color-text-secondary)]">
                {attribute.enum?.length ? (
                  <div className="flex flex-wrap gap-1">
                    {attribute.enum.map(value => (
                      <span key={value} className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-600">
                        {value}
                      </span>
                    ))}
                  </div>
                ) : attribute.notes || (isKey ? '唯一识别该业务实体' : '—')}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

function RelationMap({ source, relations, ctx }: { source: string; relations: RelRow[]; ctx: Ctx }) {
  if (!relations.length) return <Empty text="当前没有定义对象关系；这里只展示已经确认的连接。" />
  return (
    <div className="space-y-2">
      {relations.map((relation, index) => (
        <div
          key={`${relation.name || relation.target || 'relation'}-${index}`}
          className="rounded-xl border border-[var(--color-border)] bg-white px-3 py-3"
        >
          <div className="flex flex-wrap items-center gap-2">
            <span className="rounded-md bg-sky-50 px-2 py-1 text-xs font-medium text-sky-700">{source}</span>
            <span className="inline-flex items-center gap-1 text-[11px] text-[var(--color-text-tertiary)]">
              <ArrowRight size={13} />
              {relation.display_name || relation.name || '关联'}
            </span>
            <EntityRef name={relation.target} preferred="objects" ctx={ctx} />
            <Cardinality value={relation.cardinality} />
          </div>
          {relation.description && (
            <p className="mt-2 text-[11px] leading-relaxed text-[var(--color-text-tertiary)]">
              {relation.description}
            </p>
          )}
        </div>
      ))}
    </div>
  )
}

function CompactList({ items, icon: Icon = CircleCheck, tone = 'text-teal-600' }: {
  items: string[]
  icon?: ElementType
  tone?: string
}) {
  if (!items.length) return null
  return (
    <ul className="space-y-2">
      {items.map((item, index) => (
        <li key={`${item}-${index}`} className="flex gap-2.5 text-sm leading-relaxed text-[var(--color-text-primary)]">
          <Icon size={14} className={`mt-1 shrink-0 ${tone}`} />
          <span>{item}</span>
        </li>
      ))}
    </ul>
  )
}

function ScenarioTimeline({ steps, branches }: { steps: string[]; branches: ScenarioBranch[] }) {
  const branchesByStep = new Map<number, ScenarioBranch[]>()
  for (const branch of branches) {
    const from = Number(branch.from_step)
    if (!Number.isFinite(from) || from < 1) continue
    const list = branchesByStep.get(from) || []
    list.push(branch)
    branchesByStep.set(from, list)
  }

  return (
    <ol>
      {steps.map((step, index) => {
        const stepNumber = index + 1
        const stepBranches = branchesByStep.get(stepNumber) || []
        return (
          <li key={`${step}-${index}`} className="grid grid-cols-[28px_minmax(0,1fr)] gap-3">
            <div className="flex flex-col items-center">
              <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full border border-emerald-200 bg-emerald-50 font-mono text-[11px] font-semibold text-emerald-700">
                {stepNumber}
              </span>
              {index < steps.length - 1 && <span className="my-1 min-h-6 w-px flex-1 bg-emerald-200" />}
            </div>
            <div className="pb-4">
              <p className="pt-0.5 text-sm leading-relaxed text-[var(--color-text-primary)]">{step}</p>
              {stepBranches.length > 0 && (
                <div className="mt-2 space-y-1.5 border-l-2 border-amber-200 pl-3">
                  {stepBranches.map((branch, branchIndex) => (
                    <div
                      key={`${branch.condition || 'branch'}-${branchIndex}`}
                      className="flex flex-wrap items-center gap-1.5 text-[11px] leading-relaxed"
                    >
                      <GitBranch size={12} className="text-amber-600" />
                      <span className="font-medium text-amber-800">{branch.condition || '条件未命名'}</span>
                      <ArrowRight size={11} className="text-amber-400" />
                      <span className="text-[var(--color-text-secondary)]">
                        {branch.to_step == null ? '流程结束' : `进入第 ${branch.to_step} 步`}
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </li>
        )
      })}
    </ol>
  )
}

/** 流程结构化步骤：ProcessStep 是对象（seq/name/actor/behavior/inputs/outputs），
 *  与场景的字符串步骤不同，不能复用 ScenarioTimeline。 */
function ProcessSteps({ steps, ctx }: { steps: ProcessStep[]; ctx: Ctx }) {
  const ordered = [...steps].sort((a, b) => (a.seq ?? 0) - (b.seq ?? 0))
  return (
    <ol>
      {ordered.map((step, index) => {
        const stepNumber = index + 1
        const inputs = asArr<string>(step.inputs)
        const outputs = asArr<string>(step.outputs)
        return (
          <li key={step.id || `${step.name || 'step'}-${index}`} className="grid grid-cols-[28px_minmax(0,1fr)] gap-3">
            <div className="flex flex-col items-center">
              <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full border border-indigo-200 bg-indigo-50 font-mono text-[11px] font-semibold text-indigo-700">
                {step.seq ?? stepNumber}
              </span>
              {index < ordered.length - 1 && <span className="my-1 min-h-6 w-px flex-1 bg-indigo-200" />}
            </div>
            <div className="pb-4">
              <p className="pt-0.5 text-sm font-medium leading-relaxed text-[var(--color-text-primary)]">
                {step.name || `第 ${stepNumber} 步`}
              </p>
              <div className="mt-1.5 flex flex-wrap items-center gap-1.5 text-[11px] text-[var(--color-text-tertiary)]">
                <span>执行</span>
                {step.actor
                  ? <EntityRef name={step.actor} preferred="actors" ctx={ctx} />
                  : <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-500">线下步骤</span>}
                <span aria-hidden="true">·</span>
                <span>行为</span>
                {step.behavior
                  ? <EntityRef name={step.behavior} preferred="behaviors" ctx={ctx} />
                  : <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-500">未绑定</span>}
              </div>
              {(inputs.length > 0 || outputs.length > 0) && (
                <div className="mt-1.5 space-y-1 text-[11px] leading-relaxed text-[var(--color-text-tertiary)]">
                  {inputs.length > 0 && <div>输入：{inputs.join('、')}</div>}
                  {outputs.length > 0 && <div>输出：{outputs.join('、')}</div>}
                </div>
              )}
              {step.description && (
                <p className="mt-1.5 text-[11px] leading-relaxed text-[var(--color-text-tertiary)]">
                  {step.description}
                </p>
              )}
            </div>
          </li>
        )
      })}
    </ol>
  )
}

/** 流程分支表：from/to 步骤下标 + 条件 + 类型（正常/异常路径） */
function ProcessBranchTable({ branches }: { branches: ProcessBranch[] }) {
  return (
    <div className="overflow-hidden rounded-xl border border-[var(--color-border)]">
      <div className="hidden grid-cols-[minmax(150px,1.4fr)_90px_90px_70px] gap-3 bg-[#f7f9fb] dark:bg-[#121820] px-3 py-2 text-[10px] font-medium text-[var(--color-text-tertiary)] sm:grid">
        <span>条件</span>
        <span>从步骤</span>
        <span>到步骤</span>
        <span>类型</span>
      </div>
      <div className="divide-y divide-[var(--color-border)] bg-white">
        {branches.map((branch, index) => {
          const isException = str(branch.kind) === 'exception'
          return (
            <div
              key={`${branch.condition || 'branch'}-${index}`}
              className="grid gap-2 px-3 py-2.5 sm:grid-cols-[minmax(150px,1.4fr)_90px_90px_70px] sm:items-center sm:gap-3"
            >
              <span className="text-xs font-medium text-[var(--color-text-primary)]">
                {branch.condition || '条件未命名'}
              </span>
              <span className="font-mono text-[11px] text-[var(--color-text-secondary)]">
                第 {branch.from_step ?? '—'} 步
              </span>
              <span className="font-mono text-[11px] text-[var(--color-text-secondary)]">
                {branch.to_step == null ? '流程结束' : `第 ${branch.to_step} 步`}
              </span>
              <span>
                <span className={`inline-flex rounded px-1.5 py-0.5 text-[10px] font-medium ${isException
                  ? 'bg-rose-50 text-rose-700' : 'bg-teal-50 text-teal-700'}`}>
                  {isException ? '异常路径' : '正常路径'}
                </span>
              </span>
            </div>
          )
        })}
      </div>
    </div>
  )
}

/** 产出度量表：指标 + 定量口径 + 来源对象（可下钻）+ 目标值；流程与场景共用 */
function MetricTable({ metrics, ctx }: { metrics: MetricRow[]; ctx: Ctx }) {
  if (!metrics.length) return <Empty text="当前还没有定义产出度量。" />
  return (
    <div className="overflow-hidden rounded-xl border border-[var(--color-border)]">
      <div className="hidden grid-cols-[minmax(120px,1fr)_minmax(150px,1.4fr)_minmax(120px,1fr)_80px] gap-3 bg-[#f7f9fb] dark:bg-[#121820] px-3 py-2 text-[10px] font-medium text-[var(--color-text-tertiary)] sm:grid">
        <span>指标</span>
        <span>计算口径</span>
        <span>来源对象</span>
        <span>目标值</span>
      </div>
      <div className="divide-y divide-[var(--color-border)] bg-white">
        {metrics.map((metric, index) => {
          const sources = asArr<string>(metric.source_objects)
          const alias = metric.display_name && metric.name && metric.display_name !== metric.name
          return (
            <div
              key={`${metric.name || 'metric'}-${index}`}
              className="grid gap-2 px-3 py-3 sm:grid-cols-[minmax(120px,1fr)_minmax(150px,1.4fr)_minmax(120px,1fr)_80px] sm:items-start sm:gap-3"
            >
              <div className="min-w-0">
                <span className="text-sm font-medium text-[var(--color-text-primary)]">
                  {metric.display_name || metric.name}
                </span>
                {alias && (
                  <code className="mt-0.5 block truncate font-mono text-[10px] text-[var(--color-text-tertiary)]">
                    {metric.name}
                  </code>
                )}
              </div>
              <div className="min-w-0 text-[11px] leading-relaxed text-[var(--color-text-secondary)]">
                {metric.formula || '口径未定义'}
              </div>
              <div className="flex flex-wrap gap-1">
                {sources.length
                  ? sources.map(name => <EntityRef key={name} name={name} preferred="objects" ctx={ctx} />)
                  : <span className="text-[11px] text-[var(--color-text-tertiary)]">—</span>}
              </div>
              <div className="text-[11px] text-[var(--color-text-secondary)]">{metric.target || '—'}</div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

function ruleOutcome(kind: string, errorMessage: string): string {
  if (errorMessage) return errorMessage
  if (kind === 'approval') return '满足条件时进入审批流程'
  if (kind === 'alert') return '满足条件时触发告警'
  if (kind === 'derivation') return '根据规则计算派生结果'
  if (kind === 'validation') return '不满足时阻止提交或状态变更'
  return '约束相关业务操作'
}

function ElementBody({ sectionKey, el, ctx }: { sectionKey: CanvasKey; el: CanvasElement; ctx: Ctx }) {
  if (sectionKey === 'objects') {
    const attributes = asArr<AttrRow>(el.attributes)
    const relations = asArr<RelRow>(el.relations)
    const keyName = str(el.key_attribute)
    const keyAttribute = attributes.find(attribute => norm(attribute.name) === norm(keyName))
    return (
      <>
        <LogicChain
          title="对象如何进入业务网络"
          description="对象模型不是字段表。它先解决“如何唯一识别”，再描述业务状态，最后通过关系连接其它对象。"
          items={[
            {
              eyebrow: '唯一识别',
              Icon: Key,
              value: keyName ? (keyAttribute?.display_name || keyName) : '尚未指定主键',
              meta: keyName ? <code className="font-mono">{keyName}</code> : '缺少稳定的业务身份',
              warning: !keyName,
            },
            {
              eyebrow: '业务对象',
              Icon: Box,
              value: displayName(el),
              meta: `${attributes.length} 个属性描述它`,
              emphasized: true,
            },
            {
              eyebrow: '业务连接',
              Icon: Share2,
              value: relations.length ? `${relations.length} 条对象关系` : '当前无关系',
              meta: relations.length ? relations.map(relation => relation.display_name || relation.name || relation.target).slice(0, 3).join('、') : '暂未连接其它业务对象',
            },
          ]}
        />
        <Section icon={Rows3} title="属性结构" count={attributes.length}>
          <AttributeTable attributes={attributes} keyName={keyName} />
        </Section>
        <Section icon={Share2} title="关系网络" count={relations.length}>
          <RelationMap source={displayName(el)} relations={relations} ctx={ctx} />
        </Section>
      </>
    )
  }

  if (sectionKey === 'actors') {
    const kind = ACTOR_KIND[str(el.kind)] || { label: str(el.kind) || '角色', Icon: UserCog }
    const responsibilities = asArr<string>(el.responsibilities)
    const attributes = asArr<AttrRow>(el.attributes)
    const keyName = str(el.key_attribute)
    const isDataActor = ['person', 'org'].includes(str(el.kind))
    return (
      <>
        <LogicChain
          title="主体如何参与业务"
          description="主体模型先界定参与方身份，再明确它负责什么；人员和组织还需要有稳定的数据身份。"
          items={[
            {
              eyebrow: '主体身份',
              Icon: kind.Icon,
              value: kind.label,
              meta: isDataActor ? '同时作为数据实体管理' : '承担业务执行或决策职责',
            },
            {
              eyebrow: '参与方',
              Icon: Users,
              value: displayName(el),
              meta: keyName ? `由 ${keyName} 识别` : (isDataActor ? '尚未指定业务主键' : '无需实体主键'),
              emphasized: true,
              warning: isDataActor && !keyName,
            },
            {
              eyebrow: '责任边界',
              Icon: ListChecks,
              value: responsibilities.length ? `${responsibilities.length} 项职责` : '职责尚未定义',
              meta: responsibilities.slice(0, 2).join('、') || '暂未说明该主体负责什么',
              warning: responsibilities.length === 0,
            },
          ]}
        />
        {isDataActor && (
          <Section icon={Rows3} title="主体档案" count={attributes.length}>
            <AttributeTable attributes={attributes} keyName={keyName} />
          </Section>
        )}
        <Section icon={ListChecks} title="职责边界" count={responsibilities.length}>
          {responsibilities.length
            ? <CompactList items={responsibilities} icon={CircleCheck} tone="text-violet-500" />
            : <Empty text="当前没有沉淀职责；详情页不会用问题清单填充这一部分。" />}
        </Section>
      </>
    )
  }

  if (sectionKey === 'behaviors') {
    const actor = str(el.actor)
    const object = str(el.object)
    const trigger = str(el.trigger)
    const outcome = str(el.outcome)
    const inputs = asArr<AttrRow>(el.inputs)
    const constraints = asArr<string>(el.constraints)
    return (
      <>
        <LogicChain
          title="一次业务动作如何发生"
          description="行为模型应形成完整的因果链：什么条件触发、谁对什么执行动作、业务因此发生什么变化。"
          items={[
            {
              eyebrow: '触发条件',
              Icon: Zap,
              value: trigger || '触发条件未定义',
              meta: trigger ? '满足条件后进入执行' : '因果链缺少起点',
              warning: !trigger,
            },
            {
              eyebrow: '执行动作',
              Icon: Play,
              value: displayName(el),
              meta: (
                <span className="flex flex-wrap items-center gap-1">
                  <EntityRef name={actor} preferred="actors" ctx={ctx} />
                  <span>作用于</span>
                  <EntityRef name={object} preferred="objects" ctx={ctx} />
                </span>
              ),
              emphasized: true,
              warning: !actor || !object,
            },
            {
              eyebrow: '业务结果',
              Icon: Flag,
              value: outcome || '结果尚未定义',
              meta: outcome ? '应体现状态或数据如何变化' : '因果链缺少落点',
              warning: !outcome,
            },
          ]}
        />
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <span className={`inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1.5 text-xs font-medium ${el.needs_approval
            ? 'border-amber-200 bg-amber-50 text-amber-700'
            : 'border-slate-200 bg-slate-50 text-slate-600'}`}>
            <ShieldCheck size={13} />
            {el.needs_approval ? '执行前需要审批' : '无需审批，可直接执行'}
          </span>
          {inputs.length > 0 && (
            <span className="text-xs text-[var(--color-text-tertiary)]">{inputs.length} 项输入参数</span>
          )}
        </div>
        {inputs.length > 0 && (
          <Section icon={Rows3} title="执行输入" count={inputs.length}>
            <AttributeTable attributes={inputs} />
          </Section>
        )}
        <Section icon={ShieldCheck} title="执行约束" count={constraints.length}>
          {constraints.length
            ? <CompactList items={constraints} icon={ShieldCheck} tone="text-rose-500" />
            : <Empty text="当前没有额外执行约束。" />}
        </Section>
      </>
    )
  }

  if (sectionKey === 'events') {
    const source = str(el.source)
    const payload = asArr<string>(el.payload)
    const consequences = asArr<string>(el.consequences)
    return (
      <>
        <LogicChain
          title="一个业务事实如何继续驱动系统"
          description="事件本身不是动作。它记录动作、外部系统或时间产生的事实，并把必要信息传给后续响应。"
          items={[
            {
              eyebrow: '事件来源',
              Icon: CornerDownRight,
              value: source ? <EntityRef name={source} preferred="behaviors" ctx={ctx} /> : '来源尚未定义',
              meta: source || '需要明确来自行为、外部系统或时间',
              warning: !source,
            },
            {
              eyebrow: '记录事实',
              Icon: Zap,
              value: displayName(el),
              meta: payload.length ? `携带 ${payload.length} 项业务数据` : '当前没有事件载荷',
              emphasized: true,
            },
            {
              eyebrow: '后续影响',
              Icon: GitBranch,
              value: consequences.length ? `${consequences.length} 项响应` : '后续影响未定义',
              meta: consequences.slice(0, 2).join('、') || '事件发生后暂时没有明确动作',
              warning: consequences.length === 0,
            },
          ]}
        />
        <Section icon={Package} title="事件携带的信息" count={payload.length}>
          {payload.length ? (
            <div className="flex flex-wrap gap-2">
              {payload.map(item => (
                <span key={item} className="rounded-md border border-[var(--color-border)] bg-white px-2.5 py-1.5 text-xs text-[var(--color-text-secondary)]">
                  {item}
                </span>
              ))}
            </div>
          ) : <Empty text="当前没有定义事件载荷。" />}
        </Section>
        <Section icon={GitBranch} title="事件后的响应" count={consequences.length}>
          {consequences.length
            ? <CompactList items={consequences} icon={CornerDownRight} tone="text-amber-600" />
            : <Empty text="当前没有定义事件发生后的业务响应。" />}
        </Section>
      </>
    )
  }

  if (sectionKey === 'rules') {
    const kindValue = str(el.kind)
    const kind = RULE_KIND[kindValue] || { label: kindValue || '约束', cls: 'bg-slate-100 text-slate-700' }
    const target = str(el.applies_to)
    const statement = str(el.statement)
    const errorMessage = str(el.error_message)
    return (
      <>
        <LogicChain
          title="规则如何约束业务"
          description="规则必须有明确的作用对象、可执行的判定表达，以及命中或不满足时的业务结果。"
          items={[
            {
              eyebrow: '作用对象',
              Icon: Target,
              value: target ? <EntityRef name={target} ctx={ctx} /> : '作用对象未定义',
              meta: target ? '规则只在该模型语境下生效' : '无法判断规则约束谁',
              warning: !target,
            },
            {
              eyebrow: `${kind.label}判定`,
              Icon: Scale,
              value: statement || '判定表达尚未定义',
              meta: (
                <span className={`inline-flex rounded px-1.5 py-0.5 text-[10px] font-medium ${kind.cls}`}>
                  {kind.label}规则
                </span>
              ),
              emphasized: true,
              warning: !statement,
            },
            {
              eyebrow: '执行结果',
              Icon: Flag,
              value: ruleOutcome(kindValue, errorMessage),
              meta: errorMessage ? '面向业务用户的反馈' : '由规则类型决定系统行为',
            },
          ]}
        />
        <Section icon={Scale} title="规则原文">
          {statement ? (
            <blockquote className="border-l-2 border-rose-300 bg-rose-50/40 px-4 py-3 text-sm leading-7 text-[var(--color-text-primary)]">
              {statement}
            </blockquote>
          ) : <Empty text="当前还没有形成可执行的规则表达。" />}
        </Section>
        {errorMessage && (
          <Section icon={CircleAlert} title="不满足规则时">
            <div className="rounded-xl border border-rose-200 bg-rose-50 px-3.5 py-3 text-sm leading-relaxed text-rose-800">
              {errorMessage}
            </div>
          </Section>
        )}
      </>
    )
  }

  if (sectionKey === 'processes') {
    const steps = asArr<ProcessStep>(el.steps)
    const branches = asArr<ProcessBranch>(el.branches)
    const metrics = asArr<MetricRow>(el.metrics)
    const goal = str(el.goal)
    const trigger = str(el.trigger)
    const expectedOutcome = str(el.expected_outcome)
    const exceptionCount = branches.filter(branch => str(branch.kind) === 'exception').length
    return (
      <>
        <LogicChain
          title="业务流程如何标准运转"
          description="流程模型是标准骨架：目标与触发界定边界，步骤与分支定义路径，度量定义产出口径；场景作为情境变体挂接在它上面。"
          items={[
            {
              eyebrow: '业务目标',
              Icon: Target,
              value: goal || '目标尚未定义',
              meta: trigger ? `触发：${trigger}` : '触发条件未定义',
              warning: !goal,
            },
            {
              eyebrow: '步骤与分支',
              Icon: GitBranch,
              value: steps.length ? `${steps.length} 个步骤` : '步骤尚未定义',
              meta: branches.length
                ? `${branches.length} 条分支${exceptionCount ? `（含 ${exceptionCount} 条异常路径）` : ''}`
                : '当前为线性主路径',
              emphasized: true,
              warning: steps.length === 0,
            },
            {
              eyebrow: '预期结果',
              Icon: Flag,
              value: expectedOutcome || '预期结果尚未定义',
              meta: metrics.length ? `${metrics.length} 项产出度量` : '产出度量尚未定义',
              warning: !expectedOutcome,
            },
          ]}
        />
        <Section icon={Route} title="流程步骤" count={steps.length}>
          {steps.length
            ? <ProcessSteps steps={steps} ctx={ctx} />
            : <Empty text="当前还没有形成流程步骤。" />}
        </Section>
        {branches.length > 0 && (
          <Section icon={GitBranch} title="条件分支" count={branches.length}>
            <ProcessBranchTable branches={branches} />
          </Section>
        )}
        <Section icon={ListChecks} title="产出度量" count={metrics.length}>
          <MetricTable metrics={metrics} ctx={ctx} />
        </Section>
        {expectedOutcome && (
          <Section icon={Flag} title="预期结果">
            <div className="rounded-xl border border-indigo-200 bg-indigo-50 px-3.5 py-3 text-sm font-medium leading-relaxed text-indigo-800">
              {expectedOutcome}
            </div>
          </Section>
        )}
      </>
    )
  }

  const actors = asArr<string>(el.actors)
  const objects = asArr<string>(el.objects)
  const behaviors = asArr<string>(el.behaviors)
  const steps = asArr<string>(el.steps)
  const branches = asArr<ScenarioBranch>(el.branches)
  const goal = str(el.goal)
  const expectedOutcome = str(el.expected_outcome)
  const processRef = str(el.process_ref)
  const metrics = asArr<MetricRow>(el.metrics)
  return (
    <>
      <LogicChain
        title="场景是否形成端到端闭环"
        description="场景把参与方、业务对象和行为串成可验收流程；条件分支直接挂在对应步骤下，不再藏在文字列表里。"
        items={[
          {
            eyebrow: '业务目标',
            Icon: Target,
            value: goal || '目标尚未定义',
            meta: '说明为什么要执行这条流程',
            warning: !goal,
          },
          {
            eyebrow: '端到端流程',
            Icon: Route,
            value: steps.length ? `${steps.length} 个主流程步骤` : '流程步骤尚未定义',
            meta: branches.length ? `包含 ${branches.length} 条条件分支` : '当前为线性流程',
            emphasized: true,
            warning: steps.length === 0,
          },
          {
            eyebrow: '预期结果',
            Icon: Flag,
            value: expectedOutcome || '预期结果尚未定义',
            meta: '用于验证流程是否真正闭环',
            warning: !expectedOutcome,
          },
        ]}
      />
      {processRef && (
        <Section icon={GitBranch} title="所属流程">
          <EntityRef name={processRef} preferred="processes" ctx={ctx} />
        </Section>
      )}
      <Section icon={Users} title="场景参与要素">
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            <span className="w-10 shrink-0 text-[11px] text-[var(--color-text-tertiary)]">主体</span>
            {actors.length
              ? actors.map(name => <EntityRef key={name} name={name} preferred="actors" ctx={ctx} />)
              : <span className="text-xs text-[var(--color-text-tertiary)]">未定义</span>}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <span className="w-10 shrink-0 text-[11px] text-[var(--color-text-tertiary)]">对象</span>
            {objects.length
              ? objects.map(name => <EntityRef key={name} name={name} preferred="objects" ctx={ctx} />)
              : <span className="text-xs text-[var(--color-text-tertiary)]">未定义</span>}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <span className="w-10 shrink-0 text-[11px] text-[var(--color-text-tertiary)]">行为</span>
            {behaviors.length
              ? behaviors.map(name => <EntityRef key={name} name={name} preferred="behaviors" ctx={ctx} />)
              : <span className="text-xs text-[var(--color-text-tertiary)]">未定义</span>}
          </div>
        </div>
      </Section>
      <Section icon={Route} title="主流程与分支" count={steps.length}>
        {steps.length
          ? <ScenarioTimeline steps={steps} branches={branches} />
          : <Empty text={processRef
              ? '该场景挂接所属流程的主路径，未定义变体步骤。'
              : '当前还没有形成可顺序验收的场景步骤。'} />}
      </Section>
      {metrics.length > 0 && (
        <Section icon={ListChecks} title="产出度量" count={metrics.length}>
          <MetricTable metrics={metrics} ctx={ctx} />
        </Section>
      )}
      {expectedOutcome && (
        <Section icon={Flag} title="闭环结果">
          <div className="rounded-xl border border-emerald-200 bg-emerald-50 px-3.5 py-3 text-sm font-medium leading-relaxed text-emerald-800">
            {expectedOutcome}
          </div>
        </Section>
      )}
    </>
  )
}

function structureGaps(sectionKey: CanvasKey, el: CanvasElement): string[] {
  const gaps: string[] = []
  if (sectionKey === 'objects') {
    const attributes = asArr<AttrRow>(el.attributes)
    const relations = asArr<RelRow>(el.relations)
    if (!str(el.key_attribute)) gaps.push('缺少业务主键')
    if (!attributes.length) gaps.push('缺少对象属性')
    if (relations.some(relation => !relation.cardinality)) gaps.push('部分关系缺少基数')
  } else if (sectionKey === 'actors') {
    const isDataActor = ['person', 'org'].includes(str(el.kind))
    if (!asArr<string>(el.responsibilities).length) gaps.push('职责边界尚未形成')
    if (isDataActor && !asArr<AttrRow>(el.attributes).length) gaps.push('缺少主体档案属性')
    if (isDataActor && !str(el.key_attribute)) gaps.push('缺少主体业务主键')
  } else if (sectionKey === 'behaviors') {
    if (!str(el.trigger)) gaps.push('缺少触发条件')
    if (!str(el.actor)) gaps.push('缺少执行主体')
    if (!str(el.object)) gaps.push('缺少作用对象')
    if (!str(el.outcome)) gaps.push('缺少业务结果')
  } else if (sectionKey === 'events') {
    if (!str(el.source)) gaps.push('缺少事件来源')
    if (!asArr<string>(el.consequences).length) gaps.push('缺少后续影响')
  } else if (sectionKey === 'rules') {
    if (!str(el.applies_to)) gaps.push('缺少作用对象')
    if (!str(el.statement)) gaps.push('缺少可执行判定')
  } else if (sectionKey === 'processes') {
    if (!str(el.goal)) gaps.push('缺少业务目标')
    if (!asArr<ProcessStep>(el.steps).length) gaps.push('缺少流程步骤')
    if (!str(el.expected_outcome)) gaps.push('缺少预期结果')
  } else {
    if (!str(el.goal)) gaps.push('缺少业务目标')
    if (!asArr<string>(el.steps).length) gaps.push('缺少流程步骤')
    if (!str(el.expected_outcome)) gaps.push('缺少预期结果')
  }
  return gaps
}

function relatedRefs(sectionKey: CanvasKey, el: CanvasElement, canvas: BusinessCanvas | null): RefSpec[] {
  const refs: RefSpec[] = []
  const push = (name: unknown, preferred?: CanvasKey) => {
    const value = str(name)
    if (!value || norm(value) === norm(el.name) || norm(value) === norm(el.display_name)) return
    if (refs.some(ref => norm(ref.name) === norm(value) && ref.preferred === preferred)) return
    refs.push({ name: value, preferred })
  }

  if (sectionKey === 'objects') {
    asArr<RelRow>(el.relations).forEach(relation => push(relation.target, 'objects'))
    canvas?.behaviors
      .filter(behavior => norm(behavior.object) === norm(el.name) || norm(behavior.object) === norm(el.display_name))
      .forEach(behavior => push(behavior.name, 'behaviors'))
    canvas?.rules
      .filter(rule => norm(rule.applies_to) === norm(el.name) || norm(rule.applies_to) === norm(el.display_name))
      .forEach(rule => push(rule.name, 'rules'))
  } else if (sectionKey === 'actors') {
    canvas?.behaviors
      .filter(behavior => norm(behavior.actor) === norm(el.name) || norm(behavior.actor) === norm(el.display_name))
      .forEach(behavior => push(behavior.name, 'behaviors'))
  } else if (sectionKey === 'behaviors') {
    push(el.actor, 'actors')
    push(el.object, 'objects')
    canvas?.events
      .filter(event => norm(event.source) === norm(el.name) || norm(event.source) === norm(el.display_name))
      .forEach(event => push(event.name, 'events'))
    canvas?.rules
      .filter(rule => norm(rule.applies_to) === norm(el.name) || norm(rule.applies_to) === norm(el.display_name))
      .forEach(rule => push(rule.name, 'rules'))
  } else if (sectionKey === 'events') {
    push(el.source, 'behaviors')
  } else if (sectionKey === 'rules') {
    push(el.applies_to)
  } else if (sectionKey === 'processes') {
    asArr<string>(el.objects).forEach(name => push(name, 'objects'))
    asArr<ProcessStep>(el.steps).forEach(step => {
      push(step.actor, 'actors')
      push(step.behavior, 'behaviors')
    })
    asArr<MetricRow>(el.metrics)
      .forEach(metric => asArr<string>(metric.source_objects).forEach(name => push(name, 'objects')))
  } else {
    push(el.process_ref, 'processes')
    asArr<string>(el.actors).forEach(name => push(name, 'actors'))
    asArr<string>(el.objects).forEach(name => push(name, 'objects'))
    asArr<string>(el.behaviors).forEach(name => push(name, 'behaviors'))
  }

  return refs.slice(0, 8)
}

function HeaderMeta({ sectionKey, el }: { sectionKey: CanvasKey; el: CanvasElement }) {
  const items: string[] = []
  if (sectionKey === 'objects') {
    items.push(`${asArr(el.attributes).length} 属性`, `${asArr(el.relations).length} 关系`)
    if (str(el.key_attribute)) items.push(`主键 ${str(el.key_attribute)}`)
  } else if (sectionKey === 'actors') {
    items.push(ACTOR_KIND[str(el.kind)]?.label || str(el.kind) || '角色')
    items.push(`${asArr(el.responsibilities).length} 职责`)
  } else if (sectionKey === 'behaviors') {
    if (str(el.actor)) items.push(str(el.actor))
    if (str(el.object)) items.push(`作用于 ${str(el.object)}`)
    items.push(el.needs_approval ? '需要审批' : '无需审批')
  } else if (sectionKey === 'events') {
    if (str(el.source)) items.push(`来源 ${str(el.source)}`)
    items.push(`${asArr(el.consequences).length} 后续影响`)
  } else if (sectionKey === 'rules') {
    items.push(`${RULE_KIND[str(el.kind)]?.label || '约束'}规则`)
    if (str(el.applies_to)) items.push(`作用于 ${str(el.applies_to)}`)
  } else if (sectionKey === 'processes') {
    items.push(`${asArr(el.steps).length} 步`, `${asArr(el.branches).length} 分支`)
    const metrics = asArr(el.metrics).length
    if (metrics) items.push(`${metrics} 指标`)
  } else {
    items.push(`${asArr(el.steps).length} 步`, `${asArr(el.branches).length} 分支`)
  }
  return (
    <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-[var(--color-text-tertiary)]">
      {items.map(item => (
        <span key={item} className="inline-flex items-center gap-1.5">
          <span className="h-1 w-1 rounded-full bg-slate-300" />
          {item}
        </span>
      ))}
    </div>
  )
}

function ModelAside({ sectionKey, el, canvas, ctx }: {
  sectionKey: CanvasKey
  el: CanvasElement
  canvas: BusinessCanvas | null
  ctx: Ctx
}) {
  const guide = KIND_GUIDE[sectionKey]
  const gaps = structureGaps(sectionKey, el)
  const refs = relatedRefs(sectionKey, el, canvas)
  return (
    <aside className="space-y-3">
      <section className="rounded-xl border border-[var(--color-border)] bg-white px-4 py-4">
        <div className="text-[10px] font-semibold tracking-[0.12em] text-[var(--color-text-tertiary)]">模型定位</div>
        <p className="mt-2 text-sm font-medium leading-relaxed text-[var(--color-text-primary)]">{guide.purpose}</p>
        <p className="mt-2 text-[11px] leading-5 text-[var(--color-text-tertiary)]">{guide.reading}</p>
      </section>

      <section className="rounded-xl border border-[var(--color-border)] bg-white px-4 py-4">
        <div className="flex items-center gap-2">
          {gaps.length ? (
            <CircleAlert size={14} className="text-amber-600" />
          ) : (
            <CircleCheck size={14} className="text-teal-600" />
          )}
          <h4 className="text-xs font-semibold text-[var(--color-text-primary)]">结构状态</h4>
        </div>
        {gaps.length ? (
          <>
            <p className="mt-2 text-[11px] leading-5 text-[var(--color-text-tertiary)]">
              当前模型还有 {gaps.length} 个结构缺口，具体口径在澄清账本统一处理。
            </p>
            <div className="mt-2 flex flex-wrap gap-1.5">
              {gaps.slice(0, 3).map(gap => (
                <span key={gap} className="rounded-md bg-amber-50 px-2 py-1 text-[10px] text-amber-700">
                  {gap}
                </span>
              ))}
            </div>
          </>
        ) : (
          <p className="mt-2 text-[11px] leading-5 text-teal-700">关键结构已经形成，可以继续检查关联模型。</p>
        )}
      </section>

      <section className="rounded-xl border border-[var(--color-border)] bg-white px-4 py-4">
        <div className="flex items-center gap-2">
          <Share2 size={14} className="text-[var(--color-text-tertiary)]" />
          <h4 className="text-xs font-semibold text-[var(--color-text-primary)]">关联模型</h4>
        </div>
        {refs.length ? (
          <div className="mt-3 flex flex-wrap gap-2">
            {refs.map(ref => (
              <EntityRef key={`${ref.preferred || 'any'}-${ref.name}`} name={ref.name} preferred={ref.preferred} ctx={ctx} />
            ))}
          </div>
        ) : (
          <p className="mt-2 text-[11px] leading-5 text-[var(--color-text-tertiary)]">当前没有可下钻的直接关联。</p>
        )}
      </section>
    </aside>
  )
}

export default function ElementDetailModal({ sectionKey, el, canvas, onClose, onNavigate, onBack }: {
  sectionKey: CanvasKey
  el: CanvasElement
  canvas: BusinessCanvas | null
  onClose: () => void
  onNavigate?: (key: CanvasKey, el: CanvasElement) => void
  onBack?: () => void
}) {
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [onClose])

  const index = useMemo(() => {
    const result = new Map<string, Hit[]>()
    const keys: CanvasKey[] = ['objects', 'actors', 'behaviors', 'events', 'rules', 'processes', 'scenarios']
    if (!canvas) return result
    for (const key of keys) {
      const list = canvas[key]
      if (!Array.isArray(list)) continue
      for (const item of list) {
        for (const name of [item.name, str(item.display_name)]) {
          const normalized = norm(name)
          if (!normalized) continue
          const hits = result.get(normalized) || []
          hits.push({ key, el: item })
          result.set(normalized, hits)
        }
      }
    }
    return result
  }, [canvas])

  const ctx: Ctx = {
    resolve: (name, preferred) => {
      const hits = index.get(norm(name)) || []
      if (!hits.length) return null
      return preferred ? (hits.find(hit => hit.key === preferred) || hits[0]) : hits[0]
    },
    onNavigate,
  }

  const style = KIND_STYLE[sectionKey]
  const Icon = style.Icon
  const description = str(el.description)
  const titleId = `element-detail-${el.id || norm(el.name)}`

  return (
    <div
      className="modal-overlay fixed inset-0 z-[300] flex items-center justify-center bg-slate-950/45 p-3 backdrop-blur-[2px] sm:p-6"
      onMouseDown={event => {
        if (event.target === event.currentTarget) onClose()
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="modal-content flex max-h-[90vh] w-[980px] max-w-[96vw] flex-col overflow-hidden rounded-[22px] border border-white/80 bg-white shadow-[0_28px_90px_rgba(15,23,42,0.24)]"
      >
        <header className="relative shrink-0 border-b border-[var(--color-border)] bg-white px-5 py-4 sm:px-6">
          <div className={`absolute inset-y-0 left-0 w-1 ${style.accent}`} />
          <div className="flex items-start gap-3.5">
            {onBack && (
              <button
                type="button"
                onClick={onBack}
                aria-label="返回上一个模型"
                title="返回上一个模型"
                className="mt-1 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-[var(--color-border)] text-[var(--color-text-secondary)] transition hover:bg-[var(--color-bg-hover)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-500/30"
              >
                <ArrowLeft size={15} />
              </button>
            )}
            <span className={`mt-0.5 flex h-10 w-10 shrink-0 items-center justify-center rounded-xl text-white ${style.accent}`}>
              <Icon size={18} />
            </span>
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <h3
                  id={titleId}
                  className="text-xl font-semibold tracking-[-0.025em] text-[var(--color-text-primary)] sm:text-[22px]"
                >
                  {displayName(el)}
                </h3>
                <span className={`rounded-md border px-2 py-0.5 text-[10px] font-semibold ${style.soft} ${style.text} ${style.border}`}>
                  {style.label}
                </span>
              </div>
              <div className="mt-0.5 flex flex-wrap items-center gap-2">
                {str(el.display_name) && str(el.display_name) !== el.name && (
                  <code className="font-mono text-[11px] text-[var(--color-text-tertiary)]">{el.name}</code>
                )}
              </div>
              <HeaderMeta sectionKey={sectionKey} el={el} />
            </div>
            <button
              type="button"
              onClick={onClose}
              aria-label="关闭模型详情"
              autoFocus
              className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-[var(--color-text-tertiary)] transition hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text-primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-500/30"
            >
              <X size={17} />
            </button>
          </div>
        </header>

        <div className="flex-1 overflow-y-auto bg-[#f5f7f9] p-4 sm:p-5 dark:bg-[#121820]">
          <div className="grid items-start gap-4 lg:grid-cols-[minmax(0,1fr)_250px]">
            <main className="min-w-0 rounded-2xl border border-[var(--color-border)] bg-white p-4 sm:p-5">
              {description && (
                <section className="mb-5 border-b border-[var(--color-border)] pb-5">
                  <div className="text-[10px] font-semibold tracking-[0.12em] text-[var(--color-text-tertiary)]">业务定义</div>
                  <p className="mt-2 max-w-[68ch] text-sm leading-7 text-[var(--color-text-secondary)]">{description}</p>
                </section>
              )}
              <ElementBody sectionKey={sectionKey} el={el} ctx={ctx} />
            </main>
            <ModelAside sectionKey={sectionKey} el={el} canvas={canvas} ctx={ctx} />
          </div>
        </div>
      </div>
    </div>
  )
}
