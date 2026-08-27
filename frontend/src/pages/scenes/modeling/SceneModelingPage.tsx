/**
 * 场景建模页 — 参照数据管家页：左侧与场景助手对话（草稿态场景 / 从零新建），
 * 支持会话历史切换与消息回放；右侧白模画布实时渲染 + 版本管理条。
 * 对话应用的定义以 source=assistant 冻结新版本，版本条可回看任意历史版本。
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { AlertTriangle, ArrowLeft, History, Send, Sparkles, Square } from 'lucide-react'
import { scenesApi } from '@/api/scenes'
import { createConversation, listConversations, listMessages, streamSceneChat } from '@/api/sceneAssistant'
import type { ConversationMessage, ConversationSummary, SceneSseEvent } from '@/types/sceneAssistant'
import type { SceneDefinition, SceneSummary } from '@/types/scene'
import { modelApi } from '@/api/ontologies'
import { Button } from '@/components/ui/Button'
import { LoadingState } from '@/components/ui/LoadingState'
import { ConfirmModal } from '@/components/ui/Modal'
import { useToast } from '@/components/ui/Toast'
import SessionHistoryPopover, { type SessionHistoryItem } from '@/components/SessionHistoryPopover'
import TargetSceneSelector from './TargetSceneSelector'
import { SceneCanvas } from '@/lib/scene3d/SceneCanvas'

type TimelineItem =
  | { kind: 'user'; id: string; content: string }
  | { kind: 'assistant'; id: string; content: string }
  | { kind: 'system'; id: string; text: string; sceneId?: string; versionNo?: number }
  | { kind: 'error'; id: string; message: string; issues?: { path: string; message: string }[] }

let seq = 0
const nextId = () => 'tl-' + Date.now() + '-' + (seq++)
const NEW_SCENE = '__new__'

function messagesToTimeline(messages: ConversationMessage[]): TimelineItem[] {
  const items: TimelineItem[] = []
  for (const message of messages) {
    if (message.role === 'user') {
      items.push({ kind: 'user', id: message.id, content: message.content })
    } else if (message.status === 'error') {
      items.push({ kind: 'error', id: message.id, message: message.content })
    } else if (message.version_no != null) {
      items.push({
        kind: 'system', id: message.id,
        text: '已应用 v' + message.version_no + ' · ' + message.content,
        versionNo: message.version_no,
      })
    } else {
      items.push({ kind: 'assistant', id: message.id, content: message.content })
    }
  }
  return items
}

export default function SceneModelingPage() {
  const queryClient = useQueryClient()
  const { toast } = useToast()

  const draftsQuery = useQuery({
    queryKey: ['scenes', 'drafts-for-steward'],
    queryFn: () => scenesApi.list({ status: 'draft', page_size: 200 }),
  })
  const modelsQuery = useQuery({
    queryKey: ['models'],
    queryFn: () => modelApi.list(),
  })
  const conversationsQuery = useQuery({
    queryKey: ['scene-conversations'],
    queryFn: () => listConversations({ page_size: 50 }),
  })
  const llmModels = (modelsQuery.data ?? []).filter(
    model => model.config_type === 'llm' || !model.config_type)

  const [targetSceneId, setTargetSceneId] = useState<string>(NEW_SCENE)
  const [modelId, setModelId] = useState('')
  const [conversationId, setConversationId] = useState<string | null>(null)
  const [timeline, setTimeline] = useState<TimelineItem[]>([])
  const [input, setInput] = useState('')
  const [streaming, setStreaming] = useState(false)
  const [showHistory, setShowHistory] = useState(false)
  const abortRef = useRef<AbortController | null>(null)
  const bottomRef = useRef<HTMLDivElement>(null)

  const conversations: ConversationSummary[] = conversationsQuery.data?.items ?? []
  const historyItems: SessionHistoryItem[] = useMemo(
    () => conversations.map(conversation => ({
      id: conversation.id,
      title: conversation.title || '未命名会话',
      updatedAt: conversation.updated_at ?? conversation.created_at ?? '',
    })),
    [conversations],
  )

  // 右侧：绑定场景的版本与定义
  const boundSceneId =
    targetSceneId !== NEW_SCENE ? targetSceneId : liveBoundSceneId(timeline)
  const versionsQuery = useQuery({
    queryKey: ['scenes', boundSceneId, 'versions'],
    queryFn: () => scenesApi.versions(boundSceneId ?? ''),
    enabled: !!boundSceneId,
  })
  const versionList = versionsQuery.data?.items ?? []
  const [selectedVersionNo, setSelectedVersionNo] = useState<number | null>(null)
  useEffect(() => {
    if (versionList.length > 0) {
      setSelectedVersionNo(current =>
        current && versionList.some(v => v.version_no === current)
          ? current
          : versionList[0].version_no)
    }
  }, [versionList])
  const versionQuery = useQuery({
    queryKey: ['scenes', boundSceneId, 'version', selectedVersionNo],
    queryFn: () => scenesApi.version(boundSceneId ?? '', selectedVersionNo ?? 0),
    enabled: !!boundSceneId && selectedVersionNo != null,
  })
  const definition = (versionQuery.data?.definition ?? null) as SceneDefinition | null

  // 手动回滚：将当前查看的版本定义整体复制冻结为一个新草稿版本（历史不动、发布指针不受影响）。
  // 冻结成功即选中该新版本；后续 SSE scene_updated 仍会自动跟随最新版。
  const [rollbackOpen, setRollbackOpen] = useState(false)
  const nextVersionNo = versionList.reduce((max, v) => Math.max(max, v.version_no), 0) + 1
  const rollbackMutation = useMutation({
    mutationFn: () => scenesApi.saveDefinition(
      boundSceneId ?? '',
      definition as SceneDefinition,
      { note: '回滚自 v' + selectedVersionNo, source: 'manual' }),
    onSuccess: result => {
      void queryClient.invalidateQueries({ queryKey: ['scenes'] })
      setSelectedVersionNo(result.version.version_no)
      setRollbackOpen(false)
      toast({ tone: 'success', title: '已回滚并生成新版本 v' + result.version.version_no })
    },
    onError: () => toast({ tone: 'error', title: '回滚失败，请稍后重试' }),
  })

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [timeline])

  const draftScenes: SceneSummary[] = useMemo(
    () => draftsQuery.data?.items ?? [], [draftsQuery.data])

  function resetChat() {
    setConversationId(null)
    setTimeline([])
    setShowHistory(false)
  }

  async function loadConversation(conversationIdToLoad: string) {
    const conversation = conversations.find(item => item.id === conversationIdToLoad)
    const result = await listMessages(conversationIdToLoad)
    setConversationId(conversationIdToLoad)
    setTargetSceneId(conversation?.scene_id ?? NEW_SCENE)
    setTimeline(messagesToTimeline(result.items))
    setShowHistory(false)
  }

  async function ensureConversation(): Promise<string> {
    if (conversationId) return conversationId
    const created = await createConversation({
      scene_id: targetSceneId === NEW_SCENE ? null : targetSceneId,
      title: input.slice(0, 50),
      model_config_id: modelId || null,
    })
    setConversationId(created.id)
    return created.id
  }

  function applyEvent(event: SceneSseEvent) {
    if (event.event === 'text') {
      setTimeline(list => [...list, {
        kind: 'assistant', id: nextId(), content: event.data.content,
      }])
    } else if (event.event === 'scene_updated') {
      const data = event.data
      if (targetSceneId === NEW_SCENE) setTargetSceneId(data.scene_id)
      setTimeline(list => [...list, {
        kind: 'system', id: nextId(),
        text: '已应用 v' + data.version_no + ' · ' + data.note,
        sceneId: data.scene_id, versionNo: data.version_no,
      }])
      void queryClient.invalidateQueries({ queryKey: ['scenes'] })
      setSelectedVersionNo(data.version_no)
    } else if (event.event === 'error') {
      setTimeline(list => [...list, {
        kind: 'error', id: nextId(),
        message: event.data.message, issues: event.data.issues,
      }])
    }
  }

  async function send() {
    const content = input.trim()
    if (!content || streaming) return
    setStreaming(true)
    setInput('')
    setTimeline(list => [...list, { kind: 'user', id: nextId(), content }])
    const controller = new AbortController()
    abortRef.current = controller
    try {
      const conversationForChat = await ensureConversation()
      await streamSceneChat(
        { conversationId: conversationForChat, content, modelId: modelId || null },
        applyEvent,
        controller.signal,
      )
    } catch (error) {
      if ((error as Error).name !== 'AbortError') {
        toast({ tone: 'error', title: errorMessageText(error) })
      }
    } finally {
      abortRef.current = null
      setStreaming(false)
      // 标题/更新时间可能变化：刷新会话历史
      void queryClient.invalidateQueries({ queryKey: ['scene-conversations'] })
    }
  }

  return (
    <div className="flex h-[calc(100vh-8.5rem)] flex-col gap-2 px-6 py-4">
      {modelsQuery.isSuccess && llmModels.length === 0 && (
        <div className="flex shrink-0 items-center gap-2 rounded-lg border border-amber-200 bg-amber-50 px-4 py-2.5 text-sm text-amber-800">
          <AlertTriangle size={15} className="shrink-0" />
          <span className="flex-1">尚未配置对话模型：场景助手需要一个 LLM 才能工作。</span>
          <Link to="/models" className="flex items-center gap-1 rounded-lg border border-amber-300 bg-white px-2.5 py-1 text-xs hover:bg-amber-100">去模型配置</Link>
        </div>
      )}

      <div className="flex shrink-0 items-center justify-between">
        <div className="flex items-center gap-3">
          <Link to="/scenes" className="inline-flex items-center gap-1 text-xs text-slate-500 hover:text-teal-700">
            <ArrowLeft size={13} /> 返回三维场景列表
          </Link>
          <h1 className="text-base font-semibold text-slate-800">场景助手 · 对话式建模</h1>
        </div>
        <div className="flex items-center gap-2">
          <TargetSceneSelector
            targetSceneId={targetSceneId === NEW_SCENE ? null : targetSceneId}
            drafts={draftScenes}
            onChange={id => { resetChat(); setTargetSceneId(id ?? NEW_SCENE) }}
          />
          <select
            value={modelId}
            aria-label="选择对话模型"
            onChange={event => setModelId(event.target.value)}
            className="h-8 rounded-md border border-slate-200 bg-white px-2 text-xs text-slate-700 focus:border-teal-400 focus:outline-none focus:ring-2 focus:ring-teal-500/20"
          >
            <option value="">选择对话模型</option>
            {llmModels.map(model => (
              <option key={model.id} value={model.id}>{model.name}</option>
            ))}
          </select>
        </div>
      </div>

      <div className="flex min-h-0 flex-1 gap-4">
        {/* 左侧对话面板 */}
        <section className="flex w-[400px] shrink-0 flex-col overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm/50">
          <div className="relative flex shrink-0 items-center justify-between border-b border-slate-100 px-4 py-2">
            <span className="inline-flex items-center gap-1.5 text-sm font-semibold text-slate-800">
              <Sparkles size={14} className="text-teal-600" /> 场景助手
            </span>
            <button
              type="button"
              onClick={() => setShowHistory(true)}
              className="inline-flex h-8 items-center gap-1 rounded-lg bg-slate-100 px-2.5 text-xs font-medium text-slate-700 transition-colors hover:bg-teal-50 hover:text-teal-700"
              title="历史会话"
            >
              <History size={13} /> 历史会话
            </button>
            <SessionHistoryPopover
              open={showHistory}
              items={historyItems}
              currentId={conversationId}
              onClose={() => setShowHistory(false)}
              onCreate={resetChat}
              onSelect={id => void loadConversation(id)}
              renderItemIcon={() => <Sparkles size={16} />}
              emptyDescription="新建会话后，可随时回到之前的场景建设过程。"
            />
          </div>
          <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-4">
            {timeline.length === 0 && (
              <div className="mt-10 space-y-2 text-center text-xs leading-5 text-slate-400">
                <Sparkles size={22} className="mx-auto text-teal-500" />
                <p>描述你想构建的业务场景，例如：</p>
                <p>「建一个供应链园区：采购、仓库、生产三栋建筑，</p>
                <p>库位利用率超 95% 时告警」</p>
                <p>生成的定义会自动冻结为草稿场景的新版本。</p>
              </div>
            )}
            {timeline.map(item => {
              if (item.kind === 'user') {
                return (
                  <div key={item.id} className="flex justify-end">
                    <div className="max-w-[85%] rounded-lg bg-teal-600 px-3 py-2 text-xs leading-5 text-white">
                      {item.content}
                    </div>
                  </div>
                )
              }
              if (item.kind === 'assistant') {
                return (
                  <div key={item.id} className="max-w-[90%] rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-xs leading-5 text-slate-700">
                    {item.content}
                  </div>
                )
              }
              if (item.kind === 'system') {
                return (
                  <div key={item.id} className="rounded-md bg-teal-50 px-2.5 py-1.5 text-center text-[11px] text-teal-700">
                    {item.text}
                  </div>
                )
              }
              return (
                <div key={item.id} className="rounded-md border border-red-200 bg-red-50/70 px-2.5 py-1.5 text-[11px] text-red-700">
                  <div className="flex items-center gap-1 font-medium">
                    <AlertTriangle size={12} /> {item.message}
                  </div>
                  {item.issues && item.issues.length > 0 && (
                    <ul className="mt-1 list-inside list-disc space-y-0.5">
                      {item.issues.map(issue => (
                        <li key={issue.path}>{issue.path}: {issue.message}</li>
                      ))}
                    </ul>
                  )}
                </div>
              )
            })}
            <div ref={bottomRef} />
          </div>
          <div className="border-t border-slate-100 p-3">
            <textarea
              value={input}
              onChange={event => setInput(event.target.value)}
              onKeyDown={event => {
                if (event.key === 'Enter' && !event.shiftKey) {
                  event.preventDefault()
                  void send()
                }
              }}
              rows={3}
              maxLength={4000}
              placeholder={targetSceneId === NEW_SCENE ? '描述要从零构建的场景…' : '描述对当前场景的调整…'}
              className="w-full resize-none rounded-md border border-slate-200 bg-white px-3 py-2 text-xs text-slate-700 placeholder:text-slate-400 focus:border-teal-400 focus:outline-none focus:ring-2 focus:ring-teal-500/20"
            />
            <div className="mt-2 flex justify-end gap-2">
              {streaming ? (
                <Button variant="outline" onClick={() => abortRef.current?.abort()}>
                  <Square size={13} /> 停止
                </Button>
              ) : (
                <Button onClick={() => void send()} disabled={!input.trim() || (llmModels.length > 0 && !modelId)}>
                  <Send size={13} /> 发送
                </Button>
              )}
            </div>
          </div>
        </section>

        {/* 右侧画布 + 版本管理 */}
        <section className="flex min-w-0 flex-1 flex-col gap-3">
          <div className="relative min-h-0 flex-1 overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm/50">
            {definition
              ? <SceneCanvas definition={definition} className="absolute inset-0" />
              : (
                <div className="flex h-full items-center justify-center px-6 text-center text-xs leading-5 text-slate-400">
                  {boundSceneId
                    ? '该场景还没有版本定义：在左侧描述需求，助手将生成第一个版本'
                    : '选择或创建场景后，这里将实时渲染助手生成的白模场景'}
                </div>
              )}
          </div>
          <div className="rounded-xl border border-slate-200 bg-white p-3 shadow-sm/50">
            <div className="mb-2 flex items-center justify-between gap-2">
              <h3 className="text-xs font-semibold text-slate-800">版本管理</h3>
              {!!boundSceneId && versionList.length >= 1 && definition && (
                <Button
                  variant="outline"
                  className="h-7 px-2.5 text-xs"
                  title="将该版本定义整体保存为一个新草稿版本（当前最新版保留）"
                  onClick={() => setRollbackOpen(true)}
                >
                  回滚为当前
                </Button>
              )}
            </div>
            {!boundSceneId ? (
              <p className="text-[11px] text-slate-400">尚未绑定场景</p>
            ) : versionsQuery.isLoading ? (
              <LoadingState />
            ) : versionList.length === 0 ? (
              <p className="text-[11px] text-slate-400">暂无版本</p>
            ) : (
              <div className="flex flex-wrap gap-1.5">
                {versionList.map(version => (
                  <button
                    key={version.id}
                    type="button"
                    aria-pressed={selectedVersionNo === version.version_no}
                    onClick={() => setSelectedVersionNo(version.version_no)}
                    className={
                      'rounded-full border px-2.5 py-0.5 text-[11px] transition-colors ' +
                      (selectedVersionNo === version.version_no
                        ? 'border-teal-500 bg-teal-50 text-teal-700'
                        : 'border-slate-200 text-slate-500 hover:border-teal-300')
                    }
                    title={version.note}
                  >
                    v{version.version_no} · {version.source === 'assistant' ? '助手' : version.source === 'clone' ? '克隆' : '手动'}
                  </button>
                ))}
              </div>
            )}
          </div>
        </section>
      </div>

      <ConfirmModal
        open={rollbackOpen}
        onClose={() => { if (!rollbackMutation.isPending) setRollbackOpen(false) }}
        onConfirm={() => rollbackMutation.mutate()}
        title={'回滚到 v' + selectedVersionNo + '？'}
        description={
          '将把 v' + selectedVersionNo + ' 的定义复制冻结为新版本 v' + nextVersionNo
          + '（source=manual、备注 “回滚自 v' + selectedVersionNo + '”），'
          + '不会改动任何历史版本，也不会影响已发布指针。'}
        confirmText="确认回滚"
        loading={rollbackMutation.isPending}
      />
    </div>
  )
}

function liveBoundSceneId(timeline: TimelineItem[]): string | null {
  for (let i = timeline.length - 1; i >= 0; i--) {
    const item = timeline[i]
    if (item.kind === 'system' && item.sceneId) return item.sceneId
  }
  return null
}

function errorMessageText(error: unknown): string {
  if (error && typeof error === 'object') {
    const detail = (error as { detail?: unknown }).detail
    if (typeof detail === 'string') return detail
  }
  return (error as Error)?.message || '对话失败，请稍后重试'
}

// 引用保持：ConversationMessage 类型供历史回放使用
export type { ConversationMessage }
