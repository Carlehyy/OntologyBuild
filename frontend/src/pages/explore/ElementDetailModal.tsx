/**
 * 业务画布元素详情弹窗
 *
 * 六类模型字段各异，且元素之间互相引用（行为的执行主体/作用对象、对象的关系目标、
 * 规则作用对象、场景涉及要素都是画布里的其它元素）。这个弹窗把这些引用渲染为
 * 「按被引用元素类别着色、可点击下钻」的实体标签，让详情成为业务模型的连通视图，
 * 而不是一张扁平的字段清单：
 *   · 头部按类别着色，带一眼可见的统计条（属性/关系/主键、审批、来源…）
 *   · 属性按类型（文本/数字/金额/日期/布尔/枚举）着色分类，主键高亮
 *   · 关系用 名称 → 目标[基数] 的方式表达，目标是可下钻的实体标签
 *   · 行为用 主体 → 行为 → 对象 的流程头表达
 *   · 规则正文单独成块，错误提示走危险色 callout；场景步骤走连线时间轴
 */
import { useMemo } from 'react'
import {
  ArrowRight, Box, Building2, Calendar, CircleAlert, CircleCheck, Coins, CornerDownRight,
  Flag, GitBranch, Hash, Key, List, ListChecks, Map as MapIcon, Package, Play,
  Route, Rows3, Scale, Server, Share2, ShieldCheck, Target, ToggleLeft, Type,
  User, UserCog, Users, X, Zap,
} from 'lucide-react'
import type { BusinessCanvas, CanvasElement } from '@/api/exploration'

type CanvasKey = keyof BusinessCanvas

interface KindStyle {
  label: string
  Icon: React.ElementType
  band: string   // 头部浅色底
  solid: string  // 图标底色（实色）
  chip: string   // 类别 pill
  soft: string   // 实体标签底
  text: string   // 实体标签/强调文字
  bar: string    // 左强调条
}

const KIND_STYLE: Record<CanvasKey, KindStyle> = {
  objects:   { label: '对象模型', Icon: Box,     band: 'bg-sky-50',     solid: 'bg-sky-500',     chip: 'bg-sky-100 text-sky-700',         soft: 'bg-sky-50',     text: 'text-sky-700',     bar: 'border-sky-300' },
  actors:    { label: '主体模型', Icon: Users,   band: 'bg-violet-50',  solid: 'bg-violet-500',  chip: 'bg-violet-100 text-violet-700',   soft: 'bg-violet-50',  text: 'text-violet-700',  bar: 'border-violet-300' },
  behaviors: { label: '行为模型', Icon: Play,    band: 'bg-teal-50',    solid: 'bg-teal-500',    chip: 'bg-teal-100 text-teal-700',       soft: 'bg-teal-50',    text: 'text-teal-700',    bar: 'border-teal-300' },
  events:    { label: '事件模型', Icon: Zap,     band: 'bg-amber-50',   solid: 'bg-amber-500',   chip: 'bg-amber-100 text-amber-700',     soft: 'bg-amber-50',   text: 'text-amber-700',   bar: 'border-amber-300' },
  rules:     { label: '规则模型', Icon: Scale,   band: 'bg-rose-50',    solid: 'bg-rose-500',    chip: 'bg-rose-100 text-rose-700',       soft: 'bg-rose-50',    text: 'text-rose-700',    bar: 'border-rose-300' },
  scenarios: { label: '场景模型', Icon: MapIcon, band: 'bg-emerald-50', solid: 'bg-emerald-500', chip: 'bg-emerald-100 text-emerald-700', soft: 'bg-emerald-50', text: 'text-emerald-700', bar: 'border-emerald-300' },
}

const ACTOR_KIND: Record<string, { label: string; Icon: React.ElementType }> = {
  person: { label: '人员', Icon: User },
  org: { label: '组织', Icon: Building2 },
  system: { label: '系统', Icon: Server },
  role: { label: '角色', Icon: UserCog },
}

