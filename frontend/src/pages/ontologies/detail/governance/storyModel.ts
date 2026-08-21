/* 治理叙事拼装纯逻辑:把 pending log / firing / sentinel / 动作定义 / 执行日志
   组合成「起因 → 判定 → 后果」三段式故事模型,以及自治等级的执行履历。
   与组件解耦,全部可在 node:test 中验证。 */

export interface PendingLogLike {
  id: string
  actionId: string
  actionName?: string | null
  objectInstanceId?: string | null
  parameters?: Record<string, unknown> | null
  actorId?: string | null
  executedAt?: string | null
  objectTypeName?: string | null
  objectInstanceLabel?: string | null
  triggerSource?: string | null
}

/** 待审批条目(治理页统一口径,原 PendingStoryList.PendingLog 迁入)。 */
export interface PendingLog extends PendingLogLike {
  objectTypeId?: string | null
  status?: string | null
}

export interface FiringLike {
  id: string
  sentinelId: string
  sentinelName: string
  triggerSource?: string
  status: string
  matchCount: number
  matches?: Array<Record<string, string>> | null
  entered?: string[] | null
  left?: string[] | null
  actionResults?: Array<{ logId?: string | null } | null> | null
  durationMs?: number | null
  createdAt?: string | null
}

export interface ConditionRow {
  leftAlias?: string
  leftProp?: string
  op?: string
  rightKind?: string
  rightValue?: unknown
  rightAlias?: string
  rightProp?: string
}

export interface SentinelLike {
  id: string
  name?: string
  displayName?: string
  bindings?: Array<{ alias: string; objectTypeId: string }> | null
  links?: Array<{ from: string; linkTypeId: string; to: string }> | null
  condition?: string | null
  conditionRows?: ConditionRow[] | null
  conditionLogic?: string | null
  actionIds?: string[] | null
  muted?: boolean
  enabled?: boolean
  onChange?: boolean
  onSchedule?: boolean
  scanIntervalSeconds?: number
}

export interface ActionRule {
  id?: string
  type: string
  name?: string
  enabled?: boolean
  config?: Record<string, unknown> | null
  description?: string
}

export interface WorkspaceActionLike {
  id: string
  name?: string
  displayName?: string
  description?: string | null
  requiresApproval?: boolean
  rules?: ActionRule[] | null
}

/** 待审批 ↔ 触发它的 firing:经 firing.actionResults[].logId 反查(硬关联)。 */
export function findTriggerFiring(
  log: PendingLogLike,
  firings: FiringLike[],
): FiringLike | null {
  for (const firing of firings) {
    const hit = (firing.actionResults || []).some(result => result?.logId === log.id)
    if (hit) return firing
  }
  return null
}

/** firing 命中的实例 id 集(去重,保持出现顺序),entered 优先。 */
export function firingMatchedInstanceIds(firing: FiringLike): {
  entered: string[]
  others: string[]
} {
  const seen = new Set<string>()
  const all: string[] = []
  for (const match of firing.matches || []) {
    for (const id of Object.values(match || {})) {
      if (id && !seen.has(id)) {
        seen.add(id)
        all.push(id)
      }
    }
  }
  const enteredSet = new Set((firing.entered || []).filter(Boolean))
  return {
    entered: all.filter(id => enteredSet.has(id)),
    others: all.filter(id => !enteredSet.has(id)),
  }
}

/** 待审批的叙事上下文:触发 firing(硬关联)、哨兵定义(firing 优先,actionIds 绑定兜底)、动作定义。 */
export function resolvePendingContext<
  S extends SentinelLike,
  A extends WorkspaceActionLike,
>(
  log: PendingLogLike,
  firings: FiringLike[],
  sentinels: S[],
  actions: A[],
): { firing: FiringLike | null; sentinel: S | null; actionDef: A | null } {
  const firing = findTriggerFiring(log, firings)
  const sentinel = firing
    ? sentinels.find(item => item.id === firing.sentinelId) || null
    : sentinels.find(item => (item.actionIds || []).includes(log.actionId)) || null
  const actionDef = actions.find(item => item.id === log.actionId) || null
  return { firing, sentinel, actionDef }
}

/* ═══ 治理工作台(待审批 + 自治等级 + 哨兵 一表汇总) ═══ */

export interface OperationsAutonomyLike {
  actionId: string
  actionName: string
  requiresApproval: boolean
  level: AutonomyLevelKey
  sentinels: { id: string; name: string; muted: boolean; enabled: boolean }[]
  decisions: {
    approved: number
    rejected: number
    total: number
    recentCount: number
    recentApprovalRate: number | null
  }
  autoRuns: { total: number; failed: number }
  pending: number
  recommendation: 'promote' | 'demote' | 'observe' | null
  recommendationReason: string | null
  thresholds: { promoteMinDecisions: number; promoteRate: number }
}

export interface OperationsSentinelView {
  id: string
  name: string
  status: 'online' | 'muted' | 'disabled'
  recentHits: number
}

