import type { ElementType } from 'react'
import {
  Bot,
  Blocks,
  BookOpenCheck,
  BrainCircuit,
  ClipboardList,
  Compass,
  Cpu,
  Database,
  GitBranch,
  Globe,
  History,
  KeyRound,
  LayoutDashboard,
  Network,
  PlugZap,
  Repeat,
  Settings,
  Table2,
  Waypoints,
  Workflow,
} from 'lucide-react'
import type { User } from '@/types/auth'


export interface PlatformNavItem {
  key: string
  to: string
  icon: ElementType
  label: string
  description?: string
  adminOnly?: boolean
  hiddenFromNavigation?: boolean
  subItems?: PlatformNavItem[]
}

export const DEFAULT_NON_ADMIN_MENU_KEYS = [
  'overview',
  'super_assistant',
  'explore',
  'ontologies',
  'agent',
  'events',
  'data',
  'data.pipelines',
  'data.sync_tasks',
  'data.structured',
  'community',
  'community.skills',
  'community.plugins',
  'models',
]

export const DEFAULT_CUSTOM_MENU_KEYS = ['overview']

export const PLATFORM_NAV_ITEMS: PlatformNavItem[] = [
  { key: 'overview', to: '/overview', icon: LayoutDashboard, label: '平台概览', description: '平台运行与数据总览', hiddenFromNavigation: true },
  { key: 'super_assistant', to: '/super-assistant', icon: BrainCircuit, label: '超级助手', description: '通用智能协作入口', hiddenFromNavigation: true },
  { key: 'explore', to: '/explore', icon: Compass, label: '业务探索', description: '业务建模与需求探索' },
  { key: 'ontologies', to: '/ontologies', icon: Network, label: '本体管理', description: '本体、图谱与对象建模' },
  { key: 'agent', to: '/agent', icon: Bot, label: '本体助手', description: '本体智能体与分析报告' },
  { key: 'events', to: '/events', icon: ClipboardList, label: '事件登记', description: '业务事件采集与审计' },
  {
    key: 'data', to: '/data', icon: Database, label: '数据通道', description: '数据接入、加工与治理', subItems: [
      { key: 'data.pipelines', to: '/data/pipelines', icon: GitBranch, label: '数据流水线', description: '连接、转换与编排' },
      { key: 'data.sync_tasks', to: '/data/pipelines/sync-tasks', icon: Repeat, label: '数据任务池', description: '同步任务与运行历史' },
      { key: 'data.structured', to: '/data/structured', icon: Table2, label: '数据资产湖', description: '结构化数据资产' },
    ],
  },
  {
    key: 'api_hub', to: '/api-hub', icon: Waypoints, label: '接口代理', description: '接口接入、调用与授权', subItems: [
      { key: 'api_hub.interfaces', to: '/api-hub/interfaces', icon: PlugZap, label: '接口管理', description: '接口定义与代理配置' },
      { key: 'api_hub.history', to: '/api-hub/history', icon: History, label: '调用历史', description: '接口调用记录' },
      { key: 'api_hub.authorization', to: '/api-hub/authorization', icon: KeyRound, label: '授权配置', description: '凭据与授权策略' },
    ],
  },
  {
    key: 'community', to: '/community', icon: Blocks, label: '开放社区', description: '技能与插件能力中心', subItems: [
      { key: 'community.skills', to: '/community/skills', icon: BookOpenCheck, label: '技能社区', description: '发现与管理平台技能' },
      { key: 'community.plugins', to: '/community/plugins', icon: PlugZap, label: '插件社区', description: '管理 MCP Server 清单' },
    ],
  },
  { key: 'models', to: '/models', icon: Cpu, label: '模型配置', description: '模型提供商与运行配置' },
  {
    key: 'system_settings', to: '/settings', icon: Settings, label: '系统设置', adminOnly: true, subItems: [
      { key: 'settings.users', to: '/settings/users', icon: Network, label: '用户管理', adminOnly: true },
      { key: 'settings.agents', to: '/settings/agents', icon: Bot, label: '智能体配置', adminOnly: true },
      { key: 'settings.workflows', to: '/settings/workflows', icon: Workflow, label: '工作流配置', adminOnly: true },
      { key: 'settings.domains', to: '/settings/domains', icon: Globe, label: '领域设置', adminOnly: true },
    ],
  },
]

