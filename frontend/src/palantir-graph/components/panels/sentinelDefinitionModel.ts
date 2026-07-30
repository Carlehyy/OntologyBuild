import type { SentinelLink } from '../../../api/sentinelApi'

export type DefinitionLoadState = 'idle' | 'loading' | 'ready' | 'error'

export interface SentinelConditionRow {
  leftAlias: string
  leftProp: string
  op: string
  rightKind: 'property' | 'value'
  rightAlias?: string
  rightProp?: string
  rightValue?: string
}

export interface SentinelDraft {
  id?: string
  displayName: string
  description?: string
  bindings: { alias: string; objectTypeId: string; filter?: string | null }[]
  // 关系会改变命中集合和动作对象，必须由用户明确选择；[] 表示全组合。
  links: SentinelLink[]
  primaryAlias: string
  condRows: SentinelConditionRow[]
  condLogic: 'and' | 'or'
  advanced: boolean
  conditionRaw: string
  actionIds: string[]
  actionParameters: Record<string, Record<string, unknown>>
  onChange: boolean
  onSchedule: boolean
  scanIntervalSeconds: number
  triggerMode: 'on_enter' | 'on_enter_leave' | 'run_on_all'
  muted: boolean
  enabled: boolean
}

export type SentinelParameterMode =
  | 'default'
  | 'property'
  | 'constant'
  | 'primary_id'
  | 'event'
  | 'template'
  | 'advanced'

export const SENTINEL_OPERATOR_LABELS: Record<string, string> = {
  '==': '等于',
  '!=': '不等于',
  '>': '大于',
  '>=': '大于等于',
  '<': '小于',
  '<=': '小于等于',
  contains: '包含',
}

export const SENTINEL_EVENT_PARAMETER_PROPERTIES = [
  ['edge', '触发边沿（enter/leave）'],
  ['matchKey', '命中键'],
  ['occurredAt', '触发时间'],
  ['sentinelId', '哨兵 ID'],
  ['sentinelName', '哨兵名称'],
] as const

const aliasOf = (index: number) => String.fromCharCode(97 + index)

/** 生成首个未占用的代号——删掉中间绑定再添加时不能撞车（后端按 alias 作键）。 */
export function nextSentinelAlias(bindings: { alias: string }[]) {
  const used = new Set(bindings.map(binding => binding.alias))
  for (let index = 0; index < 26; index += 1) {
    const alias = aliasOf(index)
    if (!used.has(alias)) return alias
  }
  return `x${bindings.length}`
}

export const createEmptySentinelConditionRow = (
  alias: string,
): SentinelConditionRow => ({
  leftAlias: alias,
  leftProp: '',
  op: '>=',
  rightKind: 'value',
  rightValue: '',
})

export const createEmptySentinelDraft = (): SentinelDraft => ({
  displayName: '',
  description: '',
  bindings: [{ alias: 'a', objectTypeId: '' }],
  links: [],
  primaryAlias: 'a',
  condRows: [],
  condLogic: 'and',
  advanced: false,
  conditionRaw: '',
  actionIds: [],
  actionParameters: {},
  onChange: true,
  onSchedule: false,
  scanIntervalSeconds: 300,
  triggerMode: 'on_enter',
  muted: false,
  enabled: true,
})
