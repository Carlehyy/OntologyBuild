import { useCallback, useEffect, useRef, useState } from 'react'
import {
  Box, Users, Play, Zap, Scale, Map as MapIcon, ChevronDown, ChevronRight, CircleAlert,
  CircleCheck, CircleHelp, GitBranch, Share2, ShieldAlert, ShieldCheck, Copy, Loader2, FileText,
} from 'lucide-react'
import {
  explorationApi, type BusinessCanvas, type BxQuestion, type CanvasElement,
  type Completeness, type DiagramKind, type Readiness,
} from '@/api/exploration'
import MermaidBlock from '@/components/MermaidBlock'
import { writeTextToClipboard } from '@/utils/clipboard'
import ElementDetailView from './ElementDetailView'
import {
  DIAGRAM_TABS, canvasProcessNames, diagramTargetOptions, diagramTargetPlaceholder,
  elementBadges, type CanvasKey,
} from './canvasPanelLogic'

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
  { key: 'processes', label: '流程模型', icon: GitBranch, tint: 'text-indigo-600 bg-indigo-50' },
  { key: 'scenarios', label: '场景模型', icon: MapIcon, tint: 'text-emerald-600 bg-emerald-50' },
]

const errorMessage = (error: unknown, fallback: string): string => {
  if (!error || typeof error !== 'object') return fallback
  const value = error as { detail?: string | { message?: string }; message?: string }
  return typeof value.detail === 'string' ? value.detail : value.detail?.message || value.message || fallback
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

type TreeSelection =
  | { kind: 'section'; key: CanvasKey }
  | { kind: 'diagram' }

/**
 * 业务场景视图：左侧模型目录树（七类模型 + 底部「图示」目录），右侧内容展示。
 * 质量门与澄清账本置顶在目录树上方，随 SSE canvas 事件实时刷新。
 */
export default function CanvasPanel({ sessionId, canvas, completeness, readiness, onAsk }: {
  sessionId?: string
  canvas: BusinessCanvas | null
  completeness: Completeness | null
  readiness?: Readiness | null
  onAsk?: (text: string) => void
}) {
  const [sel, setSel] = useState<TreeSelection>({ kind: 'section', key: 'objects' })
  const [expanded, setExpanded] = useState<Record<string, boolean>>({})
  const [elementStack, setElementStack] = useState<{ key: CanvasKey; el: CanvasElement }[]>([])
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

  // 每次切换会话都回到对象模型总览；图示请求代际递增防串会话。
  useEffect(() => {
    setSel({ kind: 'section', key: 'objects' })
    setExpanded({})
    setElementStack([])
    setDgKind('er')
    setDgTarget('')
    setDgMermaid('')
    setDgWarnings([])
    setDgError('')
    setDgBusy(false)
    dgRequestSeq.current += 1
  }, [sessionId])

  const scenarioNames = (canvas?.scenarios || []).map(s => String(s.display_name || s.name))
  const processNames = canvasProcessNames(canvas)
  const objectNames = (canvas?.objects || [])
    .filter(o => ((o.attributes as { name?: string; display_name?: string; enum?: string[] }[] | undefined) || [])
      .some(a => (a.enum?.length || 0) > 0 && /状态|阶段|status|state|stage/i.test(`${a.name || ''}${a.display_name || ''}`)))
    .map(o => String(o.display_name || o.name))

  const loadDiagram = useCallback(async (kind: DiagramKind, target: string) => {
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
  }, [sessionId])

  // 选中「图示」目录且尚无内容时自动加载当前图种
  useEffect(() => {
    if (sel.kind === 'diagram' && !dgMermaid && !dgBusy && !dgError) {
      void loadDiagram(dgKind, dgTarget)
    }
  }, [sel.kind, dgMermaid, dgBusy, dgError, dgKind, dgTarget, loadDiagram])

  const targetSpec = DIAGRAM_TABS.find(t => t.kind === dgKind)?.needsTarget
  const targetOptions = diagramTargetOptions(targetSpec, { scenarioNames, objectNames, processNames })
  const topElement = elementStack[elementStack.length - 1] || null

  const selectSection = (key: CanvasKey) => {
    setElementStack([])
    setSel({ kind: 'section', key })
    setExpanded(e => ({ ...e, [key]: !(e[key] ?? true) }))
  }
  const openElement = (key: CanvasKey, el: CanvasElement) => {
    setSel({ kind: 'section', key })
    setElementStack([{ key, el }])
  }

  return (
    <div className="workspace-topology-surface flex h-full min-h-0" data-testid="business-scenario-region">
      {/* 左：模型目录树 */}
      <div className="flex w-60 shrink-0 flex-col border-r border-[var(--color-border)] bg-slate-50/55">
        <div className="flex-1 space-y-1.5 overflow-y-auto p-2">
          {readiness && total > 0 && <GatePanel readiness={readiness} />}
          <LedgerPanel questions={canvas?.questions || []} onAsk={onAsk} />

          <div className="px-2 pt-1 text-[10px] font-semibold tracking-[0.1em] text-[var(--color-text-tertiary)]">业务模型</div>
          {SECTIONS.map(section => {
            const { key, label, icon: Icon, tint } = section
            const items = (canvas?.[key] || []) as CanvasElement[]
            const selected = sel.kind === 'section' && sel.key === key
            const isExpanded = expanded[key] ?? true
            return (
              <div key={key}>
                <button
                  onClick={() => selectSection(key)}
                  aria-expanded={isExpanded}
                  className={`w-full flex items-center gap-2 rounded-md px-2 py-1.5 text-left transition-colors ${selected
                    ? 'bg-teal-50 text-teal-800'
                    : 'text-[var(--color-text-primary)] hover:bg-[var(--color-bg-hover)]'}`}
                >
                  <span className={`flex h-5 w-5 shrink-0 items-center justify-center rounded-md ${tint}`}>
                    <Icon size={12} />
                  </span>
                  <span className="flex-1 truncate text-xs font-medium">{label}</span>
                  <span className="text-[11px] text-[var(--color-text-tertiary)]">{items.length}</span>
                  {isExpanded
                    ? <ChevronDown size={12} className="shrink-0 text-[var(--color-text-tertiary)]" />
                    : <ChevronRight size={12} className="shrink-0 text-[var(--color-text-tertiary)]" />}
                </button>
                {isExpanded && items.length > 0 && (
                  <div className="ml-[18px] mt-0.5 space-y-0.5 border-l border-[var(--color-border)] pl-2">
                    {items.map(el => {
                      const activeElement = topElement?.key === key && topElement.el.id === el.id
                      return (
                        <button
                          key={el.id}
                          onClick={() => openElement(key, el)}
                          title="查看详情"
                          className={`block w-full truncate rounded px-2 py-1 text-left text-[11px] transition-colors ${activeElement
                            ? 'bg-teal-50 font-medium text-teal-800'
                            : 'text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text-primary)]'}`}
                        >
                          {el.display_name || el.name}
                        </button>
                      )
                    })}
                  </div>
                )}
              </div>
            )
          })}
        </div>

        {/* 目录树底部：图示（由画布确定性生成的 ER/流程/时序/状态图） */}
        <div className="shrink-0 border-t border-[var(--color-border)] p-2">
          <button
            onClick={() => { setElementStack([]); setSel({ kind: 'diagram' }) }}
            disabled={!sessionId || (counts.objects || 0) === 0}
            title={(counts.objects || 0) === 0 ? '画布还没有对象模型' : '从画布确定性生成 ER/流程/时序/状态图（不经 LLM）'}
            data-testid="business-flow-button"
            className={`w-full flex items-center gap-2 rounded-md px-2 py-1.5 text-left transition-colors disabled:opacity-40 ${sel.kind === 'diagram'
              ? 'bg-emerald-50 text-emerald-800'
              : 'text-[var(--color-text-primary)] hover:bg-[var(--color-bg-hover)]'}`}
          >
            <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-md bg-emerald-50 text-emerald-600">
              <Share2 size={12} />
            </span>
            <span className="flex-1 truncate text-xs font-medium">图示</span>
          </button>
        </div>
      </div>

      {/* 右：内容展示 */}
      <div className="min-h-0 min-w-0 flex-1">
        {topElement ? (
          <ElementDetailView
            sectionKey={topElement.key}
            el={topElement.el}
            canvas={canvas}
            onBack={() => setElementStack(stack => stack.slice(0, -1))}
            onNavigate={(key, el) => {
              setElementStack(stack => {
                const current = stack[stack.length - 1]
                if (current?.key === key && current.el.id === el.id) return stack
                return [...stack, { key, el }]
              })
            }}
          />
        ) : sel.kind === 'diagram' ? (
          /* 图示视图：ER/流程/时序/状态，全部由画布确定性生成 */
          <div className="flex h-full min-h-0 flex-col" data-testid="canvas-diagram-pane">
            <div className="flex shrink-0 items-center justify-between gap-3 border-b border-[var(--color-border)] px-5 py-3">
              <div className="min-w-0">
                <div data-testid="canvas-diagram-title" className="text-sm font-semibold text-[var(--color-text-primary)] truncate">
                  {dgTitle || '业务建模图表'}
                </div>
                <div className="mt-0.5 text-[11px] text-[var(--color-text-tertiary)]">
                  由画布确定性生成（不经 LLM），与画布严格一致
                </div>
              </div>
              {dgMermaid && (
                <button
                  onClick={() => { void writeTextToClipboard(dgMermaid).catch(() => undefined) }}
                  className="inline-flex shrink-0 items-center gap-1 rounded-md border border-[var(--color-border)] px-2.5 py-1.5 text-xs text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)]"
                >
                  <Copy size={12} /> 复制源码
                </button>
              )}
            </div>
            <div className="flex shrink-0 items-center gap-2 border-b border-[var(--color-border)] px-5 py-2.5">
              <div className="flex overflow-hidden rounded-md border border-[var(--color-border)]">
                {DIAGRAM_TABS.map(t => (
                  <button
                    key={t.kind}
                    onClick={() => {
                      setDgKind(t.kind); setDgTarget(''); void loadDiagram(t.kind, '')
                    }}
                    className={`px-3 py-1.5 text-xs transition-colors ${dgKind === t.kind
                      ? 'bg-teal-600 font-medium text-white'
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
                    {diagramTargetPlaceholder(targetSpec)}
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
        ) : (
          /* 分组总览：该模型的元素清单，点击进入元素详情 */
          (() => {
            const section = SECTIONS.find(s => s.key === (sel.kind === 'section' ? sel.key : 'objects'))!
            const items = (canvas?.[section.key] || []) as CanvasElement[]
            const { icon: SectionIcon, tint } = section
            return (
              <div className="flex h-full min-h-0 flex-col">
                <div className="flex h-14 shrink-0 items-center gap-2.5 border-b border-[var(--color-border)] bg-white px-5">
                  <span className={`flex h-7 w-7 items-center justify-center rounded-lg ${tint}`}>
                    <SectionIcon size={15} />
                  </span>
                  <div className="text-sm font-semibold text-[var(--color-text-primary)]">{section.label}</div>
                  <span className="text-xs text-[var(--color-text-tertiary)]">共 {items.length} 项</span>
                </div>
                <div className="flex-1 space-y-2 overflow-y-auto px-5 py-4">
                  {items.length === 0 && (
                    <div className="flex h-full flex-col items-center justify-center gap-2 text-center">
                      <FileText size={20} className="text-[var(--color-text-tertiary)]" />
                      <p className="text-xs text-[var(--color-text-tertiary)]">
                        还没有{section.label}内容，在右侧对话中澄清业务后自动沉淀。
                      </p>
                    </div>
                  )}
                  {items.map(el => (
                    <button
                      key={el.id}
                      onClick={() => openElement(section.key, el)}
                      title="查看详情"
                      className="group block w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-elevated)] px-3.5 py-2.5 text-left transition-colors hover:border-teal-300 hover:bg-teal-50/40"
                    >
                      <div className="flex items-center gap-1 text-xs font-medium text-[var(--color-text-primary)]">
                        <span className="truncate">
                          {el.display_name || el.name}
                          {el.display_name && el.display_name !== el.name && (
                            <span className="ml-1.5 font-mono text-[11px] font-normal text-[var(--color-text-tertiary)]">{el.name}</span>
                          )}
                        </span>
                        <ChevronRight size={12} className="ml-auto shrink-0 text-[var(--color-text-tertiary)] opacity-0 transition-opacity group-hover:opacity-100" />
                      </div>
                      {(el.description || elementBadges(section.key, el).length > 0) && (
                        <div className="mt-1 flex flex-wrap items-center gap-1">
                          {elementBadges(section.key, el).map((b, i) => (
                            <span key={i} className="rounded bg-black/[0.04] px-1.5 py-px text-[10px] text-[var(--color-text-secondary)]">{b}</span>
                          ))}
                          {el.description && (
                            <span className="max-w-full truncate text-[11px] text-[var(--color-text-tertiary)]">{el.description}</span>
                          )}
                        </div>
                      )}
                    </button>
                  ))}
                </div>
              </div>
            )
          })()
        )}
      </div>
    </div>
  )
}
