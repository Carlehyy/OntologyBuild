/**
 * 数据管家 — 对话式新建与编排 n8n 数据流水线
 *
 * 对 n8n 的持久写权限只有两件事：新建流水线与编排未发布未启用的流水线；
 * 用户明确要求时可触发一次隔离执行预览，执行后恢复原启停状态且不写资产湖；
 * 另可在当前会话隔离空间内创建、编辑和删除文件。
 * 左侧：与数据管家对话（create_pipeline 新建骨架、update_workflow 补全编排）
 * 右侧：可编排流水线看板（只展示未发布、未启用的 n8n 流水线）。
 */
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import {
  Activity, AlertTriangle, ArrowLeft, BookOpen, Bot, ChevronDown, ChevronRight,
  CheckCircle2, ClipboardCheck, Copy, Download, ExternalLink, Eye, FileArchive, FileText, FolderOpen,
  GitBranch, Globe, Globe2, History, KeyRound, Library, List, Loader2, Monitor, MousePointer2,
  Paperclip, Pencil, Plus, RefreshCw, Search, Send, Settings, ShieldCheck, Sparkles, Table2, Trash2, Upload,
  User, Workflow, X, Zap, Wifi, WifiOff,
} from 'lucide-react'
import {
  downloadBrowserCompanion, downloadStewardFile, getStewardFileBlob, stewardApi, streamStewardChat,
  type BrowserCapture, type BrowserSource, type StewardArtifact,
  type StewardConversationDTO, type StewardPipeline, type StewardPipelineDetail,
  type StewardStatus, type StewardStep, type StewardTablePreview,
} from '@/api/steward'
import pipelinesApi from '@/api/v2/pipelines'
import type { Pipeline } from '@/api/v2/pipelines'
import SessionHistoryPopover from '@/components/SessionHistoryPopover'
import PipelineEditWizard from '../PipelineEditWizard'
import { ReactFlow, ReactFlowProvider, Background, type Node, type Edge } from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import dagre from '@dagrejs/dagre'

// ---------- 状态样式（发布状态 = 影子流水线，与流水线列表同一口径） ----------

const PUBLISH_META: Record<string, { label: string; cls: string }> = {
  draft:     { label: '草稿', cls: 'bg-slate-100 text-slate-600 border-slate-200' },
  published: { label: '已发布', cls: 'bg-teal-50 text-teal-700 border-teal-200' },
}

const TOOL_META: Record<string, { label: string; icon: React.ElementType }> = {
  steward_overview:    { label: '查看全景', icon: Eye },
  list_pipelines:      { label: '列出流水线', icon: GitBranch },
  get_workflow:        { label: '读取工作流', icon: Search },
  create_pipeline:     { label: '新建流水线', icon: Plus },
  update_workflow:     { label: '编排工作流', icon: Workflow },
  check_workflow:      { label: '体检', icon: ClipboardCheck },
  execute_pipeline:    { label: '执行流水线', icon: Zap },
  inspect_runs:        { label: '诊断执行', icon: Activity },
  check_credentials:   { label: '凭据检查', icon: KeyRound },
  list_node_types:     { label: '查节点目录', icon: Zap },
  describe_node:       { label: '查节点详情', icon: BookOpen },
  n8n_reference:       { label: '查编排参考', icon: Library },
  web_search:          { label: '联网检索', icon: Globe2 },
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
  browser_click_element: { label: '点击页面元素', icon: MousePointer2 },
  browser_page_resources: { label: '查找页面资源', icon: Search },
  browser_save_resource: { label: '保存页面资源', icon: Download },
  browser_type:        { label: '填写页面', icon: Pencil },
  browser_network_requests: { label: '分析页面接口', icon: Activity },
  download_captured_file: { label: '下载到会话', icon: Download },
  register_proxy_interface: { label: '登记代理接口', icon: Zap },
}

const SUGGESTED = [
  '帮我执行指定n8n流水线并展示结果',
  '新建一条n8n流水线并进行托管',
]

interface ChatMsg {
  id: string
  role: 'user' | 'assistant'
  content: string
  steps: StewardStep[]
  targetName?: string
  loading?: boolean
  error?: string
  createdAt?: string
}

let msgSeq = 0
const nextId = () => `m${Date.now()}_${msgSeq++}`

const ATTACH_ACCEPT = '.csv,.xlsx,.xls,.json,.xml,.pdf,.docx,.doc,.pptx,.ppt,.md,.txt'
const TEXTAREA_LINE_HEIGHT = 20
const TEXTAREA_MAX_LINES = 10
const TEXTAREA_MIN_HEIGHT = 28
const TEXTAREA_MAX_HEIGHT = TEXTAREA_LINE_HEIGHT * TEXTAREA_MAX_LINES + 8

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

function PreviewCell({ value }: { value: string | number | boolean | null }) {
  if (value === null || value === undefined || value === '') {
    return <span className="text-slate-300">空</span>
  }
  if (typeof value === 'boolean') {
    return <span className={value ? 'text-emerald-700' : 'text-slate-500'}>{value ? '是' : '否'}</span>
  }
  const text = String(value)
  return <span title={text} className="block max-w-[280px] truncate">{text}</span>
}