const RULE_KIND: Record<string, { label: string; cls: string }> = {
  constraint: { label: '约束', cls: 'bg-slate-100 text-slate-600' },
  validation: { label: '校验', cls: 'bg-blue-50 text-blue-600' },
  derivation: { label: '派生', cls: 'bg-violet-50 text-violet-600' },
  approval: { label: '审批', cls: 'bg-amber-50 text-amber-700' },
  alert: { label: '告警', cls: 'bg-rose-50 text-rose-600' },
}

const CARDINALITY: Record<string, [string, string]> = {
  'one-to-one': ['一对一', '1:1'],
  'one-to-many': ['一对多', '1:N'],
  'many-to-one': ['多对一', 'N:1'],
  'many-to-many': ['多对多', 'N:N'],
}

interface AttrRow {
  name?: string; display_name?: string; type_hint?: string
  required?: boolean; enum?: string[]; notes?: string
}
interface RelRow {
  target?: string; name?: string; display_name?: string
  cardinality?: string; description?: string
}
interface Hit { key: CanvasKey; el: CanvasElement }
interface Ctx {
  resolve: (name: string, preferred?: CanvasKey) => Hit | null
  onNavigate?: (key: CanvasKey, el: CanvasElement) => void
}

const asArr = <T,>(v: unknown): T[] => (Array.isArray(v) ? (v as T[]) : [])
const str = (v: unknown): string => (typeof v === 'string' ? v : '')
const norm = (s: unknown): string =>
  String(s ?? '').trim().toLowerCase().replace(/[\s_\-]+/g, '')

/** 自然语言类型提示 → 类型分类（图标 + 配色）。 */
function typeCategory(hint?: string): { Icon: React.ElementType; cls: string } {
  const h = (hint || '').toLowerCase()
  if (!h) return { Icon: Type, cls: 'bg-slate-100 text-slate-500' }
  if (/金额|价格|费用|money|price|amount|currency|decimal/.test(h)) return { Icon: Coins, cls: 'bg-emerald-50 text-emerald-600' }
  if (/日期|时间|date|time/.test(h)) return { Icon: Calendar, cls: 'bg-amber-50 text-amber-600' }
  if (/是否|布尔|bool|标志/.test(h)) return { Icon: ToggleLeft, cls: 'bg-violet-50 text-violet-600' }
  if (/枚举|enum|选项|类别/.test(h)) return { Icon: List, cls: 'bg-rose-50 text-rose-600' }
  if (/数字|数值|整数|数量|个数|number|int|float|count|qty/.test(h)) return { Icon: Hash, cls: 'bg-blue-50 text-blue-600' }
  return { Icon: Type, cls: 'bg-slate-100 text-slate-500' }
}

// ---- 通用展示件 ----

function EntityRef({ name, preferred, ctx }: { name?: string; preferred?: CanvasKey; ctx: Ctx }) {
  if (!name) return null
  const hit = ctx.resolve(name, preferred)
  if (!hit) {
    return (
      <span title="尚未在画布中定义（悬空引用）"
            className="inline-flex items-center gap-1 rounded-md border border-dashed border-[var(--color-border-hover)] px-1.5 py-0.5 text-xs text-[var(--color-text-tertiary)]">
        <CircleAlert size={11} /> {name}
      </span>
    )
  }
  const s = KIND_STYLE[hit.key]
  const I = s.Icon
  const label = str(hit.el.display_name) || hit.el.name
  const cls = `inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-xs font-medium ${s.soft} ${s.text}`
  return ctx.onNavigate
    ? <button onClick={() => ctx.onNavigate!(hit.key, hit.el)} title="查看该元素详情"
              className={`${cls} transition hover:brightness-95`}><I size={11} /> {label}</button>
    : <span className={cls}><I size={11} /> {label}</span>
}

function FlowSlot({ name, preferred, placeholder, ctx }: {
  name?: string; preferred: CanvasKey; placeholder: string; ctx: Ctx
}) {
  if (!name) {
    return (
      <span className="inline-flex items-center gap-1 rounded-md border border-dashed border-[var(--color-border-hover)] px-2 py-1 text-xs text-[var(--color-text-tertiary)]">
        {placeholder}
      </span>
    )
  }
  return <EntityRef name={name} preferred={preferred} ctx={ctx} />
}

