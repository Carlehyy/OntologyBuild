/**
 * 追问队列（Queued Prompts）—— 借鉴 DataFoundry 的 queued-chat-runs：
 * 回合运行中继续提交的问题进入会话级队列，回合终态后自动派发下一条。
 * 队列持久化到 sessionStorage（按会话分桶），刷新不丢；全部纯函数便于单测。
 */
export interface QueuedPrompt {
  id: string
  text: string
}

const STORAGE_PREFIX = 'ontoagent:queued-prompts:v1:'

export type StorageLike = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>

/** 会话级存储 key；无会话时使用占位 key（新会话首回合完成前排队）。 */
export function queuedPromptsKey(conversationId: string | null): string {
  return `${STORAGE_PREFIX}${conversationId ?? 'pending'}`
}

export function makeQueuedPrompt(text: string): QueuedPrompt {
  return {
    id: `q-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    text,
  }
}

function defaultStorage(): StorageLike | null {
  if (typeof window === 'undefined' || !window.sessionStorage) return null
  return window.sessionStorage
}

function isQueuedPrompt(value: unknown): value is QueuedPrompt {
  if (!value || typeof value !== 'object') return false
  const record = value as Record<string, unknown>
  return typeof record.id === 'string' && typeof record.text === 'string'
}

export function loadQueuedPrompts(
  key: string,
  storage: StorageLike | null = defaultStorage(),
): QueuedPrompt[] {
  if (!storage) return []
  try {
    const raw = storage.getItem(key)
    if (!raw) return []
    const parsed: unknown = JSON.parse(raw)
    return Array.isArray(parsed) ? parsed.filter(isQueuedPrompt) : []
  } catch {
    return []
  }
}

export function persistQueuedPrompts(
  key: string,
  prompts: QueuedPrompt[],
  storage: StorageLike | null = defaultStorage(),
): void {
  if (!storage) return
  try {
    if (prompts.length === 0) {
      storage.removeItem(key)
      return
    }
    storage.setItem(key, JSON.stringify(prompts))
  } catch {
    // sessionStorage 不可用/超限时静默降级：队列仅本页内有效
  }
}

/** 追问入队：去重（同文本仅保留一条）、限制最多 8 条，返回新队列。 */
export function enqueuePrompt(
  prompts: QueuedPrompt[],
  text: string,
): QueuedPrompt[] {
  const trimmed = text.trim()
  if (!trimmed) return prompts
  const withoutDuplicates = prompts.filter(item => item.text.trim() !== trimmed)
  const next = [...withoutDuplicates, makeQueuedPrompt(trimmed)]
  return next.slice(-8)
}

/** 会话切换时合并旧 key（'pending' 占位）的排队项到新 key，返回合并结果。 */
export function mergeQueuedPrompts(
  current: QueuedPrompt[],
  pending: QueuedPrompt[],
): QueuedPrompt[] {
  const seen = new Set(current.map(item => item.id))
  const merged = [...current, ...pending.filter(item => !seen.has(item.id))]
  return merged.slice(-8)
}
