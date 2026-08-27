// 悬浮 AI 助手的纯逻辑：会话挑选、SSE 流事件归约、思考链视图模型、错误文案提取。
// 本模块被单元测试在 Node --experimental-strip-types 下直接执行，
// 只允许类型级 import（运行时装载前会被擦除），禁止引入任何运行时依赖。

import type { StreamEvent, SuperMessage, ToolStep } from '../../api/superAssistant'

export interface PendingConfirmation {
  toolRunId: string
  toolName: string
  serverName: string
  arguments: Record<string, unknown>
}

/**
 * 会话初始选中：优先 URL/调用方指定的会话（须存在于列表中），否则取最新一条。
 * 超级助手页面（?conversation= 参数）与悬浮窗共用同一套挑选语义。
 */
export function pickInitialConversationId(
  conversations: ReadonlyArray<{ id: string }>,
  requestedId?: string | null,
): string | null {
  if (requestedId && conversations.some(item => item.id === requestedId)) return requestedId
  return conversations[0]?.id ?? null
}

/** 提取接口/流式错误的可读文案。apiClient 拦截器会把后端响应体（{detail}）直接作为拒绝值。 */
export function errorMessage(error: unknown, fallback = '操作失败'): string {
  const detail = (error as { detail?: unknown } | null)?.detail
  if (typeof detail === 'string' && detail) return detail
  if (detail && typeof detail === 'object') {
    const message = (detail as { message?: unknown }).message
    if (typeof message === 'string' && message) return message
  }
  const message = (error as { message?: unknown } | null)?.message
  return typeof message === 'string' && message ? message : fallback
}

/** 后端 menu_guard 的无权限拒绝（403 + detail.code = MENU_ACCESS_DENIED）。 */
export function isMenuAccessDenied(error: unknown): boolean {
  const detail = (error as { detail?: unknown } | null)?.detail
  return Boolean(
    detail
    && typeof detail === 'object'
    && (detail as { code?: unknown }).code === 'MENU_ACCESS_DENIED',
  )
}

export interface StreamReduceResult {
  message: SuperMessage
  /** tool_confirmation_required 事件给出的待确认卡片信息 */
  pendingConfirmation?: PendingConfirmation
  /** tool_result 事件给出应解除待确认卡片的 toolRunId */
  clearPendingFor?: string
  /** error 事件的可读文案（供全局提示使用） */
  errorText?: string
}

/**
 * 把单个 SSE 事件归约到乐观插入的助手消息上。
 * 语义与 SuperAssistantPage 的内联处理保持一致，修改时需两侧同步验证。
 * thinking / done 事件不改变消息本体（thinkingRound 由调用方单独维护），返回原引用。
 */
export function reduceStreamEvent(message: SuperMessage, event: StreamEvent): StreamReduceResult {
  const { event: name, data } = event
  if (name === 'text_delta') {
    return { message: { ...message, content: message.content + String(data.delta || '') } }
  }
  if (name === 'tool_start') {
    const step: ToolStep = { toolName: data.toolName, status: 'running', arguments: data.arguments }
    return { message: { ...message, steps: [...message.steps, step] } }
  }
  if (name === 'tool_confirmation_required') {
    const steps = message.steps.map((step, index) => (
      index === message.steps.length - 1 ? { ...step, status: 'awaiting_confirmation' } : step
    ))
    return {
      message: { ...message, steps },
      pendingConfirmation: {
        toolRunId: String(data.toolRunId ?? ''),
        toolName: String(data.toolName ?? ''),
        serverName: String(data.serverName ?? ''),
        arguments: (data.arguments as Record<string, unknown>) || {},
      },
    }
  }
  if (name === 'tool_result') {
    const steps = message.steps.map((step, index) => (
      index === message.steps.length - 1
        ? { ...step, status: String(data.status || step.status), preview: data.preview }
        : step
    ))
    return { message: { ...message, steps }, clearPendingFor: String(data.toolRunId ?? '') }
  }
  if (name === 'message_end') {
    return {
      message: {
        ...message,
        content: data.message?.content || message.content,
        steps: data.message?.steps || message.steps,
        token_usage: data.message?.tokenUsage || {},
        status: 'complete',
      },
    }
  }
  if (name === 'cancelled') {
    return { message: { ...message, status: 'cancelled' } }
  }
  if (name === 'error') {
    const text = typeof data.message === 'string' && data.message ? data.message : '生成失败'
    return { message: { ...message, content: text, status: 'error' }, errorText: text }
  }
  return { message }
}