function Cardinality({ value }: { value?: string }) {
  if (!value) return null
  const c = CARDINALITY[value]
  return (
    <span title={c ? c[0] : value}
          className="inline-flex items-center rounded-md border border-[var(--color-border)] bg-[var(--color-bg-base)] px-1.5 py-0.5 font-mono text-[10px] text-[var(--color-text-secondary)]">
      {c ? c[1] : value}
    </span>
  )
}

function Section({ icon: Icon, title, count, children }: {
  icon: React.ElementType; title: string; count?: number; children: React.ReactNode
}) {
  return (
    <section className="mt-5 first:mt-0">
      <div className="mb-2.5 flex items-center gap-2">
        <Icon size={14} className="text-[var(--color-text-tertiary)]" />
        <h4 className="text-[13px] font-semibold text-[var(--color-text-secondary)]">{title}</h4>
        {count != null && (
          <span className="rounded-full bg-[var(--color-bg-base)] px-1.5 py-px text-[10px] text-[var(--color-text-tertiary)]">{count}</span>
        )}
        <div className="ml-1 h-px flex-1 bg-[var(--color-border)]" />
      </div>
      {children}
    </section>
  )
}

function Facts({ items }: { items: { icon: React.ElementType; label: string; value: string }[] }) {
  const list = items.filter(i => i.value)
  if (!list.length) return null
  return (
    <div className="grid grid-cols-1 gap-px overflow-hidden rounded-lg border border-[var(--color-border)] bg-[var(--color-border)] sm:grid-cols-2">
      {list.map((it, i) => {
        const I = it.icon
        return (
          <div key={i} className="bg-[var(--color-bg-elevated)] px-3 py-2.5">
            <div className="flex items-center gap-1.5 text-[11px] text-[var(--color-text-tertiary)]">
              <I size={12} /> {it.label}
            </div>
            <div className="mt-1 text-sm leading-relaxed text-[var(--color-text-primary)]">{it.value}</div>
          </div>
        )
      })}
    </div>
  )
}

function Callout({ tone, icon: Icon, label, children }: {
  tone: 'danger' | 'success' | 'info'; icon: React.ElementType; label: string; children: React.ReactNode
}) {
  const map = {
    danger: { wrap: 'bg-[var(--color-danger-bg)]', tx: 'text-[var(--color-danger)]' },
    success: { wrap: 'bg-[var(--color-success-bg)]', tx: 'text-[var(--color-success)]' },
    info: { wrap: 'bg-[var(--color-info-bg)]', tx: 'text-[var(--color-info)]' },
  }[tone]
  return (
    <div className={`rounded-lg px-3 py-2.5 ${map.wrap}`}>
      <div className={`flex items-center gap-1.5 text-[11px] font-medium ${map.tx}`}>
        <Icon size={13} /> {label}
      </div>
      <div className="mt-1 text-sm leading-relaxed text-[var(--color-text-primary)]">{children}</div>
    </div>
  )
}

function Checklist({ items, icon: Icon = CircleCheck, tone = 'text-[var(--color-text-tertiary)]' }: {
  items: string[]; icon?: React.ElementType; tone?: string
}) {
  return (
    <ul className="space-y-1.5">
      {items.map((s, i) => (
        <li key={i} className="flex gap-2 text-sm leading-relaxed text-[var(--color-text-primary)]">
          <Icon size={15} className={`mt-0.5 shrink-0 ${tone}`} />
          <span className="min-w-0">{s}</span>
        </li>
      ))}
    </ul>
  )
}

function Pills({ items }: { items: string[] }) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {items.map((s, i) => (
        <span key={i} className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg-elevated)] px-2 py-1 text-xs text-[var(--color-text-secondary)]">{s}</span>
      ))}
    </div>
  )
}

function Timeline({ items }: { items: string[] }) {
  return (
    <ol className="space-y-0">
      {items.map((s, i) => (
        <li key={i} className="flex gap-3">
          <div className="flex flex-col items-center">
            <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-emerald-100 text-[11px] font-semibold text-emerald-700">{i + 1}</span>
            {i < items.length - 1 && <span className="my-1 w-px flex-1 bg-[var(--color-border)]" />}
          </div>
          <p className="pb-4 pt-0.5 text-sm leading-relaxed text-[var(--color-text-primary)]">{s}</p>
        </li>
      ))}
    </ol>
  )
}

