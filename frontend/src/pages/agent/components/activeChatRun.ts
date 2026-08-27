/**
 * 后台回合登记（Active Chat Run）—— 本体助手的对话回合已与 SSE 推送解耦：
 * 用户发送消息后离开页面（SPA 内跳转、刷新、关页），回合仍在后端执行完毕并
 * 落库。这里把进行中的 run_id 登记到 sessionStorage，返回页面后凭它恢复
 * 「正在处理」的展示并轮询至终态（MYW-71）。
 *
 * sessionStorage 刷新不丢（同标签页）；关页后登记随标签页消亡，但回合结果
 * 已落库，可在历史会话中查看。全部纯函数便于单测。
 */
export interface ActiveChatRun {
  runId: string
  ontologyId: string
  /** meta 事件后回填；用于回合状态不可知时直接定位会话 */
  conversationId: string | null
  question: string
  startedAt: string
}

const STORAGE_KEY = 'ontoagent:active-chat-run:v1'

/** 恢复轮询参数：2s 一次；上限 20 分钟（回合受 max_steps × LLM 超时约束）。 */
export const RESUME_POLL_INTERVAL_MS = 2000
export const RESUME_POLL_TIMEOUT_MS = 20 * 60 * 1000

export type StorageLike = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>

function defaultStorage(): StorageLike | null {
  if (typeof window === 'undefined' || !window.sessionStorage) return null
  return window.sessionStorage
}

function isActiveChatRun(value: unknown): value is ActiveChatRun {
  if (!value || typeof value !== 'object') return false
  const record = value as Record<string, unknown>
  return typeof record.runId === 'string' && record.runId.length > 0
    && typeof record.ontologyId === 'string' && record.ontologyId.length > 0
    && (record.conversationId === null || typeof record.conversationId === 'string')
    && typeof record.question === 'string'
    && typeof record.startedAt === 'string'
}

export function loadActiveChatRun(storage: StorageLike | null = defaultStorage()): ActiveChatRun | null {
  if (!storage) return null
  try {
    const raw = storage.getItem(STORAGE_KEY)
    if (!raw) return null
    const parsed: unknown = JSON.parse(raw)
    return isActiveChatRun(parsed) ? parsed : null
  } catch {
    return null
  }
}

export function persistActiveChatRun(
  run: ActiveChatRun | null,
  storage: StorageLike | null = defaultStorage(),
): void {
  if (!storage) return
  try {
    if (!run) {
      storage.removeItem(STORAGE_KEY)
      return
    }
    storage.setItem(STORAGE_KEY, JSON.stringify(run))
  } catch {
    // sessionStorage 不可用/超限时静默降级：仅失去「返回后恢复」能力
  }
}

/** meta 事件拿到 conversationId 后回填登记；登记不存在时忽略。 */
export function patchActiveChatRunConversationId(
  conversationId: string,
  storage: StorageLike | null = defaultStorage(),
): void {
  const run = loadActiveChatRun(storage)
  if (!run || run.conversationId === conversationId) return
  persistActiveChatRun({ ...run, conversationId }, storage)
}
