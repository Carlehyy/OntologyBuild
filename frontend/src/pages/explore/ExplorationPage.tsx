/**
 * 业务探索 — 对话式业务建模/需求建模工作台
 *
 * 双区工作台：探索对话（SSE 流式 + 工具轨迹） | 业务场景（六类模型实时沉淀）
 * 顶部动作：生成需求文档 → 需求文档工作区里生成本体模型 → 人审后落地本体。
 */
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  Bot, CircleHelp, Compass, Download, ExternalLink, Files, FileText, Globe2, History, List,
  Loader2, Paperclip, Plus, Send, ShieldAlert, ShieldCheck, Trash2, User, Wrench, X,
} from 'lucide-react'
import {
  explorationApi, streamExplorationChat,
  type BusinessCanvas, type BxAttachment, type BxDraft, type BxQuestion, type BxStep,
  type BxSession, type Completeness, type Readiness,
} from '@/api/exploration'
import { modelApi } from '@/api/ontologies'
import MermaidBlock from '@/components/MermaidBlock'
import { ConfirmModal } from '@/components/ui/Modal'
import { useToast } from '@/components/ui/Toast'
import Md from './Md'
import CanvasPanel from './CanvasPanel'
import DocumentsDrawer from './DocumentsDrawer'
import DraftReviewDrawer from './DraftReviewDrawer'
import FileWorkspaceDrawer from './FileWorkspaceDrawer'
import type { ModelConfig } from '@/types/ontology'

interface ChatMsg {
  id: string
  role: 'user' | 'assistant'
  content: string
  steps: BxStep[]
  streaming?: boolean
  createdAt?: string
}

let _mid = 0
const nextId = () => `m-${Date.now()}-${_mid++}`

const SUGGESTIONS = [
  '我们是一家贸易公司，想梳理订单到回款的业务',
  '帮我梳理设备巡检与维修派工的业务模型',
  '我想把售后工单的处理流程建成本体',
]

// 与后端 allowed_upload_extensions 对齐
const ATTACH_ACCEPT = '.csv,.xlsx,.xls,.json,.xml,.pdf,.docx,.doc,.pptx,.ppt,.md,.txt'
const TEXTAREA_LINE_HEIGHT = 20
const TEXTAREA_MAX_LINES = 10
const TEXTAREA_MIN_HEIGHT = 28
const TEXTAREA_MAX_HEIGHT = TEXTAREA_LINE_HEIGHT * TEXTAREA_MAX_LINES + 8
const clamp = (value: number, min: number, max: number) => Math.min(Math.max(value, min), max)

function useExplorationLayout() {
  const containerRef = useRef<HTMLDivElement>(null)
  const [sizes, setSizes] = useState<[number, number]>([68, 32])

  const startResize = useCallback((event: React.PointerEvent<HTMLDivElement>) => {
    event.preventDefault()
    const rect = containerRef.current?.getBoundingClientRect()
    if (!rect) return

    const startX = event.clientX
    const start = sizes
    const min: [number, number] = [48, 24]
    const pairTotal = start[0] + start[1]
    const previousCursor = document.body.style.cursor
    const previousUserSelect = document.body.style.userSelect
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'

    const onMove = (moveEvent: PointerEvent) => {
      const delta = ((moveEvent.clientX - startX) / rect.width) * 100
      const left = clamp(start[0] + delta, min[0], pairTotal - min[1])
      setSizes([left, pairTotal - left])
    }
    const onUp = () => {
      document.body.style.cursor = previousCursor
      document.body.style.userSelect = previousUserSelect
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
    }
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
  }, [sizes])

  return { containerRef, sizes, startResize }
}

function ExplorationSplitHandle({ onPointerDown }: { onPointerDown: (event: React.PointerEvent<HTMLDivElement>) => void }) {
  return (
    <div
      role="separator"
      aria-label="调整探索对话与业务场景宽度"
      aria-orientation="vertical"
      onPointerDown={onPointerDown}
      className="group flex cursor-col-resize items-center justify-center"
    >
      <div className="h-16 w-1 rounded-full bg-[var(--color-border)] transition-all group-hover:h-24 group-hover:bg-teal-500/70" />
    </div>
  )
}

const formatSize = (n: number) =>
  n < 1024 ? `${n} B`
    : n < 1024 * 1024 ? `${(n / 1024).toFixed(0)} KB`
      : `${(n / 1024 / 1024).toFixed(1)} MB`

const errorMessage = (error: unknown, fallback: string): string => {
  if (!error || typeof error !== 'object') return fallback
  const value = error as { detail?: string | { message?: string }; message?: string }
  return typeof value.detail === 'string' ? value.detail : value.detail?.message || value.message || fallback
}

const STEP_LABELS: Record<string, string> = {
  upsert_elements: '沉淀画布',
  remove_elements: '修正画布',
  raise_questions: '登记问题',
  resolve_questions: '销账',
  show_diagram: '生成图表',
  use_skill: '激活技能',
  web_search: '联网检索',
}