function AttrCard({ attr, isKey }: { attr: AttrRow; isKey: boolean }) {
  const t = typeCategory(attr.type_hint)
  const T = t.Icon
  const nameDiffers = !!attr.name && !!attr.display_name && attr.display_name !== attr.name
  return (
    <div className={`flex items-start gap-2.5 rounded-lg border px-3 py-2 ${isKey
      ? 'border-amber-200 bg-amber-50/50' : 'border-[var(--color-border)] bg-[var(--color-bg-elevated)]'}`}>
      <span className={`mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-md ${t.cls}`}>
        <T size={13} />
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
          {isKey && <Key size={12} className="text-amber-500" />}
          <span className="text-sm font-medium text-[var(--color-text-primary)]">{attr.display_name || attr.name}</span>
          {nameDiffers && <code className="font-mono text-[11px] text-[var(--color-text-tertiary)]">{attr.name}</code>}
          {attr.required && <span className="rounded bg-rose-50 px-1 py-px text-[10px] font-medium text-rose-500">必填</span>}
          {attr.type_hint && <span className="ml-auto text-[11px] text-[var(--color-text-tertiary)]">{attr.type_hint}</span>}
        </div>
        {attr.enum && attr.enum.length > 0 && (
          <div className="mt-1.5 flex flex-wrap gap-1">
            {attr.enum.map((e, i) => (
              <span key={i} className="rounded bg-[var(--color-bg-base)] px-1.5 py-px text-[10px] text-[var(--color-text-secondary)]">{e}</span>
            ))}
          </div>
        )}
        {attr.notes && <p className="mt-1 text-[11px] leading-relaxed text-[var(--color-text-tertiary)]">{attr.notes}</p>}
      </div>
    </div>
  )
}

function RelationRow({ rel, ctx }: { rel: RelRow; ctx: Ctx }) {
  return (
    <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-elevated)] px-3 py-2.5">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-medium text-[var(--color-text-primary)]">{rel.display_name || rel.name || '关联'}</span>
        <ArrowRight size={14} className="text-[var(--color-text-tertiary)]" />
        <EntityRef name={rel.target} preferred="objects" ctx={ctx} />
        <Cardinality value={rel.cardinality} />
      </div>
      {rel.description && <p className="mt-1.5 text-xs leading-relaxed text-[var(--color-text-tertiary)]">{rel.description}</p>}
    </div>
  )
}

function RefRow({ label, names, preferred, ctx }: {
  label: string; names: string[]; preferred: CanvasKey; ctx: Ctx
}) {
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <span className="mr-0.5 w-8 shrink-0 text-[11px] text-[var(--color-text-tertiary)]">{label}</span>
      {names.map((n, i) => <EntityRef key={i} name={n} preferred={preferred} ctx={ctx} />)}
    </div>
  )
}

function Empty({ text }: { text: string }) {
  return (
    <div className="rounded-lg border border-dashed border-[var(--color-border)] px-3 py-4 text-center text-xs text-[var(--color-text-tertiary)]">
      {text}
    </div>
  )
}

// ---- 各类别主体 ----

