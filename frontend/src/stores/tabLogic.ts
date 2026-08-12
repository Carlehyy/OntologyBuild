/**
 * 顶栏多标签页（tags-view）的纯逻辑层。
 *
 * 本模块刻意保持零运行时依赖：单元测试通过 node:test + --experimental-strip-types
 * 直接执行本文件，不能引入 zustand / react-router / lucide 等运行时模块。
 */

/** 一个顶栏标签：key 为叶子 menu key（或无菜单映射时的路径），path 含 query。 */
export interface NavTab {
  key: string
  title: string
  path: string
  lastUsedAt: number
}

export interface NavTabListState {
  tabs: NavTab[]
  activeKey: string | null
  /** 标签列表所属用户名：同一浏览器切换账号时按 owner 隔离。 */
  owner: string | null
}

export const EMPTY_NAV_TAB_STATE: NavTabListState = {
  tabs: [],
  activeKey: null,
  owner: null,
}

/**
 * 记录一次页面访问：owner 不一致（换账号）先清空；同 key 标签原位更新
 * 标题/路径/最近使用时间，否则按打开顺序追加；该标签成为激活标签。
 */
export function recordVisit(
  state: NavTabListState,
  username: string,
  tab: { key: string; title: string; path: string },
  now: number,
): NavTabListState {
  const base = state.owner === username ? state : { ...EMPTY_NAV_TAB_STATE, owner: username }
  const existing = base.tabs.find(t => t.key === tab.key)
  const tabs = existing
    ? base.tabs.map(t => (t.key === tab.key
        ? { ...t, title: tab.title, path: tab.path, lastUsedAt: now }
        : t))
    : [...base.tabs, { ...tab, lastUsedAt: now }]
  return { tabs, activeKey: tab.key, owner: username }
}

export interface CloseTabResult {
  state: NavTabListState
  closedActive: boolean
  /** 关闭的是激活标签时，剩余标签中最近使用者的 path；无剩余为 null。 */
  nextPath: string | null
}

/**
 * 关闭标签：关闭非激活标签只移除；关闭激活标签回退到剩余标签中最近使用的
 * 一个；没有剩余标签时 activeKey 置空、nextPath 为 null（调用方跳默认落地页）。
 */
export function closeTab(state: NavTabListState, key: string): CloseTabResult {
  const index = state.tabs.findIndex(t => t.key === key)
  if (index === -1) return { state, closedActive: false, nextPath: null }
  const tabs = state.tabs.filter(t => t.key !== key)
  const closedActive = state.activeKey === key
  if (!closedActive) {
    return { state: { ...state, tabs }, closedActive, nextPath: null }
  }
  if (tabs.length === 0) {
    return { state: { ...state, tabs, activeKey: null }, closedActive, nextPath: null }
  }
  const next = tabs.reduce((latest, t) => (t.lastUsedAt > latest.lastUsedAt ? t : latest))
  return { state: { ...state, tabs, activeKey: next.key }, closedActive, nextPath: next.path }
}