function StepTrace({ steps, running }: { steps: BxStep[]; running?: boolean }) {
  if (steps.length === 0 && !running) return null
  return (
    <div className="mb-3 rounded-lg bg-[var(--color-bg-base)] border border-[var(--color-border)] px-3 py-2.5 space-y-2">
      {steps.map((s, i) => (
        <div key={i}>
          <div className="flex items-start gap-2.5">
            <div className={`mt-px w-5 h-5 rounded-md flex items-center justify-center shrink-0 ${s.error
              ? 'bg-[var(--color-danger-bg)] text-[var(--color-danger)]'
              : 'bg-teal-50 text-teal-600'}`}>
              {s.tool === 'web_search' ? <Globe2 size={11} /> : <Wrench size={11} />}
            </div>
            <div className="min-w-0 text-xs leading-5">
              <span className={`font-medium ${s.error ? 'text-[var(--color-danger)]' : 'text-[var(--color-text-primary)]'}`}>
                {STEP_LABELS[s.tool] || s.tool}
              </span>
              <span className="text-[var(--color-text-tertiary)]"> · {s.summary}</span>
            </div>
          </div>
          {s.searchResults && s.searchResults.length > 0 && (
            <div className="ml-[30px] mt-1.5 space-y-1">
              {s.searchResults.map((result, index) => (
                <a
                  key={`${result.url}-${index}`}
                  href={result.url}
                  target="_blank"
                  rel="noreferrer"
                  title={result.snippet || result.title}
                  className="group/source flex min-w-0 items-center gap-1.5 rounded px-1.5 py-1 text-[11px] text-[var(--color-text-secondary)] transition-colors hover:bg-teal-50 hover:text-teal-700"
                >
                  <span className="shrink-0 font-mono text-[10px] text-[var(--color-text-tertiary)]">[{index + 1}]</span>
                  <span className="min-w-0 flex-1 truncate">{result.title}</span>
                  <ExternalLink size={10} className="shrink-0 opacity-0 transition-opacity group-hover/source:opacity-100" />
                </a>
              ))}
            </div>
          )}
          {/* show_diagram 的确定性 mermaid 随步骤直接出现在对话流中，历史可回放 */}
          {s.diagram && (
            <div className="mt-2 ml-[30px]">
              <div className="text-[11px] font-medium text-[var(--color-text-secondary)] mb-1">
                {s.diagram.title}
                <span className="ml-1.5 font-normal text-[var(--color-text-tertiary)]">由画布确定性生成 · 请核对与实际是否一致</span>
              </div>
              <MermaidBlock chart={s.diagram.mermaid} title={s.diagram.title} warnings={s.diagram.warnings || []} />
            </div>
          )}
        </div>
      ))}
      {running && (
        <div className="flex items-center gap-2.5">
          <div className="w-5 h-5 rounded-md bg-teal-50 flex items-center justify-center shrink-0">
            <Loader2 size={11} className="animate-spin text-teal-600" />
          </div>
          <span className="text-xs text-[var(--color-text-tertiary)]">
            {steps.length === 0 ? '正在理解业务，规划澄清问题…' : '正在把确认的信息沉淀进画布…'}
          </span>
        </div>
      )}
    </div>
  )
}

/** 开放堵门问题的快捷答复：点选候选值直接作为回答发送（定量闭环的最短路径） */
function QuickReplies({ questions, disabled, onAnswer, onCustom }: {
  questions: BxQuestion[]
  disabled?: boolean
  onAnswer: (text: string) => void
  onCustom: (prefill: string) => void
}) {
  if (questions.length === 0) return null
  return (
    <div className="mb-2 space-y-1.5">
      {questions.map(q => (
        <div key={q.id} className="flex items-start gap-2 rounded-lg border border-amber-200/80 bg-amber-50/50 px-2.5 py-1.5">
          <CircleHelp size={13} className="mt-[3px] shrink-0 text-amber-500" />
          <div className="min-w-0 flex-1">
            <span className="text-xs text-amber-900/90 leading-5">{q.question}</span>
            <span className="ml-2 inline-flex flex-wrap gap-1 align-middle">
              {(q.options || []).slice(0, 4).map(opt => (
                <button
                  key={opt}
                  disabled={disabled}
                  onClick={() => onAnswer(`「${q.question}」我的答复：${opt}`)}
                  className="px-2 py-0.5 rounded-md text-[11px] border border-amber-300 bg-white text-amber-800 hover:bg-amber-100 disabled:opacity-40"
                >
                  {opt}
                </button>
              ))}
              <button
                disabled={disabled}
                onClick={() => onCustom(`「${q.question}」我的答复：`)}
                className="px-2 py-0.5 rounded-md text-[11px] border border-dashed border-amber-300 text-amber-700 hover:bg-amber-100 disabled:opacity-40"
              >
                自定义…
              </button>
            </span>
          </div>
        </div>
      ))}
    </div>
  )
}

