import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react'
import ReactMarkdown, { type Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import {
  Bot, Check, ChevronRight, CircleAlert, Copy, FileCode2, FileText, Folder, Gauge,
  History, List, Loader2, MessageSquare, Pencil, PlugZap, Plus,
  Save, Send, Settings2, ShieldCheck, Square, Trash2, Upload, User,
  Wrench, X,
} from 'lucide-react'

import { modelApi } from '@/api/ontologies'
import {
  superAssistantApi,
  type McpTransport,
  type SkillFile,
  type SuperConversation,
  type SuperMcpServer,
  type SuperMessage,
  type SuperSkill,
  type ToolStep,
} from '@/api/superAssistant'
import { useToast } from '@/components/ui/Toast'
import SessionHistoryPopover from '@/components/SessionHistoryPopover'
import type { ModelConfig } from '@/types/ontology'
import { writeTextToClipboard } from '@/utils/clipboard'


const errorText = (error: any, fallback = '操作失败') =>
  error?.detail || error?.message || fallback


function EmptyState() {
  return (
    <div className="absolute inset-x-0 bottom-full mb-8 flex flex-col items-center px-4 text-center">
      <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-teal-50 text-teal-700 ring-1 ring-teal-100">
        <Bot size={23} strokeWidth={1.8} />
      </div>
      <h1 className="mt-4 text-xl font-semibold tracking-tight text-[var(--color-text-primary)]">有什么可以帮你？</h1>
    </div>
  )
}


function ToolSteps({ steps }: { steps: ToolStep[] }) {
  if (!steps?.length) return null
  return (
    <div className="mb-2 space-y-1.5">
      {steps.map((step, index) => (
        <details key={`${step.toolName}-${index}`} className="group rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-base)]">
          <summary className="flex min-h-10 cursor-pointer list-none items-center gap-2 px-3 text-xs text-[var(--color-text-secondary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-teal-500">
            {step.status === 'running' || step.status === 'awaiting_confirmation'
              ? <Loader2 size={13} className="animate-spin text-teal-600" />
              : step.status === 'success'
                ? <Check size={13} className="text-emerald-600" />
                : <CircleAlert size={13} className="text-amber-600" />}
            <span className="font-medium">{step.toolName}</span>
            <span className="ml-auto text-[10px] text-[var(--color-text-tertiary)]">{step.status}</span>
            <ChevronRight size={12} className="transition-transform group-open:rotate-90" />
          </summary>
          <pre className="max-h-48 overflow-auto border-t border-[var(--color-border)] p-3 text-[11px] leading-5 text-[var(--color-text-secondary)] whitespace-pre-wrap break-all">
            {step.preview || JSON.stringify(step.arguments || {}, null, 2)}
          </pre>
        </details>
      ))}
    </div>
  )
}


const markdownText = (child: unknown): string => {
  if (typeof child === 'string' || typeof child === 'number') return String(child)
  if (Array.isArray(child)) return child.map(markdownText).join('')
  if (child && typeof child === 'object' && 'props' in child) {
    return markdownText((child as { props?: { children?: unknown } }).props?.children)
  }
  return ''
}

/**
 * 部分模型会把整段答复包进 ```markdown / ````markdown 围栏。
 * react-markdown 会正确但不符合预期地把它显示为“Markdown 源码”；当围栏内容占答复主体时，
 * 去掉这一层展示围栏，同时保留里面真正的代码围栏。
 */
function normalizeAssistantMarkdown(value: string) {
  const normalized = value.replace(/\r\n?/g, '\n')
  const lines = normalized.split('\n')
  const blocks: Array<{ start: number; end: number; contentLength: number }> = []

  for (let index = 0; index < lines.length; index += 1) {
    const opening = /^\s*(`{3,}|~{3,})[ \t]*(?:markdown|md)[ \t]*$/i.exec(lines[index])
    if (!opening) continue
    const marker = opening[1][0]
    const minimumLength = opening[1].length

    for (let end = index + 1; end < lines.length; end += 1) {
      const closing = /^\s*(`+|~+)\s*$/.exec(lines[end])
      if (!closing || closing[1][0] !== marker || closing[1].length < minimumLength) continue
      blocks.push({
        start: index,
        end,
        contentLength: lines.slice(index + 1, end).join('\n').trim().length,
      })
      index = end
      break
    }
  }

  if (blocks.length > 0) {
    const dominant = blocks.reduce((best, block) => block.contentLength > best.contentLength ? block : best)
    const outside = [...lines.slice(0, dominant.start), ...lines.slice(dominant.end + 1)].join('\n').trim()
    const totalLength = normalized.trim().length || 1
    if (dominant.contentLength / totalLength >= 0.45 || outside.length <= 240) {
      return [
        ...lines.slice(0, dominant.start),
        ...lines.slice(dominant.start + 1, dominant.end),
        ...lines.slice(dominant.end + 1),
      ].join('\n').trim()
    }
  }

  // 流式响应尚未收到闭合围栏时，也先按 Markdown 展示，避免生成过程中整段闪成源码。
  const unfinishedOpening = lines.findIndex(line => /^\s*(`{3,}|~{3,})[ \t]*(?:markdown|md)[ \t]*$/i.test(line))
  if (blocks.length === 0 && unfinishedOpening >= 0 && lines.slice(0, unfinishedOpening).join('\n').trim().length <= 240) {
    return [...lines.slice(0, unfinishedOpening), ...lines.slice(unfinishedOpening + 1)].join('\n').trim()
  }

  return normalized
}

function MarkdownCodeBlock({ children }: { children: React.ReactNode }) {
  const [copied, setCopied] = useState(false)
  const child = Array.isArray(children) ? children[0] : children
  const childProps = child && typeof child === 'object' && 'props' in child
    ? (child as { props?: { className?: string; children?: unknown } }).props
    : undefined
  const language = /language-([^\s]+)/.exec(childProps?.className || '')?.[1]
  const source = markdownText(childProps?.children ?? children).replace(/\n$/, '')

  const copy = () => {
    writeTextToClipboard(source).then(() => {
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1400)
    }).catch(() => undefined)
  }

  return (
    <div className="my-4 overflow-hidden rounded-xl border border-[var(--color-border)] bg-slate-50 shadow-[0_8px_22px_rgba(15,118,110,0.06)]">
      <div className="flex min-h-9 items-center justify-between border-b border-[var(--color-border)] bg-slate-100/80 px-3">
        <span className="font-mono text-[10px] font-semibold uppercase tracking-[0.12em] text-teal-700">
          {language || 'code'}
        </span>
        <button
          type="button"
          onClick={copy}
          className="inline-flex min-h-7 items-center gap-1.5 rounded-md px-2 text-[10px] text-slate-500 transition-colors hover:bg-white hover:text-teal-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-400"
          aria-label={copied ? '代码已复制' : '复制代码'}
        >
          {copied ? <Check size={11} /> : <Copy size={11} />}
          {copied ? '已复制' : '复制'}
        </button>
      </div>
      <pre className="overflow-x-auto bg-slate-50 p-4 text-[12px] leading-6 text-slate-700 selection:bg-teal-100 selection:text-teal-950">
        <code className="font-mono">{source}</code>
      </pre>
    </div>
  )
}

const assistantMarkdownComponents: Components = {
  p: ({ className, ...props }) => <p className={`mb-3 text-sm leading-7 text-[var(--color-text-primary)] last:mb-0 ${className || ''}`} {...props} />,
  h1: ({ className, ...props }) => <h2 className={`mb-3 mt-7 text-xl font-semibold leading-tight tracking-tight text-[var(--color-text-primary)] first:mt-0 ${className || ''}`} {...props} />,
  h2: ({ className, ...props }) => <h3 className={`mb-2.5 mt-6 text-base font-semibold leading-snug tracking-tight text-[var(--color-text-primary)] first:mt-0 ${className || ''}`} {...props} />,
  h3: ({ className, ...props }) => <h4 className={`mb-2 mt-5 text-sm font-semibold leading-snug text-[var(--color-text-primary)] first:mt-0 ${className || ''}`} {...props} />,
  h4: ({ className, ...props }) => <h5 className={`mb-2 mt-4 text-sm font-semibold text-[var(--color-text-primary)] first:mt-0 ${className || ''}`} {...props} />,
  h5: ({ className, ...props }) => <h6 className={`mb-2 mt-4 text-xs font-semibold text-[var(--color-text-primary)] first:mt-0 ${className || ''}`} {...props} />,
  h6: ({ className, ...props }) => <h6 className={`mb-2 mt-4 text-xs font-medium text-[var(--color-text-secondary)] first:mt-0 ${className || ''}`} {...props} />,
  strong: ({ className, ...props }) => <strong className={`font-semibold text-[var(--color-text-primary)] ${className || ''}`} {...props} />,
  em: ({ className, ...props }) => <em className={`italic text-[var(--color-text-secondary)] ${className || ''}`} {...props} />,
  del: ({ className, ...props }) => <del className={`text-[var(--color-text-tertiary)] decoration-slate-400 ${className || ''}`} {...props} />,
  ul: ({ className, ...props }) => <ul className={`mb-3 ml-1 list-disc space-y-1.5 pl-5 marker:text-teal-600 [&.contains-task-list]:list-none [&.contains-task-list]:pl-0 ${className || ''}`} {...props} />,
  ol: ({ className, ...props }) => <ol className={`mb-3 ml-1 list-decimal space-y-1.5 pl-5 marker:font-medium marker:text-[var(--color-text-tertiary)] ${className || ''}`} {...props} />,
  li: ({ className, ...props }) => <li className={`pl-1 text-sm leading-7 text-[var(--color-text-primary)] [&.task-list-item]:list-none [&.task-list-item]:pl-0 ${className || ''}`} {...props} />,
  input: ({ className, ...props }) => <input className={`mr-2 h-3.5 w-3.5 translate-y-0.5 rounded border-slate-300 accent-teal-600 ${className || ''}`} {...props} />,
  blockquote: ({ className, ...props }) => <blockquote className={`my-4 rounded-r-lg border-l-[3px] border-teal-500 bg-teal-50/60 py-2 pl-4 pr-3 text-[var(--color-text-secondary)] [&>p]:text-[var(--color-text-secondary)] ${className || ''}`} {...props} />,
  a: ({ className, ...props }) => <a className={`font-medium text-teal-700 underline decoration-teal-200 underline-offset-4 transition-colors hover:text-teal-900 hover:decoration-teal-500 focus-visible:rounded-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-400 ${className || ''}`} target="_blank" rel="noreferrer noopener" {...props} />,
  img: ({ className, alt, ...props }) => <img className={`my-4 max-h-[32rem] max-w-full rounded-xl border border-[var(--color-border)] object-contain shadow-sm ${className || ''}`} alt={alt || 'Markdown 图片'} loading="lazy" decoding="async" {...props} />,
  code: ({ className, ...props }) => <code className={`rounded-md border border-slate-200 bg-slate-100 px-1.5 py-0.5 font-mono text-[0.86em] text-slate-800 ${className || ''}`} {...props} />,
  pre: ({ children }) => <MarkdownCodeBlock>{children}</MarkdownCodeBlock>,
  table: ({ className, ...props }) => (
    <div className="my-4 max-w-full overflow-x-auto rounded-xl border border-[var(--color-border)] shadow-sm">
      <table className={`w-full min-w-[32rem] border-collapse text-left text-xs ${className || ''}`} {...props} />
    </div>
  ),
  thead: ({ className, ...props }) => <thead className={`bg-slate-50 text-[var(--color-text-secondary)] ${className || ''}`} {...props} />,
  tbody: ({ className, ...props }) => <tbody className={`divide-y divide-[var(--color-border)] bg-[var(--color-bg-elevated)] ${className || ''}`} {...props} />,
  th: ({ className, ...props }) => <th className={`border-b border-[var(--color-border)] px-3.5 py-2.5 font-semibold whitespace-nowrap ${className || ''}`} {...props} />,
  td: ({ className, ...props }) => <td className={`px-3.5 py-2.5 leading-5 text-[var(--color-text-primary)] ${className || ''}`} {...props} />,
  hr: ({ className, ...props }) => <hr className={`my-6 border-0 border-t border-[var(--color-border)] ${className || ''}`} {...props} />,
}

function AssistantMarkdown({ content }: { content: string }) {
  const normalized = useMemo(() => normalizeAssistantMarkdown(content), [content])
  return (
    <div className="min-w-0 break-words [overflow-wrap:anywhere]">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={assistantMarkdownComponents}>
        {normalized}
      </ReactMarkdown>
    </div>
  )
}


function ChatMessage({ message }: { message: SuperMessage }) {
  const assistant = message.role === 'assistant'
  return (
    <article
      id={assistant ? undefined : `super-assistant-msg-${message.id}`}
      className={`flex scroll-mt-6 gap-3 ${assistant ? '' : 'justify-end'}`}
    >
      {assistant && (
        <div className="mt-1 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-teal-50 text-teal-700 ring-1 ring-teal-100">
          <Bot size={16} />
        </div>
      )}
      <div className={`min-w-0 ${assistant ? 'w-full max-w-3xl' : 'max-w-[82%]'}`}>
        {assistant && <ToolSteps steps={message.steps} />}
        <div className={assistant
          ? 'max-w-none text-[var(--color-text-primary)]'
          : 'rounded-2xl rounded-br-md bg-[var(--color-nav-bg)] px-4 py-2.5 text-sm leading-6 text-white'}>
          {assistant ? (
            message.content
              ? <AssistantMarkdown content={message.content} />
              : <span className="inline-flex items-center gap-2 text-sm text-[var(--color-text-tertiary)]"><Loader2 size={14} className="animate-spin" /> 正在思考…</span>
          ) : message.content}
        </div>
        {message.status === 'error' && (
          <p role="alert" className="mt-2 inline-flex items-center gap-1 text-xs text-[var(--color-danger)]"><CircleAlert size={12} /> 生成失败</p>
        )}
        {message.status === 'cancelled' && (
          <p className="mt-2 text-xs text-[var(--color-text-tertiary)]">已停止生成</p>
        )}
      </div>
      {!assistant && (
        <div className="mt-1 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-[var(--color-bg-hover)] text-[var(--color-text-secondary)]">
          <User size={16} />
        </div>
      )}
    </article>
  )
}


const usageNumber = (value: unknown) => {
  const number = Number(value)
  return Number.isFinite(number) && number >= 0 ? number : 0
}

const compactTokenCount = (value: number, divisor: number, suffix: string) => {
  const scaled = value / divisor
  const digits = scaled >= 10 ? 0 : 1
  return `${scaled.toFixed(digits).replace(/\.0$/, '')}${suffix}`
}

const formatTokenCount = (value: number) => value >= 1_000_000
  ? compactTokenCount(value, 1_000_000, 'M')
  : value >= 1000
    ? compactTokenCount(value, 1000, 'k')
    : String(Math.round(value))

function ContextUsage({ messages, model }: { messages: SuperMessage[]; model?: ModelConfig }) {
  const lastAssistant = [...messages].reverse().find(message => (
    message.role === 'assistant' && message.status === 'complete' && Object.keys(message.token_usage || {}).length > 0
  ))
  const usage = lastAssistant?.token_usage || {}
  const configuredLimit = usageNumber(model?.options?.max_context_tokens)
  const limit = usageNumber(usage.contextLimit) || configuredLimit || 64_000
  const contextTokens = Number(usage.contextTokens)
  const hasSnapshot = Object.prototype.hasOwnProperty.call(usage, 'contextTokens')
    && Number.isFinite(contextTokens)
    && contextTokens >= 0
  const used = hasSnapshot ? usageNumber(usage.contextTokens) : usageNumber(usage.inputTokens)
  const percentage = limit > 0 ? Math.min(100, (used / limit) * 100) : 0
  const percentageLabel = percentage === 0
    ? '0%'
    : percentage < 0.05
      ? '<0.1%'
      : percentage < 10
        ? `${percentage.toFixed(1)}%`
        : `${Math.round(percentage)}%`
  const tone = percentage >= 85
    ? 'bg-rose-500'
    : percentage >= 65
      ? 'bg-amber-500'
      : 'bg-teal-600'
  const sourceLabel = hasSnapshot ? '上下文' : used ? '上下文估算' : '上下文'
  const sourceDescription = hasSnapshot
    ? '最近一次模型实际输入上下文'
    : used
      ? '旧会话仅记录累计输入；单轮调用通常准确，多轮工具调用可能偏大'
      : '发送消息后更新'

  return (
    <aside
      data-testid="super-assistant-context-usage"
      aria-label={`${sourceLabel}占比 ${percentageLabel}，${formatTokenCount(used)} / ${formatTokenCount(limit)}`}
      title={sourceDescription}
      className="flex h-9 w-40 shrink-0 flex-col justify-center rounded-lg border border-teal-200 bg-teal-50/80 px-2.5 shadow-[inset_0_0_0_1px_rgba(255,255,255,0.55)] xl:w-48"
    >
      <div className="flex min-w-0 items-center gap-1.5 text-[10px] leading-none">
        <Gauge size={11} className="shrink-0 text-teal-700" aria-hidden="true" />
        <span className="truncate font-medium text-[var(--color-text-secondary)]">{sourceLabel}</span>
        <span className="ml-auto shrink-0 font-semibold tabular-nums text-[var(--color-text-primary)]">{percentageLabel}</span>
        <span className="hidden shrink-0 tabular-nums text-[var(--color-text-tertiary)] xl:inline">
          {formatTokenCount(used)} / {formatTokenCount(limit)}
        </span>
      </div>
      <div className="mt-1.5 h-1 overflow-hidden rounded-full bg-[var(--color-bg-hover)]">
        <div
          className={`h-full rounded-full transition-[width] duration-300 ${tone}`}
          style={{ width: used > 0 ? `max(2px, ${percentage}%)` : '0%' }}
        />
      </div>
    </aside>
  )
}


interface PendingConfirmation {
  toolRunId: string
  toolName: string
  serverName: string
  arguments: Record<string, unknown>
}

function ConfirmationCard({ pending, busy, onDecision }: {
  pending: PendingConfirmation
  busy: boolean
  onDecision: (decision: 'approve' | 'deny') => void
}) {
  return (
    <div role="alert" className="ml-11 max-w-3xl rounded-xl border border-amber-200 bg-amber-50/80 p-4">
      <div className="flex items-start gap-3">
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-amber-100 text-amber-700"><ShieldCheck size={18} /></div>
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold text-amber-950">等待执行确认</p>
          <p className="mt-1 text-xs leading-5 text-amber-800">
            MCP「{pending.serverName}」请求调用 <span className="font-mono font-medium">{pending.toolName}</span>
          </p>
          <pre className="mt-3 max-h-40 overflow-auto rounded-lg border border-amber-200 bg-white/80 p-3 text-[11px] leading-5 text-slate-700 whitespace-pre-wrap break-all">
            {JSON.stringify(pending.arguments, null, 2)}
          </pre>
          <div className="mt-3 flex gap-2">
            <button type="button" disabled={busy} onClick={() => onDecision('deny')}
              className="min-h-10 rounded-lg border border-amber-300 px-4 text-xs font-medium text-amber-900 transition-colors hover:bg-amber-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-500 disabled:opacity-50">
              拒绝
            </button>
            <button type="button" disabled={busy} onClick={() => onDecision('approve')}
              className="inline-flex min-h-10 items-center gap-2 rounded-lg bg-amber-700 px-4 text-xs font-medium text-white transition-colors hover:bg-amber-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-500 focus-visible:ring-offset-2 disabled:opacity-50">
              {busy && <Loader2 size={13} className="animate-spin" />} 确认执行
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}


function DialogShell({ title, description, size = 'default', onClose, children }: {
  title: string
  description?: string
  size?: 'default' | 'large' | 'wide'
  onClose: () => void
  children: React.ReactNode
}) {
  const titleId = useId()
  const descriptionId = useId()
  const dialogRef = useRef<HTMLElement>(null)

  useEffect(() => {
    const dialog = dialogRef.current
    if (!dialog) return
    const previousFocus = document.activeElement as HTMLElement | null
    const focusableSelector = 'button:not([disabled]), input:not([disabled]), textarea:not([disabled]), select:not([disabled]), summary, [tabindex]:not([tabindex="-1"])'
    const focusables = () => Array.from(dialog.querySelectorAll<HTMLElement>(focusableSelector))
    focusables()[0]?.focus()
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { event.preventDefault(); onClose(); return }
      if (event.key !== 'Tab') return
      const items = focusables()
      if (!items.length) return
      const first = items[0]
      const last = items[items.length - 1]
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault(); last.focus()
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault(); first.focus()
      }
    }
    document.addEventListener('keydown', handleKeyDown)
    return () => {
      document.removeEventListener('keydown', handleKeyDown)
      previousFocus?.focus()
    }
  }, [onClose])

  const sizeClass = {
    default: 'max-h-[90dvh] max-w-xl',
    large: 'max-h-[85dvh] max-w-3xl',
    wide: 'max-h-[90dvh] max-w-5xl',
  }[size]

  return (
    <div className="fixed inset-0 z-[70] flex items-center justify-center bg-slate-950/35 p-4" onMouseDown={onClose}>
      <section ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby={titleId} aria-describedby={description ? descriptionId : undefined}
        onMouseDown={event => event.stopPropagation()}
        className={`flex w-full flex-col overflow-hidden rounded-2xl border border-[var(--color-border)] bg-[var(--color-bg-elevated)] shadow-2xl ${sizeClass}`}>
        <header className="flex shrink-0 items-start justify-between border-b border-[var(--color-border)] px-5 py-4">
          <div>
            <h2 id={titleId} className="text-sm font-semibold text-[var(--color-text-primary)]">{title}</h2>
            {description && <p id={descriptionId} className="mt-1 text-xs text-[var(--color-text-tertiary)]">{description}</p>}
          </div>
          <button type="button" onClick={onClose} aria-label="关闭"
            className="flex h-10 w-10 items-center justify-center rounded-lg text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-500"><X size={17} /></button>
        </header>
        {children}
      </section>
    </div>
  )
}


function SkillCreateDialog({ onClose, onSaved }: { onClose: () => void; onSaved: () => Promise<void> }) {
  const { toast } = useToast()
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [content, setContent] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const save = async () => {
    if (!/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(name) || name.length > 64 || !description.trim() || !content.trim()) {
      setError('请填写合法的技能名称、技能描述和具体内容')
      return
    }
    setBusy(true); setError('')
    try {
      await superAssistantApi.createSkill({
        name,
        description: description.trim(),
        content,
        enabled: true,
      })
      await onSaved()
      toast({ tone: 'success', title: 'Skill 已创建', description: '已生成标准目录和 SKILL.md' })
      onClose()
    } catch (error) { setError(errorText(error, '创建失败')) } finally { setBusy(false) }
  }

  return (
    <DialogShell title="新建 Skill" description="系统会生成标准 SKILL.md，并在独立目录中保存该技能。" onClose={onClose}>
      <div className="flex-1 space-y-4 overflow-y-auto p-5">
        <label className="block text-xs text-[var(--color-text-secondary)]">技能名称 <span className="text-red-500">*</span>
          <input value={name} onChange={event => setName(event.target.value.toLowerCase().replace(/[_\s]+/g, '-'))} placeholder="research-helper"
            className="mt-1.5 min-h-11 w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-base)] px-3 font-mono text-sm outline-none focus:border-teal-500 focus:ring-2 focus:ring-teal-100" />
          <span className="mt-1 block text-[10px] leading-4 text-[var(--color-text-tertiary)]">用于技能包目录和调用标识，仅支持小写字母、数字和连字符。</span>
        </label>
        <label className="block text-xs text-[var(--color-text-secondary)]">技能描述 <span className="text-red-500">*</span>
          <textarea value={description} onChange={event => setDescription(event.target.value)} rows={2}
            placeholder="说明这个技能做什么，以及什么情况下应使用它"
            className="mt-1.5 w-full resize-y rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-base)] p-3 text-sm outline-none focus:border-teal-500 focus:ring-2 focus:ring-teal-100" />
        </label>
        <label className="block text-xs text-[var(--color-text-secondary)]">具体内容 <span className="text-red-500">*</span>
          <textarea value={content} onChange={event => setContent(event.target.value)} rows={10} placeholder="# 工作流程&#10;&#10;1. …"
            className="mt-1.5 w-full resize-y rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-base)] p-3 font-mono text-xs leading-5 outline-none focus:border-teal-500 focus:ring-2 focus:ring-teal-100" />
        </label>
        {error && <p role="alert" className="text-xs text-red-600">{error}</p>}
      </div>
      <footer className="flex justify-center gap-2 border-t border-[var(--color-border)] px-5 py-4">
        <button onClick={onClose} className="min-h-10 rounded-lg px-4 text-xs text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)]">取消</button>
        <button onClick={save} disabled={busy} className="inline-flex min-h-10 items-center gap-2 rounded-lg bg-teal-700 px-4 text-xs font-medium text-white hover:bg-teal-800 disabled:opacity-50">
          {busy && <Loader2 size={13} className="animate-spin" />} 创建
        </button>
      </footer>
    </DialogShell>
  )
}


function SkillEditor({ skill, onClose, onSaved }: { skill: SuperSkill; onClose: () => void; onSaved: () => Promise<void> }) {
  const { toast } = useToast()
  const [files, setFiles] = useState<SkillFile[]>(skill.manifest)
  const [revision, setRevision] = useState(skill.revision)
  const [selectedPath, setSelectedPath] = useState('SKILL.md')
  const [content, setContent] = useState('')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [newPath, setNewPath] = useState('')
  const [error, setError] = useState('')

  const loadFile = useCallback(async (path: string) => {
    setLoading(true); setError('')
    try {
      const result = await superAssistantApi.skillFile(skill.id, path)
      setContent(result.content); setSelectedPath(path)
    } catch (error) { setError(errorText(error, '读取文件失败')) } finally { setLoading(false) }
  }, [skill.id])

  useEffect(() => { void loadFile('SKILL.md') }, [loadFile])

  const save = async () => {
    setSaving(true); setError('')
    try {
      const result: any = await superAssistantApi.putSkillFile(skill.id, selectedPath, content)
      setFiles(result.manifest || files)
      setRevision(result.revision || revision)
      await onSaved()
      toast({ tone: 'success', title: '文件已保存', description: `${selectedPath} · revision ${result.revision}` })
    } catch (error) { setError(errorText(error, '保存失败')) } finally { setSaving(false) }
  }

  const startNewFile = () => {
    const path = newPath.trim().replace(/^\/+/, '')
    if (!path || path.includes('..')) { setError('请输入 Skill 目录内的合法相对路径'); return }
    setSelectedPath(path); setContent(''); setNewPath(''); setLoading(false); setError('')
  }

  const removeFile = async () => {
    if (selectedPath === 'SKILL.md' || !window.confirm(`确定删除 ${selectedPath}？`)) return
    try {
      await superAssistantApi.deleteSkillFile(skill.id, selectedPath)
      const next = await superAssistantApi.skillFiles(skill.id)
      setFiles(next); await loadFile('SKILL.md'); await onSaved()
      toast({ tone: 'success', title: '文件已删除' })
    } catch (error) { setError(errorText(error, '删除失败')) }
  }

  return (
    <DialogShell size="wide" title={`编辑 Skill：${skill.name}`} description={`revision ${revision} · ${files.length} 个文件`} onClose={onClose}>
      <div className="grid min-h-0 flex-1 grid-cols-1 md:grid-cols-[260px_minmax(0,1fr)]">
        <aside className="flex min-h-40 flex-col border-b border-[var(--color-border)] bg-[var(--color-bg-base)] md:border-b-0 md:border-r">
          <div className="border-b border-[var(--color-border)] p-3">
            <label className="text-[11px] text-[var(--color-text-secondary)]">新建相对路径</label>
            <div className="mt-1 flex gap-1.5">
              <input value={newPath} onChange={event => setNewPath(event.target.value)} onKeyDown={event => event.key === 'Enter' && startNewFile()}
                placeholder="references/guide.md" className="min-w-0 flex-1 rounded-md border border-[var(--color-border)] bg-white px-2 text-xs outline-none focus:border-teal-500" />
              <button onClick={startNewFile} aria-label="新建文件" className="flex h-10 w-10 items-center justify-center rounded-md border border-[var(--color-border)] bg-white hover:bg-teal-50"><Plus size={14} /></button>
            </div>
          </div>
          <div className="max-h-52 flex-1 overflow-y-auto p-2 md:max-h-none">
            {files.map(file => (
              <button key={file.path} onClick={() => file.editable && void loadFile(file.path)} disabled={!file.editable}
                className={`mb-1 flex min-h-9 w-full items-center gap-2 rounded-md px-2 text-left text-xs ${selectedPath === file.path ? 'bg-teal-100 text-teal-900' : 'text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)]'} disabled:cursor-not-allowed disabled:opacity-45`}>
                {file.path.includes('/') ? <FileCode2 size={13} /> : <FileText size={13} />}
                <span className="min-w-0 flex-1 truncate">{file.path}</span>
                <span className="text-[9px] text-[var(--color-text-tertiary)]">{Math.ceil(file.size / 1024)}K</span>
              </button>
            ))}
          </div>
        </aside>
        <div className="flex min-h-0 flex-col">
          <div className="flex min-h-12 items-center justify-between border-b border-[var(--color-border)] px-4">
            <span className="truncate font-mono text-xs text-[var(--color-text-secondary)]">{selectedPath}</span>
            <div className="flex gap-1">
              {selectedPath !== 'SKILL.md' && (
                <button onClick={removeFile} aria-label="删除当前文件" className="flex h-10 w-10 items-center justify-center rounded-lg text-[var(--color-text-tertiary)] hover:bg-red-50 hover:text-red-600"><Trash2 size={14} /></button>
              )}
              <button onClick={save} disabled={saving || loading} className="inline-flex min-h-10 items-center gap-2 rounded-lg bg-teal-700 px-3 text-xs font-medium text-white hover:bg-teal-800 disabled:opacity-50">
                {saving ? <Loader2 size={13} className="animate-spin" /> : <Save size={13} />} 保存
              </button>
            </div>
          </div>
          {loading ? (
            <div className="flex flex-1 items-center justify-center"><Loader2 size={20} className="animate-spin text-teal-600" /></div>
          ) : (
            <textarea aria-label={`编辑 ${selectedPath}`} value={content} onChange={event => setContent(event.target.value)} spellCheck={false}
              className="min-h-[360px] flex-1 resize-none bg-[var(--color-bg-elevated)] p-4 font-mono text-xs leading-6 text-[var(--color-text-primary)] outline-none focus:ring-2 focus:ring-inset focus:ring-teal-500" />
          )}
          {error && <p role="alert" className="border-t border-red-100 bg-red-50 px-4 py-2 text-xs text-red-700">{error}</p>}
        </div>
      </div>
    </DialogShell>
  )
}


function McpDialog({ server, onClose, onSaved }: {
  server?: SuperMcpServer
  onClose: () => void
  onSaved: () => Promise<void>
}) {
  const { toast } = useToast()
  const [name, setName] = useState(server?.name || '')
  const [transport, setTransport] = useState<McpTransport>(server?.transport || 'streamable_http')
  const [url, setUrl] = useState(server?.url || '')
  const [command, setCommand] = useState(server?.command || '')
  const [args, setArgs] = useState(JSON.stringify(server?.args || [], null, 2))
  const [headers, setHeaders] = useState('')
  const [env, setEnv] = useState('')
  const [clientConfig, setClientConfig] = useState('')
  const clientConfigRef = useRef<HTMLTextAreaElement>(null)
  const [enabled, setEnabled] = useState(server?.enabled ?? true)
  const [confirmation, setConfirmation] = useState(server?.require_confirmation ?? true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    const textarea = clientConfigRef.current
    if (!textarea) return
    textarea.style.height = 'auto'
    textarea.style.height = `${textarea.scrollHeight}px`
  }, [clientConfig])

  const parseStringMap = (value: string, label: string): Record<string, string> | undefined => {
    if (!value.trim()) return undefined
    const parsed = JSON.parse(value)
    if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object' || Object.values(parsed).some(item => typeof item !== 'string')) {
      throw new Error(`${label} 必须是字符串键值 JSON 对象`)
    }
    return parsed as Record<string, string>
  }

  const applyClientConfig = () => {
    setError('')
    try {
      const parsed = JSON.parse(clientConfig)
      const collection = parsed?.mcpServers || parsed
      if (!collection || Array.isArray(collection) || typeof collection !== 'object') {
        throw new Error('配置必须包含 mcpServers 对象')
      }
      const entries = Object.entries(collection)
      if (entries.length !== 1) throw new Error('请每次粘贴一个 MCP Server 配置')
      const [configName, raw] = entries[0] as [string, any]
      if (!raw || Array.isArray(raw) || typeof raw !== 'object') throw new Error('MCP Server 配置无效')
      if (!server) setName(configName)

      if (raw.command) {
        const parsedArgs = Array.isArray(raw.args) ? raw.args.map(String) : []
        const remoteIndex = parsedArgs.findIndex((item: string) => item === 'mcp-remote' || item.startsWith('mcp-remote@'))
        const remoteUrl = remoteIndex >= 0
          ? parsedArgs.slice(remoteIndex + 1).find((item: string) => /^https?:\/\//.test(item))
          : undefined
        if (/^(?:.*[\\/])?npx(?:\.cmd)?$/i.test(String(raw.command)) && remoteUrl) {
          setTransport('streamable_http'); setUrl(remoteUrl); setCommand(''); setArgs('[]')
        } else {
          setTransport('stdio'); setCommand(String(raw.command)); setArgs(JSON.stringify(parsedArgs, null, 2)); setUrl('')
        }
        setEnv(raw.env ? JSON.stringify(raw.env, null, 2) : '')
        setHeaders('')
        return
      }
      if (!raw.url) throw new Error('配置需要 command 或 url')
      const rawTransport = String(raw.transport || 'streamable_http').toLowerCase().replace(/[ -]/g, '_')
      setTransport(rawTransport === 'sse' ? 'sse' : 'streamable_http')
      setUrl(String(raw.url)); setHeaders(raw.headers ? JSON.stringify(raw.headers, null, 2) : '')
      setCommand(''); setArgs('[]'); setEnv('')
    } catch (error) { setError(errorText(error, '无法解析 MCP 配置')) }
  }

  const save = async () => {
    setBusy(true); setError('')
    try {
      const parsedHeaders = parseStringMap(headers, 'Headers')
      const parsedEnv = parseStringMap(env, 'env')
      const parsedArgs = JSON.parse(args || '[]')
      if (!Array.isArray(parsedArgs) || parsedArgs.some(item => typeof item !== 'string')) {
        throw new Error('args 必须是字符串 JSON 数组')
      }
      if (transport === 'stdio' && !command.trim()) throw new Error('stdio 传输必须填写 command')
      if (transport !== 'stdio' && !url.trim()) throw new Error('HTTP 传输必须填写 URL')
      const changedTransport = !!server && server.transport !== transport
      const connection = transport === 'stdio'
        ? { transport, url: '', command: command.trim(), args: parsedArgs }
        : { transport, url: url.trim(), command: null, args: [] }
      if (server) {
        await superAssistantApi.updateMcpServer(server.id, {
          ...connection, enabled, require_confirmation: confirmation,
          ...(parsedHeaders ? { headers: parsedHeaders } : changedTransport && transport === 'stdio' ? { headers: {} } : {}),
          ...(parsedEnv ? { env: parsedEnv } : changedTransport && transport !== 'stdio' ? { env: {} } : {}),
        })
      } else {
        await superAssistantApi.createMcpServer({
          name, ...connection, headers: parsedHeaders || {}, env: parsedEnv || {},
          enabled, require_confirmation: confirmation,
        })
      }
      await onSaved()
      toast({ tone: 'success', title: server ? 'MCP 配置已更新' : 'MCP Server 已添加', description: '请执行连接测试以发现工具清单' })
      onClose()
    } catch (error) { setError(errorText(error, '保存失败')) } finally { setBusy(false) }
  }

  return (
    <DialogShell size="large" title={server ? `编辑 MCP：${server.name}` : '添加 MCP Server'} description="支持粘贴客户端 JSON，也可直接配置 stdio、SSE 或 Streamable HTTP。" onClose={onClose}>
      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto overscroll-contain p-5 [scrollbar-gutter:stable] sm:p-6">
        {!server && <details className="rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-base)]" open>
          <summary className="cursor-pointer px-3 py-2.5 text-xs font-medium text-[var(--color-text-secondary)]">粘贴 MCP 客户端 JSON</summary>
          <div className="space-y-2 border-t border-[var(--color-border)] p-3">
            <textarea ref={clientConfigRef} value={clientConfig} onChange={event => setClientConfig(event.target.value)} rows={8}
              placeholder={'{\n  "mcpServers": {\n    "api-hub": {\n      "command": "npx",\n      "args": ["-y", "mcp-remote", "https://example.com/mcp"]\n    }\n  }\n}'}
              className="w-full resize-none overflow-hidden rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-elevated)] p-3 font-mono text-xs leading-5 outline-none focus:border-teal-500 focus:ring-2 focus:ring-teal-100" />
            <button type="button" onClick={applyClientConfig} disabled={!clientConfig.trim()}
              className="min-h-9 rounded-md border border-[var(--color-border)] bg-white px-3 text-xs text-teal-700 hover:bg-teal-50 disabled:opacity-50">解析并填入下方表单</button>
          </div>
        </details>}
        <label className="block text-xs text-[var(--color-text-secondary)]">名称 <span className="text-red-500">*</span>
          <input value={name} disabled={!!server} onChange={event => setName(event.target.value)} placeholder="knowledge_search"
            className="mt-1.5 min-h-11 w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-base)] px-3 font-mono text-sm outline-none focus:border-teal-500 focus:ring-2 focus:ring-teal-100 disabled:opacity-60" />
        </label>
        <label className="block text-xs text-[var(--color-text-secondary)]">传输方式 <span className="text-red-500">*</span>
          <select value={transport} onChange={event => setTransport(event.target.value as McpTransport)}
            className="mt-1.5 min-h-11 w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-base)] px-3 text-sm outline-none focus:border-teal-500 focus:ring-2 focus:ring-teal-100">
            <option value="streamable_http">Streamable HTTP（推荐）</option>
            <option value="sse">SSE（旧版兼容）</option>
            <option value="stdio">stdio（启动本地进程）</option>
          </select>
        </label>
        {transport === 'stdio' ? <>
          <label className="block text-xs text-[var(--color-text-secondary)]">command <span className="text-red-500">*</span>
            <input value={command} onChange={event => setCommand(event.target.value)} placeholder="npx"
              className="mt-1.5 min-h-11 w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-base)] px-3 font-mono text-sm outline-none focus:border-teal-500 focus:ring-2 focus:ring-teal-100" />
          </label>
          <label className="block text-xs text-[var(--color-text-secondary)]">args JSON
            <textarea value={args} onChange={event => setArgs(event.target.value)} rows={4} placeholder={'["-y", "@example/mcp-server"]'}
              className="mt-1.5 w-full resize-y rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-base)] p-3 font-mono text-xs leading-5 outline-none focus:border-teal-500 focus:ring-2 focus:ring-teal-100" />
          </label>
          <label className="block text-xs text-[var(--color-text-secondary)]">env JSON
            <textarea value={env} onChange={event => setEnv(event.target.value)} rows={4} placeholder={server ? `留空保持现有环境变量（${server.env_names.join(', ') || '无'}）` : '{\n  "API_KEY": "…"\n}'}
              className="mt-1.5 w-full resize-y rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-base)] p-3 font-mono text-xs leading-5 outline-none focus:border-teal-500 focus:ring-2 focus:ring-teal-100" />
          </label>
          <p className="rounded-lg bg-amber-50 p-3 text-[11px] leading-5 text-amber-800">stdio 会在后端容器内启动进程，部署方必须显式启用并允许该 command。env 会加密存储且不回显。</p>
        </> : <>
          <label className="block text-xs text-[var(--color-text-secondary)]">MCP URL <span className="text-red-500">*</span>
            <input type="url" value={url} onChange={event => setUrl(event.target.value)} placeholder="https://mcp.example.com/mcp"
              className="mt-1.5 min-h-11 w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-base)] px-3 text-sm outline-none focus:border-teal-500 focus:ring-2 focus:ring-teal-100" />
            <span className="mt-1 block text-[10px] leading-4 text-[var(--color-text-tertiary)]">公网地址可直接连接；生产环境会拒绝环回、内网和链路本地地址。</span>
          </label>
          <label className="block text-xs text-[var(--color-text-secondary)]">请求头 JSON
            <textarea value={headers} onChange={event => setHeaders(event.target.value)} rows={4} placeholder={server ? `留空保持现有请求头（${server.header_names.join(', ') || '无'}）` : '{\n  "Authorization": "Bearer …"\n}'}
              className="mt-1.5 w-full resize-y rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-base)] p-3 font-mono text-xs leading-5 outline-none focus:border-teal-500 focus:ring-2 focus:ring-teal-100" />
          </label>
        </>}
        <label className="flex min-h-11 items-center justify-between rounded-lg border border-[var(--color-border)] px-3 text-xs text-[var(--color-text-secondary)]">
          启用此 Server
          <input type="checkbox" checked={enabled} onChange={event => setEnabled(event.target.checked)} className="h-4 w-4 accent-teal-700" />
        </label>
        <label className="flex min-h-11 items-center justify-between rounded-lg border border-[var(--color-border)] px-3 text-xs text-[var(--color-text-secondary)]">
          每次工具调用前要求确认
          <input type="checkbox" checked={confirmation} onChange={event => setConfirmation(event.target.checked)} className="h-4 w-4 accent-teal-700" />
        </label>
        {!confirmation && <p className="rounded-lg bg-amber-50 p-3 text-xs leading-5 text-amber-800">关闭确认会允许模型直接执行该 Server 的所有工具，请仅对完全可信、只读的服务使用。</p>}
        {error && <p role="alert" className="text-xs text-red-600">{error}</p>}
      </div>
      <footer className="flex shrink-0 justify-center gap-3 border-t border-[var(--color-border)] px-5 py-4">
        <button onClick={onClose} className="min-h-10 min-w-24 rounded-lg px-4 text-xs text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)]">取消</button>
        <button onClick={save} disabled={busy || !name || (transport === 'stdio' ? !command : !url)} className="inline-flex min-h-10 min-w-24 items-center justify-center gap-2 rounded-lg bg-teal-700 px-4 text-xs font-medium text-white hover:bg-teal-800 disabled:opacity-50">
          {busy && <Loader2 size={13} className="animate-spin" />} 保存
        </button>
      </footer>
    </DialogShell>
  )
}


function SettingSwitch({ label, ariaLabel, checked, busy, onToggle }: {
  label: string
  ariaLabel: string
  checked: boolean
  busy: boolean
  onToggle: () => void
}) {
  return (
    <div className="inline-flex items-center gap-1.5">
      <span className="text-[10px] text-[var(--color-text-secondary)]">{label}</span>
      <button type="button" role="switch" aria-label={ariaLabel} aria-checked={checked} aria-busy={busy} disabled={busy} onClick={onToggle}
        className="relative inline-flex h-11 w-11 shrink-0 touch-manipulation items-center justify-center rounded-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-500/40 focus-visible:ring-offset-1 disabled:cursor-wait disabled:opacity-60">
        <span aria-hidden="true" className={`relative inline-flex h-4 w-7 items-center rounded-full transition-colors motion-reduce:transition-none ${busy ? 'animate-pulse motion-reduce:animate-none' : ''} ${checked ? 'bg-teal-600' : 'bg-slate-300'}`}>
          <span className={`inline-block h-3 w-3 rounded-full bg-white shadow-sm transition-transform motion-reduce:transition-none ${checked ? 'translate-x-3.5' : 'translate-x-0.5'}`} />
        </span>
      </button>
    </div>
  )
}


function ConfigurationPanel({ open, onClose, skills, servers, refreshSkills, refreshServers }: {
  open: boolean
  onClose: () => void
  skills: SuperSkill[]
  servers: SuperMcpServer[]
  refreshSkills: () => Promise<void>
  refreshServers: () => Promise<void>
}) {
  const { toast } = useToast()
  const [tab, setTab] = useState<'skills' | 'mcp'>('skills')
  const [creatingSkill, setCreatingSkill] = useState(false)
  const [editingSkill, setEditingSkill] = useState<SuperSkill | null>(null)
  const [editingMcp, setEditingMcp] = useState<SuperMcpServer | 'new' | null>(null)
  const [testingId, setTestingId] = useState<string | null>(null)
  const [updatingSkillId, setUpdatingSkillId] = useState<string | null>(null)
  const [updatingServerSetting, setUpdatingServerSetting] = useState<string | null>(null)
  const uploadRef = useRef<HTMLInputElement>(null)
  const configurableServers = servers.filter(server => server.builtin_key !== 'minio')

  const importZip = async (file?: File) => {
    if (!file) return
    try {
      await superAssistantApi.importSkill(file)
      await refreshSkills()
      toast({ tone: 'success', title: 'Skill 文件夹已导入', description: file.name })
    } catch (error) { toast({ tone: 'error', title: '导入失败', description: errorText(error) }) }
    if (uploadRef.current) uploadRef.current.value = ''
  }

  const toggleSkill = async (skill: SuperSkill) => {
    if (updatingSkillId) return
    setUpdatingSkillId(skill.id)
    try {
      await superAssistantApi.updateSkill(skill.id, { enabled: !skill.enabled })
      await refreshSkills()
    } catch (error) {
      toast({ tone: 'error', title: 'Skill 设置更新失败', description: errorText(error) })
    } finally {
      setUpdatingSkillId(null)
    }
  }

  const removeSkill = async (skill: SuperSkill) => {
    if (!window.confirm(`确定删除 Skill「${skill.name}」及其整个文件夹？`)) return
    try { await superAssistantApi.deleteSkill(skill.id); await refreshSkills(); toast({ tone: 'success', title: 'Skill 已删除' }) }
    catch (error) { toast({ tone: 'error', title: '删除失败', description: errorText(error) }) }
  }

  const testServer = async (server: SuperMcpServer) => {
    setTestingId(server.id)
    try {
      const result = await superAssistantApi.testMcpServer(server.id)
      await refreshServers()
      toast({ tone: result.ok ? 'success' : 'error', title: result.ok ? 'MCP 连接成功' : 'MCP 连接失败', description: result.message })
    } catch (error) { toast({ tone: 'error', title: 'MCP 测试失败', description: errorText(error) }) }
    finally { setTestingId(null) }
  }

  const updateServerSetting = async (
    server: SuperMcpServer,
    setting: 'enabled' | 'require_confirmation',
    value: boolean,
  ) => {
    if (updatingServerSetting) return
    setUpdatingServerSetting(`${server.id}:${setting}`)
    try {
      const patch = setting === 'enabled' ? { enabled: value } : { require_confirmation: value }
      await superAssistantApi.updateMcpServer(server.id, patch)
      await refreshServers()
    } catch (error) {
      toast({ tone: 'error', title: 'MCP 设置更新失败', description: errorText(error) })
    } finally {
      setUpdatingServerSetting(null)
    }
  }

  const removeServer = async (server: SuperMcpServer) => {
    if (!window.confirm(`确定删除 MCP Server「${server.name}」？`)) return
    try { await superAssistantApi.deleteMcpServer(server.id); await refreshServers(); toast({ tone: 'success', title: 'MCP Server 已删除' }) }
    catch (error) { toast({ tone: 'error', title: '删除失败', description: errorText(error) }) }
  }

  return (
    <>
      <aside
        aria-hidden={!open}
        inert={!open}
        className={`absolute inset-y-0 right-0 z-30 w-[min(26rem,100%)] overflow-hidden transition-[width] duration-300 ease-[cubic-bezier(0.22,1,0.36,1)] motion-reduce:transition-none lg:relative lg:inset-auto lg:z-auto ${open
          ? 'pointer-events-auto lg:w-[26rem]'
          : 'pointer-events-none lg:w-0'}`}
      >
        <section
          aria-label="助手配置"
          className={`absolute inset-y-0 right-0 flex w-[min(26rem,100vw)] min-h-0 flex-col overflow-hidden border-l border-[var(--color-border)] bg-[var(--color-bg-elevated)] transition-transform duration-300 ease-[cubic-bezier(0.22,1,0.36,1)] motion-reduce:transition-none lg:translate-x-0 ${open ? 'translate-x-0' : 'translate-x-full'}`}
        >
          <header className="flex shrink-0 items-start justify-between border-b border-[var(--color-border)] px-4 py-3.5">
            <div className="min-w-0">
              <h2 className="text-sm font-semibold text-[var(--color-text-primary)]">助手配置</h2>
              <p className="mt-1 text-[10px] leading-4 text-[var(--color-text-tertiary)]">管理当前助手可用的 Skill 与 MCP Server</p>
            </div>
            <button type="button" onClick={onClose} aria-label="关闭助手配置"
              className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg text-[var(--color-text-secondary)] transition-colors hover:bg-[var(--color-bg-hover)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-500">
              <X size={16} />
            </button>
          </header>
          <div className="relative mx-4 mt-3 grid grid-cols-2 gap-1 rounded-lg border border-slate-200 bg-slate-50/70 p-0.5">
            <div className={`absolute bottom-0.5 top-0.5 w-[calc(50%_-_4px)] rounded-md bg-teal-600 shadow-sm transition-all duration-300 ease-out ${tab === 'skills' ? 'left-0.5' : 'left-[calc(50%_+_2px)]'}`} />
            <button type="button" onClick={() => setTab('skills')}
              className={`relative z-10 min-h-9 rounded-md text-xs font-medium transition-colors duration-200 ${tab === 'skills' ? 'text-white' : 'text-slate-500 hover:text-slate-700'}`}>
              Skill <span className={`ml-1 text-[10px] tabular-nums ${tab === 'skills' ? 'text-teal-100' : 'text-slate-400'}`}>{skills.length}</span>
            </button>
            <button type="button" onClick={() => setTab('mcp')}
              className={`relative z-10 min-h-9 rounded-md text-xs font-medium transition-colors duration-200 ${tab === 'mcp' ? 'text-white' : 'text-slate-500 hover:text-slate-700'}`}>
              MCP <span className={`ml-1 text-[10px] tabular-nums ${tab === 'mcp' ? 'text-teal-100' : 'text-slate-400'}`}>{configurableServers.length}</span>
            </button>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto p-4">
          {tab === 'skills' ? (
            <>
              <div className="grid gap-3">
                {skills.length === 0 && <div className="rounded-xl border border-dashed border-[var(--color-border)] p-10 text-center text-xs text-[var(--color-text-tertiary)]"><Folder size={22} className="mx-auto mb-2" />暂无 Skill</div>}
                {skills.map(skill => (
                  <article key={skill.id} className="rounded-xl border border-[var(--color-border)] bg-[var(--color-bg-elevated)] p-4 transition-colors hover:border-teal-200">
                    <div className="flex items-start gap-2">
                      <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-teal-50 text-teal-700"><Folder size={16} /></div>
                      <div className="min-w-0 flex-1">
                        <p className="truncate font-mono text-xs font-semibold text-[var(--color-text-primary)]">{skill.name}</p>
                        <p className="mt-0.5 truncate text-[10px] text-[var(--color-text-tertiary)]">r{skill.revision} · {skill.manifest.length} files</p>
                      </div>
                    </div>
                    {!skill.enabled && <span className="mt-2 inline-flex rounded bg-slate-100 px-1.5 py-0.5 text-[9px] text-slate-500">已停用</span>}
                    <p className="mt-2 line-clamp-2 text-[11px] leading-5 text-[var(--color-text-secondary)]">{skill.description || '暂无描述'}</p>
                    <div className="mt-2 flex items-center justify-between gap-2">
                      <SettingSwitch
                        label="启用"
                        ariaLabel={`${skill.enabled ? '停用' : '启用'} Skill ${skill.name}`}
                        checked={skill.enabled}
                        busy={updatingSkillId !== null}
                        onToggle={() => void toggleSkill(skill)}
                      />
                      <div className="flex shrink-0 items-center gap-1">
                        <button type="button" onClick={() => setEditingSkill(skill)} className="inline-flex min-h-9 items-center gap-1 rounded-md px-2 text-[11px] text-[var(--color-text-secondary)] transition-colors hover:bg-teal-50 hover:text-teal-800"><Pencil size={12} /> 文件</button>
                        <button type="button" onClick={() => void removeSkill(skill)} aria-label={`删除 ${skill.name}`} className="flex h-9 w-9 items-center justify-center rounded-md text-[var(--color-text-tertiary)] transition-colors hover:bg-red-50 hover:text-red-600"><Trash2 size={12} /></button>
                      </div>
                    </div>
                  </article>
                ))}
              </div>
            </>
          ) : (
            <>
              <div className="grid gap-3">
                {configurableServers.length === 0 && <div className="rounded-xl border border-dashed border-[var(--color-border)] p-10 text-center text-xs text-[var(--color-text-tertiary)]"><PlugZap size={22} className="mx-auto mb-2" />暂无 MCP Server</div>}
                {configurableServers.map(server => (
                  <article key={server.id} className="rounded-xl border border-[var(--color-border)] p-4 transition-colors hover:border-teal-200">
                    <div className="flex items-start gap-2">
                      <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-sky-50 text-sky-700"><PlugZap size={16} /></div>
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-1.5"><p className="truncate text-xs font-semibold text-[var(--color-text-primary)]">{server.name}</p><span className={`h-2 w-2 rounded-full ${server.last_test_status === 'success' ? 'bg-emerald-500' : server.last_test_status === 'error' ? 'bg-red-500' : 'bg-slate-300'}`} /></div>
                        <p className="mt-1 truncate text-[10px] text-[var(--color-text-tertiary)]">{server.transport === 'stdio' ? `${server.command} ${server.args.join(' ')}` : server.url}</p>
                      </div>
                    </div>
                    <div className="mt-2 flex flex-wrap gap-1">
                      <span className="rounded bg-[var(--color-bg-base)] px-1.5 py-0.5 text-[9px] text-[var(--color-text-tertiary)]">{server.tool_manifest.length} tools</span>
                      <span className="rounded bg-[var(--color-bg-base)] px-1.5 py-0.5 text-[9px] text-[var(--color-text-tertiary)]">{server.transport === 'streamable_http' ? 'Streamable HTTP' : server.transport.toUpperCase()}</span>
                      <span className="rounded bg-[var(--color-bg-base)] px-1.5 py-0.5 text-[9px] text-[var(--color-text-tertiary)]">{server.require_confirmation ? '执行前确认' : '自动执行'}</span>
                      {!server.enabled && <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[9px] text-slate-500">已停用</span>}
                    </div>
                    {server.last_test_message && <p className="mt-2 line-clamp-2 text-[10px] leading-4 text-[var(--color-text-tertiary)]">{server.last_test_message}</p>}
                    <div className="mt-2 flex items-center justify-between gap-2">
                      <div className="flex min-w-0 items-center gap-3">
                        <SettingSwitch label="启用" ariaLabel={`${server.enabled ? '停用' : '启用'} MCP ${server.name}`} checked={server.enabled}
                          busy={updatingServerSetting !== null}
                          onToggle={() => void updateServerSetting(server, 'enabled', !server.enabled)} />
                        <SettingSwitch label="自动执行" ariaLabel={`${server.require_confirmation ? '开启' : '关闭'} ${server.name} 自动执行`} checked={!server.require_confirmation}
                          busy={updatingServerSetting !== null}
                          onToggle={() => void updateServerSetting(server, 'require_confirmation', !server.require_confirmation)} />
                      </div>
                      <div className="flex shrink-0 items-center gap-1">
                        <button type="button" onClick={() => void testServer(server)} disabled={testingId === server.id} className="inline-flex min-h-9 items-center gap-1 rounded-md px-2 text-[11px] text-teal-700 transition-colors hover:bg-teal-50 disabled:opacity-50">{testingId === server.id ? <Loader2 size={12} className="animate-spin" /> : <Wrench size={12} />} 测试</button>
                        {!server.builtin_key && <button type="button" onClick={() => setEditingMcp(server)} aria-label={`编辑 MCP ${server.name}`} className="flex h-9 w-9 items-center justify-center rounded-md text-[var(--color-text-tertiary)] transition-colors hover:bg-[var(--color-bg-hover)]"><Pencil size={12} /></button>}
                        <button type="button" onClick={() => void removeServer(server)} aria-label={`删除 MCP ${server.name}`} className="flex h-9 w-9 items-center justify-center rounded-md text-[var(--color-text-tertiary)] transition-colors hover:bg-red-50 hover:text-red-600"><Trash2 size={12} /></button>
                      </div>
                    </div>
                  </article>
                ))}
              </div>
            </>
          )}
          </div>
          <footer className="shrink-0 border-t border-[var(--color-border)] bg-[var(--color-bg-elevated)] p-3">
            {tab === 'skills' ? (
              <div className="grid grid-cols-2 gap-2">
                <button type="button" onClick={() => setCreatingSkill(true)}
                  className="inline-flex min-h-11 items-center justify-center gap-1.5 rounded-lg border border-dashed border-teal-400 bg-teal-50/70 px-3 text-xs font-medium text-teal-700 transition-all hover:border-teal-500 hover:bg-teal-100 active:scale-[0.98] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-400">
                  <Plus size={14} /> 新建 Skill
                </button>
                <button type="button" onClick={() => uploadRef.current?.click()}
                  className="inline-flex min-h-11 items-center justify-center gap-1.5 rounded-lg border border-dashed border-teal-400 bg-teal-50/70 px-3 text-xs font-medium text-teal-700 transition-all hover:border-teal-500 hover:bg-teal-100 active:scale-[0.98] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-400">
                  <Upload size={14} /> 导入 ZIP
                </button>
              </div>
            ) : (
              <button type="button" onClick={() => setEditingMcp('new')}
                className="inline-flex min-h-11 w-full items-center justify-center gap-1.5 rounded-lg border border-dashed border-teal-400 bg-teal-50/70 px-3 text-xs font-medium text-teal-700 transition-all hover:border-teal-500 hover:bg-teal-100 active:scale-[0.98] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-400">
                <Plus size={14} /> 添加 MCP
              </button>
            )}
            <input ref={uploadRef} type="file" accept=".zip,application/zip" className="hidden" onChange={event => void importZip(event.target.files?.[0])} />
          </footer>
        </section>
      </aside>
      {creatingSkill && <SkillCreateDialog onClose={() => setCreatingSkill(false)} onSaved={refreshSkills} />}
      {editingSkill && <SkillEditor skill={editingSkill} onClose={() => setEditingSkill(null)} onSaved={refreshSkills} />}
      {editingMcp && <McpDialog server={editingMcp === 'new' ? undefined : editingMcp} onClose={() => setEditingMcp(null)} onSaved={refreshServers} />}
    </>
  )
}


export default function SuperAssistantPage() {
  const { toast } = useToast()
  const [conversations, setConversations] = useState<SuperConversation[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [messages, setMessages] = useState<SuperMessage[]>([])
  const [models, setModels] = useState<ModelConfig[]>([])
  const [skills, setSkills] = useState<SuperSkill[]>([])
  const [servers, setServers] = useState<SuperMcpServer[]>([])
  const [input, setInput] = useState('')
  const [running, setRunning] = useState(false)
  const [stopping, setStopping] = useState(false)
  const [pending, setPending] = useState<PendingConfirmation | null>(null)
  const [decisionBusy, setDecisionBusy] = useState(false)
  const [sessionsOpen, setSessionsOpen] = useState(false)
  const [showMessageHistory, setShowMessageHistory] = useState(false)
  const [configOpen, setConfigOpen] = useState(false)
  const [editingTitle, setEditingTitle] = useState(false)
  const [titleDraft, setTitleDraft] = useState('')
  const [savingTitle, setSavingTitle] = useState(false)
  const [loading, setLoading] = useState(true)
  const [modelLoadFailed, setModelLoadFailed] = useState(false)
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  const refreshConversations = useCallback(async () => {
    const data = await superAssistantApi.conversations()
    setConversations(data)
    return data
  }, [])
  const refreshSkills = useCallback(async () => setSkills(await superAssistantApi.skills()), [])
  const refreshServers = useCallback(async () => setServers(await superAssistantApi.mcpServers()), [])

  useEffect(() => {
    let alive = true
    Promise.allSettled([
      superAssistantApi.conversations(),
      modelApi.list(),
      superAssistantApi.skills(),
      superAssistantApi.mcpServers(),
    ]).then(([conversationResult, modelResult, skillResult, serverResult]) => {
      if (!alive) return
      const failures: string[] = []

      if (conversationResult.status === 'fulfilled') {
        setConversations(conversationResult.value)
        if (conversationResult.value[0]) setSelectedId(conversationResult.value[0].id)
      } else {
        failures.push(`会话：${errorText(conversationResult.reason, '加载失败')}`)
      }
      if (modelResult.status === 'fulfilled') {
        setModels(modelResult.value.filter(model => model.config_type === 'llm' && model.enabled !== false))
        setModelLoadFailed(false)
      } else {
        setModelLoadFailed(true)
        failures.push(`模型：${errorText(modelResult.reason, '加载失败')}`)
      }
      if (skillResult.status === 'fulfilled') setSkills(skillResult.value)
      else failures.push(`Skills：${errorText(skillResult.reason, '加载失败')}`)
      if (serverResult.status === 'fulfilled') setServers(serverResult.value)
      else failures.push(`MCP：${errorText(serverResult.reason, '加载失败')}`)

      if (failures.length) {
        toast({
          tone: 'error',
          title: failures.length === 4 ? '超级助手加载失败' : '超级助手部分功能加载失败',
          description: failures.join('；'),
        })
      }
    })
      .finally(() => alive && setLoading(false))
    return () => { alive = false }
  }, [toast])

  useEffect(() => {
    setShowMessageHistory(false)
    setEditingTitle(false)
    if (!selectedId) { setMessages([]); return }
    let alive = true
    superAssistantApi.messages(selectedId).then(data => { if (alive) setMessages(data) })
      .catch(error => toast({ tone: 'error', title: '会话消息加载失败', description: errorText(error) }))
    return () => { alive = false }
  }, [selectedId, toast])

  useEffect(() => { messagesEndRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' }) }, [messages, pending])

  const selectedConversation = conversations.find(item => item.id === selectedId) || null
  const selectedModelId = selectedConversation?.model_config_id || models.find(model => model.is_default)?.id || models[0]?.id || ''
  const selectedModel = models.find(model => model.id === selectedModelId)
  const myMessages = useMemo(() => messages.filter(message => message.role === 'user'), [messages])

  const createConversation = async () => {
    try {
      const item = await superAssistantApi.createConversation({ model_config_id: selectedModelId || null })
      setConversations(current => [item, ...current]); setSelectedId(item.id); setMessages([])
      return item
    } catch (error) { toast({ tone: 'error', title: '新建会话失败', description: errorText(error) }); return null }
  }

  const deleteConversation = async (conversation: SuperConversation) => {
    if (!window.confirm(`确定删除会话「${conversation.title}」？`)) return
    try {
      await superAssistantApi.deleteConversation(conversation.id)
      const next = conversations.filter(item => item.id !== conversation.id)
      setConversations(next)
      if (selectedId === conversation.id) { setSelectedId(next[0]?.id || null); setMessages([]) }
      toast({ tone: 'success', title: '会话已删除' })
    } catch (error) { toast({ tone: 'error', title: '删除失败', description: errorText(error) }) }
  }

  const changeModel = async (modelId: string) => {
    if (!selectedId) return
    try {
      const updated = await superAssistantApi.updateConversation(selectedId, { model_config_id: modelId || null })
      setConversations(current => current.map(item => item.id === updated.id ? updated : item))
    } catch (error) { toast({ tone: 'error', title: '模型切换失败', description: errorText(error) }) }
  }

  const saveTitle = async () => {
    if (!selectedId || savingTitle) return
    const title = titleDraft.trim()
    if (!title) {
      toast({ tone: 'error', title: '会话名称不能为空' })
      return
    }
    if (title === selectedConversation?.title) {
      setEditingTitle(false)
      return
    }
    setSavingTitle(true)
    try {
      const updated = await superAssistantApi.updateConversation(selectedId, { title })
      setConversations(current => current.map(item => item.id === updated.id ? updated : item))
      setEditingTitle(false)
      toast({ tone: 'success', title: '会话名称已保存' })
    } catch (error) {
      toast({ tone: 'error', title: '名称保存失败', description: errorText(error) })
    } finally {
      setSavingTitle(false)
    }
  }

  const jumpToMessage = (messageId: string) => {
    setShowMessageHistory(false)
    requestAnimationFrame(() => {
      document.getElementById(`super-assistant-msg-${messageId}`)?.scrollIntoView({
        behavior: 'smooth',
        block: 'center',
      })
    })
  }

  const send = async () => {
    const message = input.trim()
    if (!message || running) return
    let conversation = selectedConversation
    if (!conversation) conversation = await createConversation()
    if (!conversation) return
    const conversationId = conversation.id
    const now = new Date().toISOString()
    const tempUserId = `user-${Date.now()}`
    const tempAssistantId = `assistant-${Date.now()}`
    setInput(''); setRunning(true); setStopping(false); setPending(null)
    setMessages(current => [...current,
      { id: tempUserId, conversation_id: conversationId, role: 'user', content: message, status: 'complete', steps: [], token_usage: {}, created_at: now },
      { id: tempAssistantId, conversation_id: conversationId, role: 'assistant', content: '', status: 'streaming', steps: [], token_usage: {}, created_at: now },
    ])
    try {
      await superAssistantApi.streamChat(conversationId, { message, model_config_id: selectedModelId || null }, ({ event, data }) => {
        if (event === 'text_delta') {
          setMessages(current => current.map(item => item.id === tempAssistantId ? { ...item, content: item.content + String(data.delta || '') } : item))
        } else if (event === 'tool_start') {
          const step: ToolStep = { toolName: data.toolName, status: 'running', arguments: data.arguments }
          setMessages(current => current.map(item => item.id === tempAssistantId ? { ...item, steps: [...item.steps, step] } : item))
        } else if (event === 'tool_confirmation_required') {
          setPending({ toolRunId: data.toolRunId, toolName: data.toolName, serverName: data.serverName, arguments: data.arguments || {} })
          setMessages(current => current.map(item => item.id === tempAssistantId
            ? { ...item, steps: item.steps.map((step, index) => index === item.steps.length - 1 ? { ...step, status: 'awaiting_confirmation' } : step) }
            : item))
        } else if (event === 'tool_result') {
          setPending(current => current?.toolRunId === data.toolRunId ? null : current)
          setMessages(current => current.map(item => item.id === tempAssistantId
            ? { ...item, steps: item.steps.map((step, index) => index === item.steps.length - 1 ? { ...step, status: data.status, preview: data.preview } : step) }
            : item))
        } else if (event === 'message_end') {
          setMessages(current => current.map(item => item.id === tempAssistantId ? {
            ...item, content: data.message?.content || item.content, steps: data.message?.steps || item.steps,
            token_usage: data.message?.tokenUsage || {}, status: 'complete',
          } : item))
        } else if (event === 'cancelled') {
          setMessages(current => current.map(item => item.id === tempAssistantId ? { ...item, status: 'cancelled' } : item))
        } else if (event === 'error') {
          setMessages(current => current.map(item => item.id === tempAssistantId ? { ...item, content: data.message || '生成失败', status: 'error' } : item))
          toast({ tone: 'error', title: '生成失败', description: data.message })
        }
      })
    } catch (error) {
      setMessages(current => current.map(item => item.id === tempAssistantId ? { ...item, content: errorText(error, '生成失败'), status: 'error' } : item))
      toast({ tone: 'error', title: '生成失败', description: errorText(error) })
    } finally {
      setRunning(false); setStopping(false); setPending(null)
      try {
        const [messageRows] = await Promise.all([superAssistantApi.messages(conversationId), refreshConversations()])
        setMessages(messageRows)
      } catch { /* optimistic state remains usable */ }
      window.setTimeout(() => textareaRef.current?.focus(), 0)
    }
  }

  const stop = async () => {
    if (!selectedId || stopping) return
    setStopping(true)
    try { await superAssistantApi.cancel(selectedId) }
    catch (error) { setStopping(false); toast({ tone: 'error', title: '停止失败', description: errorText(error) }) }
  }

  const decide = async (decision: 'approve' | 'deny') => {
    if (!pending) return
    setDecisionBusy(true)
    try { await superAssistantApi.decideToolRun(pending.toolRunId, decision); setPending(null) }
    catch (error) { toast({ tone: 'error', title: '确认失败', description: errorText(error) }) }
    finally { setDecisionBusy(false) }
  }

  const canSend = input.trim().length > 0 && !running && models.length > 0
  const placeholder = loading
    ? '正在加载可用模型…'
    : modelLoadFailed
      ? '模型列表加载失败，请刷新页面重试'
      : models.length
        ? '输入消息；Shift + Enter 换行'
        : '请先到“模型配置”启用一个文本 LLM'
  const hasMessages = messages.length > 0

  const renderComposer = (prominent = false) => (
    <div className="w-full">
      <div data-testid="super-assistant-composer" className={`flex items-end gap-2 rounded-2xl border bg-[var(--color-bg-elevated)] p-2 transition-all focus-within:border-teal-500 focus-within:ring-2 focus-within:ring-teal-100 ${prominent
        ? 'border-slate-200 shadow-[0_18px_50px_rgba(15,118,110,0.12)]'
        : 'border-[var(--color-border)] shadow-[0_8px_28px_rgba(15,23,42,0.08)]'}`}>
        <textarea
          ref={textareaRef}
          autoFocus
          value={input}
          onChange={event => setInput(event.target.value)}
          rows={1}
          aria-label="向超级助手发送消息"
          placeholder={placeholder}
          disabled={running || models.length === 0}
          onKeyDown={event => {
            if (event.key === 'Enter' && !event.shiftKey) {
              event.preventDefault()
              if (canSend) void send()
            }
          }}
          className="max-h-40 min-h-11 flex-1 resize-none bg-transparent px-3 py-3 text-sm leading-5 text-[var(--color-text-primary)] outline-none placeholder:text-[var(--color-text-tertiary)] disabled:opacity-60"
        />
        <div className="relative flex shrink-0 items-center gap-2">
          {running ? (
            <button type="button" onClick={() => void stop()} disabled={stopping} aria-label="停止生成" title="停止生成"
              className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-[var(--color-text-primary)] text-white transition-opacity hover:opacity-90 active:scale-[0.98] disabled:opacity-50">
              {stopping ? <Loader2 size={15} className="animate-spin" /> : <Square size={14} fill="currentColor" />}
            </button>
          ) : (
            <button type="button" onClick={() => void send()} disabled={!canSend} aria-label="发送消息" title="发送消息"
              className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-teal-700 text-white transition-colors hover:bg-teal-800 active:scale-[0.98] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-500 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-40">
              <Send size={16} />
            </button>
          )}
          <button
            type="button"
            onClick={() => setShowMessageHistory(value => !value)}
            disabled={myMessages.length === 0}
            title="我发送的消息 · 快速跳转"
            aria-label="查看我发送的消息"
            aria-expanded={showMessageHistory}
            className={`flex h-11 w-11 shrink-0 items-center justify-center rounded-xl border transition-colors active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-30 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-500 ${showMessageHistory
              ? 'border-teal-300 bg-teal-50 text-teal-700'
              : 'border-[var(--color-border)] text-[var(--color-text-tertiary)] hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text-secondary)]'}`}
          >
            <List size={16} />
          </button>
          {showMessageHistory && (
            <>
              <div className="fixed inset-0 z-20" onClick={() => setShowMessageHistory(false)} />
              <div data-testid="super-assistant-message-history" className="absolute bottom-full right-0 z-30 mb-5 w-72 overflow-hidden rounded-xl border border-[var(--color-border)] bg-[var(--color-bg-elevated)] shadow-[0_16px_40px_rgba(15,23,42,0.16)]">
                <div className="flex items-center justify-between border-b border-[var(--color-border)] px-3 py-2.5">
                  <span className="text-[11px] font-medium text-[var(--color-text-secondary)]">我发送的消息</span>
                  <span className="text-[10px] text-[var(--color-text-tertiary)]">点击跳转 · 共 {myMessages.length} 条</span>
                </div>
                <div className="max-h-64 overflow-y-auto py-1">
                  {[...myMessages].reverse().map((message, index) => (
                    <button
                      type="button"
                      key={message.id}
                      onClick={() => jumpToMessage(message.id)}
                      title={message.content}
                      className="flex w-full items-start gap-2 px-3 py-2 text-left transition-colors hover:bg-[var(--color-bg-hover)] focus-visible:bg-[var(--color-bg-hover)] focus-visible:outline-none"
                    >
                      <span className="mt-0.5 shrink-0 font-mono text-[10px] text-[var(--color-text-tertiary)]">#{myMessages.length - index}</span>
                      <span className="min-w-0 flex-1 truncate text-xs text-[var(--color-text-secondary)]">{message.content}</span>
                    </button>
                  ))}
                </div>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )

  return (
    <div className="relative flex h-full min-h-0 overflow-hidden bg-[var(--color-bg-base)]">
      <section className="flex min-w-0 flex-1 flex-col bg-[var(--color-bg-elevated)]">
        <header className="relative z-10 flex h-[4.3125rem] shrink-0 items-center gap-2 border-b border-[var(--color-border)] px-3 sm:px-4">
          <div className="min-w-0 flex-1">
            {editingTitle ? (
              <form className="flex max-w-lg items-center gap-1.5" onSubmit={event => { event.preventDefault(); void saveTitle() }}>
                <input
                  autoFocus
                  value={titleDraft}
                  maxLength={200}
                  onChange={event => setTitleDraft(event.target.value)}
                  onKeyDown={event => {
                    if (event.key === 'Escape') setEditingTitle(false)
                  }}
                  aria-label="编辑会话名称"
                  className="h-9 min-w-0 flex-1 rounded-lg border border-teal-300 bg-[var(--color-bg-base)] px-2.5 text-sm font-semibold text-[var(--color-text-primary)] outline-none ring-2 ring-teal-100"
                />
                <button type="submit" disabled={savingTitle} aria-label="保存会话名称"
                  className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-teal-700 text-white transition-colors hover:bg-teal-800 disabled:opacity-50">
                  {savingTitle ? <Loader2 size={14} className="animate-spin" /> : <Check size={15} />}
                </button>
                <button type="button" onClick={() => setEditingTitle(false)} aria-label="取消编辑会话名称"
                  title="取消编辑"
                  className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-rose-200 bg-rose-50 text-rose-600 transition-colors hover:border-rose-300 hover:bg-rose-100 hover:text-rose-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-rose-300">
                  <X size={14} />
                </button>
              </form>
            ) : (
              <button
                type="button"
                disabled={!selectedConversation}
                onClick={() => {
                  if (!selectedConversation) return
                  setTitleDraft(selectedConversation.title)
                  setEditingTitle(true)
                }}
                title={selectedConversation ? '点击编辑会话名称' : undefined}
                className="group flex max-w-full items-center gap-1.5 rounded-md py-1 text-left text-sm font-semibold text-[var(--color-text-primary)] outline-none transition-colors hover:text-teal-800 focus-visible:ring-2 focus-visible:ring-teal-400 disabled:cursor-default disabled:hover:text-[var(--color-text-primary)]"
              >
                <span className="truncate">{selectedConversation?.title || '新的超级助手会话'}</span>
                {selectedConversation && <Pencil size={12} className="shrink-0 opacity-0 transition-opacity group-hover:opacity-70 group-focus-visible:opacity-70" />}
              </button>
            )}
          </div>
          {!loading && selectedConversation && <ContextUsage messages={messages} model={selectedModel} />}
          <label className="sr-only" htmlFor="super-assistant-model">会话模型</label>
          <select id="super-assistant-model" value={selectedModelId} onChange={event => void changeModel(event.target.value)} disabled={!selectedId || running}
            className="h-9 w-48 rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-base)] px-2 text-xs text-[var(--color-text-secondary)] outline-none transition-colors focus:border-teal-500 focus:ring-2 focus:ring-teal-100 disabled:opacity-60 sm:w-64 xl:w-80">
            {models.length === 0 && <option value="">无可用模型</option>}
            {models.map(model => <option key={model.id} value={model.id}>{model.name} · {model.models?.[0]}</option>)}
          </select>
          <button
            type="button"
            onClick={() => { setSessionsOpen(false); setConfigOpen(value => !value) }}
            aria-label={configOpen ? '关闭助手配置' : '打开助手配置'}
            aria-expanded={configOpen}
            title="助手配置"
            className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border transition-all active:scale-[0.98] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-300 ${configOpen
              ? 'border-amber-400 bg-amber-100 text-amber-800'
              : 'border-amber-200 bg-amber-50 text-amber-700 hover:border-amber-300 hover:bg-amber-100 hover:text-amber-800'}`}
          >
            <Settings2 size={15} />
          </button>
          <div className="relative">
            <button
              type="button"
              onClick={() => setSessionsOpen(value => !value)}
              aria-label="查看会话记录"
              aria-expanded={sessionsOpen}
              title="查看会话记录"
              className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border transition-all active:scale-[0.98] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-300 ${sessionsOpen
                ? 'border-sky-400 bg-sky-100 text-sky-800'
                : 'border-sky-200 bg-sky-50 text-sky-700 hover:border-sky-300 hover:bg-sky-100 hover:text-sky-800'}`}
            >
              <History size={15} />
            </button>
            <SessionHistoryPopover
              open={sessionsOpen}
              items={conversations.map(conversation => ({ ...conversation, updatedAt: conversation.updated_at }))}
              currentId={selectedId}
              onClose={() => setSessionsOpen(false)}
              onCreate={async () => {
                const created = await createConversation()
                if (created) setSessionsOpen(false)
              }}
              onSelect={id => { setSelectedId(id); setSessionsOpen(false) }}
              onDelete={id => {
                const conversation = conversations.find(item => item.id === id)
                if (conversation) return deleteConversation(conversation)
              }}
              renderItemIcon={() => <MessageSquare size={16} />}
              emptyDescription="新建会话后，可随时回到之前的任务、Skill 调用与 MCP 执行记录。"
              topOffsetClassName="mt-[22px]"
            />
          </div>
        </header>

        <main className="relative flex min-h-0 flex-1 flex-col overflow-hidden">
          {loading ? (
            <div className="flex flex-1 items-center justify-center"><Loader2 size={22} className="animate-spin text-teal-600" /></div>
          ) : !hasMessages ? (
            <div className="flex flex-1 items-center justify-center px-4 sm:px-8">
              <div className="relative w-full max-w-3xl -translate-y-10 sm:-translate-y-14">
                <EmptyState />
                {renderComposer(true)}
              </div>
            </div>
          ) : (
            <div className="h-full overflow-y-auto">
              <div className="mx-auto w-full max-w-4xl space-y-7 px-4 pb-28 pt-6 sm:px-8">
                {messages.map(message => <ChatMessage key={message.id} message={message} />)}
                {pending && <ConfirmationCard pending={pending} busy={decisionBusy} onDecision={decision => void decide(decision)} />}
                <div ref={messagesEndRef} />
              </div>
            </div>
          )}
        </main>

        {hasMessages && (
          <footer className="shrink-0 bg-[var(--color-bg-elevated)] px-4 pb-8 pt-2 sm:px-8 sm:pb-10">
            <div className="mx-auto max-w-4xl">
              {renderComposer()}
            </div>
          </footer>
        )}
      </section>

      <ConfigurationPanel
        open={configOpen}
        onClose={() => setConfigOpen(false)}
        skills={skills}
        servers={servers}
        refreshSkills={refreshSkills}
        refreshServers={refreshServers}
      />
    </div>
  )
}
