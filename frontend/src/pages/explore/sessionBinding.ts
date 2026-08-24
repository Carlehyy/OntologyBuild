/* 探索会话的本体版本绑定纯逻辑：URL 参数解析与绑定会话选择。
   与 ExplorationPage.tsx 解耦（无 React 依赖），便于 node:test 单测。 */
import type { BxSession } from '@/api/exploration'

/** URL 绑定锚点：/explore?ontologyId=…&versionId=…（HashRouter，query 在 hash 内）。 */
export interface SessionBinding {
  ontologyId: string
  versionId: string
}

/** 解析绑定参数：两个参数都齐备才构成绑定意图，缺一按非绑定态处理。 */
export function parseSessionBinding(params: Pick<URLSearchParams, 'get'>): SessionBinding | null {
  const ontologyId = (params.get('ontologyId') || '').trim()
  const versionId = (params.get('versionId') || '').trim()
  if (!ontologyId || !versionId) return null
  return { ontologyId, versionId }
}

export function sessionBindingKey(binding: SessionBinding): string {
  return `${binding.ontologyId}:${binding.versionId}`
}

type BindableSession = Pick<BxSession, 'id' | 'ontologyId' | 'ontologyVersionId'>

export type BoundSessionResolution =
  | { action: 'select'; sessionId: string }
  | { action: 'create' }
  | { action: 'none' }

/**
 * 绑定态会话解析：当前会话已是目标绑定 → 不动；
 * 列表里已有同绑定会话 → 选中它；否则 → 创建绑定会话。
 */
export function resolveBoundSession(
  sessions: BindableSession[],
  binding: SessionBinding,
  currentSid: string,
): BoundSessionResolution {
  const matches = (session: BindableSession) =>
    session.ontologyId === binding.ontologyId
    && session.ontologyVersionId === binding.versionId
  const current = sessions.find(session => session.id === currentSid)
  if (current && matches(current)) return { action: 'none' }
  const existing = sessions.find(matches)
  if (existing) return { action: 'select', sessionId: existing.id }
  return { action: 'create' }
}
