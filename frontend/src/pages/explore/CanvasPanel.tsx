import { useRef, useState } from 'react'
import {
  Box, Users, Play, Zap, Scale, Map as MapIcon, ChevronDown, ChevronRight, CircleAlert,
  CircleCheck, CircleHelp, Share2, ShieldAlert, ShieldCheck, X, Copy, Loader2, FileText,
} from 'lucide-react'
import {
  explorationApi, type BusinessCanvas, type BxQuestion, type CanvasElement,
  type Completeness, type DiagramKind, type Readiness,
} from '@/api/exploration'
import MermaidBlock from '@/components/MermaidBlock'
import ElementDetailModal from './ElementDetailModal'

type CanvasKey = 'objects' | 'actors' | 'behaviors' | 'events' | 'rules' | 'scenarios'

const SECTIONS: {
  key: CanvasKey
  label: string
  icon: React.ElementType
  tint: string
}[] = [
  { key: 'objects', label: '对象模型', icon: Box, tint: 'text-sky-600 bg-sky-50' },
  { key: 'actors', label: '主体模型', icon: Users, tint: 'text-violet-600 bg-violet-50' },
  { key: 'behaviors', label: '行为模型', icon: Play, tint: 'text-teal-600 bg-teal-50' },
  { key: 'events', label: '事件模型', icon: Zap, tint: 'text-amber-600 bg-amber-50' },
  { key: 'rules', label: '规则模型', icon: Scale, tint: 'text-rose-600 bg-rose-50' },
  { key: 'scenarios', label: '场景模型', icon: MapIcon, tint: 'text-emerald-600 bg-emerald-50' },
]

const DIAGRAM_TABS: { kind: DiagramKind; label: string; needsTarget?: 'scenario' | 'object' }[] = [
  { kind: 'er', label: 'ER 图' },
  { kind: 'flow', label: '流程图', needsTarget: 'scenario' },
  { kind: 'sequence', label: '时序图', needsTarget: 'scenario' },
  { kind: 'state', label: '状态图', needsTarget: 'object' },
]

const errorMessage = (error: unknown, fallback: string): string => {
  if (!error || typeof error !== 'object') return fallback
  const value = error as { detail?: string | { message?: string }; message?: string }
  return typeof value.detail === 'string' ? value.detail : value.detail?.message || value.message || fallback
}

function elementBadges(key: CanvasKey, el: CanvasElement): string[] {
  const badges: string[] = []
  if (key === 'objects') {
    const attrs = (el.attributes as unknown[] | undefined)?.length || 0
    const rels = (el.relations as unknown[] | undefined)?.length || 0
    badges.push(`${attrs} 属性`)
    if (rels) badges.push(`${rels} 关系`)
    if (el.key_attribute) badges.push(`主键 ${el.key_attribute}`)
  } else if (key === 'actors') {
    const attrs = (el.attributes as unknown[] | undefined)?.length || 0
    const resp = (el.responsibilities as unknown[] | undefined)?.length || 0
    if (el.kind) badges.push(String(el.kind))
    if (attrs) badges.push(`${attrs} 属性`)
    if (resp) badges.push(`${resp} 职责`)
  } else if (key === 'behaviors') {
    if (el.actor) badges.push(String(el.actor))
    if (el.object) badges.push(`→ ${el.object}`)
    if (el.needs_approval) badges.push('需审批')
  } else if (key === 'events') {
    if (el.source) badges.push(`来源 ${el.source}`)
  } else if (key === 'rules') {
    if (el.kind) badges.push(String(el.kind))
    if (el.applies_to) badges.push(`→ ${el.applies_to}`)
  } else if (key === 'scenarios') {
    const steps = (el.steps as unknown[] | undefined)?.length || 0
    if (steps) badges.push(`${steps} 步`)
  }
  return badges
}