export function grantedMenuKeys(user: User | null): Set<string> {
  if (!user) return new Set()
  if (user.role === 'admin') {
    return new Set(PLATFORM_NAV_ITEMS.flatMap(item => [
      item.key,
      ...(item.subItems?.map(child => child.key) ?? []),
    ]))
  }
  // Persisted sessions created before RBAC did not contain this field. Preserve
  // their former menu set until the next profile refresh; an explicit [] means
  // the administrator deliberately granted no pages.
  const fallbackMenuKeys = user.role === 'custom'
    ? DEFAULT_CUSTOM_MENU_KEYS
    : DEFAULT_NON_ADMIN_MENU_KEYS
  return new Set(user.menu_permissions ?? fallbackMenuKeys)
}

export function hasMenuAccess(user: User | null, key: string): boolean {
  if (!user) return false
  if (user.role === 'admin') return true
  if (key === 'system_settings' || key.startsWith('settings.')) return false
  return grantedMenuKeys(user).has(key)
}

export function visibleNavigation(user: User | null): PlatformNavItem[] {
  if (!user) return []
  return PLATFORM_NAV_ITEMS.flatMap(item => {
    if (item.hiddenFromNavigation) return []
    if (item.adminOnly && user.role !== 'admin') return []
    const subItems = item.subItems?.filter(child => !child.hiddenFromNavigation && hasMenuAccess(user, child.key))
    if (item.subItems && !subItems?.length && !hasMenuAccess(user, item.key)) return []
    if (!item.subItems && !hasMenuAccess(user, item.key)) return []
    return [{ ...item, subItems }]
  })
}

export function menuKeyForPath(pathname: string): string | null {
  if (pathname === '/settings' || pathname.startsWith('/settings/')) return 'system_settings'
  if (pathname === '/data/pipelines/sync-tasks' || pathname.startsWith('/data/pipelines/sync-tasks/')) return 'data.sync_tasks'
  if (pathname === '/data/structured' || pathname.startsWith('/data/structured/')) return 'data.structured'
  if (pathname === '/data' || pathname === '/data/') return 'data'
  if (pathname.startsWith('/data/pipelines')) return 'data.pipelines'
  if (pathname === '/api-hub' || pathname === '/api-hub/') return 'api_hub'
  if (pathname.startsWith('/api-hub/history')) return 'api_hub.history'
  if (pathname.startsWith('/api-hub/authorization') || pathname.startsWith('/api-hub/operations')) return 'api_hub.authorization'
  if (pathname.startsWith('/api-hub/interfaces')) return 'api_hub.interfaces'
  if (pathname === '/community' || pathname === '/community/') return 'community'
  if (pathname.startsWith('/community/skills')) return 'community.skills'
  if (pathname.startsWith('/community/plugins')) return 'community.plugins'
  if (pathname.startsWith('/ontologies')) return 'ontologies'
  if (pathname.startsWith('/agent')) return 'agent'
  if (pathname.startsWith('/overview')) return 'overview'
  if (pathname.startsWith('/super-assistant')) return 'super_assistant'
  if (pathname.startsWith('/explore')) return 'explore'
  if (pathname.startsWith('/events')) return 'events'
  if (pathname.startsWith('/models')) return 'models'
  return null
}

export function canAccessPath(user: User | null, pathname: string): boolean {
  const key = menuKeyForPath(pathname)
  return key === null || hasMenuAccess(user, key)
}

export function firstAccessiblePath(user: User | null): string {
  if (!user) return '/no-access'
  const first = PLATFORM_NAV_ITEMS.find(item => {
    if (item.adminOnly && user.role !== 'admin') return false
    return hasMenuAccess(user, item.key)
      || (item.subItems?.some(child => hasMenuAccess(user, child.key)) ?? false)
  })
  if (!first) return '/no-access'
  const firstSubItem = first.subItems?.find(child => hasMenuAccess(user, child.key))
  return firstSubItem?.to ?? first.to
}

export const CONFIGURABLE_NAV_ITEMS = PLATFORM_NAV_ITEMS.filter(item => !item.adminOnly)
