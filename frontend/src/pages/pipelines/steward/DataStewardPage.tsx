/**
 * 数据管家 — 对话式新建与编排 n8n 数据流水线
 *
 * 职权只有两件事：新建流水线（Webhook→输出 骨架）与编排未发布未启用的流水线。
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
  ClipboardCheck, ExternalLink, Eye, GitBranch, Globe, History, KeyRound, Library, Loader2,
  Plus, RefreshCw, Rocket, Search, Send, Settings, Sparkles, Trash2,
  User, Workflow, X, Zap,
} from 'lucide-react'
import {
  stewardApi, streamStewardChat,
  type StewardConversationDTO, type StewardPipeline, type StewardPipelineDetail,
  type StewardStatus, type StewardStep,
} from '@/api/steward'
import pipelinesApi from '@/api/v2/pipelines'
import type { Pipeline } from '@/api/v2/pipelines'
import PipelineEditWizard from '../PipelineEditWizard'

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

  useEffect(() => { loadStatus(); loadRecords(); loadConversations() }, [loadStatus, loadRecords, loadConversations])
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
            const { type: _t, ...step } = e
            patchBot(m => ({ steps: [...m.steps, step as StewardStep] }))
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
      <div ref={chatContainerRef} className="flex flex-1 min-h-0 max-xl:flex-col">
        {/* 对话区 */}
        <section style={isWide ? { width: `${chatWidthPct}%` } : undefined}
          className="flex h-full min-w-0 min-h-0 flex-col bg-white border overflow-hidden max-xl:w-full max-xl:min-h-[55%]">
          {/* 抬头：标题 + 操作按钮 */}
          <div className="flex items-center justify-between border-b px-4 py-2.5 shrink-0">
            <h2 className="flex items-center gap-1.5 text-sm font-semibold text-gray-800">
              <Sparkles size={15} className="text-violet-600" /> 数据管家
            </h2>
            <div className="flex items-center gap-2 shrink-0">
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

// ---------- 单条记录卡片 ----------

function RecordCard({ record: r, expanded, onToggle, n8nApiUrl, onOpenWizard }: {
  record: StewardPipeline
  expanded: boolean
  onToggle: () => void
  n8nApiUrl: string
  onOpenWizard: (pipelineId: string) => void
}) {
  const navigate = useNavigate()
  const published = r.pipelineStatus === 'published'
  const meta = PUBLISH_META[published ? 'published' : 'draft']
  const [detail, setDetail] = useState<StewardPipelineDetail | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)

  useEffect(() => {
    if (expanded && !detail && !detailLoading) {
      setDetailLoading(true)
      stewardApi.pipeline(r.id).then(setDetail).catch(() => {}).finally(() => setDetailLoading(false))
    }
  }, [expanded, detail, detailLoading, r.id])

  // 记录变化后（如发布/撤回/编排更新）重置已缓存的详情
  useEffect(() => { setDetail(null) }, [r.pipelineStatus, r.updatedAt])

  const btn = 'flex items-center gap-1 rounded-lg border px-2 py-1 text-xs transition-colors disabled:opacity-40'

  const n8nWebUrl = n8nApiUrl ? n8nApiUrl.replace(/\/api\/.*$/, '') : ''
  const canJump = !!(n8nWebUrl && r.n8nWorkflowId)

  return (
    <div className={`rounded-lg border transition-shadow ${expanded ? 'shadow-sm border-violet-200' : 'hover:border-gray-300'}`}>
      <button onClick={onToggle} className="flex w-full items-start gap-2 px-3 py-2.5 text-left">
        {expanded ? <ChevronDown size={14} className="mt-0.5 shrink-0 text-gray-400" /> : <ChevronRight size={14} className="mt-0.5 shrink-0 text-gray-400" />}
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            <p className="truncate text-sm font-medium text-gray-900">{r.name}</p>
            <span className={`shrink-0 rounded border px-1.5 py-px text-[10px] ${meta.cls}`}>{meta.label}</span>
            {r.active && <span className="shrink-0 rounded border border-green-200 bg-green-50 px-1.5 py-px text-[10px] text-green-600">n8n 已激活</span>}
            {canJump && (
              <button onClick={(e) => { e.stopPropagation(); window.open(`${n8nWebUrl}/workflow/${r.n8nWorkflowId}`, '_blank') }}
                className="p-0.5 rounded hover:bg-gray-100 text-gray-400 hover:text-gray-600 shrink-0" title="跳转 n8n 工作流">
                <ExternalLink size={11} />
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
        <div className="border-t px-3 py-2.5 space-y-2.5">
          {/* 节点链路 */}
          {(r.summary?.nodes?.length ?? 0) > 0 && (
            <div className="flex flex-wrap items-center gap-1">
              {r.summary.nodes.map((n, i) => (
                <span key={i} className="inline-flex items-center gap-1 rounded-md border bg-gray-50 px-1.5 py-0.5 text-[10px] text-gray-600" title={n.type}>
                  {n.name}
                </span>
              ))}
            </div>
          )}

          {/* n8n 可达性（只读；执行观测/试跑已不在管家职权内） */}
          {detail?.n8nError ? (
            <p className="text-[11px] text-amber-600">n8n 暂不可达：{detail.n8nError}</p>
          ) : null}

          {/* 管理入口：试跑/发布/撤回/归档都在流水线列表与编辑向导，不在管家 */}
          <div className="flex flex-wrap gap-1.5 pt-0.5">
            {!published ? (
              <button onClick={() => { if (r.pipelineId) onOpenWizard(r.pipelineId) }}
                title="打开编辑向导，预览数据、确认契约后发布并启用"
                className={`${btn} border-violet-200 text-violet-700 hover:bg-violet-50`}>
                <Rocket size={11} /> 去发布
              </button>
            ) : (
              <button onClick={() => navigate('/data/pipelines')}
                title="已发布流水线的撤回发布、启用开关都在流水线列表"
                className={`${btn} text-gray-600 hover:bg-gray-50`}>
                <GitBranch size={11} /> 在流水线列表管理
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