function ElementBody({ sectionKey, el, ctx }: { sectionKey: CanvasKey; el: CanvasElement; ctx: Ctx }) {
  if (sectionKey === 'objects') {
    const attrs = asArr<AttrRow>(el.attributes)
    const rels = asArr<RelRow>(el.relations)
    const keyName = str(el.key_attribute)
    return (
      <>
        <Section icon={Rows3} title="属性" count={attrs.length || undefined}>
          {attrs.length ? (
            <div className="space-y-1.5">
              {attrs.map((a, i) => (
                <AttrCard key={i} attr={a} isKey={!!keyName && norm(a.name) === norm(keyName)} />
              ))}
            </div>
          ) : <Empty text="尚未定义属性 —— 可在对话中继续补全" />}
        </Section>
        {rels.length > 0 && (
          <Section icon={Share2} title="关系" count={rels.length}>
            <div className="space-y-1.5">{rels.map((r, i) => <RelationRow key={i} rel={r} ctx={ctx} />)}</div>
          </Section>
        )}
      </>
    )
  }

  if (sectionKey === 'actors') {
    const k = ACTOR_KIND[str(el.kind)] || { label: str(el.kind) || '角色', Icon: UserCog }
    const KI = k.Icon
    const resp = asArr<string>(el.responsibilities)
    const attrs = asArr<AttrRow>(el.attributes)
    const keyName = str(el.key_attribute)
    const isDataActor = ['person', 'org'].includes(str(el.kind))
    return (
      <>
        <span className="inline-flex items-center gap-1.5 rounded-lg bg-violet-50 px-2.5 py-1.5 text-sm font-medium text-violet-700">
          <KI size={14} /> {k.label}
        </span>
        {(attrs.length > 0 || isDataActor) && (
          <Section icon={Rows3} title="属性" count={attrs.length || undefined}>
            {attrs.length ? (
              <div className="space-y-1.5">
                {attrs.map((a, i) => (
                  <AttrCard key={i} attr={a} isKey={!!keyName && norm(a.name) === norm(keyName)} />
                ))}
              </div>
            ) : <Empty text="尚未定义属性 —— person/org 主体也是数据实体，建议补充识别/档案属性" />}
          </Section>
        )}
        {resp.length > 0 && (
          <Section icon={ListChecks} title="职责" count={resp.length}><Checklist items={resp} /></Section>
        )}
      </>
    )
  }

  if (sectionKey === 'behaviors') {
    const inputs = asArr<AttrRow>(el.inputs)
    const constraints = asArr<string>(el.constraints)
    const needsApproval = !!el.needs_approval
    return (
      <>
        <div className="flex flex-wrap items-center gap-2 rounded-xl border border-[var(--color-border)] bg-[var(--color-bg-base)] px-3 py-3">
          <FlowSlot name={str(el.actor)} preferred="actors" placeholder="未指定主体" ctx={ctx} />
          <ArrowRight size={15} className="text-[var(--color-text-tertiary)]" />
          <span className="inline-flex items-center gap-1 rounded-md bg-teal-100 px-2 py-1 text-sm font-medium text-teal-700">
            <Play size={12} /> {str(el.display_name) || el.name}
          </span>
          <ArrowRight size={15} className="text-[var(--color-text-tertiary)]" />
          <FlowSlot name={str(el.object)} preferred="objects" placeholder="未指定对象" ctx={ctx} />
        </div>
        <div className="mt-3">
          <Facts items={[
            { icon: Zap, label: '触发条件', value: str(el.trigger) },
            { icon: Flag, label: '结果', value: str(el.outcome) },
          ]} />
        </div>
        <div className="mt-2.5">
          {needsApproval
            ? <span className="inline-flex items-center gap-1.5 rounded-lg bg-amber-50 px-2.5 py-1.5 text-xs font-medium text-amber-700"><ShieldCheck size={14} /> 此行为需要审批</span>
            : <span className="inline-flex items-center gap-1.5 rounded-lg bg-[var(--color-bg-base)] px-2.5 py-1.5 text-xs font-medium text-[var(--color-text-secondary)]"><CircleCheck size={14} /> 无需审批</span>}
        </div>
        {inputs.length > 0 && (
          <Section icon={Rows3} title="输入" count={inputs.length}>
            <div className="space-y-1.5">{inputs.map((a, i) => <AttrCard key={i} attr={a} isKey={false} />)}</div>
          </Section>
        )}
        {constraints.length > 0 && (
          <Section icon={ShieldCheck} title="约束" count={constraints.length}>
            <Checklist items={constraints} icon={ShieldCheck} tone="text-rose-400" />
          </Section>
        )}
      </>
    )
  }

  if (sectionKey === 'events') {
    const payload = asArr<string>(el.payload)
    const consequences = asArr<string>(el.consequences)
    return (
      <>
        <Facts items={[{ icon: CornerDownRight, label: '触发来源', value: str(el.source) }]} />
        {payload.length > 0 && (
          <Section icon={Package} title="事件载荷" count={payload.length}><Pills items={payload} /></Section>
        )}
        {consequences.length > 0 && (
          <Section icon={GitBranch} title="后续影响" count={consequences.length}>
            <Checklist items={consequences} icon={CornerDownRight} />
          </Section>
        )}
      </>
    )
  }

  if (sectionKey === 'rules') {
    const rk = RULE_KIND[str(el.kind)] || { label: str(el.kind) || '约束', cls: 'bg-slate-100 text-slate-600' }
    return (
      <>
        <div className="flex flex-wrap items-center gap-2">
          <span className={`inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-sm font-medium ${rk.cls}`}>
            <Scale size={14} /> {rk.label}规则
          </span>
          {str(el.applies_to) && (
            <>
              <span className="text-xs text-[var(--color-text-tertiary)]">作用于</span>
              <EntityRef name={str(el.applies_to)} ctx={ctx} />
            </>
          )}
        </div>
        {str(el.statement) && (
          <div className={`mt-3 rounded-lg border-l-2 bg-[var(--color-bg-base)] px-3 py-2.5 text-sm leading-relaxed text-[var(--color-text-primary)] ${KIND_STYLE.rules.bar}`}>
            {str(el.statement)}
          </div>
        )}
        {str(el.error_message) && (
          <div className="mt-2.5">
            <Callout tone="danger" icon={CircleAlert} label="校验不通过时提示">{str(el.error_message)}</Callout>
          </div>
        )}
      </>
    )
  }

  // scenarios
  const actors = asArr<string>(el.actors)
  const objects = asArr<string>(el.objects)
  const behaviors = asArr<string>(el.behaviors)
  const steps = asArr<string>(el.steps)
  const hasRefs = actors.length || objects.length || behaviors.length
  return (
    <>
      {str(el.goal) && <Callout tone="info" icon={Target} label="场景目标">{str(el.goal)}</Callout>}
      {hasRefs > 0 && (
        <Section icon={Users} title="涉及要素">
          <div className="space-y-2">
            {actors.length > 0 && <RefRow label="主体" names={actors} preferred="actors" ctx={ctx} />}
            {objects.length > 0 && <RefRow label="对象" names={objects} preferred="objects" ctx={ctx} />}
            {behaviors.length > 0 && <RefRow label="行为" names={behaviors} preferred="behaviors" ctx={ctx} />}
          </div>
        </Section>
      )}
      {steps.length > 0 && (
        <Section icon={Route} title="流程步骤" count={steps.length}><Timeline items={steps} /></Section>
      )}
      {str(el.expected_outcome) && (
        <div className="mt-4"><Callout tone="success" icon={Flag} label="预期结果">{str(el.expected_outcome)}</Callout></div>
      )}
    </>
  )
}