function OutputPreviewTable({ preview }: { preview: StewardTablePreview }) {
  const hasRows = preview.rows.length > 0 && preview.columns.length > 0
  return (
    <div className="ml-7 overflow-hidden rounded-xl border border-teal-200 bg-white shadow-[0_8px_24px_-20px_rgba(15,118,110,0.5)]">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-slate-100 bg-teal-50/60 px-3 py-2">
        <div className="flex min-w-0 items-center gap-1.5 text-xs font-semibold text-slate-800">
          <Table2 size={13} className="shrink-0 text-teal-700" />
          <span className="truncate">{preview.title || '输出样例'}</span>
        </div>
        <span className="text-[10px] text-slate-500">
          {preview.node ? `${preview.node} · ` : ''}{preview.shownRows}/{preview.totalRows} 行
          {preview.totalColumns > 0 ? ` · ${preview.columns.length}/${preview.totalColumns} 列` : ''}
        </span>
        {preview.redactedColumns.length > 0 && (
          <span className="ml-auto inline-flex items-center gap-1 rounded-full border border-emerald-200 bg-white px-2 py-0.5 text-[10px] text-emerald-700">
            <ShieldCheck size={10} /> 敏感字段已隐藏
          </span>
        )}
      </div>
      {hasRows ? (
        <div className="max-h-[320px] overflow-auto">
          <table className="min-w-full border-separate border-spacing-0 text-left text-xs">
            <thead className="sticky top-0 z-10 bg-slate-50/95 backdrop-blur">
              <tr>
                <th className="w-10 border-b border-r border-slate-200 px-2.5 py-2 text-center font-medium text-slate-400">#</th>
                {preview.columns.map(column => (
                  <th key={column} className="whitespace-nowrap border-b border-r border-slate-200 px-3 py-2 font-semibold text-slate-600 last:border-r-0">
                    {column}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {preview.rows.map((row, rowIndex) => (
                <tr key={rowIndex} className="odd:bg-white even:bg-slate-50/45 hover:bg-teal-50/40">
                  <td className="border-b border-r border-slate-100 px-2.5 py-2 text-center font-mono text-[10px] text-slate-400">{rowIndex + 1}</td>
                  {preview.columns.map(column => (
                    <td key={column} className="whitespace-nowrap border-b border-r border-slate-100 px-3 py-2 text-slate-700 last:border-r-0">
                      <PreviewCell value={row[column]} />
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="px-3 py-5 text-center text-xs text-slate-400">最近一次执行没有产生可展示的行数据</div>
      )}
      {(preview.truncated || preview.missingColumns.length > 0) && (
        <div className="border-t border-slate-100 px-3 py-2 text-[10px] leading-4 text-slate-500">
          {preview.truncated && `当前仅展示部分结果${preview.omittedColumns > 0 ? `，另有 ${preview.omittedColumns} 个字段未展开` : ''}。`}
          {preview.missingColumns.length > 0 && ` 本次输出中未找到：${preview.missingColumns.join('、')}。`}
        </div>
      )}
    </div>
  )
}

function StepTrace({ steps, running }: { steps: StewardStep[]; running?: boolean }) {
  if (steps.length === 0 && !running) return null
  return (
    <div className="mb-3 space-y-2 rounded-xl border border-slate-200 bg-slate-50 px-3.5 py-3">
      {steps.map((s, i) => {
        const meta = TOOL_META[s.tool] || { label: s.tool, icon: Zap }
        const Icon = meta.icon
        return (
          <div key={i} className="space-y-2">
            <div className="flex items-start gap-2.5">
              <div className={`mt-px w-5 h-5 rounded-md flex items-center justify-center shrink-0 ${
                s.error ? 'bg-red-50 text-red-500' : 'bg-teal-100 text-teal-700'}`}>
                {s.tool === 'web_search' ? <Globe2 size={11} /> : <Icon size={11} />}
              </div>
              <div className="min-w-0 text-xs leading-5">
                <span className={`font-medium ${s.error ? 'text-red-600' : 'text-gray-800'}`}>{meta.label}</span>
                <span className="text-slate-400"> · {s.summary}</span>
              </div>
            </div>
            {s.searchResults && s.searchResults.length > 0 && (
              <div className="ml-7 space-y-1">
                {s.searchResults.map((result, index) => (
                  <a
                    key={`${result.url}-${index}`}
                    href={result.url}
                    target="_blank"
                    rel="noreferrer"
                    title={result.snippet || result.title}
                    className="group/source flex min-w-0 items-center gap-1.5 rounded-md px-2 py-1 text-[11px] text-slate-600 transition-colors hover:bg-teal-50 hover:text-teal-700"
                  >
                    <span className="shrink-0 font-mono text-[10px] text-slate-400">[{index + 1}]</span>
                    <span className="min-w-0 flex-1 truncate">{result.title}</span>
                    <ExternalLink size={10} className="shrink-0 opacity-0 transition-opacity group-hover/source:opacity-100" />
                  </a>
                ))}
              </div>
            )}
            {s.preview && <OutputPreviewTable preview={s.preview} />}
          </div>
        )
      })}
      {running && (
        <div className="flex items-center gap-2.5">
          <div className="w-5 h-5 rounded-md bg-teal-50 flex items-center justify-center shrink-0">
            <Loader2 size={11} className="animate-spin text-teal-600" />
          </div>
          <span className="text-xs text-gray-400">
            {steps.length === 0 ? '正在识别你的意图并选择最合适的处理路径…' : '正在综合工具结果继续…'}
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
  const [webSearch, setWebSearch] = useState(false)
  const [showMessageHistory, setShowMessageHistory] = useState(false)
  const [files, setFiles] = useState<StewardArtifact[]>([])
  const [uploads, setUploads] = useState<{ uid: string; name: string; ts: number }[]>([])
  const [fileError, setFileError] = useState('')
  const bottomRef = useRef<HTMLDivElement>(null)
  const abortRef = useRef<AbortController | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  const [records, setRecords] = useState<StewardPipeline[]>([])
  const [recordsLoading, setRecordsLoading] = useState(true)
  const [expandedId, setExpandedId] = useState<string | null>(searchParams.get('record'))
  const [selectedRecordId, setSelectedRecordId] = useState<string | null>(searchParams.get('record'))
  const [targetMenuOpen, setTargetMenuOpen] = useState(false)
  const [targetSearch, setTargetSearch] = useState('')
  const targetPickerRef = useRef<HTMLDivElement>(null)
  const [editTarget, setEditTarget] = useState<Pipeline | null>(null)
  const n8nApiUrl = status?.n8n?.api_url ?? ''
  // 拖拽调整对话区/审批面板宽度（仅宽屏有效）
  const [chatWidthPct, setChatWidthPct] = useState(58)
  const chatContainerRef = useRef<HTMLDivElement>(null)
  const [isWide, setIsWide] = useState(typeof window !== 'undefined' && window.innerWidth >= 1280)

  const myMessages = useMemo(() => messages.filter(message => message.role === 'user'), [messages])
  const timeline = useMemo(() => {
    const items: (
      | { key: string; ts: number; kind: 'message'; message: ChatMsg }
      | { key: string; ts: number; kind: 'file'; file: StewardArtifact }
      | { key: string; ts: number; kind: 'upload'; upload: { uid: string; name: string; ts: number } }
    )[] = []
    let lastTimestamp = 0
    messages.forEach(message => {
      let timestamp = message.createdAt ? Date.parse(message.createdAt) : NaN
      if (Number.isNaN(timestamp)) timestamp = lastTimestamp + 1
      lastTimestamp = timestamp
      items.push({ key: message.id, ts: timestamp, kind: 'message', message })
    })
    files
      .filter(file => file.source === 'upload')
      .forEach(file => items.push({
        key: `file-${file.id}`,
        ts: Date.parse(file.createdAt) || 0,
        kind: 'file',
        file,
      }))
    uploads.forEach(upload => items.push({
      key: upload.uid,
      ts: upload.ts,
      kind: 'upload',
      upload,
    }))
    return items.sort((left, right) => left.ts - right.ts)
  }, [files, messages, uploads])

  useLayoutEffect(() => {
    const textarea = textareaRef.current
    if (!textarea) return
    textarea.style.height = `${TEXTAREA_MIN_HEIGHT}px`
    const contentHeight = input ? textarea.scrollHeight : TEXTAREA_MIN_HEIGHT
    textarea.style.height = `${Math.max(TEXTAREA_MIN_HEIGHT, Math.min(contentHeight, TEXTAREA_MAX_HEIGHT))}px`
    textarea.style.overflowY = contentHeight > TEXTAREA_MAX_HEIGHT ? 'auto' : 'hidden'
  }, [input])
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

  const loadSessionFiles = useCallback((cid: string) => {
    return stewardApi.files(cid)
      .then(res => setFiles(Array.isArray(res) ? res : []))
      .catch(() => setFiles([]))
  }, [])

  useEffect(() => {
    const timer = window.setTimeout(() => { loadStatus(); void loadRecords(); loadConversations() }, 0)
    return () => window.clearTimeout(timer)
  }, [loadStatus, loadRecords, loadConversations])
  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [timeline])
  useEffect(() => () => abortRef.current?.abort(), [])
  useEffect(() => {
    const onPointerDown = (event: MouseEvent) => {
      if (!targetPickerRef.current?.contains(event.target as globalThis.Node)) setTargetMenuOpen(false)
    }
    document.addEventListener('mousedown', onPointerDown)
    return () => document.removeEventListener('mousedown', onPointerDown)
  }, [])

  const selectedRecord = records.find(record => record.id === selectedRecordId) || null
  const filteredTargetRecords = records.filter(record => {
    const keyword = targetSearch.trim().toLowerCase()
    if (!keyword) return true
    return record.name.toLowerCase().includes(keyword)
      || record.description.toLowerCase().includes(keyword)
      || record.id.toLowerCase().includes(keyword)
  })

  useEffect(() => {
    if (selectedRecordId && !recordsLoading && !records.some(record => record.id === selectedRecordId)) {
      setSelectedRecordId(null)
    }
  }, [records, recordsLoading, selectedRecordId])

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
    setFiles([])
    setUploads([])
    setFileError('')
    setShowHistory(false)
    setShowMessageHistory(false)
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
        id: m.id, role: m.role, content: m.content, steps: m.steps || [], createdAt: m.createdAt,
      })))
      setUploads([])
      setFileError('')
      await loadSessionFiles(cid)
      setShowHistory(false)
      setShowMessageHistory(false)
    } catch { /* 忽略 */ }
  }

  const removeConversation = async (cid: string) => {
    try {
      await stewardApi.deleteConversation(cid)
      if (cid === conversationId) resetChat()
      loadConversations()
    } catch { /* 忽略 */ }
  }

  const pickFiles = () => fileInputRef.current?.click()

  const uploadFiles = async (selected: FileList | null) => {
    if (!selected || selected.length === 0) return
    setFileError('')
    let cid: string
    try {
      cid = await ensureConversation()
    } catch (error: unknown) {
      setFileError(errorText(error, '无法创建会话，附件未上传'))
      return
    }
    for (const file of Array.from(selected)) {
      const uid = `upload-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`
      setUploads(previous => [...previous, { uid, name: file.name, ts: Date.now() }])
      try {
        const artifact = await stewardApi.uploadFile(cid, file)
        setFiles(previous => [...previous, artifact])
      } catch (error: unknown) {
        setFileError(`「${file.name}」上传失败：${errorText(error, '无法读取文件内容')}`)
      } finally {
        setUploads(previous => previous.filter(upload => upload.uid !== uid))
      }
    }
    if (fileInputRef.current) fileInputRef.current.value = ''
    loadConversations()
  }

  const removeUploadedFile = async (artifactId: string) => {
    if (!conversationId) return
    setFiles(previous => previous.filter(file => file.id !== artifactId))
    try {
      await stewardApi.deleteFile(conversationId, artifactId)
    } catch (error: unknown) {
      setFileError(errorText(error, '文件移除失败，请刷新后重试'))
      void loadSessionFiles(conversationId)
    }
  }

  const downloadUploadedFile = async (file: StewardArtifact) => {
    if (!conversationId) return
    try {
      await downloadStewardFile(conversationId, file.id, file.filename)
    } catch (error: unknown) {
      setFileError(`「${file.filename}」下载失败：${errorText(error, '文件不可用')}`)
    }
  }

  const jumpToMessage = (messageId: string) => {
    setShowMessageHistory(false)
    requestAnimationFrame(() => {
      document.getElementById(`steward-msg-${messageId}`)?.scrollIntoView({
        behavior: 'smooth',
        block: 'center',
      })
    })
  }

  const send = async (preset?: string) => {
    const text = (preset ?? input).trim()
    if (!text || busy) return
    setInput('')
    setBusy(true)

    const target = selectedRecord
    const createdAt = new Date().toISOString()
    const userMsg: ChatMsg = {
      id: nextId(),
      role: 'user',
      content: text,
      steps: [],
      targetName: target?.name,
      createdAt,
    }
    const botMsg: ChatMsg = {
      id: nextId(),
      role: 'assistant',
      content: '',
      steps: [],
      loading: true,
      createdAt,
    }
    setMessages(prev => [...prev, userMsg, botMsg])

    const patchBot = (patch: Partial<ChatMsg> | ((m: ChatMsg) => Partial<ChatMsg>)) =>
      setMessages(prev => prev.map(m =>
        m.id === botMsg.id ? { ...m, ...(typeof patch === 'function' ? patch(m) : patch) } : m))

    const ctrl = new AbortController()
    abortRef.current = ctrl
    let touched: string[] = []
    let activeConversationId = conversationId
    try {
      await streamStewardChat(
        {
          message: text,
          conversationId,
          targetRecordId: target?.id,
          webSearch,
        },
        e => {
          if (e.type === 'meta') {
            activeConversationId = e.conversationId
            setConversationId(e.conversationId)
          }
          else if (e.type === 'step') {
            const step: StewardStep = {
              tool: e.tool, arguments: e.arguments, summary: e.summary,
              durationMs: e.durationMs, ...(e.error ? { error: e.error } : {}),
              ...(e.preview ? { preview: e.preview } : {}),
              ...(e.searchResults ? { searchResults: e.searchResults } : {}),
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
      if (activeConversationId) void loadSessionFiles(activeConversationId)
      // 本回合动过流水线 → 刷新受管流水线面板并展开最近触达的一条
      if (touched.length > 0) {
        loadRecords()
        setExpandedId(touched[touched.length - 1])
      }
    }
  }

  const n8nReady = !!status && status.n8n.configured && status.n8n.enabled && status.n8n.reachable !== false

  return (
    <div className="flex h-full flex-col bg-slate-50/70">
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

      {/* 主体：对话 + 草稿面板（窄屏纵向堆叠） */}
      <div ref={chatContainerRef} className="flex min-h-0 flex-1 gap-0 p-1 max-xl:flex-col max-xl:gap-1">
        {/* 对话区 */}
        <section style={isWide ? { width: `${chatWidthPct}%` } : undefined}
          className="flex h-full min-h-0 min-w-0 flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-[0_8px_30px_rgba(15,23,42,0.04)] max-xl:min-h-[55%] max-xl:w-full">
          {/* 抬头：标题 + 操作按钮 */}
          <div className="flex shrink-0 items-center justify-between border-b border-slate-100 px-5 py-3.5">
            <div>
              <h2 className="flex items-center gap-2 text-sm font-semibold text-slate-900">
                <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-teal-700 text-white"><Sparkles size={14} /></span>
                数据管家
              </h2>
            </div>
            <div className="flex items-center gap-2 shrink-0">
              <button
                onClick={openFiles}
                title="查看会话文件"
                aria-label="查看会话文件"
                className="group relative inline-flex h-8 w-8 items-center justify-center rounded-md border border-slate-200 text-slate-500 transition-colors hover:border-teal-300 hover:bg-teal-50 hover:text-teal-700 active:scale-[0.98] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-400"
              >
                <FolderOpen size={15} />
                {files.length > 0 && (
                  <span className="absolute -right-1.5 -top-1.5 flex min-w-4 items-center justify-center rounded-full bg-teal-600 px-1 text-[9px] font-semibold leading-4 text-white">
                    {files.length > 99 ? '99+' : files.length}
                  </span>
                )}
              </button>
              <button
                onClick={openBrowser}
                title="打开实时浏览器"
                aria-label="打开实时浏览器"
                className="inline-flex h-8 w-8 items-center justify-center rounded-md border border-slate-200 text-slate-500 transition-colors hover:border-sky-300 hover:bg-sky-50 hover:text-sky-700 active:scale-[0.98] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-400"
              >
                <Monitor size={15} />
              </button>
              <div className="relative">
                <button
                  type="button"
                  onClick={() => {
                    if (!showHistory) loadConversations()
                    setShowHistory(value => !value)
                  }}
                  title="查看会话记录"
                  aria-label="查看会话记录"
                  aria-expanded={showHistory}
                  className={`inline-flex h-8 w-8 items-center justify-center rounded-md border transition-colors active:scale-[0.98] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-400 ${showHistory
                    ? 'border-teal-300 bg-teal-50 text-teal-700'
                    : 'border-slate-200 text-slate-500 hover:border-teal-300 hover:bg-teal-50 hover:text-teal-700'}`}
                >
                  <History size={15} />
                </button>
                <SessionHistoryPopover
                  open={showHistory}
                  items={conversations}
                  currentId={conversationId}
                  onClose={() => setShowHistory(false)}
                  onCreate={resetChat}
                  onSelect={loadConversation}
                  onDelete={removeConversation}
                  renderItemIcon={() => <Sparkles size={16} />}
                  emptyDescription="新建会话后，可随时回到之前的数据采集与流水线编排过程。"
                />
              </div>
            </div>
          </div>
          <div className="scrollbar-none flex-1 overflow-y-auto px-5 py-5">
            {timeline.length === 0 ? (
              <div className="flex h-full flex-col items-center justify-center text-center px-6">
                <div className="mb-4 flex h-14 w-14 items-center justify-center rounded-2xl border border-teal-200 bg-teal-50 text-teal-700 shadow-sm">
                  <Bot size={26} />
                </div>
                <h2 className="text-base font-semibold text-gray-900">让数据管家替你编排流水线</h2>
                <p className="mt-1.5 max-w-md text-sm leading-relaxed text-gray-500">
                  数据管家Agent可以帮助您托管和编排流水线
                </p>
                <div className="mt-5 flex flex-wrap justify-center gap-2">
                  {SUGGESTED.map(q => (
                    <button key={q} onClick={() => send(q)} disabled={busy}
                      className="rounded-full border bg-white px-3 py-1.5 text-xs text-gray-600 transition-all hover:border-teal-300 hover:bg-teal-50 hover:text-teal-700 disabled:opacity-50">
                      {q}
                    </button>
                  ))}
                </div>
              </div>
            ) : (
              <div className="space-y-5">
                {timeline.map(item => {
                  if (item.kind === 'file' || item.kind === 'upload') {
                    const uploading = item.kind === 'upload'
                    const name = uploading ? item.upload.name : item.file.filename
                    return (
                      <div key={item.key} className="flex flex-row-reverse gap-3">
                        <div className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-xl border border-teal-200 bg-teal-50 text-teal-700">
                          <Paperclip size={14} />
                        </div>
                        <div className={`group flex max-w-[82%] items-center gap-2.5 rounded-xl border bg-white px-3 py-2 ${uploading
                          ? 'border-dashed border-slate-300'
                          : 'border-slate-200'}`}
                        >
                          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-teal-50 text-teal-600">
                            {uploading ? <Loader2 size={15} className="animate-spin" /> : <FileText size={16} />}
                          </span>
                          <div className="min-w-0 text-left">
                            <div className="truncate text-sm font-medium text-slate-800" title={name}>{name}</div>
                            <div className="mt-0.5 text-[11px] text-slate-400">
                              {uploading ? '上传中…' : `会话附件 · ${formatBytes(item.file.size)} · 仅本会话可见`}
                            </div>
                          </div>
                          {!uploading && (
                            <div className="flex shrink-0 items-center gap-0.5 opacity-0 transition-opacity group-hover:opacity-100">
                              <button
                                type="button"
                                onClick={() => void downloadUploadedFile(item.file)}
                                title="下载文件"
                                className="rounded p-1 text-slate-400 hover:bg-slate-50 hover:text-teal-600"
                              >
                                <Download size={13} />
                              </button>
                              <button
                                type="button"
                                onClick={() => void removeUploadedFile(item.file.id)}
                                title="移除附件"
                                className="rounded p-1 text-slate-400 hover:bg-red-50 hover:text-red-600"
                              >
                                <X size={13} />
                              </button>
                            </div>
                          )}
                        </div>
                      </div>
                    )
                  }
                  const msg = item.message
                  return msg.role === 'user' ? (
                    <div key={msg.id} id={`steward-msg-${msg.id}`} className="flex scroll-mt-4 justify-end gap-3">
                      <div className="max-w-[82%] rounded-2xl rounded-br-md bg-teal-700 px-4 py-3 text-white shadow-sm">
                        {msg.targetName && (
                          <p className="mb-1.5 flex items-center justify-end gap-1 text-[10px] font-medium text-teal-100">
                            <Workflow size={10} /> 操作目标 · {msg.targetName}
                          </p>
                        )}
                        <p className="whitespace-pre-wrap break-words text-sm leading-relaxed">{msg.content}</p>
                      </div>
                      <div className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-xl border border-teal-200 bg-teal-50 text-teal-700">
                        <User size={14} />
                      </div>
                    </div>
                  ) : (
                    <div key={msg.id} className="flex gap-3">
                      <div className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-xl bg-slate-900 text-white">
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
                  )
                })}
                <div ref={bottomRef} />
              </div>
            )}
          </div>

          <div className="relative bg-white px-4 pb-4 pt-3">
            <input
              ref={fileInputRef}
              type="file"
              multiple
              accept={ATTACH_ACCEPT}
              className="hidden"
              onChange={event => void uploadFiles(event.target.files)}
            />
            {fileError && (
              <div className="mb-1.5 truncate text-[11px] text-red-600" title={fileError}>
                {fileError}
              </div>
            )}
            <div
              ref={targetPickerRef}
              data-testid="steward-composer-shell"
              className="relative overflow-visible rounded-xl border border-slate-200 bg-white transition-colors focus-within:border-teal-500 focus-within:ring-1 focus-within:ring-teal-500/10"
            >
              <div className="flex min-h-10 items-center gap-2 border-b border-slate-100 px-3.5 py-2">
                <span className="inline-flex shrink-0 items-center gap-1.5 text-[11px] font-medium text-slate-500">
                  <Workflow size={12} className="text-teal-700" /> 操作目标
                </span>
                <button
                  type="button"
                  disabled={busy}
                  aria-haspopup="listbox"
                  aria-expanded={targetMenuOpen}
                  onClick={() => {
                    setTargetMenuOpen(open => !open)
                    setTargetSearch('')
                  }}
                  className={`flex min-w-0 flex-1 items-center gap-2 rounded-lg px-2 py-1 text-left text-xs transition ${
                    selectedRecord
                      ? 'bg-teal-50 text-teal-800 hover:bg-teal-100'
                      : 'text-slate-400 hover:bg-slate-50 hover:text-slate-600'
                  } disabled:cursor-not-allowed disabled:opacity-60`}
                >
                  <span className="min-w-0 flex-1 truncate">
                    {selectedRecord ? selectedRecord.name : recordsLoading ? '正在加载可编排流水线…' : '选择一条可编排流水线（可选）'}
                  </span>
                  {!recordsLoading && (
                    <span className="shrink-0 text-[10px] text-slate-400">
                      {selectedRecord ? `${selectedRecord.summary.node_count} 个节点` : `${records.length} 条`}
                    </span>
                  )}
                  <ChevronDown size={12} className={`shrink-0 transition-transform ${targetMenuOpen ? 'rotate-180' : ''}`} />
                </button>
                {selectedRecord && (
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => {
                      setSelectedRecordId(null)
                      setTargetMenuOpen(false)
                      textareaRef.current?.focus()
                    }}
                    aria-label="清除目标流水线"
                    title="清除目标流水线"
                    className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg text-slate-400 transition hover:bg-slate-100 hover:text-slate-700 disabled:opacity-50"
                  >
                    <X size={13} />
                  </button>
                )}
              </div>

              {targetMenuOpen && !busy && (
                <div className="absolute bottom-[calc(100%+8px)] left-0 right-0 z-40 overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-[0_18px_52px_rgba(15,23,42,0.16)]">
                  <div className="border-b border-slate-100 p-2.5">
                    <div className="relative">
                      <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
                      <input
                        autoFocus
                        value={targetSearch}
                        onChange={event => setTargetSearch(event.target.value)}
                        onKeyDown={event => {
                          if (event.key === 'Escape') setTargetMenuOpen(false)
                          event.stopPropagation()
                        }}
                        placeholder="搜索流水线名称或描述"
                        className="h-9 w-full rounded-xl border border-slate-200 bg-slate-50 pl-8 pr-3 text-xs outline-none transition focus:border-teal-400 focus:bg-white"
                      />
                    </div>
                  </div>
                  <div role="listbox" aria-label="可编排流水线" className="max-h-64 overflow-y-auto p-1.5">
                    {recordsLoading ? (
                      <div className="flex items-center justify-center gap-2 py-8 text-xs text-slate-400">
                        <Loader2 size={13} className="animate-spin" /> 正在加载
                      </div>
                    ) : filteredTargetRecords.length === 0 ? (
                      <div className="px-4 py-8 text-center text-xs leading-5 text-slate-400">
                        {records.length === 0 ? '当前没有可编排流水线，可先让数据管家新建一条。' : '没有匹配的可编排流水线。'}
                      </div>
                    ) : filteredTargetRecords.map(record => (
                      <button
                        key={record.id}
                        type="button"
                        role="option"
                        aria-selected={selectedRecordId === record.id}
                        onClick={() => {
                          setSelectedRecordId(record.id)
                          setExpandedId(record.id)
                          setTargetMenuOpen(false)
                          setTargetSearch('')
                          textareaRef.current?.focus()
                        }}
                        className={`flex w-full items-start gap-2.5 rounded-xl px-3 py-2.5 text-left transition ${
                          selectedRecordId === record.id
                            ? 'bg-teal-50 text-teal-900'
                            : 'text-slate-700 hover:bg-slate-50'
                        }`}
                      >
                        <span className={`mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg ${
                          selectedRecordId === record.id ? 'bg-white text-teal-700' : 'bg-slate-100 text-slate-500'
                        }`}>
                          <Workflow size={13} />
                        </span>
                        <span className="min-w-0 flex-1">
                          <span className="flex items-center gap-2">
                            <span className="truncate text-xs font-semibold">{record.name}</span>
                            <span className="shrink-0 text-[10px] text-slate-400">{record.summary.node_count} 个节点</span>
                          </span>
                          <span className="mt-0.5 block truncate text-[10px] text-slate-400">
                            {record.description || '暂未设置描述'}
                          </span>
                        </span>
                        {selectedRecordId === record.id && <CheckCircle2 size={14} className="mt-1 shrink-0 text-teal-600" />}
                      </button>
                    ))}
                  </div>
                </div>
              )}

              <div data-testid="steward-composer-input" className="px-3 pb-2 pt-2.5">
                <textarea
                  ref={textareaRef}
                  value={input}
                  onChange={event => setInput(event.target.value)}
                  onKeyDown={event => {
                    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
                      event.preventDefault()
                      void send()
                    }
                  }}
                  rows={1}
                  placeholder={n8nReady
                    ? selectedRecord
                      ? `告诉数据管家要如何操作「${selectedRecord.name}」…（Enter 发送，Shift+Enter 换行）`
                      : '描述数据源或流水线需求…（Enter 发送，Shift+Enter 换行）'
                    : '请先完成 n8n 配置'}
                  disabled={busy}
                  aria-label="数据管家消息"
                  data-testid="steward-composer"
                  className="scrollbar-thin block min-h-7 w-full resize-none bg-transparent py-1 text-sm leading-5 outline-none placeholder:text-slate-400 disabled:opacity-50"
                />
              </div>
              <div
                data-testid="steward-composer-toolbar"
                className="flex min-h-12 items-center justify-between gap-3 px-2.5 py-2"
              >
                <div className="flex min-w-0 items-center gap-1">
                  <button
                    type="button"
                    onClick={pickFiles}
                    title="上传会话附件（仅本会话可见）"
                    aria-label="上传会话附件"
                    className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-slate-400 transition-colors hover:bg-slate-50 hover:text-teal-600 active:scale-[0.98] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-400"
                  >
                    <Paperclip size={16} />
                  </button>
                  <button
                    type="button"
                    onClick={() => setWebSearch(value => !value)}
                    aria-pressed={webSearch}
                    data-testid="steward-web-search-toggle"
                    title={webSearch ? '联网搜索已开启，点击关闭' : '联网搜索已关闭，点击开启'}
                    className={`inline-flex h-8 shrink-0 items-center gap-1.5 rounded-lg border px-2 text-[11px] font-medium transition-all active:scale-[0.98] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-400 ${webSearch
                      ? 'border-teal-300 bg-teal-50 text-teal-700'
                      : 'border-transparent text-slate-400 hover:border-slate-200 hover:bg-slate-50 hover:text-slate-600'}`}
                  >
                    <Globe2 size={15} />
                    <span>联网</span>
                    <span className={`h-1.5 w-1.5 rounded-full transition-colors ${webSearch ? 'bg-teal-500' : 'bg-slate-200'}`} />
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
                    data-testid="steward-message-history-button"
                    className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border transition-colors active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-30 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-400 ${showMessageHistory
                      ? 'border-teal-300 bg-teal-50 text-teal-700'
                      : 'border-slate-200 text-slate-400 hover:bg-slate-50 hover:text-slate-600'}`}
                  >
                    <List size={15} />
                  </button>
                </div>
              </div>
              {showMessageHistory && (
                <>
                  <div className="fixed inset-0 z-20" onClick={() => setShowMessageHistory(false)} />
                  <div className="absolute bottom-full right-0 z-30 mb-2 w-72 overflow-hidden rounded-lg border border-slate-200 bg-white shadow-lg">
                    <div className="flex items-center justify-between border-b border-slate-200 px-3 py-2">
                      <span className="text-[11px] font-medium text-slate-600">我发送的消息</span>
                      <span className="text-[10px] text-slate-400">点击跳转 · 共 {myMessages.length} 条</span>
                    </div>
                    <div className="scrollbar-thin max-h-64 overflow-auto py-1">
                      {[...myMessages].reverse().map((message, index) => (
                        <button
                          type="button"
                          key={message.id}
                          onClick={() => jumpToMessage(message.id)}
                          title={message.content}
                          className="flex w-full items-start gap-2 px-3 py-1.5 text-left transition-colors hover:bg-slate-50 focus-visible:bg-slate-50 focus-visible:outline-none"
                        >
                          <span className="mt-0.5 shrink-0 font-mono text-[10px] text-slate-400">#{myMessages.length - index}</span>
                          <span className="min-w-0 flex-1 truncate text-xs text-slate-600">{message.content}</span>
                        </button>
                      ))}
                    </div>
                  </div>
                </>
              )}
            </div>
          </div>
        </section>

        {/* 拖拽分隔条（仅宽屏，拖动调整对话区与受管流水线面板宽度） */}
        <div
          onMouseDown={startResize}
          className="hidden w-1 shrink-0 cursor-col-resize items-center justify-center xl:flex"
        >
          <div className="h-16 w-1 rounded-full bg-slate-200 transition-all hover:h-24 hover:bg-teal-400" />
        </div>

        {/* 受管流水线面板 */}
        <ManagedPipelinesPanel
          records={records}
          loading={recordsLoading}
          expandedId={expandedId}
          onExpand={setExpandedId}
          onChanged={() => { loadRecords(); loadStatus() }}
          onBack={() => navigate('/data/pipelines')}
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
        <WorkspaceModal
          conversationId={conversationId}
          onClose={() => {
            setShowFiles(false)
            void loadSessionFiles(conversationId)
          }}
        />
      )}
      {showBrowser && conversationId && (
        <BrowserModal conversationId={conversationId} onClose={() => setShowBrowser(false)} />
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

type WorkspacePreview = {
  kind: 'empty' | 'loading' | 'text' | 'image' | 'pdf' | 'unsupported' | 'error'
  text?: string
  url?: string
  message?: string
  truncated?: boolean
}

function WorkspaceModal({ conversationId, onClose }: { conversationId: string; onClose: () => void }) {
  const [files, setFiles] = useState<StewardArtifact[]>([])
  const [loading, setLoading] = useState(true)
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState('')
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [preview, setPreview] = useState<WorkspacePreview>({ kind: 'empty' })
  const inputRef = useRef<HTMLInputElement>(null)

  const reload = useCallback(() => {
    setLoading(true)
    return stewardApi.files(conversationId)
      .then(rows => {
        const nextFiles = Array.isArray(rows) ? rows : []
        setFiles(nextFiles)
        setSelectedId(current => current && nextFiles.some(file => file.id === current)
          ? current
          : nextFiles[0]?.id || null)
      })
      .catch(err => setError(errorText(err, '会话文件加载失败')))
      .finally(() => setLoading(false))
  }, [conversationId])

  useEffect(() => {
    const timer = window.setTimeout(() => void reload(), 0)
    return () => window.clearTimeout(timer)
  }, [reload])

  const selectedFile = files.find(file => file.id === selectedId) || null

  useEffect(() => {
    if (!selectedFile) {
      setPreview({ kind: 'empty' })
      return
    }
    let cancelled = false
    let objectUrl = ''
    const loadPreview = async () => {
      setPreview({ kind: 'loading' })
      try {
        const mime = (selectedFile.mimeType || '').toLowerCase()
        if (mime.startsWith('image/')) {
          const blob = await getStewardFileBlob(conversationId, selectedFile.id)
          objectUrl = URL.createObjectURL(blob)
          if (!cancelled) setPreview({ kind: 'image', url: objectUrl })
          return
        }
        if (mime === 'application/pdf' || selectedFile.filename.toLowerCase().endsWith('.pdf')) {
          const blob = await getStewardFileBlob(conversationId, selectedFile.id)
          objectUrl = URL.createObjectURL(blob)
          if (!cancelled) setPreview({ kind: 'pdf', url: objectUrl })
          return
        }
        const result = await stewardApi.filePreview(conversationId, selectedFile.id)
        if (cancelled) return
        if (result.content) {
          setPreview({ kind: 'text', text: result.content, truncated: result.truncated })
        } else {
          setPreview({
            kind: 'unsupported',
            message: selectedFile.extractError || '此文件暂无可用的在线预览，可下载后使用本地应用打开。',
          })
        }
      } catch (err: unknown) {
        if (!cancelled) setPreview({ kind: 'error', message: errorText(err, '文件预览加载失败') })
      }
    }
    void loadPreview()
    return () => {
      cancelled = true
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [conversationId, selectedFile])

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
    setError('')
    try {
      await stewardApi.deleteFile(conversationId, id)
      await reload()
    } catch (err: unknown) {
      setError(errorText(err, '删除失败'))
    }
  }

  return (
    <div className="fixed inset-0 z-[70] flex items-center justify-center bg-black/50 p-5" onClick={onClose}>
      <div className="flex h-[76vh] min-h-[520px] w-[1040px] max-w-full flex-col overflow-hidden rounded-2xl bg-white shadow-2xl" onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between border-b px-5 py-3.5">
          <div>
            <h3 className="flex items-center gap-2 text-sm font-semibold"><FolderOpen size={16} className="text-teal-600" />会话隔离空间</h3>
            <p className="mt-0.5 text-[11px] text-gray-400">上传件和网页下载件仅在此会话可见；打包不包含浏览器登录态</p>
          </div>
          <button aria-label="关闭会话文件" onClick={onClose} className="text-gray-400 hover:text-gray-700"><X size={17} /></button>
        </div>
        {error && <div className="border-b bg-red-50 px-5 py-2 text-xs text-red-600">{error}</div>}
        <div className="flex min-h-0 flex-1">
          <aside className="flex w-[300px] shrink-0 flex-col border-r border-slate-200 bg-slate-50/70">
            <div className="grid grid-cols-2 gap-2 border-b border-slate-200 p-3">
              <input ref={inputRef} type="file" multiple className="hidden"
                accept=".doc,.docx,.ppt,.pptx,.xls,.xlsx,.pdf,.md,.txt,.csv,.json,.xml,.png,.jpg,.jpeg,.webp"
                onChange={e => void uploadFiles(e.target.files)} />
              <button onClick={() => inputRef.current?.click()} disabled={uploading}
                className="flex items-center justify-center gap-1.5 rounded-lg bg-teal-700 px-3 py-2 text-xs font-medium text-white transition hover:bg-teal-800 disabled:opacity-50">
                {uploading ? <Loader2 size={13} className="animate-spin" /> : <Upload size={13} />} 上传文件
              </button>
              <button onClick={() => void downloadStewardFile(conversationId, undefined, `data-steward-${conversationId.slice(0, 8)}.zip`)}
                className="flex items-center justify-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs text-slate-700 transition hover:border-teal-200 hover:bg-teal-50">
                <FileArchive size={13} /> 一键打包
              </button>
            </div>
            <div className="flex items-center justify-between px-3 pb-2 pt-3">
              <span className="text-xs font-semibold text-slate-600">文件树 <span className="font-normal text-slate-400">({files.length})</span></span>
              <button onClick={() => void reload()} aria-label="刷新会话文件" className="rounded-md p-1 text-slate-400 transition hover:bg-white hover:text-teal-700"><RefreshCw size={13} /></button>
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-3 pt-1">
              {loading ? <div className="py-16 text-center text-sm text-slate-400">加载中…</div> : files.length === 0 ? (
                <div className="mx-1 rounded-xl border border-dashed border-slate-300 bg-white/70 px-3 py-12 text-center text-xs text-slate-400">当前会话还没有文件</div>
              ) : (
                <div className="space-y-1">
                  {files.map(file => (
                    <button key={file.id} type="button" onClick={() => setSelectedId(file.id)}
                      className={`flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left transition ${selectedId === file.id ? 'bg-white text-teal-800 shadow-sm ring-1 ring-teal-200' : 'text-slate-600 hover:bg-white/80 hover:text-slate-900'}`}>
                      <FileText size={15} className={selectedId === file.id ? 'shrink-0 text-teal-600' : 'shrink-0 text-slate-400'} />
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-xs font-medium" title={file.filename}>{file.filename}</span>
                        <span className="mt-0.5 block text-[10px] text-slate-400">{formatBytes(file.size)}</span>
                      </span>
                    </button>
                  ))}
                </div>
              )}
            </div>
          </aside>

          <section className="flex min-w-0 flex-1 flex-col bg-white">
            {selectedFile ? (
              <>
                <div className="flex min-h-[58px] items-center gap-3 border-b border-slate-200 px-4 py-2.5">
                  <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-teal-50 text-teal-700"><FileText size={16} /></div>
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-semibold text-slate-800" title={selectedFile.filename}>{selectedFile.filename}</p>
                    <p className="mt-0.5 truncate text-[11px] text-slate-400">
                      {selectedFile.source === 'download' ? '网页下载' : selectedFile.source === 'generated' ? '管家创建' : selectedFile.source === 'edited' ? '管家编辑' : '用户上传'} · {formatBytes(selectedFile.size)}
                      {selectedFile.extractedChars > 0 ? ` · 已解析 ${selectedFile.extractedChars.toLocaleString()} 字` : ''}
                    </p>
                  </div>
                  <button title="下载文件" onClick={() => void downloadStewardFile(conversationId, selectedFile.id, selectedFile.filename)}
                    className="flex items-center gap-1.5 rounded-lg border border-slate-200 px-2.5 py-1.5 text-xs text-slate-600 transition hover:border-teal-200 hover:bg-teal-50 hover:text-teal-700">
                    <Download size={13} /> 下载
                  </button>
                  <button title="删除文件" onClick={() => void remove(selectedFile.id)}
                    className="flex items-center gap-1.5 rounded-lg border border-red-100 px-2.5 py-1.5 text-xs text-red-500 transition hover:bg-red-50">
                    <Trash2 size={13} /> 删除
                  </button>
                </div>
                <div className="min-h-0 flex-1 overflow-hidden bg-slate-50/50 p-3">
                  <div className="h-full overflow-hidden rounded-xl border border-slate-200 bg-white">
                    {preview.kind === 'loading' ? (
                      <div className="flex h-full items-center justify-center gap-2 text-sm text-slate-400"><Loader2 size={15} className="animate-spin" />正在生成预览…</div>
                    ) : preview.kind === 'image' && preview.url ? (
                      <div className="flex h-full items-center justify-center overflow-auto p-4"><img src={preview.url} alt={selectedFile.filename} className="max-h-full max-w-full object-contain" /></div>
                    ) : preview.kind === 'pdf' && preview.url ? (
                      <iframe title={`${selectedFile.filename} 预览`} src={preview.url} className="h-full w-full border-0" />
                    ) : preview.kind === 'text' ? (
                      <div className="h-full overflow-auto p-4">
                        <pre className="whitespace-pre-wrap break-words font-mono text-xs leading-6 text-slate-700">{preview.text}</pre>
                        {preview.truncated && <p className="mt-3 border-t border-slate-100 pt-3 text-xs text-amber-600">预览内容较长，当前仅展示前 60,000 个字符。</p>}
                      </div>
                    ) : preview.kind === 'unsupported' || preview.kind === 'error' ? (
                      <div className="flex h-full flex-col items-center justify-center gap-2 px-8 text-center text-sm text-slate-400">
                        <FileText size={30} className="opacity-35" />
                        <p>{preview.message}</p>
                      </div>
                    ) : null}
                  </div>
                </div>
              </>
            ) : (
              <div className="flex h-full flex-col items-center justify-center gap-2 text-sm text-slate-400">
                <FolderOpen size={32} className="opacity-30" />
                <p>从左侧文件树选择文件后查看内容</p>
              </div>
            )}
          </section>
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
  const [attaching, setAttaching] = useState(true)
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
    ws.onclose = () => { setConnected(false); setAttaching(false) }
    ws.onerror = () => { setAttaching(false); setError('实时画面连接失败') }
    ws.onmessage = event => {
      try {
        const msg = JSON.parse(event.data)
        if (msg.type === 'frame') {
          setAttaching(false)
          setFrame(`data:image/jpeg;base64,${msg.data}`)
          if (msg.url) { setCurrentUrl(msg.url); setUrl(msg.url) }
        } else if (msg.type === 'error') {
          setAttaching(false)
          setError(msg.message || '浏览器画面异常')
        }
      } catch { /* 忽略损坏帧 */ }
    }
  }, [conversationId])

  useEffect(() => {
    let cancelled = false
    const attachExisting = async () => {
      let waitingForFrame = false
      setAttaching(true)
      try {
        const session = await stewardApi.browserSession(conversationId)
        if (cancelled || !session.active) return
        if (session.url) { setCurrentUrl(session.url); setUrl(session.url) }
        waitingForFrame = true
        await connectLive()
      } catch (err: unknown) {
        if (!cancelled) setError(errorText(err, '现有浏览器画面连接失败'))
      } finally {
        if (!cancelled && !waitingForFrame) setAttaching(false)
      }
    }
    void attachExisting()
    return () => {
      cancelled = true
      wsRef.current?.close()
    }
  }, [conversationId, connectLive])

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
          <Monitor size={15} className="text-teal-600" />
          <div className={`h-2 w-2 rounded-full ${connected ? 'bg-green-500' : 'bg-gray-300'}`} />
          <input value={url} onChange={e => setUrl(e.target.value)} onKeyDown={e => e.key === 'Enter' && void open()}
            className="h-8 min-w-0 flex-1 rounded-lg border bg-white px-3 font-mono text-xs outline-none focus:border-teal-400" />
          <button onClick={() => void open()} disabled={busy}
            className="flex h-8 items-center gap-1.5 rounded-lg bg-teal-600 px-3 text-xs text-white disabled:opacity-50">
            {busy ? <Loader2 size={12} className="animate-spin" /> : <Globe size={12} />} 打开
          </button>
          <button onClick={() => { setShowNetwork(v => !v); if (!showNetwork) void loadCaptures() }}
            className={`flex h-8 items-center gap-1.5 rounded-lg border px-3 text-xs ${showNetwork ? 'border-teal-300 bg-teal-50 text-teal-700' : 'text-gray-600'}`}>
            <Activity size={12} /> 接口请求
          </button>
          <button onClick={() => setShowSources(v => !v)}
            className={`flex h-8 items-center gap-1.5 rounded-lg border px-3 text-xs ${showSources ? 'border-teal-300 bg-teal-50 text-teal-700' : 'text-gray-600'}`}>
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
                {sourceBusy && <Loader2 size={13} className="animate-spin text-teal-600" />}
              </div>
              <div className="space-y-2">
                {sources.map(source => (
                  <div key={source.id} className={`rounded-xl border bg-white p-3 ${selectedSource === source.id ? 'border-teal-300 ring-1 ring-teal-100' : 'border-gray-200'}`}>
                    <div className="flex items-start gap-2">
                      <button onClick={() => void bindSource(source.id)} className="mt-0.5 text-teal-600" aria-label={`选择${source.name}`}>
                        {selectedSource === source.id ? <CheckCircle2 size={16} /> : <span className="block h-4 w-4 rounded-full border border-gray-300" />}
                      </button>
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-1.5"><p className="truncate text-xs font-medium text-gray-800">{source.name}</p>
                          {source.sourceType === 'companion' && (source.online ? <Wifi size={12} className="text-green-500" /> : <WifiOff size={12} className="text-gray-300" />)}
                        </div>
                        <p className="mt-0.5 text-[10px] text-gray-400">{source.sourceType === 'managed' ? '平台 Docker 浏览器' : source.sourceType === 'companion' ? (source.online ? '我的电脑 · 在线' : '我的电脑 · 离线') : '管理员远程 CDP'}</p>
                      </div>
                      <button onClick={() => void testSource(source.id)} className="text-[10px] text-teal-600">测试</button>
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
                <button onClick={() => setSourceType('companion')} className={`rounded-lg border px-3 py-1.5 text-xs ${sourceType === 'companion' ? 'border-teal-300 bg-teal-50 text-teal-700' : 'bg-white text-gray-500'}`}>我的电脑</button>
                <button onClick={() => setSourceType('remote_cdp')} className={`rounded-lg border px-3 py-1.5 text-xs ${sourceType === 'remote_cdp' ? 'border-teal-300 bg-teal-50 text-teal-700' : 'bg-white text-gray-500'}`}>远程 CDP（管理员）</button>
              </div>
              <div className="mt-3 grid gap-2">
                <input value={sourceName} onChange={event => setSourceName(event.target.value)} placeholder="来源名称" className="h-8 rounded-lg border bg-white px-3 text-xs outline-none focus:border-teal-400" />
                {sourceType === 'remote_cdp' && <>
                  <input value={endpointUrl} onChange={event => setEndpointUrl(event.target.value)} placeholder="https://browser.example.com/cdp" className="h-8 rounded-lg border bg-white px-3 font-mono text-xs outline-none focus:border-teal-400" />
                  <textarea value={headerJson} onChange={event => setHeaderJson(event.target.value)} placeholder='{"Authorization":"Bearer …"}' className="h-16 resize-none rounded-lg border bg-white p-2 font-mono text-[11px] outline-none focus:border-teal-400" />
                </>}
                <button onClick={() => void createSource()} disabled={sourceBusy || (sourceType === 'remote_cdp' && !endpointUrl.trim())} className="h-8 rounded-lg bg-teal-600 px-3 text-xs font-medium text-white disabled:opacity-40">{sourceType === 'companion' ? '生成一次性配对信息' : '保存远程浏览器'}</button>
              </div>
              {sourceType === 'companion' && window.location.protocol !== 'https:' && (
                <div className="mt-3 rounded-lg border border-amber-200 bg-amber-50 p-2.5 text-[11px] leading-5 text-amber-700">当前平台不是 HTTPS。为防止配对令牌和浏览器流量泄露，生产环境助手会拒绝连接；请先为平台配置 HTTPS。</div>
              )}
              {pairing && (
                <div className="mt-3 rounded-xl border border-teal-200 bg-teal-50 p-3">
                  <p className="text-[11px] font-medium text-teal-800">配对令牌只显示这一次</p>
                  <ol className="mt-1 list-decimal space-y-1 pl-4 text-[10px] leading-5 text-teal-700"><li>安装 Node.js 22+，下载助手脚本</li><li>在脚本目录运行下面命令，Chrome/Edge 会使用独立资料目录启动</li></ol>
                  <div className="mt-2 flex gap-2"><button onClick={() => void downloadBrowserCompanion()} className="rounded-lg bg-white px-2.5 py-1.5 text-[10px] font-medium text-teal-700 shadow-sm">下载助手</button>
                    <button onClick={() => void navigator.clipboard.writeText(companionCommand)} className="flex items-center gap-1 rounded-lg bg-white px-2.5 py-1.5 text-[10px] font-medium text-teal-700 shadow-sm"><Copy size={10} />复制命令</button></div>
                  <code className="mt-2 block max-h-16 overflow-auto break-all rounded-lg bg-white/80 p-2 text-[9px] leading-4 text-teal-800">{companionCommand}</code>
                </div>
              )}
            </div>
          </div>
        )}
        <div className="flex min-h-0 flex-1 bg-[#15171b]">
          <div className="flex min-w-0 flex-1 items-center justify-center overflow-auto p-2">
            {frame ? (
              <img ref={imageRef} src={frame} draggable={false} tabIndex={0} alt="会话浏览器实时画面"
                className="max-h-full max-w-full select-none outline-none ring-teal-400 focus:ring-2"
                onMouseDown={e => { e.currentTarget.focus(); send({ type: 'mouse', action: 'down', ...point(e), button: e.button === 2 ? 'right' : 'left' }) }}
                onMouseUp={e => send({ type: 'mouse', action: 'up', ...point(e), button: e.button === 2 ? 'right' : 'left' })}
                onDoubleClick={e => send({ type: 'mouse', action: 'click', ...point(e), clickCount: 2 })}
                onWheel={e => { e.preventDefault(); send({ type: 'wheel', deltaX: e.deltaX, deltaY: e.deltaY }) }}
                onContextMenu={e => e.preventDefault()}
                onKeyDown={e => {
                  e.preventDefault()
                  if (!['Control', 'Alt', 'Shift', 'Meta'].includes(e.key)) send({ type: 'key', key: keyName(e) })
                }} />
            ) : attaching ? (
              <div className="text-center text-sm text-gray-400">
                <Loader2 size={32} className="mx-auto mb-3 animate-spin opacity-60" />
                正在连接当前会话的浏览器…
              </div>
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
                      className="mt-2 flex items-center gap-1 text-[11px] font-medium text-teal-600"><Download size={11} />保存到会话</button>}
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

function ManagedPipelinesPanel({ records, loading, expandedId, onExpand, onChanged, onBack, n8nApiUrl, onOpenWizard }: {
  records: StewardPipeline[]
  loading: boolean
  expandedId: string | null
  onExpand: (id: string | null) => void
  onChanged: () => void
  onBack: () => void
  n8nApiUrl: string
  onOpenWizard: (pipelineId: string) => void
}) {
  return (
    <aside className="flex shrink-0 flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-[0_8px_30px_rgba(15,23,42,0.04)] max-xl:max-h-[42%] max-xl:min-h-[180px] xl:min-w-0 xl:flex-1">
      <div className="flex items-center justify-between border-b border-slate-100 px-4 py-3.5">
        <div className="min-w-0">
          <h2 className="flex items-center gap-1.5 text-sm font-semibold text-gray-800 shrink-0">
            <Workflow size={15} className="text-teal-700" /> 可编排流水线
          </h2>
          <p className="mt-0.5 whitespace-nowrap text-[10px] text-slate-400">此工作区只展示处于未发布的n8n流水线</p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <button onClick={onBack}
            className="flex items-center gap-1.5 rounded-lg border border-slate-200 px-2.5 py-1.5 text-xs text-slate-600 transition hover:bg-slate-50">
            <ArrowLeft size={13} /> 返回流水线
          </button>
          <button onClick={onChanged}
            className="flex items-center gap-1.5 rounded-lg border border-slate-200 px-2.5 py-1.5 text-xs text-slate-600 transition hover:bg-slate-50">
            <RefreshCw size={13} /> 手动刷新
          </button>
        </div>
      </div>

      <div className="scrollbar-none flex-1 space-y-4 overflow-y-auto px-3 py-3">
        {loading ? (
          <div className="p-6 text-center text-sm text-gray-400">加载中…</div>
        ) : records.length === 0 ? (
          <div className="space-y-2 rounded-xl border border-dashed border-slate-300 bg-slate-50/70 p-8 text-center text-slate-400">
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

  // 记录变化后（如发布/编排更新）重置已缓存的详情
  useEffect(() => {
    const timer = window.setTimeout(() => setDetail(null), 0)
    return () => window.clearTimeout(timer)
  }, [r.pipelineStatus, r.updatedAt])

  const n8nWebUrl = n8nApiUrl ? n8nApiUrl.replace(/\/api\/.*$/, '') : ''
  const canJump = !!(n8nWebUrl && r.n8nWorkflowId)

  return (
    <div className={`overflow-hidden rounded-xl border transition ${expanded ? 'border-teal-200 bg-teal-50/20 shadow-sm' : 'border-slate-200 bg-white hover:border-slate-300 hover:shadow-sm'}`}>
      <div className="flex items-start gap-2 px-3.5 py-3">
        <button onClick={onToggle} className="flex min-w-0 flex-1 items-start gap-2 text-left" aria-expanded={expanded}>
          {expanded ? <ChevronDown size={14} className="mt-0.5 shrink-0 text-gray-400" /> : <ChevronRight size={14} className="mt-0.5 shrink-0 text-gray-400" />}
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-1.5">
            <p className="truncate text-sm font-medium text-gray-900">{r.name}</p>
            {published && <span className={`shrink-0 rounded border px-1.5 py-px text-[10px] ${meta.cls}`}>{meta.label}</span>}
            {r.active && <span className="shrink-0 rounded border border-green-200 bg-green-50 px-1.5 py-px text-[10px] text-green-600">n8n 已激活</span>}
            </div>
            {r.description && <p className="mt-0.5 truncate text-[11px] text-gray-500">{r.description}</p>}
          </div>
        </button>
        <button onClick={() => { if (r.pipelineId) onOpenWizard(r.pipelineId) }}
          className="flex shrink-0 items-center gap-1 rounded-lg border border-teal-200 px-2 py-1 text-xs text-teal-700 transition hover:bg-teal-50"
          title="打开编辑向导">
          <Pencil size={11} /> 编辑
        </button>
        {canJump && (
          <button onClick={() => window.open(`${n8nWebUrl}/workflow/${r.n8nWorkflowId}`, '_blank')}
            className="flex shrink-0 items-center gap-1 rounded-lg border border-blue-200 px-2 py-1 text-xs text-blue-600 transition-colors hover:bg-blue-50"
            title="跳转 n8n 工作流">
            <ExternalLink size={11} /> 访问
          </button>
        )}
      </div>

      {expanded && (
        <div className="space-y-2 border-t border-slate-100 px-3 py-2.5">
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
