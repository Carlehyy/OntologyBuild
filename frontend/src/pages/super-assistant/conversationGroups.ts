// 历史会话分组时间线的纯逻辑：按 updated_at 的本地日期分桶。
// 本模块被单元测试在 Node --experimental-strip-types 下直接执行，
// 只允许类型级 import（运行时装载前会被擦除），禁止引入任何运行时依赖。

export interface ConversationGroupItem {
  id: string
  status: string
  updated_at: string
}

export interface ConversationGroups<T extends ConversationGroupItem> {
  today: T[]
  yesterday: T[]
  earlier: T[]
  archived: T[]
}

/** 本地时区的「当日 0 点」时间戳；无效日期返回 null。 */
function localDayStart(value: string): number | null {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return null
  return new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime()
}

/**
 * 把会话列表分进 今日/昨日/更早/归档 四组。
 * - 归档（status === 'archived'）单独成组，不再按日期分组；
 * - 其余按 updated_at 的本地日期分桶，无法解析的日期归入「更早」；
 * - 组内保持调用方传入顺序（后端按 updated_at 倒序返回）。
 */
export function groupConversations<T extends ConversationGroupItem>(
  items: readonly T[],
  now: Date = new Date(),
): ConversationGroups<T> {
  const groups: ConversationGroups<T> = { today: [], yesterday: [], earlier: [], archived: [] }
  const todayStart = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime()
  const yesterdayStart = todayStart - 24 * 60 * 60 * 1000
  for (const item of items) {
    if (item.status === 'archived') {
      groups.archived.push(item)
      continue
    }
    const dayStart = localDayStart(item.updated_at)
    if (dayStart === null || dayStart < yesterdayStart) groups.earlier.push(item)
    else if (dayStart < todayStart) groups.yesterday.push(item)
    else groups.today.push(item)
  }
  return groups
}

/** 四组的展示顺序与标题（工作台侧栏与单测共用）。 */
export const CONVERSATION_GROUP_SECTIONS = [
  { key: 'today', label: '今日对话' },
  { key: 'yesterday', label: '昨日对话' },
  { key: 'earlier', label: '历史会话' },
] as const