export interface OperationsRow {
  stat: OperationsAutonomyLike
  pendings: PendingLog[]
  sentinelViews: OperationsSentinelView[]
}

/** 工作台行组装:以动作为中心,并联该动作的待审批条目与绑定哨兵状态;
   有待审批的动作排在前面(先处理要裁决的),其余保持原顺序。
   哨兵「命中 N」口径:近期 firing 的 matchCount 求和(与命中实例数一致)。 */
export function buildOperationsRows(input: {
  autonomy: OperationsAutonomyLike[]
  pending: PendingLog[]
  firings: FiringLike[]
}): OperationsRow[] {
  const rows = input.autonomy.map(stat => ({
    stat,
    pendings: input.pending.filter(log => log.actionId === stat.actionId),
    sentinelViews: stat.sentinels.map(sn => ({
      id: sn.id,
      name: sn.name,
      status: (sn.muted ? 'muted' : sn.enabled === false ? 'disabled' : 'online') as OperationsSentinelView['status'],
      recentHits: input.firings
        .filter(firing => firing.sentinelId === sn.id)
        .reduce((sum, firing) => sum + (firing.matchCount || 0), 0),
    })),
  }))
  return rows.sort((a, b) => Number(b.pendings.length > 0) - Number(a.pendings.length > 0))
}

const OP_LABEL: Record<string, string> = {
  '>=': '≥', '<=': '≤', '>': '>', '<': '<', '==': '=', '=': '=', '!=': '≠', '<>': '≠',
}

/** 条件可读化:优先结构化 conditionRows,兜底原始表达式字符串。 */
export function buildConditionSentence(sentinel: SentinelLike | null | undefined): string {
  const rows = sentinel?.conditionRows || []
  if (rows.length) {
    const joiner = (sentinel?.conditionLogic || '').toLowerCase() === 'or' ? ' 或 ' : ' 且 '
    return rows.map(row => {
      const leftText = row.leftProp
        ? (row.leftAlias ? `${row.leftAlias}.${row.leftProp}` : row.leftProp)
        : ''
      const op = OP_LABEL[row.op || ''] || row.op || '?'
      const right = row.rightKind === 'value'
        ? String(row.rightValue ?? '')
        : row.rightProp
          ? `${row.rightAlias ?? ''}.${row.rightProp}`
          : String(row.rightValue ?? '')
      return `${leftText} ${op} ${right}`.trim()
    }).filter(Boolean).join(joiner)
  }
  return sentinel?.condition?.trim() || '未配置条件'
}

/** 绑定可读化:监听哪些实体、经由哪些关系。 */
export function buildBindingSentence(
  sentinel: SentinelLike | null | undefined,
  typeName: (objectTypeId: string) => string,
): string {
  const bindings = sentinel?.bindings || []
  if (!bindings.length) return '未配置监听对象'
  const parts = bindings.map(binding => typeName(binding.objectTypeId))
  const linkCount = (sentinel?.links || []).length
  return `监听 ${parts.join('、')}${linkCount ? `,经由 ${linkCount} 条关系关联` : ''}`
}

/** 消息模板预渲染:{{object.x}} 用实例属性、{{params.x}} 用动作参数填充;缺值保留占位。 */
export function renderMessageTemplate(
  template: string,
  ctx: { object?: Record<string, unknown> | null; params?: Record<string, unknown> | null },
): string {
  return template.replace(/\{\{\s*(object|params)\.([\w-]+)\s*\}\}/g, (raw, scope: string, key: string) => {
    const source = scope === 'object' ? ctx.object : ctx.params
    const value = source?.[key]
    if (value === null || value === undefined || value === '') return raw
    return typeof value === 'object' ? JSON.stringify(value) : String(value)
  })
}

export interface EffectPreviewItem {
  type: string
  sentence: string
  detail?: string
}