export default function ExplorationPage() {
  const { containerRef, sizes, startResize } = useExplorationLayout()
  const { toast } = useToast()
  // -- 会话 --
  const { data: sessions = [], refetch: refetchSessions } = useQuery({
    queryKey: ['bx-sessions'], queryFn: () => explorationApi.sessions(),
  })
  const [sid, setSid] = useState('')

  // -- 模型选择 --
  const { data: models = [] } = useQuery<ModelConfig[]>({ queryKey: ['models'], queryFn: () => modelApi.list() })
  const llmModels = models.filter(m => m.config_type === 'llm' || !m.config_type)
  const [modelId, setModelId] = useState('')

  // -- 对话 + 画布 --
  const [messages, setMessages] = useState<ChatMsg[]>([])
  const [canvas, setCanvas] = useState<BusinessCanvas | null>(null)
  const [completeness, setCompleteness] = useState<Completeness | null>(null)
  const [readiness, setReadiness] = useState<Readiness | null>(null)
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [webSearch, setWebSearch] = useState(false)
  const [showMessageHistory, setShowMessageHistory] = useState(false)
  const [showSessionHistory, setShowSessionHistory] = useState(false)
  const [deleteSessionTarget, setDeleteSessionTarget] = useState<BxSession | null>(null)
  const [deletingSession, setDeletingSession] = useState(false)
  const [docsOpen, setDocsOpen] = useState(false)
  const [workspaceOpen, setWorkspaceOpen] = useState(false)
  const [reviewDraft, setReviewDraft] = useState<BxDraft | null>(null)
  const [genDocBusy, setGenDocBusy] = useState(false)
  const [banner, setBanner] = useState('')
  // -- 会话附件 --
  const [attachments, setAttachments] = useState<BxAttachment[]>([])
  const [uploads, setUploads] = useState<{ uid: string; name: string; ts: number }[]>([])
  const [attachError, setAttachError] = useState('')
  const fileInputRef = useRef<HTMLInputElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const chatScrollRef = useRef<HTMLDivElement>(null)
  const stickToBottomRef = useRef(true)

  const myMessages = useMemo(() => messages.filter(m => m.role === 'user'), [messages])
  const jumpToMessage = useCallback((id: string) => {
    setShowMessageHistory(false)
    requestAnimationFrame(() => {
      document.getElementById(`explore-msg-${id}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    })
  }, [])

  // 按真实排版高度（含自动换行）伸展；十行后只让 textarea 内部滚动。
  useLayoutEffect(() => {
    const textarea = textareaRef.current
    if (!textarea) return
    // 以单行基线测量。0 会与 min-height 叠加，auto 在部分 Chromium
    // 版本会保留上次的内容高度；显式基线能让清空/发送后的高度可靠复位。
    textarea.style.height = `${TEXTAREA_MIN_HEIGHT}px`
    // 空值时 scrollHeight 会把两行 placeholder 也算进去，不能拿它判断扩展。
    const contentHeight = input ? textarea.scrollHeight : TEXTAREA_MIN_HEIGHT
    const nextHeight = Math.max(TEXTAREA_MIN_HEIGHT, Math.min(contentHeight, TEXTAREA_MAX_HEIGHT))
    textarea.style.height = `${nextHeight}px`
    textarea.style.overflowY = contentHeight > TEXTAREA_MAX_HEIGHT ? 'auto' : 'hidden'
  }, [input])

  const updateScrollStickiness = () => {
    const el = chatScrollRef.current
    if (!el) return
    stickToBottomRef.current = el.scrollHeight - el.clientHeight - el.scrollTop < 96
  }

  // 消息与「上传的附件」按时间合并成一条对话时间线 —— 附件直接体现在对话流中，
  // 而不是单独挂在输入框上方。
  type TLItem =
    | { key: string; ts: number; kind: 'msg'; msg: ChatMsg }
    | { key: string; ts: number; kind: 'att'; att: BxAttachment }
    | { key: string; ts: number; kind: 'up'; up: { uid: string; name: string; ts: number } }
  const timeline = useMemo<TLItem[]>(() => {
    const items: TLItem[] = []
    let lastTs = 0
    messages.forEach(m => {
      let t = m.createdAt ? Date.parse(m.createdAt) : NaN
      if (Number.isNaN(t)) t = lastTs + 1   // 缺时间戳时保持相对顺序
      lastTs = t
      items.push({ key: m.id, ts: t, kind: 'msg', msg: m })
    })
    attachments.forEach(a => items.push({
      key: `att-${a.id}`, ts: Date.parse(a.createdAt) || 0, kind: 'att', att: a,
    }))
    uploads.forEach(u => items.push({ key: u.uid, ts: u.ts, kind: 'up', up: u }))
    return items.sort((x, y) => x.ts - y.ts)
  }, [messages, attachments, uploads])

  useLayoutEffect(() => {
    const el = chatScrollRef.current
    if (!el || !stickToBottomRef.current || timeline.length === 0) return
    el.scrollTop = el.scrollHeight
  }, [timeline])

  const loadSession = useCallback(async (id: string) => {
    setSid(id)
    setShowMessageHistory(false)
    setShowSessionHistory(false)
    setBanner('')
    setAttachError('')
    setAttachments([])
    setUploads([])
    const detail = await explorationApi.session(id)
    setMessages((detail.messages || []).map(m => ({
      id: m.id, role: m.role, content: m.content, steps: m.steps || [], createdAt: m.createdAt,
    })))
    setCanvas(detail.canvas)
    setCompleteness(detail.completeness)
    setReadiness(detail.readiness)
    explorationApi.attachments(id).then(setAttachments).catch(() => { /* 非致命 */ })
  }, [])

  // 首次进入自动选中最近会话
  useEffect(() => {
    if (!sid && sessions.length > 0) void loadSession(sessions[0].id)
  }, [sessions, sid, loadSession])

  const newSession = async () => {
    const s = await explorationApi.createSession()
    await refetchSessions()
    setMessages([])
    setCanvas(null)
    setCompleteness(null)
    setReadiness(null)
    setAttachments([])
    setUploads([])
    setAttachError('')
    setShowMessageHistory(false)
    setShowSessionHistory(false)
    setSid(s.id)
    const detail = await explorationApi.session(s.id)
    setCanvas(detail.canvas)
    setCompleteness(detail.completeness)
    setReadiness(detail.readiness)
  }

  const removeSession = async (id: string) => {
    setDeletingSession(true)
    try {
      await explorationApi.deleteSession(id)
      if (id === sid) {
        setSid('')
        setMessages([])
        setCanvas(null)
        setCompleteness(null)
        setReadiness(null)
        setAttachments([])
        setUploads([])
        setShowMessageHistory(false)
      }
      setDeleteSessionTarget(null)
      await refetchSessions()
      toast({ tone: 'success', title: '会话已删除' })
    } catch (error: unknown) {
      toast({ tone: 'error', title: '会话删除失败', description: errorMessage(error, '请稍后重试。') })
    } finally {
      setDeletingSession(false)
    }
  }

  // 无会话时懒创建（首条消息 / 首个附件都可能触发）
  const ensureSession = async (): Promise<string> => {
    if (sid) return sid
    const s = await explorationApi.createSession()
    setSid(s.id)
    refetchSessions()
    return s.id
  }

  const pickFiles = () => fileInputRef.current?.click()

  const handleFiles = async (files: FileList | null) => {
    if (!files || files.length === 0) return
    setAttachError('')
    const targetSid = await ensureSession()
    for (const file of Array.from(files)) {
      const uid = `up-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`
      setUploads(prev => [...prev, { uid, name: file.name, ts: Date.now() }])
      try {
        const att = await explorationApi.uploadAttachment(targetSid, file)
        setAttachments(prev => [...prev, att])
      } catch (error: unknown) {
        setAttachError(`「${file.name}」上传失败：${errorMessage(error, '无法读取文件内容')}`)
      } finally {
        setUploads(prev => prev.filter(u => u.uid !== uid))
      }
    }
    if (fileInputRef.current) fileInputRef.current.value = ''
  }

  const removeAttachment = async (aid: string) => {
    if (!sid) return
    setAttachments(prev => prev.filter(a => a.id !== aid))
    try {
      await explorationApi.deleteAttachment(sid, aid)
    } catch { /* 前端已移除，忽略后端错误 */ }
  }

  const downloadAttachment = async (att: BxAttachment) => {
    if (!sid) return
    try {
      const blob = await explorationApi.downloadAttachment(sid, att.id)
      const url = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = att.filename
      anchor.click()
      URL.revokeObjectURL(url)
    } catch (error: unknown) {
      setAttachError(`「${att.filename}」下载失败：${errorMessage(error, '文件不可用')}`)
    }
  }

  const send = async (text?: string) => {
    const message = (text ?? input).trim()
    if (!message || busy) return
    const targetSid = await ensureSession()
    setInput('')
    setBusy(true)
    setBanner('')
    const now = new Date().toISOString()
    setMessages(prev => [...prev,
      { id: nextId(), role: 'user', content: message, steps: [], createdAt: now },
      { id: nextId(), role: 'assistant', content: '', steps: [], streaming: true, createdAt: now },
    ])

    const patchLast = (fn: (m: ChatMsg) => ChatMsg) =>
      setMessages(prev => prev.map((m, i) => (i === prev.length - 1 ? fn(m) : m)))

    try {
      await streamExplorationChat(targetSid, {
        message,
        modelId: modelId || undefined,
        webSearch,
      }, e => {
        if (e.type === 'step') {
          const step: BxStep = {
            tool: e.tool, arguments: e.arguments, summary: e.summary,
            durationMs: e.durationMs, error: e.error, diagram: e.diagram,
            searchResults: e.searchResults,
          }
          patchLast(m => ({ ...m, steps: [...m.steps, step] }))
        } else if (e.type === 'canvas') {
          setCanvas(e.canvas)
          setCompleteness(e.completeness)
          setReadiness(e.readiness)
        } else if (e.type === 'answer') {
          patchLast(m => ({ ...m, content: e.content, streaming: false }))
        } else if (e.type === 'error') {
          patchLast(m => ({ ...m, content: m.content || `⚠️ ${e.message}`, streaming: false }))
        }
      })
    } catch (error: unknown) {
      patchLast(m => ({ ...m, content: `⚠️ ${errorMessage(error, '请求失败')}`, streaming: false }))
    } finally {
      patchLast(m => ({ ...m, streaming: false }))
      setBusy(false)
      explorationApi.attachments(targetSid).then(setAttachments).catch(() => { /* 非致命 */ })
      refetchSessions()   // 标题可能已更新
    }
  }

  const generateDocument = async () => {
    if (!sid || genDocBusy) return
    setGenDocBusy(true)
    setBanner('')
    try {
      await explorationApi.generateDocument(sid, modelId || undefined)
      setDocsOpen(true)
    } catch (error: unknown) {
      setBanner(errorMessage(error, '文档生成失败'))
    } finally {
      setGenDocBusy(false)
    }
  }

  const canvasCount = completeness
    ? Object.values(completeness.counts).reduce((a, b) => a + b, 0) : 0
  const panelClass = 'min-h-0 min-w-0 overflow-hidden rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-elevated)] shadow-sm'

  // 开放堵门问题 → 输入框上方的快捷答复（最多展示 2 个，按登记顺序）
  const openBlocking = (canvas?.questions || [])
    .filter(q => q.status === 'open' && q.kind === 'blocking')
  const askInChat = (text: string) => { void send(text) }
  const prefillInput = (text: string) => {
    setInput(text)
    textareaRef.current?.focus()
  }

  return (
    <div className="relative flex h-full min-h-[560px] overflow-hidden bg-[var(--color-bg-base)]">
      <div
        ref={containerRef}
        className="scrollbar-none grid min-h-0 flex-1 overflow-x-auto overflow-y-hidden p-1"
        style={{ gridTemplateColumns: `minmax(560px, ${sizes[0]}fr) 4px minmax(300px, ${sizes[1]}fr)` }}
      >
      {/* 对话区 */}
      <section className={`${panelClass} flex flex-col bg-white`}>
        <header className="flex h-14 shrink-0 items-center border-b border-[var(--color-border)] bg-white px-4">
          <div className="flex w-full min-w-0 items-center justify-between gap-2">
            <div className="flex min-w-0 flex-1 items-center gap-2">
              <div className="flex shrink-0 h-8 w-8 items-center justify-center rounded-md bg-teal-50 text-teal-600">
                <Compass size={18} />
              </div>
              <div className="min-w-0">
                <h3 className="truncate text-sm font-semibold text-[var(--color-text-primary)]">
                  {sessions.find(s => s.id === sid)?.title || '业务探索'}
                </h3>
                <p className="truncate text-[11px] text-[var(--color-text-tertiary)]">通过对话澄清业务，从而生成本体模型</p>
              </div>
            </div>
            <div className="flex shrink-0 items-center gap-2">
              {readiness && canvasCount > 0 && (
                <span
                  title={`当前阶段：${readiness.stage}\n堵门项 ${readiness.blockingCount} · 建议项 ${readiness.advisoryCount}（明细见右侧画布）`}
                  className={`inline-flex h-8 items-center gap-1.5 rounded-md border px-2.5 text-xs font-medium ${readiness.ready
                    ? 'border-teal-200 bg-teal-50 text-teal-700'
                    : 'border-amber-200 bg-amber-50 text-amber-700'}`}
                >
                  {readiness.ready ? <ShieldCheck size={13} /> : <ShieldAlert size={13} />}
                  质量门 {readiness.gatesPassed}/{readiness.gatesTotal}
                </span>
              )}
              <select
                value={modelId}
                onChange={e => setModelId(e.target.value)}
                className="h-8 cursor-pointer rounded-md border border-[var(--color-border)] bg-[var(--color-bg-base)] px-2 text-xs outline-none transition-colors focus:border-teal-400 focus:ring-2 focus:ring-teal-100"
                title="对话模型"
              >
                <option value="">默认模型</option>
                {llmModels.map(m => <option key={m.id} value={m.id}>{m.name}</option>)}
              </select>
              <button
                onClick={() => setWorkspaceOpen(true)}
                disabled={!sid}
                data-testid="workspace-files-button"
                title="查看文件清单"
                aria-label="查看文件清单"
                className="group relative inline-flex h-8 w-8 items-center justify-center rounded-md border border-[var(--color-border)] text-[var(--color-text-secondary)] transition-colors hover:border-teal-300 hover:bg-teal-50 hover:text-teal-700 active:scale-[0.98] disabled:opacity-40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-400"
              >
                <Files size={15} />
                {attachments.length > 0 && (
                  <span className="absolute -right-1.5 -top-1.5 flex min-w-4 items-center justify-center rounded-full bg-teal-600 px-1 text-[9px] font-semibold leading-4 text-white">
                    {attachments.length > 99 ? '99+' : attachments.length}
                  </span>
                )}
              </button>
              <div className="relative">
                <button
                  type="button"
                  onClick={() => setShowSessionHistory(value => !value)}
                  title="查看会话记录"
                  aria-label="查看会话记录"
                  aria-expanded={showSessionHistory}
                  className={`inline-flex h-8 w-8 items-center justify-center rounded-md border transition-colors active:scale-[0.98] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-400 ${showSessionHistory
                    ? 'border-teal-300 bg-teal-50 text-teal-700'
                    : 'border-[var(--color-border)] text-[var(--color-text-secondary)] hover:border-teal-300 hover:bg-teal-50 hover:text-teal-700'}`}
                >
                  <History size={15} />
                </button>
                {showSessionHistory && (
                  <>
                    <div className="fixed inset-0 z-20" onClick={() => setShowSessionHistory(false)} />
                    <div className="absolute right-0 top-full z-30 mt-[14px] w-[380px] overflow-hidden rounded-xl border border-[var(--color-border)] bg-[var(--color-bg-elevated)] shadow-[0_18px_52px_rgba(15,23,42,0.16)] animate-slide-up">
                      <div className="flex items-center gap-3 border-b border-[var(--color-border)] px-4 py-2.5">
                        <span className="shrink-0 text-sm font-semibold text-[var(--color-text-primary)]">历史会话</span>
                        <span className="inline-flex items-center gap-1.5 whitespace-nowrap text-xs text-teal-700">
                          <span className="h-1.5 w-1.5 rounded-full bg-teal-500" />
                          共 <span className="font-semibold tabular-nums">{sessions.length}</span> 个
                        </span>
                        <button
                          type="button"
                          onClick={() => void newSession()}
                          className="ml-auto inline-flex h-8 items-center gap-1.5 rounded-lg bg-teal-600 px-3 text-xs font-medium text-white transition-all hover:bg-teal-700 active:scale-[0.98] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-400"
                        >
                          <Plus size={13} /> 新建
                        </button>
                      </div>
                      <div className="scrollbar-thin max-h-[420px] overflow-y-auto overflow-x-hidden">
                        {sessions.length === 0 ? (
                          <div className="flex flex-col items-center px-6 py-14 text-center">
                            <span className="mb-3 flex h-12 w-12 items-center justify-center rounded-full bg-[var(--color-bg-base)] text-[var(--color-text-tertiary)]">
                              <History size={21} />
                            </span>
                            <p className="text-sm font-medium text-[var(--color-text-secondary)]">还没有历史会话</p>
                            <p className="mt-1 text-xs leading-5 text-[var(--color-text-tertiary)]">新建会话后，可随时回到之前的探索过程。</p>
                          </div>
                        ) : <div className="divide-y divide-[var(--color-border)]">{sessions.map(session => (
                          <div
                            key={session.id}
                            className={`group flex items-center gap-2.5 px-4 py-2 transition-colors ${session.id === sid
                              ? 'bg-teal-50/70'
                              : 'hover:bg-[var(--color-bg-hover)]'}`}
                          >
                            <button
                              type="button"
                              onClick={() => void loadSession(session.id)}
                              className="flex min-w-0 flex-1 items-center gap-3 text-left focus-visible:outline-none"
                            >
                              <span className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-lg ${session.id === sid
                                ? 'bg-teal-100 text-teal-700'
                                : 'bg-slate-100 text-slate-500'}`}>
                                <Compass size={16} />
                              </span>
                              <div className="min-w-0 flex-1">
                                <p className={`truncate text-sm font-medium ${session.id === sid ? 'text-teal-900' : 'text-[var(--color-text-primary)]'}`} title={session.title}>
                                  {session.title}
                                </p>
                                <p className="mt-0.5 text-[10px] tabular-nums text-[var(--color-text-tertiary)]">
                                  {new Date(session.updatedAt).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })}
                                </p>
                              </div>
                              {session.id === sid && <span className="rounded-md bg-white/80 px-2 py-1 text-[10px] font-medium text-teal-700">当前</span>}
                            </button>
                            <button
                              type="button"
                              onClick={() => setDeleteSessionTarget(session)}
                              title={`删除会话 ${session.title}`}
                              aria-label={`删除会话 ${session.title}`}
                              className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-slate-400 opacity-0 transition-all hover:bg-red-50 hover:text-red-600 group-hover:opacity-100 focus-visible:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-300"
                            >
                              <Trash2 size={14} />
                            </button>
                          </div>
                        ))}</div>}
                      </div>
                    </div>
                  </>
                )}
              </div>
            </div>
          </div>
        </header>

        {banner && (
          <div className="px-4 py-2 text-xs text-[var(--color-danger)] bg-[var(--color-danger-bg)] border-b border-[var(--color-border)]">
            {banner}
          </div>
        )}

        <div ref={chatScrollRef} onScroll={updateScrollStickiness} className="flex-1 overflow-y-auto bg-white px-4 py-4">
          {timeline.length === 0 && (
            <div className="h-full flex flex-col items-center justify-center gap-4">
              <div className="w-12 h-12 rounded-2xl bg-teal-50 flex items-center justify-center">
                <Compass size={22} className="text-teal-600" />
              </div>
              <div className="text-center">
                <div className="text-sm font-medium text-[var(--color-text-primary)]">从描述你的业务开始</div>
                <div className="mt-1 text-xs text-[var(--color-text-tertiary)] max-w-md leading-relaxed">
                  我会通过提问帮你澄清业务，并把确认的信息实时沉淀为对象、主体、行为、事件、规则、场景六类模型 —— 右侧画布随对话生长。
                </div>
              </div>
              <div className="flex flex-col gap-2 w-full max-w-md">
                {SUGGESTIONS.map(s => (
                  <button
                    key={s}
                    onClick={() => void send(s)}
                    className="text-left text-xs px-3.5 py-2.5 rounded-lg border border-[var(--color-border)] text-[var(--color-text-secondary)] hover:border-teal-400 hover:text-teal-700 transition-colors"
                  >
                    {s}
                  </button>
                ))}
              </div>
            </div>
          )}

          <div className="max-w-3xl mx-auto space-y-5">
            {timeline.map(item => {
              // 上传的附件：作为用户侧的一条对话记录出现在对话流中
              if (item.kind === 'att' || item.kind === 'up') {
                const uploading = item.kind === 'up'
                const name = uploading ? item.up.name : item.att.filename
                return (
                  <div key={item.key} className="flex gap-3 flex-row-reverse">
                    <div className="w-7 h-7 rounded-lg flex items-center justify-center shrink-0 bg-[var(--color-nav-bg)] text-white">
                      <Paperclip size={14} />
                    </div>
                    <div className={`group flex items-center gap-2.5 rounded-xl border bg-[var(--color-bg-elevated)] px-3 py-2 max-w-[85%] ${
                      uploading ? 'border-dashed border-[var(--color-border)]' : 'border-[var(--color-border)]'}`}>
                      <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-teal-50 text-teal-600">
                        {uploading ? <Loader2 size={15} className="animate-spin" /> : <FileText size={16} />}
                      </span>
                      <div className="min-w-0 text-left">
                        <div className="truncate text-sm font-medium text-[var(--color-text-primary)]" title={name}>{name}</div>
                        <div className="mt-0.5 text-[11px] text-[var(--color-text-tertiary)]">
                          {uploading ? '上传中…' : `参考资料 · ${formatSize(item.att.fileSize)} · 仅本会话可见`}
                        </div>
                      </div>
                      {!uploading && (
                        <div className="flex shrink-0 items-center gap-0.5 opacity-0 transition-opacity group-hover:opacity-100">
                          <button
                            onClick={() => void downloadAttachment(item.att)}
                            title="下载文件"
                            className="p-1 rounded text-[var(--color-text-tertiary)] hover:text-teal-600 hover:bg-[var(--color-bg-hover)]"
                          >
                            <Download size={13} />
                          </button>
                          <button
                            onClick={() => void removeAttachment(item.att.id)}
                            title="移除参考资料"
                            className="p-1 rounded text-[var(--color-text-tertiary)] hover:text-[var(--color-danger)] hover:bg-[var(--color-bg-hover)]"
                          >
                            <X size={13} />
                          </button>
                        </div>
                      )}
                    </div>
                  </div>
                )
              }
              const m = item.msg
              return (
                <div
                  key={item.key}
                  id={m.role === 'user' ? `explore-msg-${m.id}` : undefined}
                  className={`flex scroll-mt-4 gap-3 ${m.role === 'user' ? 'flex-row-reverse' : ''}`}
                >
                  <div className={`w-7 h-7 rounded-lg flex items-center justify-center shrink-0 ${m.role === 'user'
                    ? 'bg-[var(--color-nav-bg)] text-white' : 'bg-teal-50 text-teal-600'}`}>
                    {m.role === 'user' ? <User size={14} /> : <Bot size={14} />}
                  </div>
                  <div className={`min-w-0 max-w-[85%] ${m.role === 'user' ? 'text-right' : ''}`}>
                    {m.role === 'assistant' && <StepTrace steps={m.steps} running={m.streaming} />}
                    {m.content && (
                      <div className={`inline-block text-left rounded-xl px-3.5 py-2.5 ${m.role === 'user'
                        ? 'whitespace-pre-wrap break-words bg-[var(--color-nav-bg)] text-white text-sm leading-relaxed'
                        : 'bg-[var(--color-bg-elevated)] border border-[var(--color-border)]'}`}>
                        {m.role === 'user' ? m.content : <Md text={m.content} />}
                      </div>
                    )}
                  </div>
                </div>
              )
            })}
          </div>
        </div>

        <div
          data-testid="exploration-composer-region"
          className="relative bg-[var(--color-bg-elevated)] px-4 pb-4 pt-3"
        >
          <input
            ref={fileInputRef}
            type="file"
            multiple
            accept={ATTACH_ACCEPT}
            className="hidden"
            onChange={e => void handleFiles(e.target.files)}
          />
          <div className="max-w-3xl mx-auto">
            {attachError && (
              <div className="mb-1.5 text-[11px] text-[var(--color-danger)] truncate" title={attachError}>
                {attachError}
              </div>
            )}
            {/* 澄清账本的开放堵门问题 → 点选候选值即答（定量闭环最短路径） */}
            <QuickReplies
              questions={openBlocking.slice(0, 2)}
              disabled={busy}
              onAnswer={askInChat}
              onCustom={prefillInput}
            />
            {openBlocking.length > 2 && (
              <div className="mb-1.5 text-[11px] text-[var(--color-text-tertiary)]">
                还有 {openBlocking.length - 2} 个待澄清问题，见右侧画布「澄清账本」
              </div>
            )}
            {/* 消息输入框：回形针上传的附件直接体现在上方对话流中，输入框只承载本轮消息 */}
            <div
              data-testid="exploration-composer-shell"
              className="relative overflow-visible rounded-xl border border-[var(--color-border)] bg-[var(--color-bg-elevated)] transition-colors focus-within:border-teal-500 focus-within:ring-1 focus-within:ring-teal-500/10"
            >
              <div data-testid="exploration-composer-input" className="px-3 pb-2 pt-2.5">
                <textarea
                  ref={textareaRef}
                  value={input}
                  onChange={e => setInput(e.target.value)}
                  onKeyDown={e => {
                    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); void send() }
                  }}
                  rows={1}
                  placeholder="描述业务、回答澄清问题…（Enter 发送，Shift+Enter 换行）"
                  aria-label="业务探索消息"
                  data-testid="exploration-composer"
                  className="scrollbar-thin block min-h-7 w-full resize-none bg-transparent py-1 text-sm leading-5 outline-none placeholder:text-[var(--color-text-tertiary)]"
                />
              </div>

              <div
                data-testid="exploration-composer-toolbar"
                className="flex min-h-12 items-center justify-between gap-3 px-2.5 py-2"
              >
                <div className="flex min-w-0 items-center gap-1">
                  <button
                    type="button"
                    onClick={pickFiles}
                    title="上传参考资料（仅本会话可见，用于辅助澄清业务）"
                    aria-label="上传参考资料"
                    className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-[var(--color-text-tertiary)] transition-colors hover:bg-[var(--color-bg-hover)] hover:text-teal-600 active:scale-[0.98] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-400"
                  >
                    <Paperclip size={16} />
                  </button>
                  <button
                    type="button"
                    onClick={() => setWebSearch(value => !value)}
                    aria-pressed={webSearch}
                    data-testid="web-search-toggle"
                    title={webSearch ? '联网搜索已开启，点击关闭' : '联网搜索已关闭，点击开启'}
                    className={`inline-flex h-8 shrink-0 items-center gap-1.5 rounded-lg border px-2 text-[11px] font-medium transition-all active:scale-[0.98] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-400 ${webSearch
                      ? 'border-teal-300 bg-teal-50 text-teal-700'
                      : 'border-transparent text-[var(--color-text-tertiary)] hover:border-[var(--color-border)] hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text-secondary)]'}`}
                  >
                    <Globe2 size={15} />
                    <span>联网</span>
                    <span className={`h-1.5 w-1.5 rounded-full transition-colors ${webSearch ? 'bg-teal-500' : 'bg-[var(--color-border)]'}`} />
                  </button>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <button
                    type="button"
                    onClick={() => void send()}
                    disabled={busy || !input.trim()}
                    title="发送消息"
                    aria-label="发送消息"
                    className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-teal-600 text-white transition-all hover:bg-teal-700 active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-400 focus-visible:ring-offset-1"
                  >
                    {busy ? <Loader2 size={14} className="animate-spin" /> : <Send size={14} />}
                  </button>
                  <button
                    type="button"
                    onClick={() => setShowMessageHistory(value => !value)}
                    disabled={myMessages.length === 0}
                    title="我发送的消息 · 快速跳转"
                    aria-label="查看我发送的消息"
                    aria-expanded={showMessageHistory}
                    data-testid="message-history-button"
                    className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border transition-colors active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-30 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-400 ${showMessageHistory
                      ? 'border-teal-300 bg-teal-50 text-teal-700'
                      : 'border-[var(--color-border)] text-[var(--color-text-tertiary)] hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text-secondary)]'}`}
                  >
                    <List size={15} />
                  </button>
                </div>
              </div>

              {showMessageHistory && (
                <>
                  <div className="fixed inset-0 z-20" onClick={() => setShowMessageHistory(false)} />
                  <div className="absolute bottom-full right-0 z-30 mb-2 w-72 overflow-hidden rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-elevated)] shadow-lg">
                    <div className="flex items-center justify-between border-b border-[var(--color-border)] px-3 py-2">
                      <span className="text-[11px] font-medium text-[var(--color-text-secondary)]">我发送的消息</span>
                      <span className="text-[10px] text-[var(--color-text-tertiary)]">点击跳转 · 共 {myMessages.length} 条</span>
                    </div>
                    <div className="scrollbar-thin max-h-64 overflow-auto py-1">
                      {[...myMessages].reverse().map((message, index) => (
                        <button
                          type="button"
                          key={message.id}
                          onClick={() => jumpToMessage(message.id)}
                          title={message.content}
                          className="flex w-full items-start gap-2 px-3 py-1.5 text-left transition-colors hover:bg-[var(--color-bg-hover)] focus-visible:bg-[var(--color-bg-hover)] focus-visible:outline-none"
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
      </section>

      <ExplorationSplitHandle onPointerDown={startResize} />

      {/* 业务场景 */}
      <aside className={`${panelClass} flex flex-col bg-white`}>
        <CanvasPanel
          sessionId={sid || undefined}
          canvas={canvas}
          completeness={completeness}
          readiness={readiness}
          onAsk={busy ? undefined : askInChat}
          onOpenDocuments={() => setDocsOpen(true)}
        />
      </aside>
      </div>

      {docsOpen && sid && (
        <DocumentsDrawer
          sessionId={sid}
          onClose={() => setDocsOpen(false)}
          onDraftCreated={draft => { setDocsOpen(false); setReviewDraft(draft) }}
          onGenerate={generateDocument}
          documentGenerating={genDocBusy}
          canGenerateDocument={canvasCount > 0}
        />
      )}
      {workspaceOpen && sid && (
        <FileWorkspaceDrawer
          sessionId={sid}
          files={attachments}
          onFilesChange={setAttachments}
          onClose={() => setWorkspaceOpen(false)}
        />
      )}
      {reviewDraft && (
        <DraftReviewDrawer draft={reviewDraft} onClose={() => setReviewDraft(null)} />
      )}
      <ConfirmModal
        open={!!deleteSessionTarget}
        onClose={() => { if (!deletingSession) setDeleteSessionTarget(null) }}
        onConfirm={() => { if (deleteSessionTarget) void removeSession(deleteSessionTarget.id) }}
        title={deleteSessionTarget ? `删除「${deleteSessionTarget.title}」？` : '删除会话？'}
        description="该会话中的对话、附件与业务画布记录将被永久删除，此操作无法撤销。"
        confirmText="删除会话"
        variant="danger"
        loading={deletingSession}
      />
    </div>
  )
}