/** 思考链节点的展示状态（与 @ant-design/x ThoughtChainItemType.status 对齐）。 */
export type ChainStepStatus = 'loading' | 'success' | 'error' | 'abort'

export interface ChainStepView {
  key: string
  title: string
  status: ChainStepStatus
  /** 工具结果摘要（截断后由后端给出，≤800 字符） */
  previewText?: string
  /** 工具入参的 JSON 文本，可折叠展示 */
  argumentsText?: string
}

export function mapToolStepStatus(status: string): ChainStepStatus {
  if (status === 'success') return 'success'
  if (status === 'running' || status === 'awaiting_confirmation') return 'loading'
  if (status === 'cancelled') return 'abort'
  return 'error'
}

/**
 * 悬浮球/面板的锚定与层级策略。平台自身占用右下角固定位的页面需要避让：
 * - 本体图谱编辑器（/ontologies/:id/graph）：全屏覆盖层 z-[9999] + FloatingMenu 悬浮菜单，
 *   需要同时“抬层级”（压过覆盖层）和“上移”（让开菜单按钮）→ overlay
 * - 同步任务（/data/pipelines/sync-tasks）：长表滚到底部时底部分页条位于右下角 → lifted
 * - 事件登记（/events）：移动端右下角新建 FAB（仅小屏）→ liftedMobileOnly
 * - 场景助手（/scenes/modeling）：edge-to-edge 双卡布局，右卡对话输入区贴到视口
 *   右下角，「发送」按钮正处悬浮球默认位 → aboveComposer（整体抬到输入区上方）
 * 其余页面保持 z-40：高于普通页面内容，但让抽屉/模态（z-50+）与 toast（z-500）正常覆盖，
 * 避免悬浮球遮挡它们的右下角控件。新增页面若在右下角放置固定控件导致点击被遮挡，在此登记。
 */
export type WidgetAnchor = 'default' | 'overlay' | 'lifted' | 'liftedMobileOnly' | 'aboveComposer'

export function widgetAnchor(pathname: string): WidgetAnchor {
  if (pathname === '/events' || pathname.startsWith('/events/')) return 'liftedMobileOnly'
  if (/^\/ontologies\/[^/]+\/graph$/.test(pathname)) return 'overlay'
  if (pathname === '/data/pipelines/sync-tasks' || pathname.startsWith('/data/pipelines/sync-tasks/')) return 'lifted'
  if (pathname === '/scenes/modeling') return 'aboveComposer'
  return 'default'
}

/** 悬浮球 bottom 偏移（右偏移恒为 right-5）。Tailwind 扫描需要字面量类名。 */
export const WIDGET_FAB_BOTTOM: Record<WidgetAnchor, string> = {
  default: 'bottom-5',
  overlay: 'bottom-20',
  lifted: 'bottom-20',
  liftedMobileOnly: 'bottom-20 md:bottom-5',
  // 输入栏已对齐本体助手页（单行胶囊，高 ≈67px）+ 卡片间距 4px ≈ 4.5rem
  aboveComposer: 'bottom-[4.75rem]',
}

/** 面板 bottom 偏移 = 悬浮球 bottom + 球高(3rem) + 间距(0.5rem) */
export const WIDGET_PANEL_BOTTOM: Record<WidgetAnchor, string> = {
  default: 'bottom-[4.75rem]',
  overlay: 'bottom-[8.5rem]',
  lifted: 'bottom-[8.5rem]',
  liftedMobileOnly: 'bottom-[8.5rem] md:bottom-[4.75rem]',
  aboveComposer: 'bottom-[8.25rem]',
}

