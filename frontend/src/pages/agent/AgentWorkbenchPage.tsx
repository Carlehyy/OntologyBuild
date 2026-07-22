/**
 * 智能助手 — 在本体授权边界内检索、推理与行动
 *
 * 参考 Palantir AIP 的 agent×ontology 机制：
 *   - agent 的世界 = 边界配置授权的对象 / 链接 / 事实 / 动作（技能卡注入）
 *   - 每一步工具调用实时展示（可审计的推理轨迹）
 *   - 回答带对象引用；改数据只出「提案卡」，用户确认 + HITL 审批才真执行
 */
import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import dagre from '@dagrejs/dagre'
import { useNavigate } from 'react-router-dom'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import * as XLSX from 'xlsx'
import {
  Send, Bot, User, Sparkles, Boxes, Link2, Zap, Shield, History, Search, Eye,
  GitBranch, Sigma, ScrollText, ListChecks, FlaskConical, Plus, Loader2,
  AlertTriangle, BadgeCheck, FileSearch, PenLine, Network,
  FunctionSquare, Minus, Maximize2, KeyRound, X, Download, ExternalLink,
  ChevronRight, Copy, Check, List, ArrowLeftRight, FileText, Workflow,
  BellRing,
} from 'lucide-react'
import { LoadingState } from '@/components/ui/LoadingState'
import SessionHistoryPopover from '@/components/SessionHistoryPopover'
import { ontologyApi, modelApi } from '@/api/ontologies'
import { useAuthStore } from '@/stores/authStore'
import { writeTextToClipboard } from '@/utils/clipboard'
import {
  agentApi, streamAgentChat,
  type AgentCapabilities, type AgentStep, type AgentCitation, type AgentProposal,
} from '@/api/agent'
import { ProposalCard } from './ProposalCard'
import { SentinelProposalCard } from './SentinelProposalCard'
import { BoundaryDrawer } from './BoundaryDrawer'
import { DynamicSentinelDrawer } from './DynamicSentinelDrawer'
import { ChartBlock, AgentChart, type ChartSpec } from './AgentChart'
import type { GraphAssistantSignal } from './InstanceKnowledgeGraph'
import { useOntologyStore } from '../../palantir-graph/store/ontologyStore'
import type {
  Action,
  LinkType,
  ObjectType,
  OntologyFunction,
} from '../../palantir-graph/types/ontology'
import { objectTypeIconGlyph } from '../../palantir-graph/utils/objectTypeIcon'

const InstanceKnowledgeGraph = lazy(() => import('./InstanceKnowledgeGraph'))

interface ChatMsg {
  id: string
  role: 'user' | 'assistant'
  content: string
  steps: AgentStep[]
  citations: AgentCitation[]
  proposals: AgentProposal[]
  loading?: boolean
  error?: string
}

let _mid = 0
const nextId = () => `m-${Date.now()}-${_mid++}`

const clamp = (value: number, min: number, max: number) => Math.min(Math.max(value, min), max)
const itemLabel = (item: { displayName?: string; name?: string }) => item.displayName || item.name || '未命名'
const selectArrow = "url(\"data:image/svg+xml;charset=utf-8,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='10' viewBox='0 0 24 24' fill='none' stroke='%23999' stroke-width='2'%3E%3Cpath d='m6 9 6 6 6-6'/%3E%3C/svg%3E\")"

function useAssistantLayout() {
  const containerRef = useRef<HTMLDivElement>(null)
  const [sizes, setSizes] = useState<[number, number]>([40, 60])

  const startResize = useCallback((event: React.PointerEvent<HTMLDivElement>) => {
    event.preventDefault()
    const rect = containerRef.current?.getBoundingClientRect()
    if (!rect) return

    const startX = event.clientX
    const start = sizes
    const min: [number, number] = [28, 42]
    const pairTotal = start[0] + start[1]
    const prevCursor = document.body.style.cursor
    const prevUserSelect = document.body.style.userSelect
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'

    const onMove = (moveEvent: PointerEvent) => {
      const delta = ((moveEvent.clientX - startX) / rect.width) * 100
      const left = clamp(start[0] + delta, min[0], pairTotal - min[1])
      setSizes([left, pairTotal - left])
    }

    const onUp = () => {
      document.body.style.cursor = prevCursor
      document.body.style.userSelect = prevUserSelect
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
    }

    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
  }, [sizes])

  return { containerRef, sizes, startResize }
}

// ---------- Markdown 渲染（回答里的表格 / 列表 / 代码 / 图表） ----------

/** 把 react-markdown 传给 <pre> 的 <code> 子节点还原成纯文本 */
const codeText = (c: any): string =>
  typeof c === 'string' ? c
    : Array.isArray(c) ? c.map(codeText).join('')
    : c?.props?.children ? codeText(c.props.children)
    : ''

function Md({ text }: { text: string }) {
  return (
    <div className="agent-md">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          p: p => <p className="text-sm leading-[1.7] mb-2 last:mb-0" {...p} />,
          strong: p => <strong className="font-semibold text-[var(--color-text-primary)]" {...p} />,
          h1: p => <h3 className="text-sm font-semibold mt-3 mb-1.5" {...p} />,
          h2: p => <h3 className="text-sm font-semibold mt-3 mb-1.5" {...p} />,
          h3: p => <h4 className="text-sm font-semibold mt-2 mb-1" {...p} />,
          ul: p => <ul className="list-disc pl-5 mb-2 space-y-1" {...p} />,
          ol: p => <ol className="list-decimal pl-5 mb-2 space-y-1" {...p} />,
          li: p => <li className="text-sm leading-relaxed" {...p} />,
          code: p => <code className="px-1 py-0.5 rounded bg-black/[0.05] text-[12px] font-mono" {...p} />,
          pre: (p: any) => {
            const child = Array.isArray(p.children) ? p.children[0] : p.children
            const lang = /language-(\w+)/.exec(child?.props?.className || '')?.[1]
            if (lang === 'chart') return <ChartBlock source={codeText(child?.props?.children)} />
            return <pre className="p-3 my-2 rounded-lg bg-black/[0.04] text-[12px] font-mono overflow-x-auto" {...p} />
          },
          table: p => (
            <div className="overflow-x-auto my-2 rounded-lg border border-[var(--color-border)]">
              <table className="w-full text-xs border-collapse" {...p} />
            </div>
          ),
          thead: p => <thead className="bg-[var(--color-bg-base)]" {...p} />,
          th: p => <th className="px-3 py-1.5 text-left font-medium text-[var(--color-text-secondary)] border-b border-[var(--color-border)] whitespace-nowrap" {...p} />,
          td: p => <td className="px-3 py-1.5 border-b border-[var(--color-border)]" {...p} />,
          a: p => <a className="text-[var(--color-primary)] underline-offset-2 hover:underline" {...p} />,
          blockquote: p => <blockquote className="border-l-2 border-[var(--color-border)] pl-3 my-2 text-[var(--color-text-secondary)]" {...p} />,
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  )
}

// ---------- 推理轨迹时间线 ----------

const TOOL_META: Record<string, { label: string; icon: React.ElementType }> = {
  search_objects: { label: '检索对象', icon: Search },
  get_object: { label: '查看详情', icon: Eye },
  traverse_links: { label: '遍历关系', icon: GitBranch },
  traverse_path: { label: '多跳遍历', icon: Network },
  find_paths: { label: '查找路径', icon: Network },
  analyze_change_impact: { label: '关联影响预演', icon: FlaskConical },
  aggregate_objects: { label: '聚合统计', icon: Sigma },
  get_object_history: { label: '事实溯源', icon: ScrollText },
  list_actions: { label: '查看动作', icon: ListChecks },
  list_dynamic_sentinels: { label: '查看动态哨兵', icon: BellRing },
  propose_dynamic_sentinel_change: { label: '校验哨兵提案', icon: BellRing },
  propose_action: { label: '预演提案', icon: FlaskConical },
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false)
  return (
    <button
      type="button"
      onClick={e => {
        e.stopPropagation()
        writeTextToClipboard(text).then(() => {
          setCopied(true)
          setTimeout(() => setCopied(false), 1200)
        }).catch(() => {})
      }}
      className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] text-[var(--color-text-tertiary)] transition-colors hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text-secondary)]"
      title="复制"
    >
      {copied ? <Check size={11} /> : <Copy size={11} />}{copied ? '已复制' : '复制'}
    </button>
  )
}

function JsonBlock({ label, value }: { label: string; value: unknown }) {
  const text = useMemo(() => {
    if (typeof value === 'string') return value
    try { return JSON.stringify(value, null, 2) } catch { return String(value) }
  }, [value])
  return (
    <div className="overflow-hidden rounded-md border border-[var(--color-border)] bg-[var(--color-bg-elevated)]">
      <div className="flex items-center justify-between border-b border-[var(--color-border)] px-2 py-1">
        <span className="text-[10px] font-medium uppercase tracking-wide text-[var(--color-text-tertiary)]">{label}</span>
        <CopyButton text={text} />
      </div>
      <pre className="max-h-56 overflow-auto whitespace-pre-wrap break-words px-2.5 py-2 font-mono text-[11px] leading-[1.6] text-[var(--color-text-secondary)]">{text}</pre>
    </div>
  )
}

