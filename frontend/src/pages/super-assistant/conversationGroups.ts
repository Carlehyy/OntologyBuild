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

// 后端返回的 ISO 时间为 naive UTC（无时区后缀），直接 new Date 会按本地时区解析，
// 午夜前后的会话会错组一天；无显式时区时按 UTC 解析。规则与 utils/datetime.ts 一致，
// 因本模块的运行时依赖禁令在此内联。
const EXPLICIT_TIMEZONE_RE = /(Z|[+-]\d\d:?\d\d)$/

/** 本地时区的「当日 0 点」时间戳；无效日期返回 null。 */
function localDayStart(value: string): number | null {
  const date = new Date(EXPLICIT_TIMEZONE_RE.test(value) ? value : `${value}Z`)
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

/** 每组默认展示的条数；超出部分经「展开全部」查看，避免长列表挤占侧栏。 */
export const CONVERSATION_GROUP_VISIBLE_LIMIT = 10

/** 按限量截取组内可见条目；expanded 或未超限时返回全部。 */
export function capGroupItems<T>(
  items: readonly T[],
  expanded: boolean,
  limit: number = CONVERSATION_GROUP_VISIBLE_LIMIT,
): { visible: T[]; hiddenCount: number } {
  if (expanded || items.length <= limit) return { visible: [...items], hiddenCount: 0 }
  return { visible: items.slice(0, limit), hiddenCount: items.length - limit }
}
