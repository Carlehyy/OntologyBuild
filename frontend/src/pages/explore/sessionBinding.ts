/* 探索会话的本体版本绑定纯逻辑：URL 参数解析与绑定会话选择。
   与 ExplorationPage.tsx 解耦（无 React 依赖），便于 node:test 单测。 */
import type { BxSession, BxDraftOntology } from '@/api/exploration'

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

/** 工作台视图锚点：/explore?view=canvas|model|mapping|docs。 */
export const EXPLORE_VIEWS = [
  { id: 'canvas', label: '业务场景' },
  { id: 'model', label: '本体模型' },
  { id: 'mapping', label: '数据映射' },
  { id: 'docs', label: '需求文档' },
] as const

export type ExploreView = (typeof EXPLORE_VIEWS)[number]['id']

/** 解析视图参数：缺省或非法值回落到业务场景视图。 */
export function parseExploreView(params: Pick<URLSearchParams, 'get'>): ExploreView {
  const raw = (params.get('view') || '').trim()
  return (EXPLORE_VIEWS.some(v => v.id === raw) ? raw : 'canvas') as ExploreView
}

/**
 * 「业务澄清」入口锚点：/explore?session=new。
 * 表示用户从其他页面（如本体管理首卡）显式要求开启一个新会话：
 * 进入页面保持空白待建态，真实会话由首条消息/首个附件经 ensureSession 懒创建，
 * 避免重复点击入口堆积空会话。与绑定参数同时出现时待建意图优先（不自动创建绑定会话）。
 */
export function parsePendingNewSession(params: Pick<URLSearchParams, 'get'>): boolean {
  return (params.get('session') || '').trim() === 'new'
}

/** 首次落点选择：普通进入自动恢复最近会话；待建新会话时保持空白等待输入。 */
export function shouldAutoSelectLatestSession(pendingNew: boolean, hasCurrentSession: boolean): boolean {
  return !pendingNew && !hasCurrentSession
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

/** 「绑定本体」选择器选项：同一本体已收敛为最新编辑中草稿。 */
export interface DraftBindingOption {
  ontologyId: string
  ontologyName: string
  domain: string
  versionId: string
  versionNumber: string
  versionLabel: string
}

/**
 * 收敛「草稿态本体」选择器选项：同一本体保留 draftCreatedAt 最新的编辑中草稿
 * （缺失时间或同刻时先到者优先），整体按最近草稿活动倒序（稳定排序，同刻保持
 * 后端返回顺序）。输入即后端 /draft-ontologies 的 items；纯函数便于 node:test 单测。
 */
export function mergeDraftBindingOptions(items: BxDraftOntology[]): DraftBindingOption[] {
  const byOntology = new Map<string, { option: DraftBindingOption; at: string }>()
  for (const item of items || []) {
    if (!item?.ontologyId || !item.versionId) continue
    const at = item.draftCreatedAt || ''
    const existing = byOntology.get(item.ontologyId)
    if (existing && existing.at >= at) continue
    byOntology.set(item.ontologyId, {
      option: {
        ontologyId: item.ontologyId,
        ontologyName: item.ontologyName,
        domain: item.domain,
        versionId: item.versionId,
        versionNumber: item.versionNumber,
        versionLabel: item.versionLabel,
      },
      at,
    })
  }
  return [...byOntology.values()]
    .sort((a, b) => {
      if (a.at !== b.at) return a.at < b.at ? 1 : -1
      return 0
    })
    .map(entry => entry.option)
}