/** 质量门清单：与后端草稿闸门同一口径，未过门项即 agent 的追问方向 */
function GatePanel({ readiness }: { readiness: Readiness }) {
  const [expanded, setExpanded] = useState(false)
  const [open, setOpen] = useState<Record<string, boolean>>({})
  return (
    <div className="overflow-hidden rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-elevated)]">
      <button
        type="button"
        onClick={() => setExpanded(value => !value)}
        aria-expanded={expanded}
        className="flex w-full items-center gap-2 px-3 py-2.5 text-left transition-colors hover:bg-[var(--color-bg-hover)]"
      >
        <span className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-md ${readiness.ready
          ? 'bg-teal-50 text-teal-700' : 'bg-amber-50 text-amber-700'}`}>
          {readiness.ready ? <ShieldCheck size={13} /> : <ShieldAlert size={13} />}
        </span>
        <span className="min-w-0 flex-1">
          <span className="flex items-center gap-1.5 text-xs font-medium text-[var(--color-text-primary)]">
            质量门
            <span className={`rounded-full px-1.5 py-0.5 text-[10px] ${readiness.ready
              ? 'bg-teal-50 text-teal-700' : 'bg-amber-50 text-amber-700'}`}>
              {readiness.gatesPassed}/{readiness.gatesTotal}
            </span>
          </span>
          <span className="mt-0.5 block truncate text-[11px] text-[var(--color-text-tertiary)]">
            {readiness.ready ? '全部通过 · 可生成本体模型' : `${readiness.blockingCount} 项待定量 · 点击查看详情`}
          </span>
        </span>
        {expanded
          ? <ChevronDown size={13} className="shrink-0 text-[var(--color-text-tertiary)]" />
          : <ChevronRight size={13} className="shrink-0 text-[var(--color-text-tertiary)]" />}
      </button>
      {expanded && <div className="border-t border-[var(--color-border)] px-3 pb-3 pt-2.5">
        <div className={`mb-2 text-[11px] ${readiness.ready ? 'text-teal-700' : 'text-amber-800'}`}>
          当前阶段：{readiness.stage}
        </div>
        <ul className="space-y-1">
        {readiness.gates.map(g => {
          const expandable = g.blockingItems.length + g.advisoryItems.length > 0
          const expanded = open[g.id]
          return (
            <li key={g.id}>
              <button
                disabled={!expandable}
                onClick={() => setOpen(o => ({ ...o, [g.id]: !o[g.id] }))}
                className={`w-full flex items-center gap-1.5 text-[11px] leading-5 text-left ${expandable ? 'cursor-pointer' : 'cursor-default'}`}
              >
                {g.passed
                  ? <CircleCheck size={11} className="shrink-0 text-teal-600" />
                  : <CircleAlert size={11} className="shrink-0 text-amber-600" />}
                <span className={g.passed ? 'text-[var(--color-text-secondary)]' : 'text-amber-900 font-medium'}>
                  {g.label}
                </span>
                {g.blockingItems.length > 0 && (
                  <span className="text-amber-700">{g.blockingItems.length}</span>
                )}
                {g.advisoryItems.length > 0 && (
                  <span className="text-[var(--color-text-tertiary)]">+{g.advisoryItems.length} 建议</span>
                )}
                {expandable && (
                  expanded
                    ? <ChevronDown size={11} className="ml-auto shrink-0 text-[var(--color-text-tertiary)]" />
                    : <ChevronRight size={11} className="ml-auto shrink-0 text-[var(--color-text-tertiary)]" />
                )}
              </button>
              {expanded && (
                <ul className="mt-0.5 mb-1 ml-4 space-y-0.5">
                  {g.blockingItems.map((it, i) => (
                    <li key={`b${i}`} className="text-[11px] leading-relaxed text-amber-800/90">· {it}</li>
                  ))}
                  {g.advisoryItems.map((it, i) => (
                    <li key={`a${i}`} className="text-[11px] leading-relaxed text-[var(--color-text-tertiary)]">· {it}</li>
                  ))}
                </ul>
              )}
            </li>
          )
        })}
        </ul>
      </div>}
    </div>
  )
}

/** 澄清账本：开放问题可点选候选值直接作答（onAsk 发进对话），已销账项折叠留档 */
function LedgerPanel({ questions, onAsk }: {
  questions: BxQuestion[]
  onAsk?: (text: string) => void
}) {
  const [showClosed, setShowClosed] = useState(false)
  const opens = questions.filter(q => q.status === 'open')
  const closed = questions.filter(q => q.status !== 'open')
  if (questions.length === 0) return null
  return (
    <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-elevated)] px-3 py-2.5">
      <div className="flex items-center gap-1.5 text-xs font-medium text-[var(--color-text-primary)] mb-1.5">
        <CircleHelp size={13} className="text-amber-500" />
        澄清账本
        <span className="font-normal text-[var(--color-text-tertiary)]">
          {opens.length} 待答 · {closed.length} 已结
        </span>
      </div>
      <div className="space-y-1.5">
        {opens.map(q => (
          <div key={q.id} className="rounded-md bg-[var(--color-bg-base)] px-2.5 py-1.5">
            <div className="flex items-start gap-1.5">
              <span className={`mt-px shrink-0 text-[10px] px-1 py-px rounded ${q.kind === 'blocking'
                ? 'bg-amber-100 text-amber-700' : 'bg-sky-50 text-sky-600'}`}>
                {q.kind === 'blocking' ? 'B·堵门' : 'A·建议'}
              </span>
              <span className="text-[11px] leading-relaxed text-[var(--color-text-secondary)]">{q.question}</span>
            </div>
            {(q.options?.length || q.suggestion) && (
              <div className="mt-1 flex flex-wrap gap-1 pl-0.5">
                {(q.options || []).slice(0, 4).map(opt => (
                  <button
                    key={opt}
                    disabled={!onAsk}
                    onClick={() => onAsk?.(`「${q.question}」我的答复：${opt}`)}
                    className="px-1.5 py-0.5 rounded text-[10px] border border-[var(--color-border)] text-[var(--color-text-secondary)] hover:border-teal-400 hover:text-teal-700 disabled:opacity-40"
                  >
                    {opt}
                  </button>
                ))}
                {!q.options?.length && q.suggestion && (
                  <button
                    disabled={!onAsk}
                    onClick={() => onAsk?.(`「${q.question}」确认采用建议：${q.suggestion}`)}
                    className="px-1.5 py-0.5 rounded text-[10px] border border-[var(--color-border)] text-[var(--color-text-secondary)] hover:border-teal-400 hover:text-teal-700 disabled:opacity-40"
                  >
                    采纳建议：{q.suggestion}
                  </button>
                )}
              </div>
            )}
          </div>
        ))}
        {closed.length > 0 && (
          <button
            onClick={() => setShowClosed(v => !v)}
            className="flex items-center gap-1 text-[11px] text-[var(--color-text-tertiary)] hover:text-[var(--color-text-secondary)]"
          >
            {showClosed ? <ChevronDown size={11} /> : <ChevronRight size={11} />}
            已结 {closed.length} 项
          </button>
        )}
        {showClosed && closed.map(q => (
          <div key={q.id} className="rounded-md bg-[var(--color-bg-base)] px-2.5 py-1.5 opacity-70">
            <div className="text-[11px] leading-relaxed text-[var(--color-text-tertiary)] line-through decoration-[var(--color-border)]">
              {q.question}
            </div>
            <div className="text-[11px] text-teal-700">
              {q.status === 'dismissed' ? '已搁置：' : '✓ '}{q.resolution}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

/** 业务画布面板：六类模型分组卡片 + 澄清账本 + 质量门，随 SSE canvas 事件实时刷新 */
export default function CanvasPanel({ sessionId, canvas, completeness, readiness, onAsk, onOpenDocuments }: {
  sessionId?: string
  canvas: BusinessCanvas | null
  completeness: Completeness | null
  readiness?: Readiness | null
  onAsk?: (text: string) => void
  onOpenDocuments?: () => void
}) {
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({})
  const [detail, setDetail] = useState<{ section: typeof SECTIONS[number]; el: CanvasElement } | null>(null)
  const [dgOpen, setDgOpen] = useState(false)
  const [dgKind, setDgKind] = useState<DiagramKind>('er')
  const [dgTarget, setDgTarget] = useState('')
  const [dgMermaid, setDgMermaid] = useState('')
  const [dgTitle, setDgTitle] = useState('')
  const [dgWarnings, setDgWarnings] = useState<string[]>([])
  const [dgBusy, setDgBusy] = useState(false)
  const [dgError, setDgError] = useState('')
  const dgRequestSeq = useRef(0)
  const counts = completeness?.counts || {}
  const total = Object.values(counts).reduce((a, b) => a + b, 0)

  const scenarioNames = (canvas?.scenarios || []).map(s => String(s.display_name || s.name))
  const objectNames = (canvas?.objects || [])
    .filter(o => ((o.attributes as { name?: string; display_name?: string; enum?: string[] }[] | undefined) || [])
      .some(a => (a.enum?.length || 0) > 0 && /状态|阶段|status|state|stage/i.test(`${a.name || ''}${a.display_name || ''}`)))
    .map(o => String(o.display_name || o.name))

  const loadDiagram = async (kind: DiagramKind, target: string) => {
    if (!sessionId) return
    const requestSeq = ++dgRequestSeq.current
    setDgBusy(true)
    setDgError('')
    setDgMermaid('')
    setDgWarnings([])
    try {
      const d = await explorationApi.diagram(sessionId, kind, target || undefined)
      if (requestSeq !== dgRequestSeq.current) return
      setDgMermaid(d.mermaid)
      setDgTitle(d.title)
      setDgWarnings(d.warnings || [])
    } catch (error: unknown) {
      if (requestSeq !== dgRequestSeq.current) return
      setDgError(errorMessage(error, '图表生成失败'))
    } finally {
      if (requestSeq === dgRequestSeq.current) setDgBusy(false)
    }
  }

  const openDiagram = () => {
    setDgKind('er')
    setDgTarget('')
    setDgOpen(true)
    void loadDiagram('er', '')
  }

  const closeDiagram = () => {
    dgRequestSeq.current += 1
    setDgBusy(false)
    setDgOpen(false)
  }

  const targetSpec = DIAGRAM_TABS.find(t => t.kind === dgKind)?.needsTarget
  const targetOptions = targetSpec === 'scenario' ? scenarioNames
    : targetSpec === 'object' ? objectNames : []

  return (
    <div className="flex h-full flex-col bg-white">
      <div className="flex h-14 shrink-0 items-center justify-between border-b border-[var(--color-border)] bg-white px-4">
        <div className="text-sm font-semibold text-[var(--color-text-primary)]">业务场景</div>
        <div className="flex shrink-0 items-center gap-1.5">
          <button
            onClick={openDiagram}
            disabled={!sessionId || (counts.objects || 0) === 0}
            title={(counts.objects || 0) === 0 ? '画布还没有对象模型' : '从画布确定性生成 ER/流程/时序/状态图（不经 LLM）'}
            className="inline-flex items-center gap-1 px-2 py-1 rounded-md text-[11px] border border-[var(--color-border)] text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)] disabled:opacity-40"
          >
            <Share2 size={11} /> 图表
          </button>
          <button
            type="button"
            onClick={onOpenDocuments}
            disabled={!sessionId}
            title="查看需求文档"
            aria-label="查看需求文档"
            className="inline-flex h-7 w-7 items-center justify-center rounded-md border border-[var(--color-border)] text-[var(--color-text-secondary)] transition-colors hover:border-teal-300 hover:bg-teal-50 hover:text-teal-700 disabled:opacity-40"
          >
            <FileText size={13} />
          </button>
        </div>
      </div>

      <div className="flex-1 space-y-2 overflow-y-auto bg-[#f8fbff] px-3 pb-3 pt-3">
        {/* 质量门：与草稿生成闸门同一口径 */}
        {readiness && total > 0 && <GatePanel readiness={readiness} />}

        {/* 澄清账本：开放问题点选即答 */}
        <LedgerPanel questions={canvas?.questions || []} onAsk={onAsk} />

        {SECTIONS.map((section) => {
          const { key, label, icon: Icon, tint } = section
          const items = (canvas?.[key] || []) as CanvasElement[]
          const isCollapsed = collapsed[key] ?? items.length === 0
          return (
            <div key={key} className="rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-elevated)]">
              <button
                onClick={() => setCollapsed(c => ({ ...c, [key]: !isCollapsed }))}
                className="w-full flex items-center gap-2 px-3 py-2 text-left"
              >
                <span className={`w-5 h-5 rounded-md flex items-center justify-center ${tint}`}>
                  <Icon size={12} />
                </span>
                <span className="text-xs font-medium text-[var(--color-text-primary)] flex-1">{label}</span>
                <span className="text-[11px] text-[var(--color-text-tertiary)]">{items.length}</span>
                {isCollapsed
                  ? <ChevronRight size={13} className="text-[var(--color-text-tertiary)]" />
                  : <ChevronDown size={13} className="text-[var(--color-text-tertiary)]" />}
              </button>
              {!isCollapsed && items.length > 0 && (
                <div className="px-3 pb-2.5 space-y-1.5">
                  {items.map(el => (
                    <button
                      key={el.id}
                      onClick={() => setDetail({ section, el })}
                      title="查看详情"
                      className="group block w-full text-left rounded-md bg-[var(--color-bg-base)] px-2.5 py-1.5 transition-colors hover:bg-[var(--color-bg-hover)] hover:ring-1 hover:ring-teal-200"
                    >
                      <div className="flex items-center gap-1 text-xs font-medium text-[var(--color-text-primary)]">
                        <span className="truncate">
                          {el.display_name || el.name}
                          {el.display_name && el.display_name !== el.name && (
                            <span className="ml-1.5 font-normal text-[var(--color-text-tertiary)] font-mono text-[11px]">{el.name}</span>
                          )}
                        </span>
                        <ChevronRight size={12} className="ml-auto shrink-0 text-[var(--color-text-tertiary)] opacity-0 transition-opacity group-hover:opacity-100" />
                      </div>
                      {(el.description || elementBadges(key, el).length > 0) && (
                        <div className="mt-0.5 flex flex-wrap items-center gap-1">
                          {elementBadges(key, el).map((b, i) => (
                            <span key={i} className="text-[10px] px-1.5 py-px rounded bg-black/[0.04] text-[var(--color-text-secondary)]">{b}</span>
                          ))}
                          {el.description && (
                            <span className="text-[11px] text-[var(--color-text-tertiary)] truncate max-w-full">{el.description}</span>
                          )}
                        </div>
                      )}
                    </button>
                  ))}
                </div>
              )}
            </div>
          )
        })}
      </div>

      {/* 元素详情弹窗：点击画布卡片查看完整字段，引用可下钻 */}
      {detail && (
        <ElementDetailModal
          sectionKey={detail.section.key}
          el={detail.el}
          canvas={canvas}
          onClose={() => setDetail(null)}
          onNavigate={(key, el) => {
            const sec = SECTIONS.find(s => s.key === key)
            if (sec) setDetail({ section: sec, el })
          }}
        />
      )}

      {/* 图表模态：ER/流程/时序/状态，全部由画布确定性生成 */}
      {dgOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-6"
             onClick={closeDiagram}>
          <div className="w-[860px] max-w-[94vw] max-h-[86vh] rounded-xl bg-[var(--color-bg-elevated)] shadow-2xl flex flex-col"
               onClick={e => e.stopPropagation()}>
            <div className="flex items-center justify-between gap-3 px-5 py-3.5 border-b border-[var(--color-border)]">
              <div className="min-w-0">
                <div data-testid="canvas-diagram-title" className="text-sm font-semibold text-[var(--color-text-primary)] truncate">
                  {dgTitle || '业务建模图表'}
                </div>
                <div className="text-[11px] text-[var(--color-text-tertiary)] mt-0.5">
                  由画布确定性生成（不经 LLM），与画布严格一致
                </div>
              </div>
              <div className="flex items-center gap-2 shrink-0">
                {dgMermaid && (
                  <button
                    onClick={() => { void navigator.clipboard.writeText(dgMermaid) }}
                    className="inline-flex items-center gap-1 px-2.5 py-1.5 rounded-md text-xs border border-[var(--color-border)] text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)]"
                  >
                    <Copy size={12} /> 复制源码
                  </button>
                )}
                <button aria-label="关闭业务建模图表" onClick={closeDiagram}
                        className="p-1.5 rounded-md hover:bg-[var(--color-bg-hover)] text-[var(--color-text-tertiary)]">
                  <X size={15} />
                </button>
              </div>
            </div>
            <div className="flex items-center gap-2 px-5 py-2.5 border-b border-[var(--color-border)]">
              <div className="flex rounded-md border border-[var(--color-border)] overflow-hidden">
                {DIAGRAM_TABS.map(t => (
                  <button
                    key={t.kind}
                    onClick={() => {
                      setDgKind(t.kind); setDgTarget(''); void loadDiagram(t.kind, '')
                    }}
                    className={`px-3 py-1.5 text-xs transition-colors ${dgKind === t.kind
                      ? 'bg-teal-600 text-white font-medium'
                      : 'text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)]'}`}
                  >
                    {t.label}
                  </button>
                ))}
              </div>
              {targetSpec && (
                <select
                  value={dgTarget}
                  onChange={e => {
                    setDgTarget(e.target.value); void loadDiagram(dgKind, e.target.value)
                  }}
                  className="h-8 rounded-md border border-[var(--color-border)] bg-[var(--color-bg-base)] px-2 text-xs outline-none"
                >
                  <option value="">
                    {targetSpec === 'scenario' ? '默认场景（第一个）' : '自动选择对象'}
                  </option>
                  {targetOptions.map(n => <option key={n} value={n}>{n}</option>)}
                </select>
              )}
            </div>
            <div className="flex-1 overflow-auto px-5 py-4">
              {dgBusy && (
                <div className="flex items-center gap-2 text-xs text-[var(--color-text-tertiary)]">
                  <Loader2 size={13} className="animate-spin" /> 生成中…
                </div>
              )}
              {dgError && <div className="text-xs text-[var(--color-danger)]">{dgError}</div>}
              {!dgBusy && !dgError && dgMermaid && (
                <MermaidBlock chart={dgMermaid} title={dgTitle} warnings={dgWarnings} compact={false} />
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