/** 头部统计条：一眼可见的关键信息。 */
function HeaderStats({ sectionKey, el }: { sectionKey: CanvasKey; el: CanvasElement }) {
  const pills: { icon?: React.ElementType; text: string; strong?: boolean }[] = []
  if (sectionKey === 'objects') {
    const a = asArr(el.attributes).length
    const r = asArr(el.relations).length
    const key = str(el.key_attribute)
    pills.push({ text: `${a} 属性` })
    if (r) pills.push({ text: `${r} 关系` })
    pills.push(key ? { icon: Key, text: key, strong: true } : { icon: CircleAlert, text: '未指定主键' })
  } else if (sectionKey === 'actors') {
    const k = ACTOR_KIND[str(el.kind)]
    if (k) pills.push({ icon: k.Icon, text: k.label })
    const a = asArr(el.attributes).length
    const r = asArr(el.responsibilities).length
    if (a) pills.push({ text: `${a} 属性` })
    if (r) pills.push({ text: `${r} 职责` })
  } else if (sectionKey === 'behaviors') {
    if (el.needs_approval) pills.push({ icon: ShieldCheck, text: '需审批' })
    const inputs = asArr(el.inputs).length
    if (inputs) pills.push({ text: `${inputs} 输入` })
  } else if (sectionKey === 'events') {
    if (str(el.source)) pills.push({ icon: CornerDownRight, text: str(el.source) })
  } else if (sectionKey === 'rules') {
    const rk = RULE_KIND[str(el.kind)]
    if (rk) pills.push({ text: `${rk.label}规则` })
  } else if (sectionKey === 'scenarios') {
    const s = asArr(el.steps).length
    if (s) pills.push({ icon: Route, text: `${s} 步` })
  }
  if (!pills.length) return null
  return (
    <div className="mt-2 flex flex-wrap items-center gap-1.5">
      {pills.map((p, i) => {
        const I = p.icon
        return (
          <span key={i}
                className={`inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] ${p.strong
                  ? 'bg-white/70 font-mono font-medium text-[var(--color-text-secondary)]'
                  : 'bg-white/60 text-[var(--color-text-secondary)]'}`}>
            {I && <I size={11} />} {p.text}
          </span>
        )
      })}
    </div>
  )
}

