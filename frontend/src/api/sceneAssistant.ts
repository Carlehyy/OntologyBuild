/**
 * 场景建模助手 API — /api/v2/scenes/conversations
 *
 * 会话 CRUD 走 axios（自动解包 data 信封）；chat 为 SSE 流，
 * 用 fetch+ReadableStream + parseSseBuffer 分帧（对齐 steward 模式）。
 */
import { apiClientV2 } from './client'
import { parseSseBuffer } from '../lib/sse'
import type {
  ConversationMessage, ConversationSummary, SceneSseEvent,
} from '@/types/sceneAssistant'

export interface ConversationListResp {
  items: ConversationSummary[]
  total: number
}

export function createConversation(body: {
  scene_id?: string | null
  title?: string
  model_config_id?: string | null
}) {
  return apiClientV2.post<ConversationSummary>('/scenes/conversations', body)
}

export function listConversations(params: { scene_id?: string; page?: number; page_size?: number } = {}) {
  return apiClientV2.get<ConversationListResp>('/scenes/conversations', { params })
}

export function listMessages(conversationId: string) {
  return apiClientV2.get<{ items: ConversationMessage[]; total: number }>(
    '/scenes/conversations/' + conversationId + '/messages')
}

/** 发起一轮对话并消费 SSE 流。onEvent 收到的事件均为已解析的强类型。 */
export async function streamSceneChat(
  body: { conversationId: string; content: string; modelId?: string | null },
  onEvent: (e: SceneSseEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const token = localStorage.getItem('token') || ''
  const resp = await fetch('/api/v2/scenes/conversations/' + body.conversationId + '/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + token },
    body: JSON.stringify({
      content: body.content,
      model_config_id: body.modelId || undefined,
    }),
    signal,
  })
  if (!resp.ok || !resp.body) {
    const text = await resp.text().catch(() => '')
    throw new Error('对话请求失败 (' + resp.status + ') ' + text.slice(0, 200))
  }
  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const parsed = parseSseBuffer(buffer)
    buffer = parsed.rest
    for (const frame of parsed.events) {
      onEvent(frame as unknown as SceneSseEvent)
    }
  }
}