/** 动作效果预演:把动作定义 rules + 本次参数翻译成“批准后会发生什么”。 */
export function buildEffectPreview(input: {
  action?: WorkspaceActionLike | null
  parameters?: Record<string, unknown> | null
  targetLabel: string
  typeName?: (objectTypeId: string) => string
  objectValues?: Record<string, unknown> | null
}): EffectPreviewItem[] {
  const { action, parameters = {}, targetLabel, objectValues } = input
  if (!action) return [{ type: 'unknown', sentence: '该动作的定义未包含在当前发布快照中' }]
  const items: EffectPreviewItem[] = []
  if (action.description?.trim()) {
    items.push({ type: 'description', sentence: action.description.trim() })
  }
  const rules = (action.rules || []).filter(rule => rule.enabled !== false)
  for (const rule of rules) {
    const config = rule.config || {}
    switch (rule.type) {
      case 'update_property': {
        const prop = String(config.targetProperty || '')
        let valueText: string
        if (config.valueSource === 'parameter') {
          const paramName = String(config.value || '')
          const value = parameters?.[paramName]
          valueText = value === undefined || value === null ? `参数 ${paramName}` : JSON.stringify(value) ?? String(value)
        } else if (config.valueSource === 'constant') {
          valueText = JSON.stringify(config.value) ?? String(config.value)
        } else if (config.valueSource === 'expression') {
          valueText = '按表达式计算的结果'
        } else if (config.valueSource === 'function') {
          valueText = '函数计算的结果'
        } else {
          valueText = config.value === undefined ? '新值' : JSON.stringify(config.value) ?? ''
        }
        items.push({
          type: 'update_property',
          sentence: `把 ${targetLabel} 的「${prop}」更新为 ${valueText}`,
          detail: rule.description || rule.name || undefined,
        })
        break
      }
      case 'notification': {
        const recipient = String(config.recipient || '管理员')
        const template = typeof config.messageTemplate === 'string' ? config.messageTemplate : ''
        items.push({
          type: 'notification',
          sentence: `向 ${recipient} 发送站内通知`,
          detail: template
            ? renderMessageTemplate(template, { object: objectValues, params: parameters })
            : rule.description || rule.name || undefined,
        })
        break
      }
      case 'create_object':
        items.push({
          type: 'create_object',
          sentence: `新建「${input.typeName?.(String(config.targetObjectTypeId || '')) || '目标类型'}」实例`,
          detail: rule.description || rule.name || undefined,
        })
        break
      case 'create_link':
        items.push({ type: 'create_link', sentence: `建立「${rule.name || '关联'}」关系`, detail: rule.description || undefined })
        break
      case 'delete_link':
        items.push({ type: 'delete_link', sentence: `解除「${rule.name || '关联'}」关系`, detail: rule.description || undefined })
        break
      case 'webhook':
        items.push({ type: 'webhook', sentence: '调用外部 Webhook 通知下游系统', detail: rule.description || rule.name || undefined })
        break
      case 'validation':
        items.push({ type: 'validation', sentence: `先通过「${rule.name || '校验'}」校验`, detail: rule.description || undefined })
        break
      default:
        items.push({ type: rule.type, sentence: rule.name || `执行 ${rule.type} 规则`, detail: rule.description || undefined })
    }
  }
  if (!rules.length && !action.description?.trim()) {
    items.push({ type: 'unknown', sentence: '该动作未定义执行规则,批准后不会产生数据变更' })
  }
  return items
}

export type TimelineDotStatus = 'success' | 'failed' | 'rejected' | 'pending' | 'executing'

export interface TimelineDot {
  id: string
  status: TimelineDotStatus
  at: string | null
  durationMs?: number | null
  reason?: string | null
  error?: string | null
}

/** 自治履历:从最近执行日志中筛出某动作的时间线(新→旧),供点阵渲染。 */
export function buildAutonomyTimeline(
  logs: Array<{
    id: string
    actionId: string
    status?: string | null
    executedAt?: string | null
    durationMs?: number | null
    decisionReason?: string | null
    errorMessage?: string | null
    dryRun?: boolean | null
  }>,
  actionId: string,
  limit = 14,
): TimelineDot[] {
  const normalize = (status?: string | null): TimelineDotStatus => {
    if (status === 'success') return 'success'
    if (status === 'failed') return 'failed'
    if (status === 'rejected') return 'rejected'
    if (status === 'executing') return 'executing'
    return 'pending'
  }
  return logs
    .filter(log => log.actionId === actionId && !log.dryRun)
    .slice(0, limit)
    .map(log => ({
      id: log.id,
      status: normalize(log.status),
      at: log.executedAt ?? null,
      durationMs: log.durationMs,
      reason: log.decisionReason,
      error: log.errorMessage,
    }))
}

export interface DailySparkDatum {
  date: string
  fired: number
  firedError: number
  runSuccess: number
  runFailed: number
}

/** 近 7 日执行心电图数据(overview.daily7d → 迷你柱图)。 */
export function buildDailySpark(
  daily7d: Array<{
    date: string
    firings?: { fired?: number; error?: number } | null
    actionRuns?: { success?: number; failed?: number } | null
  }> | null | undefined,
): DailySparkDatum[] {
  return (daily7d || []).map(day => ({
    date: day.date,
    fired: day.firings?.fired ?? 0,
    firedError: day.firings?.error ?? 0,
    runSuccess: day.actionRuns?.success ?? 0,
    runFailed: day.actionRuns?.failed ?? 0,
  }))
}

export type AutonomyLevelKey = 'L0' | 'L1' | 'L2'

export interface LevelStep {
  key: AutonomyLevelKey
  reached: boolean
  current: boolean
}

/** 放权等级路径:L0 影子 → L1 人审 → L2 自动,当前级高亮。 */
export function buildLevelSteps(level: AutonomyLevelKey): LevelStep[] {
  const order: AutonomyLevelKey[] = ['L0', 'L1', 'L2']
  const currentIndex = order.indexOf(level)
  return order.map((key, index) => ({
    key,
    reached: index <= currentIndex,
    current: index === currentIndex,
  }))
}
