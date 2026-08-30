import { useCallback, useEffect, useMemo, useRef, useState, type ElementRef } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Sender } from '@ant-design/x'
import { ConfigProvider, theme as antdTheme } from 'antd'
import {
  Check, History, List, Loader2, MessageSquare, Pencil,
  Send, Settings2, Square, X,
} from 'lucide-react'

import { modelApi } from '@/api/ontologies'
import {
  superAssistantApi,
  type SuperConversation,
  type SuperMcpServer,
  type SuperMessage,
  type SuperSkill,
  type ToolStep,
} from '@/api/superAssistant'
import { useToast } from '@/components/ui/Toast'
import SessionHistoryPopover from '@/components/SessionHistoryPopover'
import { pickInitialConversationId } from '@/components/assistant-widget/logic'
import { useThemeStore } from '@/stores/themeStore'
import ConfigurationPanel, { errorText } from './components/AssistantConfiguration'
import {
  ChatMessage, ConfirmationCard, ContextUsage, EmptyState,
  type PendingConfirmation,
} from './components/AssistantConversation'
import type { ModelConfig } from '@/types/ontology'


export default function SuperAssistantPage() {
  const { toast } = useToast()
  const dark = useThemeStore(state => state.theme === 'dark')
  const [searchParams] = useSearchParams()
  // 悬浮窗跳转携带的 ?conversation=：初次加载时优先选中，后续参数变化继续跟随
  const initialRequestedIdRef = useRef(searchParams.get('conversation'))
  const requestedConversationId = searchParams.get('conversation')
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
  const senderRef = useRef<ElementRef<typeof Sender>>(null)

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
        const initialId = pickInitialConversationId(conversationResult.value, initialRequestedIdRef.current)
        if (initialId) setSelectedId(initialId)
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

  // 已进入页面后，悬浮窗再次跳转携带新的 ?conversation= 时跟随切换。
  // 用 lastAppliedParamRef 记录已消费的参数值：只在参数“变化”时跟随，
  // 避免用户在页面内手动切换会话后被残留参数强制拉回。
  const lastAppliedParamRef = useRef<string | null>(null)
  useEffect(() => {
    if (!requestedConversationId || requestedConversationId === lastAppliedParamRef.current) return
    if (conversations.some(item => item.id === requestedConversationId)) {
      lastAppliedParamRef.current = requestedConversationId
      setSelectedId(requestedConversationId)
    }
  }, [requestedConversationId, conversations])

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

  const send = async (value?: string) => {
    const message = (value ?? input).trim()
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
      await superAssistantApi.streamChat(conversationId, { message, model_config_id: selectedModelId || null, agent_mode: true }, ({ event, data }) => {
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
      window.setTimeout(() => senderRef.current?.focus(), 0)
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
  // SenderProps 未显式声明原生透传属性，但库内部会转发到内部 textarea
  const senderNativeProps = { autoFocus: true, 'aria-label': '向超级助手发送消息' }
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
        <Sender
          ref={senderRef}
          {...senderNativeProps}
          value={input}
          onChange={value => setInput(value)}
          onSubmit={value => { if (canSend) void send(value) }}
          onKeyDown={event => {
            // 自行处理 Enter 提交（保留平台语义）；输入法组合期间的 Enter 不触发发送
            if (event.key !== 'Enter' || event.shiftKey || event.ctrlKey || event.altKey || event.metaKey || event.nativeEvent.isComposing) return
            event.preventDefault()
            if (canSend) void send()
            return false
          }}
          onCancel={() => void stop()}
          loading={running}
          placeholder={placeholder}
          disabled={running || models.length === 0}
          autoSize={{ minRows: 1, maxRows: 6 }}
          suffix={false}
          className="min-w-0 flex-1"
          style={{ border: 'none', boxShadow: 'none', background: 'transparent' }}
          styles={{
            // antd textarea 自带 5px 纵向内边距（行高 22px），content 上下 6px 时
            // 单行总高恰为 44px（h-11），与右侧按钮一致，保证历史浮层间距不变
            content: { paddingBlock: 6 },
          }}
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

        <ConfigProvider
          theme={{
            algorithm: dark ? antdTheme.darkAlgorithm : antdTheme.defaultAlgorithm,
            token: { colorPrimary: '#059669', colorLink: '#059669' },
          }}
        >
          <main className="relative flex min-h-0 flex-1 flex-col overflow-hidden">
            {loading ? (
              <div className="flex flex-1 items-center justify-center"><Loader2 size={22} className="animate-spin text-teal-600" /></div>
            ) : !hasMessages ? (
              <div className="flex flex-1 items-center justify-center px-4 sm:px-8">
                <div className="relative w-full max-w-3xl -translate-y-10 sm:-translate-y-14">
                  <EmptyState onPromptSelect={text => {
                    setInput(text)
                    requestAnimationFrame(() => senderRef.current?.focus())
                  }} />
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
        </ConfigProvider>
      </section>

      <ConfigurationPanel
        open={configOpen}
        onClose={() => setConfigOpen(false)}
        skills={skills}
        servers={servers}
        refreshSkills={refreshSkills}
        refreshServers={refreshServers}
        conversationId={selectedId}
      />
    </div>
  )
}
