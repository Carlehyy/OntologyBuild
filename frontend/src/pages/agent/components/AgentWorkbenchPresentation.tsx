/**
 * Agent workbench presentation primitives.
 *
 * This module owns message rendering, trace/provenance presentation and the
 * resizable split-pane handle. Network visualization and page orchestration
 * remain separate concerns.
 */
import { useCallback, useMemo, useRef, useState } from 'react'
import type * as React from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import {
  BellRing,
  Check,
  ChevronRight,
  Copy,
  Eye,
  FileSearch,
  FlaskConical,
  GitBranch,
  ListChecks,
  Loader2,
  Network,
  Scale,
  ScrollText,
  Search,
  Sigma,
  Workflow,
  Zap,
} from 'lucide-react'
import {
  type AgentCitation,
  type AgentProposal,
  type AgentStep,
} from '@/api/agent'
import { writeTextToClipboard } from '@/utils/clipboard'
import { ChartBlock, type ChartSpec } from '../AgentChart'

export interface ChatMsg {
  id: string
  role: 'user' | 'assistant'
  content: string
  steps: AgentStep[]
  citations: AgentCitation[]
  proposals: AgentProposal[]
  loading?: boolean
  error?: string
}

export const clamp = (value: number, min: number, max: number) => Math.min(Math.max(value, min), max)

export function safeExportFilename(title: string, conversationId: string): string {
  const invalidFilenameChars = '<>:"/\\|?*'
  const normalizedTitle = Array.from(title, character =>
    invalidFilenameChars.includes(character) || character.charCodeAt(0) < 32 ? '_' : character,
  ).join('')
  const safeTitle = normalizedTitle
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, 80) || '未命名会话'
  return `${safeTitle}-${conversationId.slice(0, 8)}.json`
}

export function downloadJson(value: unknown, filename: string): void {
  const blob = new Blob([JSON.stringify(value, null, 2)], {
    type: 'application/json;charset=utf-8',
  })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  anchor.click()
  window.setTimeout(() => URL.revokeObjectURL(url), 0)
}

export function useAssistantLayout() {
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

export function Md({ text }: { text: string }) {
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
  run_decision_simulation: { label: '决策推演', icon: Scale },
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

export function StepTrace({ steps, running }: { steps: AgentStep[]; running?: boolean }) {
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
export function collectCharts(steps: AgentStep[]): ChartSpec[] {
  const out: ChartSpec[] = []
  for (const s of steps) {
    const chart = (s.result as any)?.chart
    if (chart && typeof chart === 'object' && Array.isArray(chart.data)) out.push(chart as ChartSpec)
  }
  return out
}

/** 「出处条」：从已持久化的 steps + citations 统计跑了什么、看了多少、耗时多少 */
export function ProvenanceBar({ steps, cited }: { steps: AgentStep[]; cited: number }) {
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

export function AgentCallChainView({ messages, conversationId, ontologyName, running }: {
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
      <div className="flex h-full items-center justify-center bg-[#f8fbff] p-8 text-center dark:bg-[#161c26]">
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
    <div className="scrollbar-thin h-full overflow-y-auto bg-[#f8fbff] px-4 py-4 dark:bg-[#161c26]" data-testid="agent-call-chain-view">
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

export function SplitHandle({ onPointerDown }: { onPointerDown: (event: React.PointerEvent<HTMLDivElement>) => void }) {
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