export default function ElementDetailModal({ sectionKey, el, canvas, onClose, onNavigate }: {
  sectionKey: CanvasKey
  el: CanvasElement
  canvas: BusinessCanvas | null
  onClose: () => void
  onNavigate?: (key: CanvasKey, el: CanvasElement) => void
}) {
  const index = useMemo(() => {
    const m = new Map<string, Hit[]>()
    const keys: CanvasKey[] = ['objects', 'actors', 'behaviors', 'events', 'rules', 'scenarios']
    if (canvas) {
      for (const key of keys) {
        for (const e of canvas[key] || []) {
          for (const nm of [e.name, str(e.display_name)]) {
            const kk = norm(nm)
            if (!kk) continue
            const arr = m.get(kk) || []
            arr.push({ key, el: e })
            m.set(kk, arr)
          }
        }
      }
    }
    return m
  }, [canvas])

  const ctx: Ctx = {
    resolve: (name, preferred) => {
      const hits = index.get(norm(name)) || []
      if (!hits.length) return null
      if (preferred) return hits.find(h => h.key === preferred) || hits[0]
      return hits[0]
    },
    onNavigate,
  }

  const s = KIND_STYLE[sectionKey]
  const Icon = s.Icon
  const description = str(el.description)

  return (
    <div className="fixed inset-0 z-[300] flex items-center justify-center bg-[var(--color-bg-overlay)] p-6" onClick={onClose}>
      <div
        className="flex max-h-[86vh] w-[620px] max-w-[94vw] flex-col overflow-hidden rounded-2xl bg-[var(--color-bg-elevated)] shadow-[var(--shadow-lg)]"
        onClick={e => e.stopPropagation()}
      >
        {/* 头部：类别着色 band + 图标 + 名称 + 统计条 */}
        <div className={`relative px-5 pb-4 pt-5 ${s.band}`}>
          <div className="flex items-start gap-3.5">
            <span className={`flex h-11 w-11 shrink-0 items-center justify-center rounded-xl text-white shadow-sm ${s.solid}`}>
              <Icon size={20} />
            </span>
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <h3 className="truncate text-lg font-semibold text-[var(--color-text-primary)]">
                  {str(el.display_name) || el.name}
                </h3>
                <span className={`shrink-0 rounded-full px-2 py-0.5 text-[10px] font-medium ${s.chip}`}>{s.label}</span>
              </div>
              <code className="mt-0.5 block truncate font-mono text-xs text-[var(--color-text-secondary)]">{el.name}</code>
              <HeaderStats sectionKey={sectionKey} el={el} />
            </div>
            <button onClick={onClose}
                    className="shrink-0 rounded-md p-1.5 text-[var(--color-text-tertiary)] transition hover:bg-black/[0.06] hover:text-[var(--color-text-secondary)]">
              <X size={17} />
            </button>
          </div>
        </div>

        {/* 主体 */}
        <div className="flex-1 overflow-y-auto px-5 py-4">
          {description && (
            <p className={`mb-4 border-l-2 pl-3 text-sm leading-relaxed text-[var(--color-text-secondary)] ${s.bar}`}>
              {description}
            </p>
          )}
          <ElementBody sectionKey={sectionKey} el={el} ctx={ctx} />
        </div>
      </div>
    </div>
  )
}
