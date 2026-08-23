/**
 * 场景建模页 — 左侧与场景助手对话（草稿态场景 / 从零新建），
 * 右侧白模画布实时渲染 + 版本管理条。对话应用的定义以
 * source=assistant 冻结新版本，版本条可回看任意历史版本。
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { AlertTriangle, ArrowLeft, Send, Sparkles, Square } from 'lucide-react'
import { scenesApi } from '@/api/scenes'
import { createConversation, streamSceneChat } from '@/api/sceneAssistant'
import type {
  ConversationMessage, SceneSseEvent,
} from '@/types/sceneAssistant'
import type { SceneDefinition, SceneSummary } from '@/types/scene'
import { modelApi } from '@/api/ontologies'
import { Button } from '@/components/ui/Button'
import { LoadingState } from '@/components/ui/LoadingState'
import { useToast } from '@/components/ui/Toast'
import { SceneCanvas } from '@/lib/scene3d/SceneCanvas'

type TimelineItem =
  | { kind: 'user'; id: string; content: string }
  | { kind: 'assistant'; id: string; content: string }
  | { kind: 'system'; id: string; text: string; sceneId?: string; versionNo?: number }
  | { kind: 'error'; id: string; message: string; issues?: { path: string; message: string }[] }

let seq = 0
const nextId = () => 'tl-' + Date.now() + '-' + (seq++)
const NEW_SCENE = '__new__'

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
  const llmModels = (modelsQuery.data ?? []).filter(
    model => model.config_type === 'llm' || !model.config_type)

  const [targetSceneId, setTargetSceneId] = useState<string>(NEW_SCENE)
  const [modelId, setModelId] = useState('')
  const [conversationId, setConversationId] = useState<string | null>(null)
  const [timeline, setTimeline] = useState<TimelineItem[]>([])
  const [input, setInput] = useState('')
  const [streaming, setStreaming] = useState(false)
  const abortRef = useRef<AbortController | null>(null)
  const bottomRef = useRef<HTMLDivElement>(null)

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

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [timeline])

  const draftScenes: SceneSummary[] = useMemo(
    () => draftsQuery.data?.items ?? [], [draftsQuery.data])

  function resetConversation() {
    setConversationId(null)
    setTimeline([])
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
    }
  }

  return (
    <div className="flex h-[calc(100vh-8.5rem)] flex-col px-6 py-4">
      <div className="mb-3 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Link to="/scenes" className="inline-flex items-center gap-1 text-xs text-[var(--color-text-secondary)] hover:text-teal-600">
            <ArrowLeft size={13} /> 返回三维场景列表
          </Link>
          <h1 className="text-base font-semibold text-[var(--color-text-primary)]">场景助手 · 对话式建模</h1>
        </div>
        <div className="flex items-center gap-2">
          <select
            value={targetSceneId}
            aria-label="选择草稿场景"
            onChange={event => { resetConversation(); setTargetSceneId(event.target.value) }}
            className="h-8 rounded-md border border-[var(--color-border)] bg-white px-2 text-xs text-[var(--color-text-primary)] focus:border-teal-500 focus:outline-none dark:bg-slate-900"
          >
            <option value={NEW_SCENE}>从零新建</option>
            {draftScenes.map(scene => (
              <option key={scene.id} value={scene.id}>{
                scene.name}</option>
            ))}
          </select>
          <select
            value={modelId}
            aria-label="选择对话模型"
            onChange={event => setModelId(event.target.value)}
            className="h-8 rounded-md border border-[var(--color-border)] bg-white px-2 text-xs text-[var(--color-text-primary)] focus:border-teal-500 focus:outline-none dark:bg-slate-900"
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
        <section className="flex w-[400px] shrink-0 flex-col rounded-xl border border-[var(--color-border)] bg-card">
          <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-4">
            {timeline.length === 0 && (
              <div className="mt-10 space-y-2 text-center text-xs leading-5 text-[var(--color-text-tertiary)]">
                <Sparkles size={22} className="mx-auto text-teal-500" />
                <p>描述你想构建的业务场景，例如：</p>
                <p>「建一个供应链园区：采购、仓库、生产三栋建筑，
                 库位利用率超 95% 时告警」</p>
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
                  <div key={item.id} className="max-w-[90%] rounded-lg border border-[var(--color-border)] bg-background px-3 py-2 text-xs leading-5 text-[var(--color-text-primary)]">
                    {item.content}
                  </div>
                )
              }
              if (item.kind === 'system') {
                return (
                  <div key={item.id} className="rounded-md bg-[var(--color-success-bg)] px-2.5 py-1.5 text-center text-[11px] text-[var(--color-success)]">
                    {item.text}
                  </div>
                )
              }
              return (
                <div key={item.id} className="rounded-md border border-red-200 bg-red-50/70 px-2.5 py-1.5 text-[11px] text-red-700 dark:border-red-900 dark:bg-red-950/60 dark:text-red-300">
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
          <div className="border-t border-[var(--color-border)] p-3">
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
              className="w-full resize-none rounded-md border border-[var(--color-border)] bg-white px-3 py-2 text-xs text-[var(--color-text-primary)] focus:border-teal-500 focus:outline-none focus:ring-1 focus:ring-teal-500/30 dark:bg-slate-900"
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
          <div className="relative min-h-0 flex-1 overflow-hidden rounded-xl border border-[var(--color-border)] bg-card">
            {definition
              ? <SceneCanvas definition={definition} className="absolute inset-0" />
              : (
                <div className="flex h-full items-center justify-center px-6 text-center text-xs leading-5 text-[var(--color-text-tertiary)]">
                  {boundSceneId
                    ? '该场景还没有版本定义：在左侧描述需求，助手将生成第一个版本'
                    : '选择或创建场景后，这里将实时渲染助手生成的白模场景'}
                </div>
              )}
          </div>
          <div className="rounded-xl border border-[var(--color-border)] bg-card p-3">
            <h3 className="mb-2 text-xs font-semibold text-[var(--color-text-primary)]">版本管理</h3>
            {!boundSceneId ? (
              <p className="text-[11px] text-[var(--color-text-tertiary)]">尚未绑定场景</p>
            ) : versionsQuery.isLoading ? (
              <LoadingState />
            ) : versionList.length === 0 ? (
              <p className="text-[11px] text-[var(--color-text-tertiary)]">暂无版本</p>
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
                        ? 'border-teal-500 bg-teal-50 text-teal-700 dark:bg-teal-950 dark:text-teal-300'
                        : 'border-[var(--color-border)] text-[var(--color-text-secondary)] hover:border-teal-300')
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

// 引用保持：ConversationMessage 类型供后续历史加载使用
export type { ConversationMessage }