/** 悬浮球/面板层级（见 widgetAnchor 注释） */
export const WIDGET_Z: Record<WidgetAnchor, string> = {
  default: 'z-40',
  overlay: 'z-[10000]',
  lifted: 'z-40',
  liftedMobileOnly: 'z-40',
  aboveComposer: 'z-40',
}

/**
 * 页面可见范围配置（系统设置 → 超级助手）使用的导航节点结构。
 * 与 config/navigation 的 PlatformNavItem 结构对齐，仅取判定所需字段；
 * 本模块保持零运行时依赖，不能 import navigation.ts（它会加载 lucide 等运行时模块），
 * 由调用方把 PLATFORM_NAV_ITEMS 传入。
 */
export interface WidgetNavNode {
  key: string
  to: string
  subItems?: readonly WidgetNavNode[]
}

/**
 * 解析路径命中的导航菜单键：取 `to` 与路径匹配（精确或 to + '/' 前缀）的最深节点，
 * 使 /data/pipelines/sync-tasks 命中 data.sync_tasks 而非 data.pipelines，
 * /ontologies/:id/graph 等详情页归属其所属的叶子菜单（ontologies）。
 * 一级目录的 to（如 /data）是重定向页，天然在最深匹配中输给子节点。
 * 未命中任何节点返回 null。
 */
export function widgetNavLeafKey(pathname: string, items: readonly WidgetNavNode[]): string | null {
  let bestKey: string | null = null
  let bestLength = -1
  const visit = (nodes: readonly WidgetNavNode[]) => {
    for (const node of nodes) {
      const matched = pathname === node.to || pathname.startsWith(node.to + '/')
      if (matched && node.to.length > bestLength) {
        bestKey = node.key
        bestLength = node.to.length
      }
      if (node.subItems) visit(node.subItems)
    }
  }
  visit(items)
  return bestKey
}

/**
 * 悬浮助手在指定路径是否可见：命中导航目录的按隐藏名单判定；
 * 未命中导航目录的路径（/inbox、/overview、公开分享页等）不在可配置范围内，
 * 保持功能上线前行为——始终可见。
 */
export function widgetVisibleOnPath(
  pathname: string,
  items: readonly WidgetNavNode[],
  hiddenMenuKeys: ReadonlySet<string>,
): boolean {
  const key = widgetNavLeafKey(pathname, items)
  if (!key) return true
  return !hiddenMenuKeys.has(key)
}

/**
 * 把一条助手消息的工具步骤映射为 ThoughtChain 视图项。
 * 流式进行中、尚无可见正文且没有正在执行/等待确认的工具时，
 * 末尾追加“正在思考”占位项（消费后端已发出但页面此前未使用的 thinking 事件轮次）。
 */
export function buildChainSteps(
  steps: readonly ToolStep[],
  opts: { streaming?: boolean; thinkingRound?: number | null; hasContent?: boolean },
): ChainStepView[] {
  const items: ChainStepView[] = steps.map((step, index) => ({
    key: `tool-${index}`,
    title: step.toolName || `工具调用 ${index + 1}`,
    status: mapToolStepStatus(step.status),
    previewText: step.preview || undefined,
    argumentsText: step.arguments && Object.keys(step.arguments).length
      ? JSON.stringify(step.arguments, null, 2)
      : undefined,
  }))
  const anyActive = steps.some(step => step.status === 'running' || step.status === 'awaiting_confirmation')
  if (opts.streaming && !anyActive && !opts.hasContent) {
    items.push({
      key: 'thinking',
      title: opts.thinkingRound ? `正在思考（第 ${opts.thinkingRound} 轮推理）` : '正在思考…',
      status: 'loading',
    })
  }
  return items
}