function StepRow({ step }: { step: AgentStep }) {
  const [open, setOpen] = useState(false)
  const meta = TOOL_META[step.tool] || { label: step.tool, icon: Zap }
  const Icon = meta.icon
  const hasResult = step.result !== undefined && step.result !== null && step.result !== ''

  return (
    <div>
      <button
        type="button"
        onClick={() => setOpen(o => !o)}
        className="group flex w-full cursor-pointer items-start gap-2.5 text-left"
      >
        <div className={`mt-px flex h-5 w-5 shrink-0 items-center justify-center rounded-md ${step.error
          ? 'bg-[var(--color-danger-bg)] text-[var(--color-danger)]'
          : 'bg-sky-50 text-sky-600'}`}>
          <Icon size={11} />
        </div>
        <div className="min-w-0 flex-1 text-xs leading-5">
          <span className={`font-medium ${step.error ? 'text-[var(--color-danger)]' : 'text-[var(--color-text-primary)]'}`}>
            {meta.label}
          </span>
          <span className="text-[var(--color-text-tertiary)]"> · {step.summary}</span>
          {typeof step.durationMs === 'number' && (
            <span className="text-[var(--color-text-tertiary)]/70"> · {step.durationMs}ms</span>
          )}
        </div>
        <ChevronRight
          size={13}
          className={`mt-0.5 shrink-0 text-[var(--color-text-tertiary)] transition-transform group-hover:text-[var(--color-text-secondary)] ${open ? 'rotate-90' : ''}`}
        />
      </button>
      {open && (
        <div className="ml-[30px] mt-2 space-y-2">
          {/* 输入、输出都可展开查看；输出缺失时给出占位说明而非隐藏 */}
          <JsonBlock label="输入" value={step.arguments ?? {}} />
          {hasResult ? (
            <JsonBlock label="输出" value={step.result} />
          ) : (
            <div className="rounded-md border border-dashed border-[var(--color-border)] bg-[var(--color-bg-elevated)] px-2.5 py-2">
              <span className="mb-1 block text-[10px] font-medium uppercase tracking-wide text-[var(--color-text-tertiary)]">输出</span>
              <span className="text-[11px] text-[var(--color-text-tertiary)]">
                暂无输出记录 —— 该消息可能来自本次升级前，重新提问即可看到完整输出。
              </span>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function StepTrace({ steps, running }: { steps: AgentStep[]; running?: boolean }) {
  if (steps.length === 0 && !running) return null
  return (
    <div className="mb-3 space-y-2 rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-base)] px-3 py-2.5">
      {steps.map((s, i) => <StepRow key={i} step={s} />)}
      {running && (
        <div className="flex items-center gap-2.5">
          <div className="w-5 h-5 rounded-md bg-sky-50 flex items-center justify-center shrink-0">
            <Loader2 size={11} className="animate-spin text-sky-600" />
          </div>
          <span className="text-xs text-[var(--color-text-tertiary)]">
            {steps.length === 0 ? '正在阅读本体技能卡，规划查询…' : '正在综合工具结果继续推理…'}
          </span>
        </div>
      )}
    </div>
  )
}

// ---------- 从工具轨迹派生的输出增强（确定性图表 / 出处条） ----------

/** 收集 aggregate_objects 步骤里后端确定性生成的图表（数字来自真实结果，非 LLM 手写） */
function collectCharts(steps: AgentStep[]): ChartSpec[] {
  const out: ChartSpec[] = []
  for (const s of steps) {
    const chart = (s.result as any)?.chart
    if (chart && typeof chart === 'object' && Array.isArray(chart.data)) out.push(chart as ChartSpec)
  }
  return out
}

/** 「出处条」：从已持久化的 steps + citations 统计跑了什么、看了多少、耗时多少 */
function ProvenanceBar({ steps, cited }: { steps: AgentStep[]; cited: number }) {
  if (!steps.length) return null
  let scanned = 0
  let durationMs = 0
  for (const s of steps) {
    if (typeof s.durationMs === 'number') durationMs += s.durationMs
    const r = s.result as any
    if (r && typeof r === 'object' && !r._truncated) {
      const n = r.scanned ?? r.total ?? r.returned
      if (typeof n === 'number') scanned += n
    }
  }
  const bits = [
    `${steps.length} 次工具调用`,
    scanned > 0 ? `扫描 ${scanned.toLocaleString('zh-CN')} 行` : null,
    cited > 0 ? `引用 ${cited} 个对象` : null,
    durationMs > 0 ? `耗时 ${durationMs} ms` : null,
  ].filter(Boolean)
  return (
    <div className="mt-2 flex items-center gap-1.5 text-[10.5px] text-[var(--color-text-tertiary)]">
      <FileSearch size={11} className="shrink-0 opacity-70" />
      <span>{bits.join(' · ')}</span>
    </div>
  )
}

function AgentCallChainView({ messages, conversationId, ontologyName, running }: {
  messages: ChatMsg[]
  conversationId: string | null
  ontologyName: string
  running: boolean
}) {
  const turns = useMemo(() => {
    let question = ''
    let turn = 0
    return messages.reduce<Array<{ turn: number; question: string; message: ChatMsg }>>((result, message) => {
      if (message.role === 'user') {
        question = message.content
        return result
      }
      turn += 1
      if (message.steps.length > 0 || message.content || message.loading || message.error) {
        result.push({ turn, question, message })
      }
      return result
    }, [])
  }, [messages])
  const allSteps = turns.flatMap(item => item.message.steps)
  const totalDuration = allSteps.reduce((sum, step) => sum + (step.durationMs || 0), 0)

  if (turns.length === 0) {
    return (
      <div className="flex h-full items-center justify-center bg-[#f8fbff] p-8 text-center">
        <div className="max-w-sm">
          <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-xl border border-sky-100 bg-white text-sky-600 shadow-sm">
            <Workflow size={21} />
          </div>
          <h3 className="mt-4 text-sm font-semibold text-slate-800">当前会话还没有调用记录</h3>
          <p className="mt-1 text-xs leading-5 text-slate-500">发起一次业务查询后，这里会按执行顺序记录工具、输入、输出、结果与耗时。</p>
        </div>
      </div>
    )
  }

  return (
    <div className="scrollbar-thin h-full overflow-y-auto bg-[#f8fbff] px-4 py-4" data-testid="agent-call-chain-view">
      <div className="mx-auto max-w-4xl">
        <div className="grid grid-cols-3 gap-2 rounded-xl border border-slate-200 bg-white p-2.5 shadow-sm">
          {[
            ['执行轮次', `${turns.length}`],
            ['工具调用', `${allSteps.length}`],
            ['累计耗时', totalDuration > 0 ? `${totalDuration.toLocaleString('zh-CN')} ms` : '—'],
          ].map(([label, value]) => (
            <div key={label} className="rounded-lg bg-slate-50 px-3 py-2">
              <p className="text-[10px] text-slate-400">{label}</p>
              <p className="mt-0.5 font-mono text-sm font-semibold text-slate-700">{value}</p>
            </div>
          ))}
        </div>
        <div className="mt-2 flex items-center justify-between px-1 text-[10px] text-slate-400">
          <span>{ontologyName} · 当前会话完整执行记录</span>
          <span className="font-mono">{conversationId ? `会话 ${conversationId.slice(0, 8)}` : '会话建立中'}</span>
        </div>

        <div className="relative mt-4 space-y-3 before:absolute before:bottom-4 before:left-[18px] before:top-4 before:w-px before:bg-slate-200">
          {turns.map(({ turn, question, message }, index) => {
            const duration = message.steps.reduce((sum, step) => sum + (step.durationMs || 0), 0)
            return (
              <details key={message.id} open={index === turns.length - 1} className="group relative rounded-xl border border-slate-200 bg-white shadow-sm">
                <summary className="flex cursor-pointer list-none items-start gap-3 px-3 py-3 marker:content-none">
                  <span className={`relative z-10 flex h-9 w-9 shrink-0 items-center justify-center rounded-lg font-mono text-[11px] font-semibold ${message.error
                    ? 'bg-red-50 text-red-600' : 'bg-teal-50 text-teal-700'}`}>
                    {String(turn).padStart(2, '0')}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-xs font-semibold text-slate-800">{question || '系统续执行'}</span>
                    <span className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-[10px] text-slate-400">
                      <span>{message.steps.length} 次工具调用</span>
                      <span>{duration > 0 ? `${duration.toLocaleString('zh-CN')} ms` : '等待耗时数据'}</span>
                      {message.loading && <span className="inline-flex items-center gap-1 text-sky-600"><Loader2 size={10} className="animate-spin" />执行中</span>}
                      {message.error && <span className="text-red-600">执行异常</span>}
                    </span>
                  </span>
                  <ChevronRight size={14} className="mt-1 shrink-0 text-slate-400 transition-transform group-open:rotate-90" />
                </summary>
                <div className="border-t border-slate-100 px-4 pb-4 pt-3">
                  {message.steps.length > 0 ? (
                    <div className="space-y-3">
                      {message.steps.map((step, stepIndex) => (
                        <div key={stepIndex} className="rounded-lg border border-slate-100 bg-slate-50/70 px-3 py-2.5">
                          <StepRow step={step} />
                        </div>
                      ))}
                    </div>
                  ) : (
                    <div className="rounded-lg border border-dashed border-slate-200 px-3 py-3 text-xs text-slate-400">
                      {message.loading ? '正在规划并等待第一次工具调用…' : '本轮未调用工具，直接生成答复。'}
                    </div>
                  )}
                  {(message.content || message.error) && (
                    <div className={`mt-3 rounded-lg border px-3 py-2.5 ${message.error ? 'border-red-100 bg-red-50' : 'border-teal-100 bg-teal-50/50'}`}>
                      <p className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-slate-400">本轮执行结果</p>
                      {message.error
                        ? <p className="text-xs text-red-600">{message.error}</p>
                        : <div className="max-h-72 overflow-y-auto text-slate-700"><Md text={message.content} /></div>}
                    </div>
                  )}
                </div>
              </details>
            )
          })}
          {running && <div className="relative z-10 ml-1 flex items-center gap-2 text-[11px] text-sky-600"><Loader2 size={12} className="animate-spin" />执行链持续写入中</div>}
        </div>
      </div>
    </div>
  )
}

function SplitHandle({ onPointerDown }: { onPointerDown: (event: React.PointerEvent<HTMLDivElement>) => void }) {
  return (
    <div
      role="separator"
      aria-orientation="vertical"
      onPointerDown={onPointerDown}
      className="group col-start-2 row-start-1 flex cursor-col-resize items-center justify-center"
    >
      <div className="h-16 w-1 rounded-full bg-[var(--color-border)] transition-all group-hover:h-24 group-hover:bg-teal-500/70" />
    </div>
  )
}

const NETWORK_PALETTE = [
  { fill: '#eff8ff', stroke: '#38bdf8', accent: '#0284c7', soft: '#e0f2fe' },
  { fill: '#f0fdfa', stroke: '#2dd4bf', accent: '#0f766e', soft: '#ccfbf1' },
  { fill: '#fffbeb', stroke: '#fbbf24', accent: '#b45309', soft: '#fef3c7' },
  { fill: '#f5f3ff', stroke: '#a78bfa', accent: '#7c3aed', soft: '#ede9fe' },
  { fill: '#fff1f2', stroke: '#fb7185', accent: '#e11d48', soft: '#ffe4e6' },
  { fill: '#f0fdf4', stroke: '#4ade80', accent: '#15803d', soft: '#dcfce7' },
]

const trimLabel = (text: string, max = 16) => text.length > max ? `${text.slice(0, max - 1)}…` : text
const NETWORK_CARD_WIDTH = 316
const NETWORK_CARD_HEIGHT = 368
const NETWORK_NODE_GAP = 104
const NETWORK_RANK_GAP = 164
const NETWORK_MARGIN_X = 96
const NETWORK_MARGIN_Y = 96

function edgeAnchor(from: { x: number; y: number }, to: { x: number; y: number }) {
  const dx = to.x - from.x
  const dy = to.y - from.y
  if (dx === 0 && dy === 0) return { x: from.x + NETWORK_CARD_WIDTH / 2 - 18, y: from.y }
  const sx = dx === 0 ? Infinity : (NETWORK_CARD_WIDTH / 2 - 14) / Math.abs(dx)
  const sy = dy === 0 ? Infinity : (NETWORK_CARD_HEIGHT / 2 - 14) / Math.abs(dy)
  const t = Math.min(sx, sy)
  return { x: from.x + dx * t, y: from.y + dy * t }
}

function propertyLabel(prop: ObjectType['properties'][number]) {
  return (prop as any).displayName || (prop as any).display_name || prop.name
}

function OntologyNetworkView({
  objectTypes,
  linkTypes,
  actions,
  functions,
  instancesCount,
  instances,
  oid,
}: {
  objectTypes: ObjectType[]
  linkTypes: LinkType[]
  actions: Action[]
  functions: OntologyFunction[]
  instancesCount: (objectTypeId: string) => number
  instances: any[]
  oid: string
}) {
  const navigate = useNavigate()
  const [viewport, setViewport] = useState({ zoom: 1, pan: { x: 0, y: 0 } })
  const { zoom, pan } = viewport
  const svgRef = useRef<SVGSVGElement>(null)
  const dragging = useRef(false)
  const lastPos = useRef({ x: 0, y: 0 })
  const [instanceModal, setInstanceModal] = useState<{ open: boolean; objectTypeId: string; objectTypeLabel: string }>({ open: false, objectTypeId: '', objectTypeLabel: '' })
  const [instanceModalPage, setInstanceModalPage] = useState(0)
  const [instanceModalPageSize, setInstanceModalPageSize] = useState(20)
  const [instanceModalFilterCol, setInstanceModalFilterCol] = useState('')
  const [instanceModalFilterKw, setInstanceModalFilterKw] = useState('')
  const [instanceModalJump, setInstanceModalJump] = useState('')
  const [columnWidths, setColumnWidths] = useState<Record<string, number>>({})
  const resizeRef = useRef<{ col: string; startX: number; startW: number } | null>(null)
  const degreeByObject = useMemo(() => {
    const degree = new Map<string, number>()
    objectTypes.forEach(o => degree.set(o.id, 0))
    linkTypes.forEach(link => {
      degree.set(link.sourceObjectTypeId, (degree.get(link.sourceObjectTypeId) || 0) + 1)
      degree.set(link.targetObjectTypeId, (degree.get(link.targetObjectTypeId) || 0) + 1)
    })
    return degree
  }, [linkTypes, objectTypes])

  const linksByObject = useMemo(() => {
    const links = new Map<string, LinkType[]>()
    objectTypes.forEach(o => links.set(o.id, []))
    linkTypes.forEach(link => {
      links.set(link.sourceObjectTypeId, [...(links.get(link.sourceObjectTypeId) || []), link])
      if (link.targetObjectTypeId !== link.sourceObjectTypeId) {
        links.set(link.targetObjectTypeId, [...(links.get(link.targetObjectTypeId) || []), link])
      }
    })
    return links
  }, [linkTypes, objectTypes])

  const actionsByObject = useMemo(() => {
    const grouped = new Map<string, Action[]>()
    objectTypes.forEach(o => grouped.set(o.id, []))
    actions.forEach(action => grouped.set(action.objectTypeId, [...(grouped.get(action.objectTypeId) || []), action]))
    return grouped
  }, [actions, objectTypes])

  const functionsByObject = useMemo(() => {
    const grouped = new Map<string, OntologyFunction[]>()
    objectTypes.forEach(o => grouped.set(o.id, []))
    functions.forEach(fn => {
      if (fn.targetObjectTypeId) grouped.set(fn.targetObjectTypeId, [...(grouped.get(fn.targetObjectTypeId) || []), fn])
    })
    return grouped
  }, [functions, objectTypes])

  const graph = useMemo(() => {
    const sortedObjects = [...objectTypes].sort((a, b) => {
      const degreeDiff = (degreeByObject.get(b.id) || 0) - (degreeByObject.get(a.id) || 0)
      return degreeDiff || itemLabel(a).localeCompare(itemLabel(b))
    })
    const layout = new dagre.graphlib.Graph().setDefaultEdgeLabel(() => ({}))
    layout.setGraph({
      rankdir: 'TB',
      ranker: 'network-simplex',
      nodesep: NETWORK_NODE_GAP,
      ranksep: NETWORK_RANK_GAP,
      marginx: NETWORK_MARGIN_X,
      marginy: NETWORK_MARGIN_Y,
    })
    sortedObjects.forEach(objectType => {
      layout.setNode(objectType.id, { width: NETWORK_CARD_WIDTH, height: NETWORK_CARD_HEIGHT })
    })
    linkTypes.forEach(link => {
      if (layout.hasNode(link.sourceObjectTypeId) && layout.hasNode(link.targetObjectTypeId)) {
        layout.setEdge(link.sourceObjectTypeId, link.targetObjectTypeId)
      }
    })
    dagre.layout(layout)

    const layoutMeta = layout.graph() as { width?: number; height?: number }
    const layoutWidth = Math.max(Number(layoutMeta.width) || 0, NETWORK_CARD_WIDTH + NETWORK_MARGIN_X * 2)
    const layoutHeight = Math.max(Number(layoutMeta.height) || 0, NETWORK_CARD_HEIGHT + NETWORK_MARGIN_Y * 2)
    const width = Math.max(820, layoutWidth)
    const height = Math.max(640, layoutHeight)
    const offsetX = (width - layoutWidth) / 2
    const offsetY = (height - layoutHeight) / 2
    const cx = width / 2
    const cy = height / 2
    const positions = new Map<string, { x: number; y: number; colorIndex: number }>()
    sortedObjects.forEach((objectType, index) => {
      const node = layout.node(objectType.id)
      positions.set(objectType.id, {
        x: (node?.x ?? layoutWidth / 2) + offsetX,
        y: (node?.y ?? layoutHeight / 2) + offsetY,
        colorIndex: index % NETWORK_PALETTE.length,
      })
    })

    return { width, height, cx, cy, positions }
  }, [degreeByObject, linkTypes, objectTypes])

  const objectById = useMemo(() => new Map(objectTypes.map(o => [o.id, o])), [objectTypes])
  const graphSignature = useMemo(
    () => `${objectTypes.map(item => item.id).sort().join(',')}|${linkTypes.map(item => item.id).sort().join(',')}`,
    [linkTypes, objectTypes],
  )
  const zoomPercent = Math.round(zoom * 100)
  const setZoomLevel = (next: number) => {
    const nextZoom = clamp(Number(next.toFixed(2)), 0.2, 1.8)
    setViewport(current => ({ ...current, zoom: nextZoom }))
  }
  const resetViewport = useCallback(() => {
    setViewport({ zoom: 1, pan: { x: 0, y: 0 } })
  }, [])

  useEffect(() => {
    resetViewport()
  }, [graphSignature, resetViewport])

  useEffect(() => {
    const svg = svgRef.current
    if (!svg) return

    const onWheel = (event: WheelEvent) => {
      event.preventDefault()
      const screenMatrix = svg.getScreenCTM()
      if (!screenMatrix) return
      const cursor = svg.createSVGPoint()
      cursor.x = event.clientX
      cursor.y = event.clientY
      const point = cursor.matrixTransform(screenMatrix.inverse())

      setViewport(current => {
        const factor = Math.exp(-event.deltaY * 0.0012)
        const nextZoom = clamp(Number((current.zoom * factor).toFixed(3)), 0.2, 1.8)
        if (nextZoom === current.zoom) return current
        const ratio = nextZoom / current.zoom
        return {
          zoom: nextZoom,
          pan: {
            x: point.x - graph.cx - ratio * (point.x - current.pan.x - graph.cx),
            y: point.y - graph.cy - ratio * (point.y - current.pan.y - graph.cy),
          },
        }
      })
    }

    svg.addEventListener('wheel', onWheel, { passive: false })
    return () => svg.removeEventListener('wheel', onWheel)
  }, [graph.cx, graph.cy])

  if (objectTypes.length === 0) {
    return (
      <div className="flex h-full flex-col items-center justify-center bg-gradient-to-br from-slate-50 via-sky-50/60 to-emerald-50/50 px-6 text-center">
        <div className="mb-4 flex h-14 w-14 items-center justify-center rounded-2xl border border-sky-100 bg-white text-sky-500 shadow-sm">
          <Network size={24} />
        </div>
        {oid ? (
          <>
            <h3 className="text-sm font-semibold text-slate-800">当前本体暂无可视化对象</h3>
            <p className="mt-1 text-xs text-slate-500">
              在建模工作区配置对象实体和实体关系后，这里将自动展示本体拓扑图
            </p>
            <button
              onClick={() => navigate(`/ontologies/${oid}`)}
              className="mt-4 inline-flex items-center gap-1.5 rounded-lg border border-teal-200 bg-teal-50 px-4 py-2 text-sm font-medium text-teal-600 transition-colors hover:bg-teal-100"
            >
              <ExternalLink size={14} />前往本体模型工作台
            </button>
          </>
        ) : (
          <h3 className="text-sm font-semibold text-slate-600">请在上方选择本体</h3>
        )}
      </div>
    )
  }

  return (
    <div className="workspace-topology-surface relative h-full overflow-hidden">
      <div className="absolute inset-x-4 top-4 z-10 flex flex-nowrap items-center justify-center gap-2">
        {[
          { icon: Boxes, label: `${objectTypes.length} 对象实体`, className: 'border-sky-100 bg-white/88 text-sky-700' },
          { icon: Link2, label: `${linkTypes.length} 实体关系`, className: 'border-cyan-100 bg-white/88 text-cyan-700' },
          { icon: Zap, label: `${actions.length} 执行动作`, className: 'border-amber-100 bg-white/88 text-amber-700' },
          { icon: FunctionSquare, label: `${functions.length} 激活函数`, className: 'border-violet-100 bg-white/88 text-violet-700' },
        ].map(stat => (
          <span key={stat.label} className={`inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-full border px-2.5 py-1 text-[11px] font-medium shadow-sm backdrop-blur ${stat.className}`}>
            <stat.icon size={12} />{stat.label}
          </span>
        ))}
      </div>

      <div className="absolute bottom-6 left-1/2 z-10 flex -translate-x-1/2 items-center overflow-hidden rounded-lg border border-slate-200 bg-white/90 shadow-sm backdrop-blur">
        <button
          type="button"
          onClick={() => setZoomLevel(zoom - 0.1)}
          className="flex h-8 w-8 items-center justify-center text-slate-500 transition-colors hover:bg-slate-50 hover:text-slate-900"
          title="缩小"
          aria-label="缩小网络图"
        >
          <Minus size={14} />
        </button>
        <div data-testid="ontology-zoom-level" className="min-w-12 border-x border-slate-100 px-2 text-center text-[11px] font-semibold tabular-nums text-slate-600">
          {zoomPercent}%
        </div>
        <button
          type="button"
          onClick={() => setZoomLevel(zoom + 0.1)}
          className="flex h-8 w-8 items-center justify-center text-slate-500 transition-colors hover:bg-slate-50 hover:text-slate-900"
          title="放大"
          aria-label="放大网络图"
        >
          <Plus size={14} />
        </button>
        <button
          type="button"
          onClick={resetViewport}
          className="flex h-8 w-8 items-center justify-center border-l border-slate-100 text-slate-500 transition-colors hover:bg-slate-50 hover:text-slate-900"
          title="重置视图"
          aria-label="重置网络图视图"
        >
          <Maximize2 size={14} />
        </button>
      </div>

      <div className="pointer-events-none absolute bottom-6 left-4 z-10 hidden items-center gap-1.5 rounded-md border border-slate-200/80 bg-white/80 px-2.5 py-1.5 text-[10px] font-medium text-slate-500 shadow-sm backdrop-blur lg:flex">
        滚轮缩放 · 按住拖拽 · 双击复位
      </div>

      <svg
        ref={svgRef}
        className="relative z-0 h-full w-full touch-none cursor-grab select-none active:cursor-grabbing"
        viewBox={`0 0 ${graph.width} ${graph.height}`}
        role="img"
        aria-label="本体拓扑图"
        onPointerDown={(e) => {
          if (e.button !== 0 || (e.target as Element).closest('button, input, select, textarea, a')) return
          e.preventDefault()
          e.currentTarget.setPointerCapture(e.pointerId)
          dragging.current = true
          lastPos.current = { x: e.clientX, y: e.clientY }
        }}
        onPointerMove={(e) => {
          if (!dragging.current) return
          const screenMatrix = e.currentTarget.getScreenCTM()
          const screenScale = screenMatrix ? Math.hypot(screenMatrix.a, screenMatrix.b) : 1
          const dx = (e.clientX - lastPos.current.x) / Math.max(screenScale, 0.001)
          const dy = (e.clientY - lastPos.current.y) / Math.max(screenScale, 0.001)
          lastPos.current = { x: e.clientX, y: e.clientY }
          setViewport(current => ({
            ...current,
            pan: { x: current.pan.x + dx, y: current.pan.y + dy },
          }))
        }}
        onPointerUp={(e) => {
          dragging.current = false
          if (e.currentTarget.hasPointerCapture(e.pointerId)) e.currentTarget.releasePointerCapture(e.pointerId)
        }}
        onPointerCancel={() => { dragging.current = false }}
        onDoubleClick={(e) => {
          if ((e.target as Element).closest('button, input, select, textarea, a')) return
          resetViewport()
        }}
      >
        <defs>
          <pattern id="ontology-grid" width="56" height="56" patternUnits="userSpaceOnUse">
            <path d="M 56 0 L 0 0 0 56" fill="none" stroke="#e2e8f0" strokeWidth="1" opacity="0.55" />
          </pattern>
          <marker id="ontology-arrow" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto" markerUnits="strokeWidth">
            <path d="M0,0 L0,6 L8,3 z" fill="#94a3b8" />
          </marker>
          <filter id="node-soft-shadow" x="-20%" y="-20%" width="140%" height="150%">
            <feDropShadow dx="0" dy="10" stdDeviation="12" floodColor="#0f172a" floodOpacity="0.12" />
          </filter>
        </defs>
        <rect width={graph.width} height={graph.height} fill="url(#ontology-grid)" opacity="0.55" />
        <g data-testid="ontology-network-viewport" transform={`translate(${pan.x} ${pan.y}) translate(${graph.cx} ${graph.cy}) scale(${zoom}) translate(${-graph.cx} ${-graph.cy})`}>
          {linkTypes.map((link, index) => {
            const source = graph.positions.get(link.sourceObjectTypeId)
            const target = graph.positions.get(link.targetObjectTypeId)
            if (!source || !target) return null
            const sourceObject = objectById.get(link.sourceObjectTypeId)
            const targetObject = objectById.get(link.targetObjectTypeId)
            const label = itemLabel(link)
            const self = link.sourceObjectTypeId === link.targetObjectTypeId
            const start = edgeAnchor(source, target)
            const end = edgeAnchor(target, source)
            const dx = end.x - start.x
            const dy = end.y - start.y
            const distance = Math.max(Math.hypot(dx, dy), 1)
            const direction = index % 2 === 0 ? 1 : -1
            const offset = Math.min(96, Math.max(34, distance * 0.13)) * direction
            const cpx = (start.x + end.x) / 2 - (dy / distance) * offset
            const cpy = (start.y + end.y) / 2 + (dx / distance) * offset
            const path = self
              ? `M ${source.x + NETWORK_CARD_WIDTH / 2 - 20} ${source.y - 34} C ${source.x + NETWORK_CARD_WIDTH / 2 + 112} ${source.y - 168}, ${source.x + NETWORK_CARD_WIDTH / 2 + 122} ${source.y + 142}, ${source.x + NETWORK_CARD_WIDTH / 2 - 20} ${source.y + 42}`
              : `M ${start.x} ${start.y} Q ${cpx} ${cpy} ${end.x} ${end.y}`
            const labelX = start.x * 0.25 + cpx * 0.5 + end.x * 0.25
            const labelY = start.y * 0.25 + cpy * 0.5 + end.y * 0.25

            return (
              <g key={link.id}>
                <path
                  d={path}
                  fill="none"
                  stroke="#94a3b8"
                  strokeWidth="2.2"
                  strokeLinecap="round"
                  markerEnd="url(#ontology-arrow)"
                  opacity="0.68"
                />
                {!self && (
                  <text
                    x={labelX}
                    y={labelY - 8}
                    textAnchor="middle"
                    className="fill-slate-500 text-[10px] font-medium"
                  >
                    <title>{`${sourceObject ? itemLabel(sourceObject) : link.sourceObjectTypeId} → ${targetObject ? itemLabel(targetObject) : link.targetObjectTypeId}`}</title>
                    {trimLabel(label, 12)}
                  </text>
                )}
              </g>
            )
          })}

          {objectTypes.map(objectType => {
            const position = graph.positions.get(objectType.id)
            if (!position) return null
            const palette = NETWORK_PALETTE[position.colorIndex]
            const iconGlyph = objectTypeIconGlyph(objectType.icon)
            const degree = degreeByObject.get(objectType.id) || 0
            const instances = instancesCount(objectType.id)
            const visibleProperties = objectType.properties.slice(0, 4)
            const remainingProperties = objectType.properties.length - visibleProperties.length
            const relatedLinks = linksByObject.get(objectType.id) || []
            const actionItems = actionsByObject.get(objectType.id) || []
            const functionItems = functionsByObject.get(objectType.id) || []
            return (
              <foreignObject
                key={objectType.id}
                x={position.x - NETWORK_CARD_WIDTH / 2}
                y={position.y - NETWORK_CARD_HEIGHT / 2}
                width={NETWORK_CARD_WIDTH}
                height={NETWORK_CARD_HEIGHT}
                className="overflow-visible"
              >
                <div
                  data-testid="ontology-network-node"
                  data-object-type-id={objectType.id}
                  className="flex h-full flex-col overflow-hidden rounded-[18px] border bg-white shadow-[0_18px_42px_rgba(15,23,42,0.12)] backdrop-blur transition-transform duration-200 hover:-translate-y-0.5"
                  style={{ borderColor: palette.stroke, background: `linear-gradient(145deg, ${palette.fill} 0%, rgba(255,255,255,0.98) 58%, #ffffff 100%)` }}
                  title={`${itemLabel(objectType)} · ${objectType.properties.length} 属性 · ${degree} 关系`}
                >
                  <div
                    className="flex items-start gap-3 border-b px-4 py-3 rounded-t-[18px]"
                    style={{ borderColor: `${palette.stroke}55`, background: `linear-gradient(135deg, ${palette.soft}, rgba(255,255,255,0.72))` }}
                  >
                    <div
                      className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl text-white shadow-sm"
                      style={{ backgroundColor: objectType.color || palette.accent }}
                    >
                      <span aria-hidden="true" data-testid="ontology-network-node-icon" className="text-[19px] leading-none">{iconGlyph}</span>
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="flex min-w-0 items-center justify-between gap-2">
                        <div className="min-w-0">
                          <div className="truncate text-[15px] font-semibold text-slate-900">{trimLabel(itemLabel(objectType), 18)}</div>
                          <div className="truncate font-mono text-[11px] text-slate-500">{trimLabel(objectType.name, 24)}</div>
                        </div>
                        <button
                          onClick={(e) => { e.stopPropagation(); setInstanceModal({ open: true, objectTypeId: objectType.id, objectTypeLabel: itemLabel(objectType) }); setInstanceModalPage(0); setInstanceModalFilterCol(''); setInstanceModalFilterKw(''); setInstanceModalPageSize(20); setInstanceModalJump('') }}
                          className="shrink-0 rounded-full bg-white/75 px-2 py-0.5 text-[10px] font-semibold text-slate-500 shadow-sm hover:bg-teal-50 hover:text-teal-600 transition-colors cursor-pointer">
                          {instances} 实例
                        </button>
                      </div>
                      {objectType.description && (
                        <div className="mt-1 truncate text-[10px] text-slate-500">{trimLabel(objectType.description, 32)}</div>
                      )}
                    </div>
                  </div>

                  <div className="flex-1 px-4 py-3">
                    <div className="mb-2 flex items-center justify-between">
                      <span className="text-[10px] font-semibold uppercase tracking-[0.08em] text-slate-400">实体属性</span>
                      <span className="text-[10px] font-medium text-slate-400">{objectType.properties.length} 项</span>
                    </div>
                    <div className="space-y-1.5">
                      {visibleProperties.map(prop => {
                        const isPrimary = prop.id === objectType.primaryKey || prop.name === objectType.primaryKey
                        return (
                          <div key={prop.id} className="flex items-center justify-between gap-2 rounded-lg bg-white/70 px-2.5 py-1.5 text-[11px] shadow-sm ring-1 ring-slate-200/70">
                            <div className="flex min-w-0 items-center gap-1.5">
                              {isPrimary && <KeyRound size={12} className="shrink-0 text-amber-500" />}
                              <span className={`truncate ${prop.required ? 'font-medium text-slate-700' : 'text-slate-500'}`}>{propertyLabel(prop)}</span>
                            </div>
                            <span className="shrink-0 rounded-md bg-slate-100 px-1.5 py-0.5 font-mono text-[10px] text-slate-500">{prop.type}</span>
                          </div>
                        )
                      })}
                      {remainingProperties > 0 && (
                        <div className="pl-1 text-[10px] font-medium text-slate-400">+ {remainingProperties} 更多实体属性</div>
                      )}
                      {objectType.properties.length === 0 && (
                        <div className="rounded-lg border border-dashed border-slate-200 bg-white/50 px-2.5 py-2 text-[11px] text-slate-400">暂无实体属性</div>
                      )}
                    </div>
                  </div>

                  <div className="space-y-2 border-t border-slate-200/70 bg-white/72 px-4 py-3">
                    <div className="flex min-w-0 items-center gap-1.5">
                      <Link2 size={12} className="shrink-0 text-cyan-500" />
                      <span className="shrink-0 text-[10px] font-semibold text-slate-400">实体关系</span>
                      <div className="flex min-w-0 flex-1 gap-1 overflow-hidden">
                        {relatedLinks.slice(0, 2).map(link => (
                          <span key={link.id} className="truncate rounded-full border border-cyan-100 bg-cyan-50 px-2 py-0.5 text-[10px] font-medium text-cyan-700">
                            {trimLabel(itemLabel(link), 8)}
                          </span>
                        ))}
                        {relatedLinks.length === 0 && <span className="text-[10px] text-slate-400">暂无实体关系</span>}
                        {relatedLinks.length > 2 && <span className="text-[10px] font-medium text-slate-400">+{relatedLinks.length - 2}</span>}
                      </div>
                    </div>
                      <div className="flex min-w-0 items-center gap-1.5">
                      <Zap size={12} className="shrink-0 text-amber-500" />
                      <span className="shrink-0 text-[10px] font-semibold text-slate-400">执行动作</span>
                      <div className="flex min-w-0 flex-1 gap-1 overflow-hidden">
                        {actionItems.slice(0, 2).map(action => (
                          <span key={action.id} className="truncate rounded-full border border-amber-100 bg-amber-50 px-2 py-0.5 text-[10px] font-medium text-amber-700">
                            {trimLabel(itemLabel(action), 8)}
                          </span>
                        ))}
                        {actionItems.length === 0 && <span className="text-[10px] text-slate-400">暂无执行动作</span>}
                        {actionItems.length > 2 && <span className="text-[10px] font-medium text-slate-400">+{actionItems.length - 2}</span>}
                      </div>
                    </div>
                    <div className="flex min-w-0 items-center gap-1.5">
                      <FunctionSquare size={12} className="shrink-0 text-violet-500" />
                      <span className="shrink-0 text-[10px] font-semibold text-slate-400">激活函数</span>
                      <div className="flex min-w-0 flex-1 gap-1 overflow-hidden">
                        {functionItems.slice(0, 2).map(fn => (
                          <span key={fn.id} className="truncate rounded-full border border-violet-100 bg-violet-50 px-2 py-0.5 text-[10px] font-medium text-violet-700">
                            {trimLabel(itemLabel(fn), 8)}
                          </span>
                        ))}
                        {functionItems.length === 0 && <span className="text-[10px] text-slate-400">暂无激活函数</span>}
                        {functionItems.length > 2 && <span className="text-[10px] font-medium text-slate-400">+{functionItems.length - 2}</span>}
              </div>
            </div>
          </div>
                </div>
              </foreignObject>
            )
          })}
        </g>
      </svg>

      {instanceModal.open && (
        <div className="absolute inset-0 z-20 flex items-center justify-center bg-white/60 backdrop-blur-sm" onClick={() => setInstanceModal({ open: false, objectTypeId: '', objectTypeLabel: '' })}>
          <div className="mx-4 max-h-[75vh] w-full max-w-3xl overflow-hidden rounded-xl border border-slate-200 bg-gradient-to-b from-white to-slate-50/50 shadow-lg" onClick={e => e.stopPropagation()}>
            {(() => {
              const objType = objectTypes.find(o => o.id === instanceModal.objectTypeId)
              const displayProperties = objType?.properties?.slice(0, 8) || []
              const valueLabel = (inst: any, prop: any) => {
                const v = inst.properties?.[prop.name]
                if (v === null || v === undefined) return '-'
                if (typeof v === 'object') return JSON.stringify(v).slice(0, 60)
                return String(v).slice(0, 80)
              }
              const headerLabel = (prop: any) => {
                const cn = prop.displayName || prop.display_name || prop.name
                const id = prop.name
                return cn === id ? cn : `${cn}(${id})`
              }

              const applyFilter = () => {
                setInstanceModalPage(0)
              }

              let filteredInstances = instances.filter((i: any) => i.objectTypeId === instanceModal.objectTypeId)
              if (instanceModalFilterCol && instanceModalFilterKw) {
                const kw = instanceModalFilterKw.toLowerCase()
                filteredInstances = filteredInstances.filter((i: any) => {
                  const v = i.properties?.[instanceModalFilterCol]
                  if (v === null || v === undefined) return false
                  return String(v).toLowerCase().includes(kw)
                })
              }

              const totalPages = Math.max(1, Math.ceil(filteredInstances.length / instanceModalPageSize))
              const safePage = Math.min(instanceModalPage, totalPages - 1)
              const pageInstances = filteredInstances.slice(safePage * instanceModalPageSize, (safePage + 1) * instanceModalPageSize)

              return (
                <div className="flex max-h-[75vh] flex-col">
                  <div className="flex items-center justify-between gap-3 px-5 py-3 border-b border-slate-100">
                    <h3 className="text-base font-semibold text-slate-700">
                      {instanceModal.objectTypeLabel} · 实例数据
                    </h3>
                    <button onClick={() => setInstanceModal({ open: false, objectTypeId: '', objectTypeLabel: '' })}
                      className="flex h-8 w-8 items-center justify-center rounded-md text-slate-400 transition-colors hover:bg-slate-100 hover:text-slate-600">
                      <X size={16} />
                    </button>
                  </div>

                  <div className="flex items-center gap-2 px-5 py-2 border-b border-slate-100">
                    <select
                      value={instanceModalFilterCol}
                      onChange={e => setInstanceModalFilterCol(e.target.value)}
                      className="h-8 w-40 cursor-pointer appearance-none rounded-md border border-slate-200 bg-white pl-2.5 pr-6 text-xs text-slate-600 outline-none focus:border-teal-300 focus:ring-1 focus:ring-teal-100"
                      style={{ backgroundImage: `url("data:image/svg+xml;charset=utf-8,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='10' viewBox='0 0 24 24' fill='none' stroke='%2394a3b8' stroke-width='2'%3E%3Cpath d='m6 9 6 6 6-6'/%3E%3C/svg%3E")`, backgroundRepeat: 'no-repeat', backgroundPosition: 'right 6px center' }}
                    >
                      <option value="">全部列</option>
                      {displayProperties.map(prop => (
                        <option key={prop.name} value={prop.name}>{headerLabel(prop)}</option>
                      ))}
                    </select>
                    <input
                      value={instanceModalFilterKw}
                      onChange={e => setInstanceModalFilterKw(e.target.value)}
                      onKeyDown={e => { if (e.key === 'Enter' && instanceModalFilterCol) applyFilter() }}
                      placeholder={instanceModalFilterCol ? '输入关键词筛选…' : '请先选择筛选列'}
                      disabled={!instanceModalFilterCol}
                      className="h-8 flex-1 rounded-md border border-slate-200 bg-white px-3 text-xs text-slate-600 outline-none placeholder:text-slate-300 focus:border-teal-300 focus:ring-1 focus:ring-teal-100 disabled:bg-slate-50 disabled:text-slate-400"
                    />
                    <button
                      onClick={applyFilter}
                      disabled={!instanceModalFilterCol}
                      className="flex h-8 w-8 items-center justify-center rounded-md border border-slate-200 text-slate-400 transition-colors hover:border-teal-200 hover:bg-teal-50 hover:text-teal-500 disabled:opacity-40 disabled:cursor-not-allowed"
                    >
                      <Search size={14} />
                    </button>
                    <button
                      onClick={() => {
                        const exportData = filteredInstances.map(inst => {
                          const row: Record<string, any> = {}
                          displayProperties.forEach(prop => {
                            row[headerLabel(prop)] = valueLabel(inst, prop)
                          })
                          return row
                        })
                        const ws = XLSX.utils.json_to_sheet(exportData)
                        const wb = XLSX.utils.book_new()
                        XLSX.utils.book_append_sheet(wb, ws, '实例数据')
                        const now = new Date()
                        const pad = (n: number) => String(n).padStart(2, '0')
                        const filename = `${instanceModal.objectTypeLabel}-实例数据-${now.getFullYear()}-${pad(now.getMonth()+1)}-${pad(now.getDate())}-${pad(now.getHours())}-${pad(now.getMinutes())}.xlsx`
                        XLSX.writeFile(wb, filename)
                      }}
                      disabled={filteredInstances.length === 0}
                      className="group/tip relative flex h-8 w-8 items-center justify-center rounded-md border border-slate-200 text-slate-400 transition-colors hover:border-emerald-200 hover:bg-emerald-50 hover:text-emerald-500 disabled:opacity-30 disabled:cursor-not-allowed"
                    >
                      <Download size={14} />
                      <span className="pointer-events-none absolute -bottom-8 left-1/2 -translate-x-1/2 whitespace-nowrap rounded bg-gray-800 px-2 py-0.5 text-[11px] text-white opacity-0 transition-opacity group-hover/tip:opacity-100">导出 Excel</span>
                    </button>
                  </div>

                  <div
                    className="flex-1 overflow-auto px-5 py-3"
                    onMouseMove={(e) => {
                      if (!resizeRef.current) return
                      const dx = e.clientX - resizeRef.current.startX
                      const newW = Math.max(60, resizeRef.current.startW + dx)
                      setColumnWidths(prev => ({ ...prev, [resizeRef.current!.col]: newW }))
                    }}
                    onMouseUp={() => { resizeRef.current = null }}
                    onMouseLeave={() => { resizeRef.current = null }}
                  >
                    {filteredInstances.length === 0 ? (
                      <div className="rounded-lg border border-dashed border-slate-200 px-4 py-8 text-center text-sm text-slate-400">
                        {instanceModalFilterKw ? '无匹配实例' : '暂无实例数据'}
                      </div>
                    ) : (
                      <table className="w-full border-collapse text-xs table-fixed">
                        <thead>
                          <tr className="bg-slate-50">
                            {displayProperties.map(prop => (
                              <th key={prop.name}
                                className="relative px-3 py-2 text-left font-medium text-slate-500 border-b border-slate-200 whitespace-nowrap select-none"
                                style={{ width: columnWidths[prop.name] || 'auto' }}
                              >
                                {headerLabel(prop)}
                                <div
                                  className="absolute right-0 top-0 h-full w-1.5 cursor-col-resize border-r-2 border-slate-200 transition-colors hover:border-teal-300"
                                  onMouseDown={(e) => {
                                    e.preventDefault()
                                    const colEl = (e.target as HTMLElement).parentElement
                                    resizeRef.current = { col: prop.name, startX: e.clientX, startW: colEl?.offsetWidth || 120 }
                                  }}
                                />
                              </th>
                            ))}
                          </tr>
                        </thead>
                        <tbody>
                          {pageInstances.map((inst, idx) => (
                            <tr key={inst.id} className={idx % 2 === 0 ? 'bg-white' : 'bg-slate-50/50'}>
                              {displayProperties.map(prop => (
                                <td key={prop.name} className="px-3 py-1.5 border-b border-slate-100 text-slate-600 truncate"
                                  style={{ maxWidth: columnWidths[prop.name] || undefined }}>
                                  {valueLabel(inst, prop)}
                                </td>
                              ))}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    )}
                  </div>

                  <div className="flex items-center justify-between gap-3 px-5 py-3 border-t border-slate-100 bg-white">
                    <div className="flex items-center gap-2">
                      <span className="text-xs text-slate-400">每页</span>
                      <select
                        value={instanceModalPageSize}
                        onChange={e => { setInstanceModalPageSize(Number(e.target.value)); setInstanceModalPage(0) }}
                        className="h-7 cursor-pointer rounded border border-slate-200 bg-white px-1.5 text-xs text-slate-500 outline-none"
                      >
                        {[10, 20, 50, 100].map(n => (
                          <option key={n} value={n}>{n}</option>
                        ))}
                      </select>
                      <span className="text-xs text-slate-400">条</span>
                    </div>
                    <div className="flex items-center gap-2">
                      <button
                        onClick={() => setInstanceModalPage(p => Math.max(0, p - 1))}
                        disabled={safePage === 0}
                        className="px-2 py-1 rounded border border-slate-200 text-xs text-slate-500 hover:bg-slate-50 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
                      >上一页</button>
                      <span className="text-xs text-slate-500">
                        {safePage + 1} / {totalPages}
                      </span>
                      <button
                        onClick={() => setInstanceModalPage(p => Math.min(totalPages - 1, p + 1))}
                        disabled={safePage >= totalPages - 1}
                        className="px-2 py-1 rounded border border-slate-200 text-xs text-slate-500 hover:bg-slate-50 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
                      >下一页</button>
                    </div>
                    <div className="flex items-center gap-1.5">
                      <span className="text-xs text-slate-400">跳至</span>
                      <input
                        value={instanceModalJump}
                        onChange={e => setInstanceModalJump(e.target.value)}
                        onKeyDown={e => {
                          if (e.key === 'Enter') {
                            const n = parseInt(instanceModalJump, 10)
                            if (n >= 1 && n <= totalPages) { setInstanceModalPage(n - 1); setInstanceModalJump('') }
                          }
                        }}
                        placeholder={String(safePage + 1)}
                        className="h-7 w-12 rounded border border-slate-200 bg-white px-1.5 text-center text-xs text-slate-500 outline-none"
                      />
                      <span className="text-xs text-slate-400">页</span>
                    </div>
                  </div>
                </div>
              )
            })()}
          </div>
        </div>
      )}
    </div>
  )
}

// ---------- 页面 ----------

export default function AgentWorkbenchPage() {
  const isAdmin = useAuthStore(s => s.user?.role === 'admin')
  const navigate = useNavigate()
  const { containerRef, sizes, startResize } = useAssistantLayout()

  // -- 本体 / 模型选择 --
  const { data: ontologies = [], isLoading: ontologiesLoading } = useQuery({
    queryKey: ['ontologies'], queryFn: () => ontologyApi.list({ page_size: 1000 }) as any,
  })
  const ontologyList = useMemo(
    () => (ontologies as any)?.items || ontologies || [], [ontologies])
  // A project may have editable drafts while its immutable current release
  // remains queryable.  project.status is only a legacy compatibility field;
  // the release pointer is the authoritative assistant scope (including v0).
  const releasedOntologyList = useMemo(
    () => ontologyList.filter((item: any) => !!item.current_release_id), [ontologyList])
  const [oid, setOid] = useState('')
  const [workspaceView, setWorkspaceView] = useState<'ontology' | 'data' | 'trace'>('ontology')

  useEffect(() => {
    if (releasedOntologyList.length === 0) {
      if (oid) setOid('')
      return
    }
    if (oid && !releasedOntologyList.some((item: any) => item.id === oid)) setOid('')
  }, [releasedOntologyList, oid])

  const selectedOntology = releasedOntologyList.find((item: any) => item.id === oid)
  const releaseId = selectedOntology?.current_release_id || ''

  const { data: models = [] } = useQuery({ queryKey: ['models'], queryFn: () => modelApi.list() as any })
  const llmModels = Array.isArray(models) ? (models as any[]).filter((m: any) => m.config_type === 'llm' || !m.config_type) : []
  const [modelId, setModelId] = useState('')
  useEffect(() => {
    if (!modelId && llmModels.length > 0) setModelId(llmModels[0].id)
  }, [llmModels, modelId])

  // -- 本体结构（复用本体模型数据源，只读展示） --
  const loadFromBackend = useOntologyStore(s => s.loadFromBackend)
  const graphOntology = useOntologyStore(s => s.ontology)
  const backendId = useOntologyStore(s => s.backendId)
  const syncStatus = useOntologyStore(s => s.syncStatus)
  const syncError = useOntologyStore(s => s.syncError)

  useEffect(() => {
    if (!oid || !releaseId) return
    void loadFromBackend(oid, releaseId)
  }, [oid, releaseId, loadFromBackend])

  const modelReady = !!oid && backendId === oid && syncStatus !== 'loading' && !!graphOntology
  const modelOntology = modelReady ? graphOntology : null
  const objectTypes = modelOntology?.objectTypes || []
  const linkTypes = modelOntology?.linkTypes || []
  const actions = modelOntology?.actions || []
  const functions = modelOntology?.functions || []
  const instances = modelOntology?.instances || []
  const instancesCount = useCallback(
    (objectTypeId: string) => instances.filter(i => i.objectTypeId === objectTypeId).length,
    [instances],
  )

  // -- 能力边界 --
  const { data: caps } = useQuery<AgentCapabilities>({
    queryKey: ['agent-capabilities', oid, releaseId],
    queryFn: () => agentApi.capabilities(oid, releaseId),
    enabled: !!oid && !!releaseId,
  })
  const dynamicObjectTypes = useMemo(() => {
    if (!caps) return []
    const allowed = new Set(caps.objectTypes.map(item => item.id))
    return objectTypes.filter(item => allowed.has(item.id))
  }, [caps, objectTypes])
  const dynamicLinkTypes = useMemo(() => {
    if (!caps) return []
    const allowed = new Set(caps.linkTypes.map(item => item.id))
    return linkTypes.filter(item => allowed.has(item.id))
  }, [caps, linkTypes])
  const dynamicActions = useMemo(() => {
    if (!caps) return []
    const allowed = new Set(caps.actions.map(item => item.id))
    return actions.filter(item => allowed.has(item.id))
  }, [actions, caps])

  // -- 会话 --
  const [conversationId, setConversationId] = useState<string | null>(null)
  const [messages, setMessages] = useState<ChatMsg[]>([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [sentinelDrawerOpen, setSentinelDrawerOpen] = useState(false)
  const [showHistory, setShowHistory] = useState(false)
  const [showJump, setShowJump] = useState(false)
  const [graphSignal, setGraphSignal] = useState<GraphAssistantSignal | null>(null)
  const bottomRef = useRef<HTMLDivElement>(null)
  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [messages])

  // 我发送过的消息（用于「跳转到我的提问」）
  const myMessages = useMemo(() => messages.filter(m => m.role === 'user'), [messages])
  const jumpToMessage = useCallback((id: string) => {
    setShowJump(false)
    requestAnimationFrame(() => {
      document.getElementById(`agent-msg-${id}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    })
  }, [])

  const { data: conversations = [], refetch: refetchConversations } = useQuery({
    queryKey: ['agent-conversations', oid, releaseId],
    queryFn: () => agentApi.conversations(oid, releaseId),
    enabled: !!oid && !!releaseId,
  })

  const resetChat = useCallback(() => {
    setConversationId(null)
    setMessages([])
    setShowHistory(false)
    setShowJump(false)
    setGraphSignal(null)
  }, [])
  useEffect(() => { resetChat() }, [oid, releaseId, resetChat])

  const loadConversation = async (cid: string) => {
    const conv = await agentApi.conversation(oid, cid)
    const restoredMessages = (conv.messages || []).map(m => ({
      id: m.id, role: m.role, content: m.content,
      steps: m.steps || [], citations: m.citations || [], proposals: m.proposals || [],
    }))
    setConversationId(cid)
    setMessages(restoredMessages)
    const lastVisual = [...restoredMessages].reverse().find(message =>
      message.role === 'assistant' && (message.citations.length > 0 || message.steps.some(step => {
        const kind = (step.result as any)?.kind
        return kind === 'path' || kind === 'impact'
      })))
    if (lastVisual) {
      setWorkspaceView('data')
      setGraphSignal({ sequence: Date.now(), steps: lastVisual.steps, citations: lastVisual.citations })
    }
    setShowHistory(false)
  }

  const removeConversation = async (cid: string) => {
    await agentApi.deleteConversation(oid, cid)
    if (cid === conversationId) resetChat()
    refetchConversations()
  }

  const send = useCallback(async (text?: string) => {
    const question = (text ?? input).trim()
    if (!question || busy || !oid) return
    setInput('')
    setBusy(true)

    setMessages(prev => [...prev, {
      id: nextId(), role: 'user', content: question, steps: [], citations: [], proposals: [],
    }])
    const aid = nextId()
    setMessages(prev => [...prev, {
      id: aid, role: 'assistant', content: '', steps: [], citations: [], proposals: [], loading: true,
    }])
    const patch = (p: Partial<ChatMsg> | ((m: ChatMsg) => Partial<ChatMsg>)) =>
      setMessages(prev => prev.map(m =>
        m.id === aid ? { ...m, ...(typeof p === 'function' ? p(m) : p) } : m))

    const turnSteps: AgentStep[] = []
    try {
      await streamAgentChat(oid, {
        message: question, conversationId, modelId, releaseId,
      }, ev => {
        if (ev.type === 'meta') setConversationId(ev.conversationId)
        else if (ev.type === 'step') {
          const { type: _t, ...step } = ev
          const typedStep = step as AgentStep
          turnSteps.push(typedStep)
          patch(m => ({ steps: [...m.steps, typedStep] }))
          const kind = (typedStep.result as any)?.kind
          if (kind === 'path' || kind === 'impact') {
            setWorkspaceView('data')
            setGraphSignal({ sequence: Date.now(), steps: [...turnSteps], citations: [] })
          }
        } else if (ev.type === 'answer') {
          patch({ content: ev.content, citations: ev.citations || [], proposals: ev.proposals || [], loading: false })
          if ((ev.citations || []).length > 0 || turnSteps.some(step => ['path', 'impact'].includes((step.result as any)?.kind))) {
            setGraphSignal({ sequence: Date.now(), steps: [...turnSteps], citations: ev.citations || [] })
          }
        } else if (ev.type === 'error') {
          patch({ error: ev.message, loading: false })
        }
      })
    } catch (e: any) {
      patch({ error: e?.message || '请求失败', loading: false })
    } finally {
      setBusy(false)
      refetchConversations()
    }
  }, [busy, conversationId, input, modelId, oid, releaseId, refetchConversations])

  const suggested = useMemo<string[]>(() => {
    const first = caps?.objectTypes?.[0]?.displayName
    return first ? [
      '“' + first + '”有哪些实例？',
      '帮我寻找两个具体实例之间的关系路径',
      '分析一个字段拟议变化的直接和间接关联范围',
    ] : []
  }, [caps])

  if (ontologiesLoading) return <LoadingState message="加载配置..." />

  const panelClass = 'workspace-topology-surface min-h-0 min-w-0 overflow-hidden rounded-lg border border-[var(--color-border)] shadow-sm'
  const graphLoading = workspaceView === 'ontology' && !!oid && backendId === oid && syncStatus === 'loading'
  const graphError = workspaceView === 'ontology' && !!oid && backendId === oid && syncStatus === 'error' && !graphOntology

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden bg-[var(--color-bg-base)]">
      <div
        ref={containerRef}
        className="scrollbar-none grid flex-1 min-h-0 overflow-x-auto overflow-y-hidden p-1"
        style={{ gridTemplateColumns: `minmax(420px, ${sizes[0]}fr) 4px minmax(560px, ${sizes[1]}fr)` }}
      >
        {/* 2. 本体结构 / 数据推演图谱 */}
        <section data-testid="agent-ontology-panel" className={`${panelClass} col-start-3 row-start-1 flex flex-col`}>
          <div className="flex h-14 shrink-0 items-center border-b border-[var(--color-border)] bg-white px-4">
            <div className="flex w-full min-w-0 items-center justify-between gap-3">
              <div className="flex min-w-0 flex-1 items-center gap-2">
                <div className="flex h-8 w-8 items-center justify-center rounded-md bg-sky-50 text-sky-600">
                  {workspaceView === 'trace' ? <Workflow size={16} /> : <Network size={16} />}
                </div>
                <div className="min-w-0">
                  <h3 className="truncate text-sm font-semibold text-[var(--color-text-primary)]">
                    {workspaceView === 'ontology' ? '本体拓扑图' : workspaceView === 'data' ? '数据推演图谱' : 'Agent调用链'}
                  </h3>
                  <p className={`truncate text-[11px] ${workspaceView === 'ontology' && syncStatus === 'error' ? 'text-red-500' : 'text-[var(--color-text-tertiary)]'}`}>
                    {workspaceView === 'ontology' && syncStatus === 'error'
                      ? (syncError || '网络图加载失败。')
                      : workspaceView === 'ontology'
                        ? `${selectedOntology?.name || '未选择本体'} · 只读展示对象类型与关系`
                        : workspaceView === 'data'
                          ? `${selectedOntology?.name || '未选择本体'} · 实例、路径与拟议变更联动`
                          : `${selectedOntology?.name || '未选择本体'} · 当前会话工具调用可审计、可复盘`}
                  </p>
                </div>
              </div>
              <div className="flex shrink-0 items-center gap-2">
                <select
                  value={oid}
                  onChange={e => setOid(e.target.value)}
                  aria-label="选择本体"
                  className="h-8 min-w-[180px] cursor-pointer appearance-none rounded-md border border-[var(--color-border)] bg-[var(--color-bg-base)] bg-no-repeat pl-3 pr-8 text-xs text-[var(--color-text-primary)] outline-none transition-colors focus:border-teal-400 focus:ring-2 focus:ring-teal-100"
                  style={{ backgroundImage: selectArrow, backgroundPosition: 'right 10px center' }}
                >
                  {releasedOntologyList.length === 0 && <option value="">无已发布本体</option>}
                  {releasedOntologyList.length > 0 && <option value="">请选择已发布本体</option>}
                  {releasedOntologyList.map((o: any) => (
                    <option key={o.id} value={o.id}>
                      {o.name} · {o.current_release_version || o.version}
                    </option>
                  ))}
                </select>
                <div className="flex items-center rounded-md border border-slate-200 bg-slate-50 p-0.5" aria-label="切换工作台视图">
                  {([
                    { id: 'ontology', label: '本体拓扑图', icon: Network },
                    { id: 'data', label: '数据推演图谱', icon: ArrowLeftRight },
                    { id: 'trace', label: 'Agent调用链', icon: Workflow },
                  ] as const).map(item => (
                    <button
                      key={item.id}
                      type="button"
                      onClick={() => setWorkspaceView(item.id)}
                      disabled={!oid}
                      title={item.label}
                      aria-label={`切换到${item.label}`}
                      aria-pressed={workspaceView === item.id}
                      data-testid={item.id === 'data' ? 'workspace-view-toggle' : `workspace-view-${item.id}`}
                      className={`flex h-7 w-8 items-center justify-center rounded transition-colors disabled:cursor-not-allowed disabled:opacity-40 ${workspaceView === item.id
                        ? 'bg-white text-teal-700 shadow-sm' : 'text-slate-400 hover:bg-white/70 hover:text-slate-700'}`}
                    >
                      <item.icon size={13} />
                    </button>
                  ))}
                </div>
              </div>
            </div>
          </div>

          <div className="workspace-topology-surface relative min-h-0 flex-1 overflow-hidden">
            {workspaceView === 'trace' ? (
              <AgentCallChainView
                messages={messages}
                conversationId={conversationId}
                ontologyName={selectedOntology?.name || '当前本体'}
                running={busy}
              />
            ) : workspaceView === 'data' ? (
              <Suspense fallback={(
                <div className="flex h-full items-center justify-center gap-2 bg-slate-50 text-xs text-slate-500">
                  <Loader2 size={14} className="animate-spin text-teal-600" />正在加载数据图谱工作台…
                </div>
              )}>
                <InstanceKnowledgeGraph
                  oid={oid}
                  releaseId={releaseId}
                  assistantSignal={graphSignal}
                  onAskAssistant={question => void send(question)}
                />
              </Suspense>
            ) : graphLoading ? (
              <div className="flex h-full flex-col items-center justify-center gap-3 bg-slate-50 text-slate-500">
                <Loader2 size={22} className="animate-spin text-sky-500" />
                <span className="text-xs">正在加载本体网络…</span>
              </div>
            ) : graphError ? (
              <div className="flex h-full items-center justify-center p-6">
                <div className="max-w-sm rounded-lg border border-red-100 bg-red-50 px-4 py-3 text-sm text-red-600">
                  <div className="mb-1 flex items-center gap-2 font-medium"><AlertTriangle size={15} />图谱加载失败</div>
                  <p className="text-xs leading-relaxed text-red-500/80">{syncError || '请稍后刷新模型结构。'}</p>
                </div>
              </div>
            ) : (
              <OntologyNetworkView
                objectTypes={objectTypes}
                linkTypes={linkTypes}
                actions={actions}
                functions={functions}
                instancesCount={instancesCount}
                instances={instances}
                oid={oid}
              />
            )}
          </div>
        </section>

        <SplitHandle onPointerDown={startResize} />

        {/* 1. 智能对话 */}
        <section data-testid="agent-chat-panel" className={`${panelClass} col-start-1 row-start-1 flex flex-col`}>
          <div className="flex h-14 shrink-0 items-center border-b border-[var(--color-border)] bg-white px-4">
            <div className="flex w-full min-w-0 items-center justify-between gap-2">
              <div className="flex min-w-0 flex-1 items-center gap-2">
                <div className="flex shrink-0 h-8 w-8 items-center justify-center rounded-md bg-teal-50 text-teal-600">
                  <Bot size={18} />
                </div>
                <div className="min-w-0">
                  <h3 className="truncate text-sm font-semibold text-[var(--color-text-primary)]">智能对话</h3>
                  <p className="truncate text-[11px] text-[var(--color-text-tertiary)]">基于授权范围回答，并可生成行动提案</p>
                </div>
                {caps && !caps.enabled && (
                  <span className="inline-flex items-center gap-1 rounded-full bg-red-50 px-2 py-0.5 text-[11px] font-medium text-red-600">
                    <AlertTriangle size={11} />智能体已停用
                  </span>
                )}
              </div>
              <div className="flex shrink-0 items-center gap-1">
                <select
                  value={modelId}
                  onChange={e => setModelId(e.target.value)}
                  aria-label="选择对话模型"
                  disabled={!oid}
                  className="h-8 w-44 cursor-pointer appearance-none rounded-md border border-[var(--color-border)] bg-[var(--color-bg-base)] bg-no-repeat pl-2 pr-7 text-xs text-[var(--color-text-primary)] outline-none transition-colors focus:border-teal-400 focus:ring-2 focus:ring-teal-100 disabled:opacity-30 disabled:cursor-not-allowed"
                  style={{ backgroundImage: selectArrow, backgroundPosition: 'right 6px center', backgroundSize: '10px' }}
                >
                  {llmModels.map((m: any) => <option key={m.id} value={m.id}>{m.name}</option>)}
                </select>
                <button
                  type="button"
                  onClick={() => setSentinelDrawerOpen(true)}
                  disabled={!oid || !releaseId}
                  aria-label="管理动态哨兵"
                  data-testid="dynamic-sentinel-button"
                  className="group/tip relative flex h-8 w-8 items-center justify-center rounded-md border border-teal-200 bg-teal-50 text-teal-600 transition-colors hover:border-teal-300 hover:bg-teal-100 hover:text-teal-700 disabled:cursor-not-allowed disabled:opacity-30"
                >
                  <BellRing size={14} />
                  <span className="pointer-events-none absolute -bottom-8 left-1/2 -translate-x-1/2 whitespace-nowrap rounded bg-gray-800 px-2 py-0.5 text-[11px] text-white opacity-0 transition-opacity group-hover/tip:opacity-100">动态哨兵</span>
                </button>
                {isAdmin && (
                  <button onClick={() => setDrawerOpen(true)} disabled={!oid} aria-label="授权边界配置"
                    className="group/tip relative flex h-8 w-8 items-center justify-center rounded-md border border-teal-200 bg-teal-50 text-teal-500 transition-colors hover:border-teal-300 hover:bg-teal-100 hover:text-teal-700 disabled:opacity-30 disabled:cursor-not-allowed">
                    <Shield size={14} />
                    <span className="pointer-events-none absolute -bottom-8 left-1/2 -translate-x-1/2 whitespace-nowrap rounded bg-gray-800 px-2 py-0.5 text-[11px] text-white opacity-0 transition-opacity group-hover/tip:opacity-100">授权边界配置</span>
                  </button>
                )}
                <button
                  type="button"
                  onClick={() => navigate(oid
                    ? `/agent/reports/new?ontologyId=${encodeURIComponent(oid)}${conversationId ? `&conversationId=${encodeURIComponent(conversationId)}` : ''}`
                    : '/agent/reports')}
                  disabled={!oid}
                  aria-label={oid ? '生成分析报告' : '分析报告'}
                  className="group/tip relative flex h-8 w-8 items-center justify-center rounded-md border border-sky-200 bg-sky-50 text-sky-600 transition-colors hover:border-sky-300 hover:bg-sky-100 hover:text-sky-700 disabled:cursor-not-allowed disabled:opacity-30"
                  title={oid ? '基于当前本体和会话生成可编辑分析报告模板' : '打开分析报告工作台'}
                >
                  <FileText size={14} />
                  <span className="pointer-events-none absolute -bottom-8 left-1/2 -translate-x-1/2 whitespace-nowrap rounded bg-gray-800 px-2 py-0.5 text-[11px] text-white opacity-0 transition-opacity group-hover/tip:opacity-100">{oid ? '生成分析报告' : '分析报告'}</span>
                </button>
                <div className="relative">
                  <button
                    type="button"
                    onClick={() => setShowHistory(value => !value)}
                    disabled={!oid}
                    aria-label="查看历史会话"
                    aria-expanded={showHistory}
                    data-testid="agent-session-history-button"
                    className={`group/tip relative flex h-8 w-8 items-center justify-center rounded-md border transition-colors active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-30 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-400 ${showHistory
                      ? 'border-teal-400 bg-teal-100 text-teal-800'
                      : 'border-teal-200 bg-teal-50 text-teal-600 hover:border-teal-300 hover:bg-teal-100 hover:text-teal-700'}`}
                  >
                    <History size={14} />
                    <span className="pointer-events-none absolute -bottom-8 left-1/2 -translate-x-1/2 whitespace-nowrap rounded bg-gray-800 px-2 py-0.5 text-[11px] text-white opacity-0 transition-opacity group-hover/tip:opacity-100">查看历史会话</span>
                  </button>
                  <SessionHistoryPopover
                    open={showHistory}
                    items={conversations}
                    currentId={conversationId}
                    onClose={() => setShowHistory(false)}
                    onCreate={resetChat}
                    onSelect={loadConversation}
                    onDelete={removeConversation}
                    renderItemIcon={() => <Bot size={16} />}
                    emptyDescription="开始对话后，可随时回到之前的查询、分析与行动提案。"
                  />
                </div>
              </div>
            </div>
          </div>

          <div data-testid="agent-chat-region" className="workspace-topology-surface scrollbar-thin flex-1 overflow-auto px-4 py-4">
            {messages.length === 0 ? (
              <div className="flex min-h-full flex-col justify-center py-8 text-center anim-scale-in">
                <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-lg bg-teal-600 text-white shadow-sm">
                  <Sparkles size={22} />
                </div>
                <h3 className="mb-1 text-base font-semibold text-[var(--color-text-primary)]">
                   OntoAgent
                </h3>
                <p className="mx-auto mb-5 max-w-sm text-xs leading-relaxed text-[var(--color-text-tertiary)]">
                  基于本体的智能Agent，支持业务查询、风险分析、决策仿真与操作执行
                </p>

                <div className="mb-5 mx-auto grid w-80 grid-cols-2 gap-2 text-left">
                  {[
                    { icon: Sparkles, title: '开始对话', desc: '输入问题，基于本体数据获取智能回答' },
                    { icon: BadgeCheck, title: '有据可查', desc: '结论来自本体数据并附对象引用' },
                    { icon: FileSearch, title: '全程可溯', desc: '每步工具调用可展开，查看输入与输出' },
                    { icon: PenLine, title: '行动预演', desc: '真实修改前先预演提案与影响' },
                  ].map(f => (
                    <div key={f.title} className="rounded-md border border-[var(--color-border)] bg-white/70 p-3">
                      <div className="mb-1.5 flex items-center gap-2 text-xs font-semibold text-[var(--color-text-primary)]">
                        <f.icon size={14} className="text-teal-600 shrink-0" />{f.title}
                      </div>
                      <p className="text-[11px] leading-relaxed text-[var(--color-text-tertiary)]">{f.desc}</p>
                    </div>
                  ))}
                </div>

                <div className="flex flex-wrap justify-center gap-2">
                  {suggested.map(q => (
                    <button key={q} onClick={() => send(q)}
                      className="rounded-full border border-[var(--color-border)] bg-white px-3 py-1.5 text-xs text-[var(--color-text-secondary)] transition-all hover:border-teal-300 hover:bg-teal-50 hover:text-teal-700">
                      {q}
                    </button>
                  ))}
                </div>
              </div>
            ) : (
              <div className="space-y-5">
                {messages.map(msg => msg.role === 'user' ? (
                  <div key={msg.id} id={`agent-msg-${msg.id}`} className="flex scroll-mt-4 justify-end gap-3">
                    <div className="max-w-[88%] rounded-lg rounded-br-sm bg-teal-700 px-3.5 py-2.5 text-white shadow-sm">
                      <p className="whitespace-pre-line text-sm leading-relaxed">{msg.content}</p>
                    </div>
                    <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-md border border-teal-200 bg-teal-50 text-teal-700 shadow-sm">
                      <User size={14} />
                    </div>
                  </div>
                ) : (
                  <div key={msg.id} className="flex gap-3">
                    <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-teal-600 text-white shadow-sm">
                      <Bot size={14} />
                    </div>
                    <div className="min-w-0 flex-1">
                      <StepTrace steps={msg.steps} running={msg.loading} />
                      {msg.error ? (
                        <div className="rounded-lg border border-red-200 bg-red-50/70 px-4 py-3">
                          <p className="flex items-start gap-2 text-sm text-red-600">
                            <AlertTriangle size={14} className="mt-0.5 shrink-0" />{msg.error}
                          </p>
                        </div>
                      ) : msg.content ? (
                        <div className="text-[var(--color-text-primary)]">
                          <Md text={msg.content} />
                        </div>
                      ) : null}
                      {/* 确定性图表：数字来自工具真实结果，前端渲染，非 LLM 手写 */}
                      {collectCharts(msg.steps).map((c, i) => <AgentChart key={i} spec={c} />)}
                      {msg.citations.length > 0 && (
                        <div className="mt-2.5 flex flex-wrap items-center gap-1.5">
                          <span className="text-[10px] text-[var(--color-text-tertiary)]">引用</span>
                          {msg.citations.map(c => (
                            <button key={c.instanceId}
                              type="button"
                              onClick={() => {
                                setWorkspaceView('data')
                                setGraphSignal({ sequence: Date.now(), steps: msg.steps, citations: [c] })
                              }}
                              title={c.snippet
                                ? `${c.sourceLabel || `${c.objectType} · ${c.label}`} — ${c.snippet}`
                                : (c.sourceLabel || c.instanceId)}
                              className="inline-flex items-center gap-1 rounded-md border border-[var(--color-border)] bg-white px-2 py-0.5 text-[11px] text-[var(--color-text-secondary)] transition-colors hover:border-cyan-300 hover:bg-cyan-50">
                              <span className="text-[var(--color-text-tertiary)]">{c.objectType}</span>
                              <span className="font-medium text-[var(--color-text-primary)]">{c.label}</span>
                            </button>
                          ))}
                        </div>
                      )}
                      {msg.proposals.map(p => p.kind === 'sentinel'
                        ? <SentinelProposalCard key={p.proposalId} oid={oid} proposal={p} />
                        : <ProposalCard key={p.proposalId} oid={oid} proposal={p} />)}
                      {!msg.loading && !msg.error && <ProvenanceBar steps={msg.steps} cited={msg.citations.length} />}
                    </div>
                  </div>
                ))}
                <div ref={bottomRef} />
              </div>
            )}
          </div>

          <div className="border-t border-[var(--color-border)] bg-[var(--color-bg-elevated)] px-4 pb-3 pt-3">
            <div className="relative flex items-center gap-2 rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-base)] py-1.5 pl-3 pr-1.5 transition-all focus-within:border-teal-400 focus-within:ring-2 focus-within:ring-teal-100">
              <input
                placeholder={oid ? '问业务问题，或让它帮你预演一个操作…' : '请先选择一个本体'}
                value={input}
                onChange={e => setInput(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && send()}
                disabled={!oid || busy}
                className="min-w-0 flex-1 bg-transparent text-sm text-[var(--color-text-primary)] outline-none placeholder:text-[var(--color-text-tertiary)] disabled:opacity-50"
              />
              <button onClick={() => send()} disabled={!input.trim() || busy || !oid}
                className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-teal-700 text-white transition-all duration-200 hover:bg-teal-600 disabled:cursor-not-allowed disabled:opacity-25">
                {busy ? <Loader2 size={14} className="animate-spin" /> : <Send size={14} />}
              </button>
              <button
                type="button"
                onClick={() => setShowJump(v => !v)}
                disabled={myMessages.length === 0}
                title="我发送的消息 · 快速跳转"
                className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-md border transition-colors disabled:cursor-not-allowed disabled:opacity-30 ${showJump
                  ? 'border-teal-300 bg-teal-50 text-teal-700'
                  : 'border-[var(--color-border)] text-[var(--color-text-tertiary)] hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text-secondary)]'}`}>
                <List size={15} />
              </button>

              {showJump && (
                <>
                  <div className="fixed inset-0 z-20" onClick={() => setShowJump(false)} />
                  <div className="absolute bottom-full right-0 z-30 mb-2 w-72 overflow-hidden rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-elevated)] shadow-lg">
                    <div className="flex items-center justify-between border-b border-[var(--color-border)] px-3 py-2">
                      <span className="text-[11px] font-medium text-[var(--color-text-secondary)]">我发送的消息</span>
                      <span className="text-[10px] text-[var(--color-text-tertiary)]">点击跳转 · 共 {myMessages.length} 条</span>
                    </div>
                    <div className="scrollbar-thin max-h-64 overflow-auto py-1">
                      {myMessages.length === 0 ? (
                        <div className="px-3 py-4 text-center text-xs text-[var(--color-text-tertiary)]">当前会话暂无发送记录</div>
                      ) : (
                        [...myMessages].reverse().map((m, i) => (
                          <button
                            key={m.id}
                            onClick={() => jumpToMessage(m.id)}
                            className="flex w-full items-start gap-2 px-3 py-1.5 text-left transition-colors hover:bg-[var(--color-bg-hover)]"
                          >
                            <span className="mt-0.5 shrink-0 font-mono text-[10px] text-[var(--color-text-tertiary)]">#{myMessages.length - i}</span>
                            <span className="min-w-0 flex-1 truncate text-xs text-[var(--color-text-secondary)]">{m.content}</span>
                          </button>
                        ))
                      )}
                    </div>
                  </div>
                </>
              )}
            </div>
          </div>
        </section>
      </div>

      <BoundaryDrawer oid={oid} open={drawerOpen} onClose={() => setDrawerOpen(false)} />
      <DynamicSentinelDrawer
        oid={oid}
        releaseId={releaseId}
        open={sentinelDrawerOpen}
        onClose={() => setSentinelDrawerOpen(false)}
        objectTypes={dynamicObjectTypes}
        linkTypes={dynamicLinkTypes}
        actions={dynamicActions}
      />
    </div>
  )
}
