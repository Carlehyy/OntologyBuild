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
import { useNavigate } from 'react-router-dom'
import {
  AlertTriangle, ArrowLeftRight, BadgeCheck, BellRing, Bot, FileSearch,
  FileText, History, List, Loader2, Network, PenLine, Scale, Send, Shield,
  Sparkles, User, Workflow,
} from 'lucide-react'
import { LoadingState } from '@/components/ui/LoadingState'
import { useToast } from '@/components/ui/Toast'
import SessionHistoryPopover from '@/components/SessionHistoryPopover'
import { ontologyApi, modelApi } from '@/api/ontologies'
import { useAuthStore } from '@/stores/authStore'
import {
  agentApi, streamAgentChat,
  type AgentCapabilities, type AgentStep,
} from '@/api/agent'
import { ProposalCard } from './ProposalCard'
import { SentinelProposalCard } from './SentinelProposalCard'
import { BoundaryDrawer } from './BoundaryDrawer'
import { DynamicSentinelDrawer } from './DynamicSentinelDrawer'
import { AgentChart } from './AgentChart'
import {
  AgentCallChainView,
  Md,
  ProvenanceBar,
  SplitHandle,
  StepTrace,
  collectCharts,
  downloadJson,
  safeExportFilename,
  useAssistantLayout,
  type ChatMsg,
} from './components/AgentWorkbenchPresentation'
import { OntologyNetworkView } from './components/OntologyNetworkView'
import type { GraphAssistantSignal } from './InstanceKnowledgeGraph'
import { useOntologyStore } from '../../palantir-graph/store/ontologyStore'

const InstanceKnowledgeGraph = lazy(() => import('./InstanceKnowledgeGraph'))
const DecisionSimulationView = lazy(() => import('./DecisionSimulationView'))

let _mid = 0
const nextId = () => `m-${Date.now()}-${_mid++}`

const selectArrow = "url(\"data:image/svg+xml;charset=utf-8,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='10' viewBox='0 0 24 24' fill='none' stroke='%23999' stroke-width='2'%3E%3Cpath d='m6 9 6 6 6-6'/%3E%3C/svg%3E\")"

// ---------- 页面 ----------

export default function AgentWorkbenchPage() {
  const isAdmin = useAuthStore(s => s.user?.role === 'admin')
  const navigate = useNavigate()
  const { toast } = useToast()
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
  const [workspaceView, setWorkspaceView] = useState<'ontology' | 'data' | 'decision' | 'trace'>('ontology')

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
  const [exportingConversationId, setExportingConversationId] = useState<string | null>(null)
  const [showJump, setShowJump] = useState(false)
  const [graphSignal, setGraphSignal] = useState<GraphAssistantSignal | null>(null)
  const [decisionRunId, setDecisionRunId] = useState<string | null>(null)
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
    setDecisionRunId(null)
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
    setDecisionRunId(null)
    setGraphSignal(null)
    const lastVisual = [...restoredMessages].reverse().find(message =>
      message.role === 'assistant' && (message.citations.length > 0 || message.steps.some(step => {
        const kind = (step.result as any)?.kind
        return kind === 'path' || kind === 'impact' || kind === 'decision_simulation'
      })))
    if (lastVisual) {
      const lastDecision = [...lastVisual.steps].reverse().find(step =>
        (step.result as any)?.kind === 'decision_simulation')
      if (lastDecision) {
        setDecisionRunId(String((lastDecision.result as any)?.runId || '') || null)
        setWorkspaceView('decision')
      } else {
        setWorkspaceView('data')
        setGraphSignal({ sequence: Date.now(), steps: lastVisual.steps, citations: lastVisual.citations })
      }
    }
    setShowHistory(false)
  }

  const removeConversation = async (cid: string) => {
    await agentApi.deleteConversation(oid, cid)
    if (cid === conversationId) resetChat()
    refetchConversations()
  }

  const exportConversation = async (cid: string) => {
    if (!oid || exportingConversationId) return
    setExportingConversationId(cid)
    try {
      const data = await agentApi.exportConversation(oid, cid)
      downloadJson(data, safeExportFilename(data.conversation.title, cid))
      const legacyCount = data.summary.contentCompleteness.legacyTruncatedToolResultCount
      toast({
        tone: legacyCount > 0 ? 'warning' : 'success',
        title: '会话记录已导出',
        description: legacyCount > 0
          ? `JSON 已包含全部消息；其中 ${legacyCount} 个旧工具结果在历史存储时已截断，文件内已标注。`
          : `已导出 ${data.summary.messageCount} 条消息和 ${data.summary.toolStepCount} 个工具步骤。`,
      })
    } catch (cause: any) {
      toast({
        tone: 'error',
        title: '会话导出失败',
        description: cause?.detail || cause?.message || '请稍后重试。',
      })
    } finally {
      setExportingConversationId(null)
    }
  }

  const send = useCallback(async (text?: string) => {
    const question = (text ?? input).trim()
    if (!question || busy || !oid) return
    setInput('')
    setBusy(true)
    if (/(决策推演|推演.{0,24}(方案|策略|未来|决策)|(?:方案|策略).{0,24}(比较|推演))/.test(question)) {
      setWorkspaceView('decision')
    }

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
          if (kind === 'decision_simulation') {
            setDecisionRunId(String((typedStep.result as any)?.runId || '') || null)
            setWorkspaceView('decision')
          } else if (kind === 'path' || kind === 'impact') {
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
      '推演一项未来决策，比较可选方案、关键风险和早期信号',
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
                  {workspaceView === 'trace' ? <Workflow size={16} /> : workspaceView === 'decision' ? <Scale size={16} /> : <Network size={16} />}
                </div>
                <div className="min-w-0">
                  <h3 className="truncate text-sm font-semibold text-[var(--color-text-primary)]">
                    {workspaceView === 'ontology' ? '本体拓扑图' : workspaceView === 'data' ? '数据推演图谱' : workspaceView === 'decision' ? '决策推演' : 'Agent调用链'}
                  </h3>
                  <p className={`truncate text-[11px] ${workspaceView === 'ontology' && syncStatus === 'error' ? 'text-red-500' : 'text-[var(--color-text-tertiary)]'}`}>
                    {workspaceView === 'ontology' && syncStatus === 'error'
                      ? (syncError || '网络图加载失败。')
                      : workspaceView === 'ontology'
                        ? `${selectedOntology?.name || '未选择本体'} · 只读展示对象类型与关系`
                        : workspaceView === 'data'
                          ? `${selectedOntology?.name || '未选择本体'} · 实例、路径与拟议变更联动`
                          : workspaceView === 'decision'
                            ? `${selectedOntology?.name || '未选择本体'} · 隔离快照、多视角与方案比较`
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
                    { id: 'decision', label: '决策推演', icon: Scale },
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
            {workspaceView === 'decision' ? (
              <Suspense fallback={(
                <div className="flex h-full items-center justify-center gap-2 bg-slate-50 text-xs text-slate-500">
                  <Loader2 size={14} className="animate-spin text-teal-600" />正在加载决策推演工作台…
                </div>
              )}>
                <DecisionSimulationView
                  oid={oid}
                  releaseId={releaseId}
                  conversationId={conversationId}
                  activeRunId={decisionRunId}
                  running={busy}
                />
              </Suspense>
            ) : workspaceView === 'trace' ? (
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
                    onExport={exportConversation}
                    onDelete={removeConversation}
                    exportingId={exportingConversationId}
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
