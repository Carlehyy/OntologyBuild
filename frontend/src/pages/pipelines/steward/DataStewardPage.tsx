/**
 * 数据管家 — 对话式新建与编排 n8n 数据流水线
 *
 * 对 n8n 的写权限只有两件事：新建流水线与编排未发布未启用的流水线；
 * 另可在当前会话隔离空间内创建、编辑和删除文件。
 * 左侧：与数据管家对话（create_pipeline 新建骨架、update_workflow 补全编排）
 * 右侧：受管流水线只读看板（状态与节点链）；试跑/发布/归档都在流水线列表
 *       与编辑向导完成，不在管家里。
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import {
  Activity, AlertTriangle, ArrowLeft, BookOpen, Bot, ChevronDown, ChevronRight,
  CheckCircle2, ClipboardCheck, Copy, Download, ExternalLink, Eye, FileArchive, FileText, FolderOpen,
  GitBranch, Globe, History, KeyRound, Library, Loader2, Monitor, MousePointer2,
  Pencil, Plus, RefreshCw, Search, Send, Settings, Sparkles, Trash2, Upload,
  User, Workflow, X, Zap, Wifi, WifiOff,
} from 'lucide-react'
import {
  downloadBrowserCompanion, downloadStewardFile, stewardApi, streamStewardChat,
  type BrowserCapture, type BrowserSource, type StewardArtifact,
  type StewardConversationDTO, type StewardPipeline, type StewardPipelineDetail,
  type StewardStatus, type StewardStep,
} from '@/api/steward'
import pipelinesApi from '@/api/v2/pipelines'
import type { Pipeline } from '@/api/v2/pipelines'
import PipelineEditWizard from '../PipelineEditWizard'
import { ReactFlow, ReactFlowProvider, Background, type Node, type Edge } from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import dagre from '@dagrejs/dagre'

// ---------- 状态样式（发布状态 = 影子流水线，与流水线列表同一口径） ----------

const PUBLISH_META: Record<string, { label: string; cls: string }> = {
  draft:     { label: '未发布', cls: 'bg-gray-100 text-gray-600 border-gray-200' },
  published: { label: '已发布', cls: 'bg-green-50 text-green-600 border-green-200' },
}

const TOOL_META: Record<string, { label: string; icon: React.ElementType }> = {
  steward_overview:    { label: '查看全景', icon: Eye },
  list_pipelines:      { label: '列出流水线', icon: GitBranch },
  get_workflow:        { label: '读取工作流', icon: Search },
  create_pipeline:     { label: '新建流水线', icon: Plus },
  update_workflow:     { label: '编排工作流', icon: Workflow },
  check_workflow:      { label: '体检', icon: ClipboardCheck },
  inspect_runs:        { label: '诊断执行', icon: Activity },
  check_credentials:   { label: '凭据检查', icon: KeyRound },
  list_node_types:     { label: '查节点目录', icon: Zap },
  describe_node:       { label: '查节点详情', icon: BookOpen },
  n8n_reference:       { label: '查编排参考', icon: Library },
  probe_url:           { label: '探测数据源', icon: Globe },
  list_session_files:  { label: '查看会话文件', icon: FolderOpen },
  read_session_file:   { label: '读取文件', icon: FileText },
  create_session_file: { label: '创建文件', icon: FileText },
  edit_session_file:   { label: '编辑文件', icon: Pencil },
  delete_session_file: { label: '删除文件', icon: Trash2 },
  browser_open:        { label: '打开会话浏览器', icon: Monitor },
  browser_state:       { label: '读取页面', icon: Eye },
  browser_navigate:    { label: '浏览器跳转', icon: Globe },
  browser_click_text:  { label: '点击页面', icon: MousePointer2 },
  browser_type:        { label: '填写页面', icon: Pencil },
  browser_network_requests: { label: '分析页面接口', icon: Activity },
  download_captured_file: { label: '下载到会话', icon: Download },
  register_proxy_interface: { label: '登记代理接口', icon: Zap },
}

const SUGGESTED = [
  '看看现在有哪些受管的 n8n 流水线',
  '新建一条流水线：定时从一个 REST API 拉取 JSON 数据，整理成表格入湖',
  '帮我完善某条未发布流水线的取数与整形节点',
]

interface ChatMsg {
  id: string
  role: 'user' | 'assistant'
  content: string
  steps: StewardStep[]
  loading?: boolean
  error?: string
}

let msgSeq = 0
const nextId = () => `m${Date.now()}_${msgSeq++}`

function Md({ text }: { text: string }) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      components={{
        p: p => <p className="text-sm leading-[1.7] mb-2 last:mb-0" {...p} />,
        strong: p => <strong className="font-semibold text-gray-900" {...p} />,
        h1: p => <h3 className="text-sm font-semibold mt-3 mb-1.5" {...p} />,
        h2: p => <h3 className="text-sm font-semibold mt-3 mb-1.5" {...p} />,
        h3: p => <h4 className="text-sm font-semibold mt-2 mb-1" {...p} />,
        ul: p => <ul className="list-disc pl-5 mb-2 space-y-1" {...p} />,
        ol: p => <ol className="list-decimal pl-5 mb-2 space-y-1" {...p} />,
        li: p => <li className="text-sm leading-relaxed" {...p} />,
        code: p => <code className="px-1 py-0.5 rounded bg-black/[0.05] text-[12px] font-mono" {...p} />,
        pre: p => <pre className="p-3 my-2 rounded-lg bg-black/[0.04] text-[12px] font-mono overflow-x-auto" {...p} />,
        table: p => (
          <div className="overflow-x-auto my-2 rounded-lg border border-gray-200">
            <table className="w-full text-xs border-collapse" {...p} />
          </div>
        ),
        th: p => <th className="px-3 py-1.5 text-left font-medium text-gray-500 border-b bg-gray-50 whitespace-nowrap" {...p} />,
        td: p => <td className="px-3 py-1.5 border-b" {...p} />,
      }}
    >
      {text}
    </ReactMarkdown>
  )
}

function StepTrace({ steps, running }: { steps: StewardStep[]; running?: boolean }) {
  if (steps.length === 0 && !running) return null
  return (
    <div className="mb-3 rounded-lg bg-gray-50 border border-gray-200 px-3 py-2.5 space-y-2">
      {steps.map((s, i) => {
        const meta = TOOL_META[s.tool] || { label: s.tool, icon: Zap }
        const Icon = meta.icon
        return (
          <div key={i} className="flex items-start gap-2.5">
            <div className={`mt-px w-5 h-5 rounded-md flex items-center justify-center shrink-0 ${
              s.error ? 'bg-red-50 text-red-500' : 'bg-violet-50 text-violet-600'}`}>
              <Icon size={11} />
            </div>
            <div className="min-w-0 text-xs leading-5">
              <span className={`font-medium ${s.error ? 'text-red-600' : 'text-gray-800'}`}>{meta.label}</span>
              <span className="text-gray-400"> · {s.summary}</span>
            </div>
          </div>
        )
      })}
      {running && (
        <div className="flex items-center gap-2.5">
          <div className="w-5 h-5 rounded-md bg-violet-50 flex items-center justify-center shrink-0">
            <Loader2 size={11} className="animate-spin text-violet-600" />
          </div>
          <span className="text-xs text-gray-400">
            {steps.length === 0 ? '正在了解流水线现状，规划操作…' : '正在综合工具结果继续…'}
          </span>
        </div>
      )}
    </div>
  )
}

// ---------- 主页面 ----------

export default function DataStewardPage() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()

  const [status, setStatus] = useState<StewardStatus | null>(null)
  const [messages, setMessages] = useState<ChatMsg[]>([])
  const [conversationId, setConversationId] = useState<string | null>(null)
  const [conversations, setConversations] = useState<StewardConversationDTO[]>([])
  const [showHistory, setShowHistory] = useState(false)
  const [showFiles, setShowFiles] = useState(false)
  const [showBrowser, setShowBrowser] = useState(false)
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const bottomRef = useRef<HTMLDivElement>(null)
  const abortRef = useRef<AbortController | null>(null)

  const [records, setRecords] = useState<StewardPipeline[]>([])
  const [recordsLoading, setRecordsLoading] = useState(true)
  const [expandedId, setExpandedId] = useState<string | null>(searchParams.get('record'))
  const [editTarget, setEditTarget] = useState<Pipeline | null>(null)
  const n8nApiUrl = status?.n8n?.api_url ?? ''
  // 拖拽调整对话区/审批面板宽度（仅宽屏有效）
  const [chatWidthPct, setChatWidthPct] = useState(58)
  const chatContainerRef = useRef<HTMLDivElement>(null)
  const [isWide, setIsWide] = useState(typeof window !== 'undefined' && window.innerWidth >= 1280)
  useEffect(() => {
    const onResize = () => setIsWide(window.innerWidth >= 1280)
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])
  const startResize = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    const container = chatContainerRef.current
    if (!container) return
    const onMove = (ev: MouseEvent) => {
      const rect = container.getBoundingClientRect()
      let pct = ((ev.clientX - rect.left) / rect.width) * 100
      pct = Math.max(30, Math.min(78, pct))
      setChatWidthPct(pct)
    }
    const onUp = () => {
      document.removeEventListener('mousemove', onMove)
      document.removeEventListener('mouseup', onUp)
      document.body.style.userSelect = ''
      document.body.style.cursor = ''
    }
    document.addEventListener('mousemove', onMove)
    document.addEventListener('mouseup', onUp)
    document.body.style.userSelect = 'none'
    document.body.style.cursor = 'col-resize'
  }, [])

  const loadStatus = useCallback(() => {
    stewardApi.status().then(setStatus).catch(() => setStatus(null))
  }, [])

  // silent=true 用于后台轮询：不切 loading 态、失败也不清空面板，避免闪烁/闪空
  const loadRecords = useCallback((opts?: { silent?: boolean }) => {
    if (!opts?.silent) setRecordsLoading(true)
    return stewardApi.pipelines()
      .then(res => setRecords(Array.isArray(res) ? res : []))
      .catch(() => { if (!opts?.silent) setRecords([]) })
      .finally(() => { if (!opts?.silent) setRecordsLoading(false) })
  }, [])

  const loadConversations = useCallback(() => {
    stewardApi.conversations().then(res => setConversations(Array.isArray(res) ? res : [])).catch(() => {})
  }, [])

  useEffect(() => {
    const timer = window.setTimeout(() => { loadStatus(); void loadRecords(); loadConversations() }, 0)
    return () => window.clearTimeout(timer)
  }, [loadStatus, loadRecords, loadConversations])
  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [messages])
  useEffect(() => () => abortRef.current?.abort(), [])

  // 受管流水线面板尽量实时：HTTP 轮询（非 WebSocket）——数据管家或流水线列表新建/
  // 改动流水线后，无需手动刷新即可反映可编排的那批。仅在标签页可见时轮询，切回时立即刷新一次。
  useEffect(() => {
    const POLL_MS = 10_000
    let stopped = false
    let timer: ReturnType<typeof setTimeout> | undefined
    const tick = async () => {
      if (stopped) return
      if (document.visibilityState === 'visible') await loadRecords({ silent: true })
      if (!stopped) timer = setTimeout(tick, POLL_MS)   // 上一轮完成后再排下一轮，避免重叠
    }
    timer = setTimeout(tick, POLL_MS)
    const onVisible = () => { if (document.visibilityState === 'visible') loadRecords({ silent: true }) }
    document.addEventListener('visibilitychange', onVisible)
    return () => {
      stopped = true
      if (timer) clearTimeout(timer)
      document.removeEventListener('visibilitychange', onVisible)
    }
  }, [loadRecords])

  const resetChat = () => {
    abortRef.current?.abort()
    setConversationId(null)
    setMessages([])
    setShowHistory(false)
  }

  const ensureConversation = useCallback(async (): Promise<string> => {
    if (conversationId) return conversationId
    const conv = await stewardApi.createConversation('新数据采集会话')
    setConversationId(conv.id)
    loadConversations()
    return conv.id
  }, [conversationId, loadConversations])

  const openFiles = async () => {
    try {
      await ensureConversation()
      setShowFiles(true)
    } catch { /* 顶部功能保持安静，弹窗由后续请求展示错误 */ }
  }

  const openBrowser = async () => {
    try {
      await ensureConversation()
      setShowBrowser(true)
    } catch { /* 同上 */ }
  }

  const loadConversation = async (cid: string) => {
    try {
      const conv = await stewardApi.conversation(cid)
      setConversationId(cid)
      setMessages((conv.messages || []).map(m => ({
        id: m.id, role: m.role, content: m.content, steps: m.steps || [],
      })))
      setShowHistory(false)
    } catch { /* 忽略 */ }
  }

  const removeConversation = async (cid: string) => {
    try {
      await stewardApi.deleteConversation(cid)
      if (cid === conversationId) resetChat()
      loadConversations()
    } catch { /* 忽略 */ }
  }

  const send = async (preset?: string) => {
    const text = (preset ?? input).trim()
    if (!text || busy) return
    setInput('')
    setBusy(true)

    const userMsg: ChatMsg = { id: nextId(), role: 'user', content: text, steps: [] }
    const botMsg: ChatMsg = { id: nextId(), role: 'assistant', content: '', steps: [], loading: true }
    setMessages(prev => [...prev, userMsg, botMsg])

    const patchBot = (patch: Partial<ChatMsg> | ((m: ChatMsg) => Partial<ChatMsg>)) =>
      setMessages(prev => prev.map(m =>
        m.id === botMsg.id ? { ...m, ...(typeof patch === 'function' ? patch(m) : patch) } : m))

    const ctrl = new AbortController()
    abortRef.current = ctrl
    let touched: string[] = []
    try {
      await streamStewardChat(
        { message: text, conversationId },
        e => {
          if (e.type === 'meta') setConversationId(e.conversationId)
          else if (e.type === 'step') {
            const step: StewardStep = {
              tool: e.tool, arguments: e.arguments, summary: e.summary,
              durationMs: e.durationMs, ...(e.error ? { error: e.error } : {}),
            }
            patchBot(m => ({ steps: [...m.steps, step] }))
          } else if (e.type === 'answer') {
            touched = e.touchedPipelineIds || []
            patchBot({ content: e.content, loading: false })
          } else if (e.type === 'error') {
            patchBot({ error: e.message, loading: false })
          }
        },
        ctrl.signal,
      )
    } catch (err: unknown) {
      if (!(err instanceof DOMException && err.name === 'AbortError')) {
        patchBot({ error: (err as Error)?.message || '对话失败', loading: false })
      }
    } finally {
      patchBot({ loading: false })
      setBusy(false)
      loadConversations()
      // 本回合动过流水线 → 刷新受管流水线面板并展开最近触达的一条
      if (touched.length > 0) {
        loadRecords()
        setExpandedId(touched[touched.length - 1])
      }
    }
  }

  const n8nReady = !!status && status.n8n.configured && status.n8n.enabled && status.n8n.reachable !== false

  return (
    <div className="flex h-full flex-col">
      {/* 前置条件提示 */}
      {status && !n8nReady && (
        <div className="flex items-center gap-2 px-4 py-2.5 rounded-lg border border-amber-200 bg-amber-50 text-sm text-amber-800 shrink-0">
          <AlertTriangle size={15} className="shrink-0" />
          <span className="flex-1">
            {!status.n8n.configured ? 'n8n 尚未配置：请先到系统设置填写 n8n 地址与 API Key 并通过连接测试。'
              : !status.n8n.enabled ? 'n8n 集成处于停用状态：请到系统设置启用。'
              : `n8n 无法连接：${status.n8n.error || '请检查服务是否在线'}`}
          </span>
          <Link to="/settings/workflows"
            className="flex items-center gap-1 text-xs px-2.5 py-1 bg-white border border-amber-300 rounded-lg hover:bg-amber-100 shrink-0">
            <Settings size={11} /> 工作流配置
          </Link>
        </div>
      )}
      {status && n8nReady && !status.llmReady && (
        <div className="flex items-center gap-2 px-4 py-2.5 rounded-lg border border-amber-200 bg-amber-50 text-sm text-amber-800 shrink-0">
          <AlertTriangle size={15} className="shrink-0" />
          <span className="flex-1">尚未配置对话模型：数据管家需要一个 LLM 才能工作。</span>
          <Link to="/models"
            className="flex items-center gap-1 text-xs px-2.5 py-1 bg-white border border-amber-300 rounded-lg hover:bg-amber-100 shrink-0">
            去模型配置
          </Link>
        </div>
      )}

      {/* 主体：对话 + 审批面板（窄屏纵向堆叠，避免挤压对话区） */}
      <div ref={chatContainerRef} className="flex flex-1 min-h-0 max-xl:flex-col m-1">
        {/* 对话区 */}
        <section style={isWide ? { width: `${chatWidthPct}%` } : undefined}
          className="flex h-full min-w-0 min-h-0 flex-col bg-white border overflow-hidden max-xl:w-full max-xl:min-h-[55%]">
          {/* 抬头：标题 + 操作按钮 */}
          <div className="flex items-center justify-between border-b px-4 py-2.5 shrink-0">
            <h2 className="flex items-center gap-1.5 text-sm font-semibold text-gray-800">
              <Sparkles size={15} className="text-violet-600" /> 数据管家
            </h2>
            <div className="flex items-center gap-2 shrink-0">
              <button onClick={openFiles}
                className="flex items-center gap-1.5 px-2.5 py-1.5 border rounded-lg text-xs text-gray-600 hover:bg-gray-50">
                <FolderOpen size={13} /> 会话文件
              </button>
              <button onClick={openBrowser}
                className="flex items-center gap-1.5 px-2.5 py-1.5 border border-violet-200 rounded-lg text-xs text-violet-700 hover:bg-violet-50">
                <Monitor size={13} /> 实时浏览器
              </button>
              <button onClick={() => navigate('/data/pipelines')}
                className="flex items-center gap-1.5 px-2.5 py-1.5 border rounded-lg text-xs text-gray-600 hover:bg-gray-50">
                <ArrowLeft size={13} /> 返回流水线
              </button>
              <button onClick={() => { loadConversations(); setShowHistory(true) }}
                className="flex items-center gap-1.5 px-2.5 py-1.5 border rounded-lg text-xs text-gray-600 hover:bg-gray-50">
                <History size={13} /> 会话
              </button>
              <button onClick={resetChat}
                className="flex items-center gap-1.5 px-2.5 py-1.5 bg-violet-600 text-white text-xs font-medium rounded-lg hover:bg-violet-700">
                <Plus size={13} /> 新会话
              </button>
            </div>
          </div>
          <div className="flex-1 overflow-y-auto px-5 py-4">
            {messages.length === 0 ? (
              <div className="flex h-full flex-col items-center justify-center text-center px-6">
                <div className="mb-4 flex h-14 w-14 items-center justify-center rounded-2xl border border-violet-100 bg-violet-50 text-violet-600">
                  <Bot size={26} />
                </div>
                <h2 className="text-base font-semibold text-gray-900">让数据管家替你编排流水线</h2>
                <p className="mt-1.5 max-w-md text-sm leading-relaxed text-gray-500">
                  描述数据从哪来、怎么加工、多久跑一次——数据管家会在 n8n 里搭好工作流并录入流水线列表。
                  编排满意后，到流水线列表的编辑向导中发布并启用。
                </p>
                <div className="mt-5 flex flex-wrap justify-center gap-2">
                  {SUGGESTED.map(q => (
                    <button key={q} onClick={() => send(q)} disabled={busy}
                      className="rounded-full border bg-white px-3 py-1.5 text-xs text-gray-600 transition-all hover:border-violet-300 hover:bg-violet-50 hover:text-violet-700 disabled:opacity-50">
                      {q}
                    </button>
                  ))}
                </div>
              </div>
            ) : (
              <div className="space-y-5">
                {messages.map(msg => msg.role === 'user' ? (
                  <div key={msg.id} className="flex justify-end gap-3">
                    <div className="max-w-[85%] rounded-lg rounded-br-sm bg-violet-600 px-3.5 py-2.5 text-white shadow-sm">
                      <p className="whitespace-pre-line text-sm leading-relaxed">{msg.content}</p>
                    </div>
                    <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-md border border-violet-200 bg-violet-50 text-violet-700">
                      <User size={14} />
                    </div>
                  </div>
                ) : (
                  <div key={msg.id} className="flex gap-3">
                    <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-violet-600 text-white">
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
                        <div className="text-gray-800"><Md text={msg.content} /></div>
                      ) : null}
                    </div>
                  </div>
                ))}
                <div ref={bottomRef} />
              </div>
            )}
          </div>

          <div className="border-t bg-gray-50/60 px-4 pb-3 pt-3">
            <div className="flex items-center gap-2 rounded-lg border bg-white py-1.5 pl-3 pr-1.5 transition-all focus-within:border-violet-400 focus-within:ring-2 focus-within:ring-violet-100">
              <input
                placeholder={n8nReady ? '描述你要的数据流水线，或让管家检查/修改现有流水线…' : '请先完成 n8n 配置'}
                value={input}
                onChange={e => setInput(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && send()}
                disabled={busy}
                className="min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-gray-400 disabled:opacity-50"
              />
              <button onClick={() => send()} disabled={!input.trim() || busy}
                className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-violet-600 text-white transition-all hover:bg-violet-700 disabled:cursor-not-allowed disabled:opacity-25">
                {busy ? <Loader2 size={14} className="animate-spin" /> : <Send size={14} />}
              </button>
            </div>
            <p className="mt-1.5 text-[11px] text-gray-400">
              管家只负责新建与编排未发布未启用的流水线；试跑、发布、归档都在流水线列表与编辑向导完成
            </p>
          </div>
        </section>

        {/* 拖拽分隔条（仅宽屏，拖动调整对话区与受管流水线面板宽度） */}
        <div
          onMouseDown={startResize}
          className="hidden xl:flex shrink-0 cursor-col-resize items-center justify-center group"
        >
          <div className="h-16 w-1 rounded-full bg-gray-200 group-hover:h-24 group-hover:bg-violet-400 transition-all" />
        </div>

        {/* 受管流水线面板 */}
        <ManagedPipelinesPanel
          records={records}
          loading={recordsLoading}
          expandedId={expandedId}
          onExpand={setExpandedId}
          onChanged={() => { loadRecords(); loadStatus() }}
          n8nApiUrl={n8nApiUrl}
          onOpenWizard={(pipelineId: string) => {
            pipelinesApi.get(pipelineId).then(setEditTarget).catch(() => {})
          }}
        />
      </div>

      {/* 编辑向导 */}
      {editTarget && (
        <PipelineEditWizard
          pipeline={editTarget}
          onClose={() => setEditTarget(null)}
          onSaved={() => { setEditTarget(null); loadRecords(); loadStatus() }}
        />
      )}

      {showFiles && conversationId && (
        <WorkspaceModal conversationId={conversationId} onClose={() => setShowFiles(false)} />
      )}
      {showBrowser && conversationId && (
        <BrowserModal conversationId={conversationId} onClose={() => setShowBrowser(false)} />
      )}

      {/* 会话管理弹窗 */}
      {showHistory && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50" onClick={() => setShowHistory(false)}>
          <div className="bg-white rounded-xl shadow-lg p-5 w-[480px] max-h-[70vh] flex flex-col" onClick={e => e.stopPropagation()}>
            <div className="flex justify-between items-center mb-3">
              <h3 className="font-semibold">会话管理</h3>
              <button onClick={() => setShowHistory(false)} className="text-gray-400 hover:text-black"><X size={16} /></button>
            </div>
            <button onClick={resetChat}
              className="w-full rounded-lg border border-violet-600 bg-violet-600 px-3 py-2 text-sm font-medium text-white hover:bg-violet-700">
              + 创建新会话
            </button>
            <div className="mt-3 flex-1 overflow-auto space-y-1.5">
              {conversations.length === 0 && (
                <div className="rounded-lg border border-dashed px-4 py-8 text-center text-sm text-gray-400">暂无历史会话</div>
              )}
              {conversations.map(c => (
                <div key={c.id} className="group flex items-center gap-2 rounded-lg border border-transparent px-2 py-1.5 hover:border-gray-200 hover:bg-gray-50">
                  <button onClick={() => loadConversation(c.id)}
                    className={`min-w-0 flex-1 text-left ${c.id === conversationId ? 'text-violet-700' : 'text-gray-800'}`}>
                    <p className="truncate text-sm font-medium">{c.title}</p>
                    <p className="mt-0.5 text-[11px] text-gray-400">{new Date(c.updatedAt).toLocaleString()}</p>
                  </button>
                  <button onClick={() => removeConversation(c.id)} title="删除会话"
                    className="flex h-7 w-7 items-center justify-center rounded-md text-gray-300 opacity-0 transition-all hover:bg-red-50 hover:text-red-500 group-hover:opacity-100">
                    <Trash2 size={13} />
                  </button>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`
  return `${(value / 1024 / 1024).toFixed(1)} MB`
}

function errorText(error: unknown, fallback: string): string {
  if (error && typeof error === 'object') {
    const value = error as { detail?: unknown; message?: unknown }
    if (typeof value.detail === 'string') return value.detail
    if (typeof value.message === 'string') return value.message
  }
  return fallback
}

function WorkspaceModal({ conversationId, onClose }: { conversationId: string; onClose: () => void }) {
  const [files, setFiles] = useState<StewardArtifact[]>([])
  const [loading, setLoading] = useState(true)
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)

  const reload = useCallback(() => {
    setLoading(true)
    return stewardApi.files(conversationId)
      .then(rows => setFiles(Array.isArray(rows) ? rows : []))
      .catch(err => setError(errorText(err, '会话文件加载失败')))
      .finally(() => setLoading(false))
  }, [conversationId])

  useEffect(() => {
    const timer = window.setTimeout(() => void reload(), 0)
    return () => window.clearTimeout(timer)
  }, [reload])

  const uploadFiles = async (selected: FileList | null) => {
    if (!selected?.length) return
    setUploading(true); setError('')
    try {
      for (const file of Array.from(selected)) await stewardApi.uploadFile(conversationId, file)
      await reload()
    } catch (err: unknown) {
      setError(errorText(err, '上传失败'))
    } finally {
      setUploading(false)
      if (inputRef.current) inputRef.current.value = ''
    }
  }

  const remove = async (id: string) => {
    await stewardApi.deleteFile(conversationId, id)
    await reload()
  }

  return (
    <div className="fixed inset-0 z-[70] flex items-center justify-center bg-black/50 p-5" onClick={onClose}>
      <div className="flex max-h-[78vh] w-[760px] max-w-full flex-col overflow-hidden rounded-xl bg-white shadow-2xl" onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between border-b px-5 py-3.5">
          <div>
            <h3 className="flex items-center gap-2 text-sm font-semibold"><FolderOpen size={16} className="text-violet-600" />会话隔离空间</h3>
            <p className="mt-0.5 text-[11px] text-gray-400">上传件和网页下载件仅在此会话可见；打包不包含浏览器登录态</p>
          </div>
          <button aria-label="关闭会话文件" onClick={onClose} className="text-gray-400 hover:text-gray-700"><X size={17} /></button>
        </div>
        <div className="flex items-center gap-2 border-b bg-gray-50/70 px-5 py-3">
          <input ref={inputRef} type="file" multiple className="hidden"
            accept=".doc,.docx,.ppt,.pptx,.xls,.xlsx,.pdf,.md,.txt,.csv,.json,.xml"
            onChange={e => void uploadFiles(e.target.files)} />
          <button onClick={() => inputRef.current?.click()} disabled={uploading}
            className="flex items-center gap-1.5 rounded-lg bg-violet-600 px-3 py-2 text-xs font-medium text-white hover:bg-violet-700 disabled:opacity-50">
            {uploading ? <Loader2 size={13} className="animate-spin" /> : <Upload size={13} />} 上传文件
          </button>
          <button onClick={() => void downloadStewardFile(conversationId, undefined, `data-steward-${conversationId.slice(0, 8)}.zip`)}
            className="flex items-center gap-1.5 rounded-lg border bg-white px-3 py-2 text-xs text-gray-700 hover:bg-gray-50">
            <FileArchive size={13} /> 一键打包
          </button>
          <button onClick={() => void reload()} className="ml-auto flex items-center gap-1 text-xs text-gray-500"><RefreshCw size={12} />刷新</button>
        </div>
        {error && <div className="border-b bg-red-50 px-5 py-2 text-xs text-red-600">{error}</div>}
        <div className="min-h-[260px] flex-1 overflow-auto p-4">
          {loading ? <div className="py-16 text-center text-sm text-gray-400">加载中…</div> : files.length === 0 ? (
            <div className="rounded-xl border-2 border-dashed py-16 text-center text-sm text-gray-400">当前会话还没有文件</div>
          ) : (
            <div className="space-y-2">
              {files.map(file => (
                <div key={file.id} className="flex items-center gap-3 rounded-lg border px-3 py-2.5">
                  <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-violet-50 text-violet-600"><FileText size={16} /></div>
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium text-gray-800">{file.filename}</p>
                    <p className="mt-0.5 truncate text-[11px] text-gray-400">
                      {file.source === 'download' ? '网页下载' : file.source === 'generated' ? '管家创建' : file.source === 'edited' ? '管家编辑' : '用户上传'} · {formatBytes(file.size)}
                      {file.extractedChars > 0 ? ` · 已解析 ${file.extractedChars.toLocaleString()} 字` : ''}
                      {file.urls?.length ? ` · 发现 ${file.urls.length} 个网址` : ''}
                    </p>
                    {file.extractError && <p className="mt-0.5 truncate text-[11px] text-amber-600">{file.extractError}</p>}
                  </div>
                  <button title="下载" onClick={() => void downloadStewardFile(conversationId, file.id, file.filename)} className="rounded p-1.5 text-gray-400 hover:bg-gray-100 hover:text-violet-600"><Download size={14} /></button>
                  <button title="删除" onClick={() => void remove(file.id)} className="rounded p-1.5 text-gray-400 hover:bg-red-50 hover:text-red-500"><Trash2 size={14} /></button>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function BrowserModal({ conversationId, onClose }: { conversationId: string; onClose: () => void }) {
  const [url, setUrl] = useState('https://')
  const [currentUrl, setCurrentUrl] = useState('')
  const [frame, setFrame] = useState('')
  const [connected, setConnected] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [captures, setCaptures] = useState<BrowserCapture[]>([])
  const [showNetwork, setShowNetwork] = useState(false)
  const [showSources, setShowSources] = useState(false)
  const [sources, setSources] = useState<BrowserSource[]>([])
  const [selectedSource, setSelectedSource] = useState('managed')
  const [sourceName, setSourceName] = useState('我的电脑')
  const [sourceType, setSourceType] = useState<'companion' | 'remote_cdp'>('companion')
  const [endpointUrl, setEndpointUrl] = useState('')
  const [headerJson, setHeaderJson] = useState('{}')
  const [pairing, setPairing] = useState<{ sourceId: string; token: string } | null>(null)
  const [sourceBusy, setSourceBusy] = useState(false)
  const wsRef = useRef<WebSocket | null>(null)
  const imageRef = useRef<HTMLImageElement>(null)

  const loadSources = useCallback(async () => {
    try {
      const [rows, conversation] = await Promise.all([
        stewardApi.browserSources(), stewardApi.conversation(conversationId),
      ])
      setSources(rows)
      setSelectedSource(conversation.browserSourceId || 'managed')
    } catch (err: unknown) {
      setError(errorText(err, '浏览器来源加载失败'))
    }
  }, [conversationId])

  useEffect(() => { void loadSources() }, [loadSources])

  const connectLive = useCallback(async () => {
    wsRef.current?.close()
    const { ticket } = await stewardApi.browserTicket(conversationId)
    const runtimeBase = ((window as Window & { __API_BASE_URL__?: string }).__API_BASE_URL__ || window.location.origin).replace(/\/$/, '')
    const wsBase = runtimeBase.replace(/^http:/, 'ws:').replace(/^https:/, 'wss:')
    const ws = new WebSocket(`${wsBase}/api/v2/steward/conversations/${conversationId}/browser/live?ticket=${encodeURIComponent(ticket)}`)
    wsRef.current = ws
    ws.onopen = () => { setConnected(true); setError('') }
    ws.onclose = () => setConnected(false)
    ws.onerror = () => setError('实时画面连接失败')
    ws.onmessage = event => {
      try {
        const msg = JSON.parse(event.data)
        if (msg.type === 'frame') {
          setFrame(`data:image/jpeg;base64,${msg.data}`)
          if (msg.url) { setCurrentUrl(msg.url); setUrl(msg.url) }
        } else if (msg.type === 'error') setError(msg.message || '浏览器画面异常')
      } catch { /* 忽略损坏帧 */ }
    }
  }, [conversationId])

  useEffect(() => () => wsRef.current?.close(), [])

  const open = async () => {
    if (!url.trim()) return
    setBusy(true); setError('')
    try {
      const state = currentUrl
        ? await stewardApi.browserNavigate(conversationId, url.trim())
        : await stewardApi.browserStart(conversationId, url.trim())
      setCurrentUrl(state.url); setUrl(state.url)
      if (!connected) await connectLive()
    } catch (err: unknown) {
      setError(errorText(err, '网址打开失败'))
    } finally { setBusy(false) }
  }

  const bindSource = async (sourceId: string) => {
    setSourceBusy(true); setError('')
    try {
      await stewardApi.bindBrowserSource(conversationId, sourceId)
      wsRef.current?.close(); setConnected(false); setFrame(''); setCurrentUrl('')
      setSelectedSource(sourceId)
    } catch (err: unknown) { setError(errorText(err, '浏览器来源切换失败')) }
    finally { setSourceBusy(false) }
  }

  const createSource = async () => {
    setSourceBusy(true); setError(''); setPairing(null)
    try {
      let headers: Record<string, string> | undefined
      if (sourceType === 'remote_cdp') {
        const parsed: unknown = JSON.parse(headerJson || '{}')
        if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') throw new Error('请求头必须是 JSON 对象')
        headers = parsed as Record<string, string>
      }
      const created = await stewardApi.createBrowserSource({
        name: sourceName.trim() || (sourceType === 'companion' ? '我的电脑' : '远程浏览器'),
        sourceType, endpointUrl: sourceType === 'remote_cdp' ? endpointUrl.trim() : undefined, headers,
      })
      if (created.pairingToken) setPairing({ sourceId: created.id, token: created.pairingToken })
      await loadSources()
      await bindSource(created.id)
    } catch (err: unknown) { setError(errorText(err, '浏览器来源创建失败')) }
    finally { setSourceBusy(false) }
  }

  const testSource = async (sourceId: string) => {
    setSourceBusy(true); setError('')
    try {
      const result = await stewardApi.testBrowserSource(sourceId)
      setError(result.reachable ? `✓ ${result.label}连接正常` : `${result.label}不可达`)
      await loadSources()
    } catch (err: unknown) { setError(errorText(err, '连接测试失败')) }
    finally { setSourceBusy(false) }
  }

  const removeSource = async (sourceId: string) => {
    setSourceBusy(true); setError('')
    try {
      if (selectedSource === sourceId) await bindSource('managed')
      await stewardApi.deleteBrowserSource(sourceId)
      await loadSources()
    } catch (err: unknown) { setError(errorText(err, '删除浏览器来源失败')) }
    finally { setSourceBusy(false) }
  }

  const companionCommand = pairing
    ? `node openontology-browser-companion.mjs --server ${window.location.origin} --source ${pairing.sourceId} --token ${pairing.token}`
    : ''

  const send = (message: Record<string, unknown>) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) wsRef.current.send(JSON.stringify(message))
  }

  const point = (event: React.MouseEvent<HTMLImageElement>) => {
    const rect = event.currentTarget.getBoundingClientRect()
    const naturalW = event.currentTarget.naturalWidth || 1365
    const naturalH = event.currentTarget.naturalHeight || 768
    return { x: (event.clientX - rect.left) * naturalW / rect.width, y: (event.clientY - rect.top) * naturalH / rect.height }
  }

  const keyName = (event: React.KeyboardEvent<HTMLImageElement>) => {
    const base = event.key === ' ' ? 'Space' : event.key
    const mods = [event.ctrlKey || event.metaKey ? 'Control' : '', event.altKey ? 'Alt' : '', event.shiftKey ? 'Shift' : ''].filter(Boolean)
    return [...mods, base].join('+')
  }

  const loadCaptures = useCallback(async () => {
    try {
      const rows = await stewardApi.browserCaptures(conversationId)
      setCaptures(rows.filter(row => row.isApi || row.isFile).reverse())
    } catch { /* 浏览器未启动时为空 */ }
  }, [conversationId])

  useEffect(() => {
    if (!showNetwork) return
    const first = window.setTimeout(() => void loadCaptures(), 0)
    const timer = window.setInterval(() => void loadCaptures(), 3000)
    return () => { window.clearTimeout(first); window.clearInterval(timer) }
  }, [showNetwork, loadCaptures])

  return (
    <div className="fixed inset-0 z-[80] flex items-center justify-center bg-black/60 p-3" onClick={onClose}>
      <div className="flex h-[88vh] w-[min(1500px,96vw)] flex-col overflow-hidden rounded-xl bg-white shadow-2xl" onClick={e => e.stopPropagation()}>
        <div className="flex items-center gap-2 border-b bg-gray-50 px-3 py-2">
          <Monitor size={15} className="text-violet-600" />
          <div className={`h-2 w-2 rounded-full ${connected ? 'bg-green-500' : 'bg-gray-300'}`} />
          <input value={url} onChange={e => setUrl(e.target.value)} onKeyDown={e => e.key === 'Enter' && void open()}
            className="h-8 min-w-0 flex-1 rounded-lg border bg-white px-3 font-mono text-xs outline-none focus:border-violet-400" />
          <button onClick={() => void open()} disabled={busy}
            className="flex h-8 items-center gap-1.5 rounded-lg bg-violet-600 px-3 text-xs text-white disabled:opacity-50">
            {busy ? <Loader2 size={12} className="animate-spin" /> : <Globe size={12} />} 打开
          </button>
          <button onClick={() => { setShowNetwork(v => !v); if (!showNetwork) void loadCaptures() }}
            className={`flex h-8 items-center gap-1.5 rounded-lg border px-3 text-xs ${showNetwork ? 'border-violet-300 bg-violet-50 text-violet-700' : 'text-gray-600'}`}>
            <Activity size={12} /> 接口请求
          </button>
          <button onClick={() => setShowSources(v => !v)}
            className={`flex h-8 items-center gap-1.5 rounded-lg border px-3 text-xs ${showSources ? 'border-violet-300 bg-violet-50 text-violet-700' : 'text-gray-600'}`}>
            <Settings size={12} /> 浏览器来源
          </button>
          <button aria-label="关闭实时浏览器" onClick={onClose} className="ml-1 text-gray-400 hover:text-gray-700"><X size={17} /></button>
        </div>
        {error && <div className={`border-b px-4 py-2 text-xs ${error.startsWith('✓') ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-600'}`}>{error}</div>}
        {showSources && (
          <div className="grid max-h-[360px] shrink-0 grid-cols-[minmax(260px,0.8fr)_minmax(360px,1.2fr)] overflow-auto border-b bg-[#fafafa]">
            <div className="border-r p-4">
              <div className="mb-3 flex items-center justify-between">
                <div><p className="text-xs font-semibold text-gray-800">当前会话的浏览器</p><p className="mt-0.5 text-[11px] text-gray-400">每个会话独立绑定，切换会关闭原浏览器上下文</p></div>
                {sourceBusy && <Loader2 size={13} className="animate-spin text-violet-600" />}
              </div>
              <div className="space-y-2">
                {sources.map(source => (
                  <div key={source.id} className={`rounded-xl border bg-white p-3 ${selectedSource === source.id ? 'border-violet-300 ring-1 ring-violet-100' : 'border-gray-200'}`}>
                    <div className="flex items-start gap-2">
                      <button onClick={() => void bindSource(source.id)} className="mt-0.5 text-violet-600" aria-label={`选择${source.name}`}>
                        {selectedSource === source.id ? <CheckCircle2 size={16} /> : <span className="block h-4 w-4 rounded-full border border-gray-300" />}
                      </button>
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-1.5"><p className="truncate text-xs font-medium text-gray-800">{source.name}</p>
                          {source.sourceType === 'companion' && (source.online ? <Wifi size={12} className="text-green-500" /> : <WifiOff size={12} className="text-gray-300" />)}
                        </div>
                        <p className="mt-0.5 text-[10px] text-gray-400">{source.sourceType === 'managed' ? '平台 Docker 浏览器' : source.sourceType === 'companion' ? (source.online ? '我的电脑 · 在线' : '我的电脑 · 离线') : '管理员远程 CDP'}</p>
                      </div>
                      <button onClick={() => void testSource(source.id)} className="text-[10px] text-violet-600">测试</button>
                      {source.id !== 'managed' && <button onClick={() => void removeSource(source.id)} aria-label="删除来源" className="text-gray-300 hover:text-red-500"><Trash2 size={12} /></button>}
                    </div>
                  </div>
                ))}
              </div>
            </div>
            <div className="p-4">
              <p className="text-xs font-semibold text-gray-800">添加兜底浏览器</p>
              <p className="mt-1 text-[11px] leading-5 text-gray-500">云端 IP 被 WAF 拒绝时，使用“我的电脑”可以复用你本机网络；平台不会公开你电脑的调试端口。</p>
              <div className="mt-3 flex gap-2">
                <button onClick={() => setSourceType('companion')} className={`rounded-lg border px-3 py-1.5 text-xs ${sourceType === 'companion' ? 'border-violet-300 bg-violet-50 text-violet-700' : 'bg-white text-gray-500'}`}>我的电脑</button>
                <button onClick={() => setSourceType('remote_cdp')} className={`rounded-lg border px-3 py-1.5 text-xs ${sourceType === 'remote_cdp' ? 'border-violet-300 bg-violet-50 text-violet-700' : 'bg-white text-gray-500'}`}>远程 CDP（管理员）</button>
              </div>
              <div className="mt-3 grid gap-2">
                <input value={sourceName} onChange={event => setSourceName(event.target.value)} placeholder="来源名称" className="h-8 rounded-lg border bg-white px-3 text-xs outline-none focus:border-violet-400" />
                {sourceType === 'remote_cdp' && <>
                  <input value={endpointUrl} onChange={event => setEndpointUrl(event.target.value)} placeholder="https://browser.example.com/cdp" className="h-8 rounded-lg border bg-white px-3 font-mono text-xs outline-none focus:border-violet-400" />
                  <textarea value={headerJson} onChange={event => setHeaderJson(event.target.value)} placeholder='{"Authorization":"Bearer …"}' className="h-16 resize-none rounded-lg border bg-white p-2 font-mono text-[11px] outline-none focus:border-violet-400" />
                </>}
                <button onClick={() => void createSource()} disabled={sourceBusy || (sourceType === 'remote_cdp' && !endpointUrl.trim())} className="h-8 rounded-lg bg-violet-600 px-3 text-xs font-medium text-white disabled:opacity-40">{sourceType === 'companion' ? '生成一次性配对信息' : '保存远程浏览器'}</button>
              </div>
              {sourceType === 'companion' && window.location.protocol !== 'https:' && (
                <div className="mt-3 rounded-lg border border-amber-200 bg-amber-50 p-2.5 text-[11px] leading-5 text-amber-700">当前平台不是 HTTPS。为防止配对令牌和浏览器流量泄露，生产环境助手会拒绝连接；请先为平台配置 HTTPS。</div>
              )}
              {pairing && (
                <div className="mt-3 rounded-xl border border-violet-200 bg-violet-50 p-3">
                  <p className="text-[11px] font-medium text-violet-800">配对令牌只显示这一次</p>
                  <ol className="mt-1 list-decimal space-y-1 pl-4 text-[10px] leading-5 text-violet-700"><li>安装 Node.js 22+，下载助手脚本</li><li>在脚本目录运行下面命令，Chrome/Edge 会使用独立资料目录启动</li></ol>
                  <div className="mt-2 flex gap-2"><button onClick={() => void downloadBrowserCompanion()} className="rounded-lg bg-white px-2.5 py-1.5 text-[10px] font-medium text-violet-700 shadow-sm">下载助手</button>
                    <button onClick={() => void navigator.clipboard.writeText(companionCommand)} className="flex items-center gap-1 rounded-lg bg-white px-2.5 py-1.5 text-[10px] font-medium text-violet-700 shadow-sm"><Copy size={10} />复制命令</button></div>
                  <code className="mt-2 block max-h-16 overflow-auto break-all rounded-lg bg-white/80 p-2 text-[9px] leading-4 text-violet-800">{companionCommand}</code>
                </div>
              )}
            </div>
          </div>
        )}
        <div className="flex min-h-0 flex-1 bg-[#15171b]">
          <div className="flex min-w-0 flex-1 items-center justify-center overflow-auto p-2">
            {frame ? (
              <img ref={imageRef} src={frame} draggable={false} tabIndex={0} alt="会话浏览器实时画面"
                className="max-h-full max-w-full select-none outline-none ring-violet-400 focus:ring-2"
                onMouseDown={e => { e.currentTarget.focus(); send({ type: 'mouse', action: 'down', ...point(e), button: e.button === 2 ? 'right' : 'left' }) }}
                onMouseUp={e => send({ type: 'mouse', action: 'up', ...point(e), button: e.button === 2 ? 'right' : 'left' })}
                onDoubleClick={e => send({ type: 'mouse', action: 'click', ...point(e), clickCount: 2 })}
                onWheel={e => { e.preventDefault(); send({ type: 'wheel', deltaX: e.deltaX, deltaY: e.deltaY }) }}
                onContextMenu={e => e.preventDefault()}
                onKeyDown={e => {
                  e.preventDefault()
                  if (!['Control', 'Alt', 'Shift', 'Meta'].includes(e.key)) send({ type: 'key', key: keyName(e) })
                }} />
            ) : (
              <div className="text-center text-sm text-gray-400">
                <Monitor size={38} className="mx-auto mb-3 opacity-40" />
                输入合法网址并点击“打开”；需要登录时直接在此画面手动操作
              </div>
            )}
          </div>
          {showNetwork && (
            <aside className="w-[420px] shrink-0 overflow-auto border-l border-white/10 bg-white">
              <div className="sticky top-0 z-10 flex items-center justify-between border-b bg-white px-3 py-2">
                <div><p className="text-xs font-semibold">捕获的接口与文件</p><p className="text-[10px] text-gray-400">分页线索会自动标注；认证头不展示</p></div>
                <button aria-label="刷新接口请求" onClick={() => void loadCaptures()} className="text-gray-400"><RefreshCw size={13} /></button>
              </div>
              <div className="space-y-2 p-2">
                {captures.length === 0 && <p className="py-12 text-center text-xs text-gray-400">操作页面后，请求会显示在这里</p>}
                {captures.map(item => (
                  <div key={item.id} className="rounded-lg border p-2.5">
                    <div className="flex items-center gap-1.5 text-[10px]">
                      <span className="rounded bg-gray-100 px-1.5 py-0.5 font-semibold">{item.method}</span>
                      <span className={item.status < 400 ? 'text-green-600' : 'text-red-500'}>{item.status}</span>
                      {item.pagination && <span className="rounded bg-blue-50 px-1.5 py-0.5 text-blue-600">分页 · {item.pagination.mode}</span>}
                      {item.isFile && <span className="rounded bg-amber-50 px-1.5 py-0.5 text-amber-600">文件</span>}
                    </div>
                    <p className="mt-1.5 break-all font-mono text-[10px] leading-4 text-gray-600">{item.url}</p>
                    {item.isFile && <button onClick={async () => { await stewardApi.downloadCapture(conversationId, item.id); await loadCaptures() }}
                      className="mt-2 flex items-center gap-1 text-[11px] font-medium text-violet-600"><Download size={11} />保存到会话</button>}
                  </div>
                ))}
              </div>
            </aside>
          )}
        </div>
        <div className="border-t bg-white px-4 py-2 text-[11px] text-gray-500">
          登录凭据由你直接输入到隔离浏览器，数据管家不会读取密码；完成登录后关闭弹窗并在对话中告诉它继续即可。
        </div>
      </div>
    </div>
  )
}

// ---------- 受管流水线面板 ----------

function ManagedPipelinesPanel({ records, loading, expandedId, onExpand, onChanged, n8nApiUrl, onOpenWizard }: {
  records: StewardPipeline[]
  loading: boolean
  expandedId: string | null
  onExpand: (id: string | null) => void
  onChanged: () => void
  n8nApiUrl: string
  onOpenWizard: (pipelineId: string) => void
}) {
  return (
    <aside className="flex xl:flex-1 xl:min-w-0 shrink-0 flex-col bg-white border overflow-hidden max-xl:max-h-[42%] max-xl:min-h-[180px]">
      <div className="flex items-center justify-between border-b px-4 py-3">
        <div className="flex items-center gap-2 min-w-0">
          <h2 className="flex items-center gap-1.5 text-sm font-semibold text-gray-800 shrink-0">
            <Workflow size={15} className="text-violet-600" /> 流水线清单
          </h2>
          <span className="truncate text-[11px] text-gray-400">当前只纳管处于未发布状态的流水线</span>
        </div>
        <button onClick={onChanged}
          className="flex items-center gap-1.5 px-2.5 py-1.5 border rounded-lg text-xs text-gray-600 hover:bg-gray-50 shrink-0">
          <RefreshCw size={13} /> 手动刷新
        </button>
      </div>

      <div className="flex-1 overflow-y-auto px-3 py-3 space-y-4">
        {loading ? (
          <div className="p-6 text-center text-sm text-gray-400">加载中…</div>
        ) : records.length === 0 ? (
          <div className="border-2 border-dashed rounded-xl p-8 text-center text-gray-400 space-y-2">
            <Workflow size={28} className="mx-auto opacity-30" />
            <p className="text-sm">还没有可编排的 n8n 流水线</p>
            <p className="text-xs">在左侧对话里让数据管家新建一条试试</p>
          </div>
        ) : (
          <div>
            <div className="space-y-2">
              {records.map(r => (
                <RecordCard
                  key={r.id} record={r}
                  expanded={expandedId === r.id}
                  onToggle={() => onExpand(expandedId === r.id ? null : r.id)}
                  n8nApiUrl={n8nApiUrl}
                  onOpenWizard={onOpenWizard}
                />
              ))}
            </div>
          </div>
        )}
      </div>
    </aside>
  )
}

// ---------- 迷你流水线图 ----------

const NODE_W = 120
const NODE_H = 32

function layoutGraph(nodes: Node[], edges: Edge[]) {
  const g = new dagre.graphlib.Graph()
  g.setDefaultEdgeLabel(() => ({}))
  g.setGraph({ rankdir: 'LR', nodesep: 20, ranksep: 50, marginx: 12, marginy: 12 })
  nodes.forEach(n => g.setNode(n.id, { width: NODE_W, height: NODE_H }))
  edges.forEach(e => g.setEdge(e.source, e.target))
  dagre.layout(g)
  return nodes.map(n => {
    const pos = g.node(n.id)
    return { ...n, position: { x: pos.x - NODE_W / 2, y: pos.y - NODE_H / 2 } }
  })
}

interface MiniWorkflowNode { name: string; type?: string; disabled?: boolean }
interface MiniWorkflowTarget { node: string; type?: string; index?: number }
interface MiniWorkflow {
  nodes?: MiniWorkflowNode[]
  connections?: Record<string, Record<string, MiniWorkflowTarget[][]>>
}

function buildGraph(workflow: unknown): { nodes: Node[]; edges: Edge[] } {
  const nodes: Node[] = []
  const edges: Edge[] = []
  const value = workflow && typeof workflow === 'object' ? workflow as MiniWorkflow : {}
  const wfNodes = value.nodes || []
  const wfConns = value.connections || {}

  wfNodes.forEach(n => {
    nodes.push({
      id: n.name,
      type: 'default',
      data: { label: n.name },
      position: { x: 0, y: 0 },
      style: {
        fontSize: '9px', padding: '4px 10px', borderRadius: '6px',
        border: '1px solid #e5e7eb', background: '#f9fafb', color: '#374151',
        width: NODE_W,
      },
    })
  })

  Object.entries(wfConns).forEach(([source, outputs]) => {
    Object.values(outputs).forEach(targetLanes => {
      targetLanes.forEach(targets => targets.forEach(t => {
        edges.push({
          id: `e-${source}-${t.node}`,
          source,
          target: t.node,
          style: { stroke: '#d1d5db', strokeWidth: 1.5 },
        })
      }))
    })
  })

  return { nodes, edges }
}

function MiniGraph({ workflow }: { workflow: unknown }) {
  const { nodes, edges } = buildGraph(workflow)
  const laidOut = layoutGraph([...nodes], [...edges])

  if (nodes.length === 0) return <div className="flex items-center justify-center h-full text-[11px] text-gray-400">暂无节点</div>

  return (
    <ReactFlowProvider>
      <ReactFlow
        nodes={laidOut}
        edges={edges}
        fitView
        fitViewOptions={{ padding: 0.15 }}
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable={false}
        panOnDrag
        zoomOnScroll
        preventScrolling={false}
        proOptions={{ hideAttribution: true }}
      >
        <Background color="#f3f4f6" gap={16} size={0.5} />
      </ReactFlow>
    </ReactFlowProvider>
  )
}

// ---------- 单条记录卡片 ----------

function RecordCard({ record: r, expanded, onToggle, n8nApiUrl, onOpenWizard }: {
  record: StewardPipeline
  expanded: boolean
  onToggle: () => void
  n8nApiUrl: string
  onOpenWizard: (pipelineId: string) => void
}) {
  const published = r.pipelineStatus === 'published'
  const meta = PUBLISH_META[published ? 'published' : 'draft']
  const [detail, setDetail] = useState<StewardPipelineDetail | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)

  useEffect(() => {
    if (expanded && !detail && !detailLoading) {
      const timer = window.setTimeout(() => {
        setDetailLoading(true)
        stewardApi.pipeline(r.id).then(setDetail).catch(() => {}).finally(() => setDetailLoading(false))
      }, 0)
      return () => window.clearTimeout(timer)
    }
  }, [expanded, detail, detailLoading, r.id])

  // 记录变化后（如发布/撤回/编排更新）重置已缓存的详情
  useEffect(() => {
    const timer = window.setTimeout(() => setDetail(null), 0)
    return () => window.clearTimeout(timer)
  }, [r.pipelineStatus, r.updatedAt])

  const n8nWebUrl = n8nApiUrl ? n8nApiUrl.replace(/\/api\/.*$/, '') : ''
  const canJump = !!(n8nWebUrl && r.n8nWorkflowId)

  return (
    <div className={`rounded-lg border transition-shadow ${expanded ? 'shadow-sm border-violet-200' : 'hover:border-gray-300'}`}>
      <button onClick={onToggle} className="flex w-full items-start gap-2 px-3 py-2.5 text-left">
        {expanded ? <ChevronDown size={14} className="mt-0.5 shrink-0 text-gray-400" /> : <ChevronRight size={14} className="mt-0.5 shrink-0 text-gray-400" />}
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            <p className="truncate text-sm font-medium text-gray-900">{r.name}</p>
            {published && <span className={`shrink-0 rounded border px-1.5 py-px text-[10px] ${meta.cls}`}>{meta.label}</span>}
            {r.active && <span className="shrink-0 rounded border border-green-200 bg-green-50 px-1.5 py-px text-[10px] text-green-600">n8n 已激活</span>}
            <button onClick={(e) => { e.stopPropagation(); if (r.pipelineId) onOpenWizard(r.pipelineId) }}
              className="flex items-center gap-1 rounded-lg border px-2 py-1 text-xs transition-colors border-violet-200 text-violet-700 hover:bg-violet-50 shrink-0 ml-auto"
              title="打开编辑向导">
              <Pencil size={11} /> 编辑
            </button>
            {canJump && (
              <button onClick={(e) => { e.stopPropagation(); window.open(`${n8nWebUrl}/workflow/${r.n8nWorkflowId}`, '_blank') }}
                className="flex items-center gap-1 rounded-lg border px-2 py-1 text-xs transition-colors border-blue-200 text-blue-600 hover:bg-blue-50 shrink-0"
                title="跳转 n8n 工作流">
                <ExternalLink size={11} /> 访问
              </button>
            )}
          </div>
          {r.description && <p className="mt-0.5 truncate text-[11px] text-gray-500">{r.description}</p>}
          <p className="mt-0.5 truncate text-[11px] text-gray-400">
            {r.summary?.node_count ?? 0} 个节点
            {r.summary?.webhook_path ? ` · webhook:/${r.summary.webhook_path}` : ' · 无 Webhook（平台不可调度）'}
          </p>
        </div>
      </button>

      {expanded && (
        <div className="border-t px-3 py-2.5 space-y-2">
          {/* 节点连线图 */}
          <div className="h-[180px] w-full rounded-lg overflow-hidden border bg-gray-50/30">
            {detailLoading ? (
              <div className="flex items-center justify-center h-full text-[11px] text-gray-400">加载中…</div>
            ) : (
              <MiniGraph workflow={detail?.workflow ?? r.summary} />
            )}
          </div>

          {/* n8n 可达性（只读；执行观测/试跑已不在管家职权内） */}
          {detail?.n8nError ? (
            <p className="text-[11px] text-amber-600">n8n 暂不可达：{detail.n8nError}</p>
          ) : null}
        </div>
      )}
    </div>
  )
}
