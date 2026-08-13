import type { DistillCluster, SuperMemory } from '@/api/superAssistant'

export const ZONE_LABELS: Record<string, string> = {
  core: '身份偏好',
  work: '当前焦点',
  episode: '会话摘要',
  general: '通用',
}

export const zoneLabel = (zone: string) => ZONE_LABELS[zone] || zone

/** 记忆面板过滤：zone 精确匹配 + content/tags 大小写不敏感子串 */
export const filterMemories = (
  memories: SuperMemory[],
  filter: { query?: string; zone?: string },
): SuperMemory[] => {
  const needle = (filter.query || '').trim().toLowerCase()
  return memories.filter(memory => {
    if (filter.zone && memory.zone !== filter.zone) return false
    if (!needle) return true
    return memory.content.toLowerCase().includes(needle)
      || memory.tags.some(tag => tag.toLowerCase().includes(needle))
  })
}

export interface CandidateAction {
  decision: string
  label: string
  primary?: boolean
}

/** 按候选类型给出审批动作（与后端 decision 枚举一一对应） */
export const candidateActions = (kind: string): CandidateAction[] => {
  if (kind === 'conflict') {
    return [
      { decision: 'new_supersedes', label: '新记忆取代旧的', primary: true },
      { decision: 'keep_old', label: '保留旧的' },
      { decision: 'skip', label: '跳过' },
    ]
  }
  return [
    { decision: 'accept', label: '接受', primary: true },
    { decision: 'reject', label: '拒绝' },
  ]
}

/** 409 记忆冲突提示文案；非冲突错误返回 null 交给通用错误文案 */
export const memoryConflictDescription = (error: any): string | null => {
  const existing = error?.existing
  if (!existing?.content) return null
  const similarity = Number(existing.similarity)
  const percent = Number.isFinite(similarity) ? `相似度 ${(similarity * 100).toFixed(0)}%` : '相似'
  return `${percent}：${String(existing.content).slice(0, 80)}`
}

/** 蒸馏簇 protected 提示：核心/常驻记忆只参与审阅、不参与自动合并；非保护簇返回 null */
export const distillProtectedHint = (cluster: Pick<DistillCluster, 'protected'>): string | null =>
  cluster.protected ? '核心/常驻，仅审不合' : null

/** 蒸馏簇成员的被保留徽标；非 survivor 返回 null */
export const distillSurvivorLabel = (
  cluster: Pick<DistillCluster, 'survivor_id'>,
  memberId: string,
): string | null => (cluster.survivor_id === memberId ? '建议保留' : null)

/** 蒸馏合并请求体：簇内全部成员 + 是否走 LLM 融合 */
export const distillMergeBody = (
  cluster: Pick<DistillCluster, 'members'>,
  useLLM: boolean,
): { member_ids: string[]; use_llm: boolean } => ({
  member_ids: cluster.members.map(member => member.id),
  use_llm: useLLM,
})
