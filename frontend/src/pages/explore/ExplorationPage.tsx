/**
 * 业务探索 — 对话式业务建模/需求建模工作台
 *
 * 三栏：会话列表 | 探索对话（SSE 流式 + 工具轨迹） | 业务画布（六类模型实时沉淀）
 * 顶部动作：生成需求文档 → 文档抽屉里生成本体草稿 → 草稿人审后落地本体。
 */
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  Bot, Compass, FileText, History, Loader2, Paperclip, Plus, Send, Sparkles,
  Trash2, User, Wrench, X,
} from 'lucide-react'
import {
  explorationApi, streamExplorationChat,
  type BusinessCanvas, type BxAttachment, type BxDraft, type BxStep, type Completeness,
} from '@/api/exploration'
import { modelApi } from '@/api/ontologies'
import Md from './Md'
import CanvasPanel from './CanvasPanel'
import DocumentsDrawer from './DocumentsDrawer'
import DraftReviewDrawer from './DraftReviewDrawer'

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

const formatSize = (n: number) =>
  n < 1024 ? `${n} B`
    : n < 1024 * 1024 ? `${(n / 1024).toFixed(0)} KB`
      : `${(n / 1024 / 1024).toFixed(1)} MB`

function StepTrace({ steps, running }: { steps: BxStep[]; running?: boolean }) {
  if (steps.length === 0 && !running) return null
  return (
    <div className="mb-3 rounded-lg bg-[var(--color-bg-base)] border border-[var(--color-border)] px-3 py-2.5 space-y-2">
      {steps.map((s, i) => (
        <div key={i} className="flex items-start gap-2.5">
          <div className={`mt-px w-5 h-5 rounded-md flex items-center justify-center shrink-0 ${s.error
            ? 'bg-[var(--color-danger-bg)] text-[var(--color-danger)]'
            : 'bg-teal-50 text-teal-600'}`}>
            <Wrench size={11} />
          </div>
          <div className="min-w-0 text-xs leading-5">
            <span className={`font-medium ${s.error ? 'text-[var(--color-danger)]' : 'text-[var(--color-text-primary)]'}`}>
              {s.tool === 'upsert_elements' ? '沉淀画布' : s.tool === 'remove_elements' ? '修正画布' : s.tool}
            </span>
            <span className="text-[var(--color-text-tertiary)]"> · {s.summary}</span>
          </div>
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

export default function ExplorationPage() {
  // -- 会话 --
  const { data: sessions = [], refetch: refetchSessions } = useQuery({
    queryKey: ['bx-sessions'], queryFn: () => explorationApi.sessions(),
  })
  const [sid, setSid] = useState('')

  // -- 模型选择 --
  const { data: models = [] } = useQuery({ queryKey: ['models'], queryFn: () => modelApi.list() as any })
  const llmModels = Array.isArray(models)
    ? (models as any[]).filter((m: any) => m.config_type === 'llm' || !m.config_type) : []
  const [modelId, setModelId] = useState('')

  // -- 对话 + 画布 --
  const [messages, setMessages] = useState<ChatMsg[]>([])
  const [canvas, setCanvas] = useState<BusinessCanvas | null>(null)
  const [completeness, setCompleteness] = useState<Completeness | null>(null)
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [docsOpen, setDocsOpen] = useState(false)
  const [reviewDraft, setReviewDraft] = useState<BxDraft | null>(null)
  const [genDocBusy, setGenDocBusy] = useState(false)
  const [banner, setBanner] = useState('')
  // -- 会话附件 --
  const [attachments, setAttachments] = useState<BxAttachment[]>([])
  const [uploads, setUploads] = useState<{ uid: string; name: string; ts: number }[]>([])
  const [attachError, setAttachError] = useState('')
  const fileInputRef = useRef<HTMLInputElement>(null)
  const chatScrollRef = useRef<HTMLDivElement>(null)
  const stickToBottomRef = useRef(true)

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
    setAttachments([])
    setUploads([])
    setAttachError('')
    setSid(s.id)
    const detail = await explorationApi.session(s.id)
    setCanvas(detail.canvas)
    setCompleteness(detail.completeness)
  }

  const removeSession = async (id: string) => {
    await explorationApi.deleteSession(id)
    if (id === sid) {
      setSid('')
      setMessages([])
      setCanvas(null)
      setCompleteness(null)
      setAttachments([])
      setUploads([])
    }
    refetchSessions()
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
      } catch (e: any) {
        setAttachError(`「${file.name}」上传失败：${e?.detail || e?.message || '无法读取文件内容'}`)
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
      await streamExplorationChat(targetSid, { message, modelId: modelId || undefined }, e => {
        if (e.type === 'step') {
          const { type: _t, ...step } = e
          patchLast(m => ({ ...m, steps: [...m.steps, step as BxStep] }))
        } else if (e.type === 'canvas') {
          setCanvas(e.canvas)
          setCompleteness(e.completeness)
        } else if (e.type === 'answer') {
          patchLast(m => ({ ...m, content: e.content, streaming: false }))
        } else if (e.type === 'error') {
          patchLast(m => ({ ...m, content: m.content || `⚠️ ${e.message}`, streaming: false }))
        }
      })
    } catch (e: any) {
      patchLast(m => ({ ...m, content: `⚠️ ${e?.message || '请求失败'}`, streaming: false }))
    } finally {
      patchLast(m => ({ ...m, streaming: false }))
      setBusy(false)
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
    } catch (e: any) {
      setBanner(e?.detail || e?.message || '文档生成失败')
    } finally {
      setGenDocBusy(false)
    }
  }

  const canvasCount = completeness
    ? Object.values(completeness.counts).reduce((a, b) => a + b, 0) : 0

  return (
    <div className="flex h-full min-h-[560px] overflow-hidden rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-base)] shadow-sm">
      {/* 会话列表 */}
      <aside className="w-56 shrink-0 border-r border-[var(--color-border)] bg-[var(--color-bg-elevated)] flex flex-col">
          <div className="px-3 pt-4 pb-2 flex items-center justify-between">
            <div className="text-xs font-semibold text-[var(--color-text-secondary)]">探索会话</div>
            <button
              onClick={newSession}
              className="inline-flex items-center gap-1 px-2 py-1 rounded-md text-xs font-medium text-teal-700 bg-teal-50 hover:bg-teal-100"
            >
              <Plus size={12} /> 新会话
            </button>
          </div>
          <div className="flex-1 overflow-y-auto p-2 space-y-1">
            {sessions.length === 0 && (
              <div className="px-2 py-1 text-xs text-[var(--color-text-tertiary)] leading-relaxed">
                还没有会话。点「新会话」开始一次业务探索。
              </div>
            )}
            {sessions.map(s => (
              <div
                key={s.id}
                onClick={() => void loadSession(s.id)}
                className={`group px-2.5 py-2 rounded-md cursor-pointer transition-colors ${s.id === sid
                  ? 'bg-teal-50' : 'hover:bg-[var(--color-bg-hover)]'}`}
              >
                <div className="flex items-center gap-1.5">
                  <Compass size={12} className={s.id === sid ? 'text-teal-600 shrink-0' : 'text-[var(--color-text-tertiary)] shrink-0'} />
                  <span className={`flex-1 truncate text-xs ${s.id === sid ? 'text-teal-800 font-medium' : 'text-[var(--color-text-secondary)]'}`}>
                    {s.title}
                  </span>
                  <button
                    onClick={e => { e.stopPropagation(); void removeSession(s.id) }}
                    className="opacity-0 group-hover:opacity-100 p-0.5 rounded text-[var(--color-text-tertiary)] hover:text-[var(--color-danger)]"
                  >
                    <Trash2 size={11} />
                  </button>
                </div>
                <div className="mt-0.5 pl-[18px] text-[10px] text-[var(--color-text-tertiary)]">
                  {new Date(s.updatedAt).toLocaleString()}
                </div>
              </div>
            ))}
          </div>
        </aside>

      {/* 对话区 */}
      <div className="flex-1 min-w-0 flex flex-col">
        <header className="flex h-14 shrink-0 items-center border-b border-[var(--color-border)] bg-[var(--color-bg-elevated)] px-4">
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
              <select
                value={modelId}
                onChange={e => setModelId(e.target.value)}
                className="h-8 cursor-pointer rounded-md border border-[var(--color-border)] bg-[var(--color-bg-base)] px-2 text-xs outline-none transition-colors focus:border-teal-400 focus:ring-2 focus:ring-teal-100"
                title="对话模型"
              >
                <option value="">默认模型</option>
                {llmModels.map((m: any) => <option key={m.id} value={m.id}>{m.name}</option>)}
              </select>
              <button
                onClick={() => setDocsOpen(true)}
                disabled={!sid}
                className="inline-flex h-8 items-center gap-1.5 rounded-md border border-[var(--color-border)] px-3 text-xs text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)] disabled:opacity-40"
              >
                <History size={13} /> 历史需求文档
              </button>
              <button
                onClick={generateDocument}
                disabled={!sid || genDocBusy || canvasCount === 0}
                title={canvasCount === 0 ? '画布还是空的，先对话沉淀模型' : ''}
                className="inline-flex h-8 items-center gap-1.5 rounded-md border border-teal-200 bg-teal-50 px-3 text-xs font-medium text-teal-600 hover:bg-teal-100 hover:text-teal-700 disabled:opacity-40"
              >
                {genDocBusy ? <Loader2 size={13} className="animate-spin" /> : <Sparkles size={13} />}
                生成需求文档
              </button>
            </div>
          </div>
        </header>

        {banner && (
          <div className="px-4 py-2 text-xs text-[var(--color-danger)] bg-[var(--color-danger-bg)] border-b border-[var(--color-border)]">
            {banner}
          </div>
        )}

        <div ref={chatScrollRef} onScroll={updateScrollStickiness} className="flex-1 overflow-y-auto px-4 py-4">
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
                        <button
                          onClick={() => void removeAttachment(item.att.id)}
                          title="移除参考资料"
                          className="shrink-0 p-1 rounded text-[var(--color-text-tertiary)] opacity-0 transition-opacity group-hover:opacity-100 hover:text-[var(--color-danger)] hover:bg-[var(--color-bg-hover)]"
                        >
                          <X size={13} />
                        </button>
                      )}
                    </div>
                  </div>
                )
              }
              const m = item.msg
              return (
                <div key={item.key} className={`flex gap-3 ${m.role === 'user' ? 'flex-row-reverse' : ''}`}>
                  <div className={`w-7 h-7 rounded-lg flex items-center justify-center shrink-0 ${m.role === 'user'
                    ? 'bg-[var(--color-nav-bg)] text-white' : 'bg-teal-50 text-teal-600'}`}>
                    {m.role === 'user' ? <User size={14} /> : <Bot size={14} />}
                  </div>
                  <div className={`min-w-0 max-w-[85%] ${m.role === 'user' ? 'text-right' : ''}`}>
                    {m.role === 'assistant' && <StepTrace steps={m.steps} running={m.streaming} />}
                    {m.content && (
                      <div className={`inline-block text-left rounded-xl px-3.5 py-2.5 ${m.role === 'user'
                        ? 'bg-[var(--color-nav-bg)] text-white text-sm leading-relaxed'
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

        <div className="px-4 pb-4 pt-4 border-t border-[var(--color-border)]">
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
            {/* 消息输入框：回形针上传的附件直接体现在上方对话流中，输入框只承载本轮消息 */}
            <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-bg-elevated)] focus-within:border-teal-500">
              <div className="flex items-end gap-2 px-3 py-2">
                <button
                  onClick={pickFiles}
                  title="上传参考资料（仅本会话可见，用于辅助澄清业务）"
                  className="shrink-0 p-2 rounded-lg text-[var(--color-text-tertiary)] hover:text-teal-600 hover:bg-[var(--color-bg-hover)]"
                >
                  <Paperclip size={16} />
                </button>
                <textarea
                  value={input}
                  onChange={e => setInput(e.target.value)}
                  onKeyDown={e => {
                    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); void send() }
                  }}
                  rows={Math.min(4, Math.max(1, input.split('\n').length))}
                  placeholder="描述业务、回答澄清问题…（Enter 发送，Shift+Enter 换行）"
                  className="flex-1 resize-none bg-transparent text-sm outline-none placeholder:text-[var(--color-text-tertiary)] py-1"
                />
                <button
                  onClick={() => void send()}
                  disabled={busy || !input.trim()}
                  className="shrink-0 p-2 rounded-lg bg-teal-600 text-white hover:bg-teal-700 disabled:opacity-40"
                >
                  {busy ? <Loader2 size={14} className="animate-spin" /> : <Send size={14} />}
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* 业务画布 */}
      <aside className="w-[280px] lg:w-[320px] 2xl:w-[340px] shrink-0 ml-3 border-l border-[var(--color-border)] bg-[var(--color-bg-elevated)]">
        <CanvasPanel sessionId={sid || undefined} canvas={canvas} completeness={completeness} />
      </aside>

      {docsOpen && sid && (
        <DocumentsDrawer
          sessionId={sid}
          onClose={() => setDocsOpen(false)}
          onDraftCreated={draft => { setDocsOpen(false); setReviewDraft(draft) }}
        />
      )}
      {reviewDraft && (
        <DraftReviewDrawer draft={reviewDraft} onClose={() => setReviewDraft(null)} />
      )}
    </div>
  )
}
