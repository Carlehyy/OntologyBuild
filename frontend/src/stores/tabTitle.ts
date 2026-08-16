/**
 * 顶栏多标签页标题的纯规则层（tags-view title）。
 *
 * 与 tabLogic.ts 同样保持零运行时依赖：单元测试通过 node:test +
 * --experimental-strip-types 直接执行本文件，不能引入 react/lucide 等运行时模块。
 *
 * 展示规则（与左侧导航分工）：
 * - 左侧导航已负责展示菜单层级（一级菜单 + 二级子菜单，最多两层）；
 * - 顶栏标签的可见标题只保留页面自身的一层描述（如“详情”“映射配置”），
 *   不再把菜单名拼进标签，避免与左侧导航连起来读成多层描述；
 * - 完整两级标题（“菜单名 · 页面”）保留在标签悬停提示（title 属性）中。
 */

/**
 * 页面自身的标签后缀规则：命中即作为标签可见标题；
 * 返回 null 表示该路径没有页面级描述（标签回退显示菜单名）。
 */
export function tabSuffixForPath(pathname: string): string | null {
  if (/^\/ontologies\/[^/]+\/mapping-config$/.test(pathname)) return '映射配置'
  if (/^\/ontologies\/[^/]+\/graph$/.test(pathname)) return '图谱'
  if (/^\/ontologies\/[^/]+\/(entities|logic|actions)\//.test(pathname)) return '详情'
  if (/^\/ontologies\/(?!new$)[^/]+$/.test(pathname)) return '详情'
  // 世界模型开发页路由为 /world-model/models/:modelId/develop（历史上曾按
  // /world-model/develop/ 匹配导致“开发”后缀丢失，标签只剩“推演模型”）。
  if (/^\/world-model\/models\/[^/]+\/develop/.test(pathname)) return '开发'
  if (/^\/agent\/reports(\/|$)/.test(pathname)) return '报告'
  if (pathname === '/data/pipelines/steward') return '数据管家'
  if (/^\/data\/pipelines\/script\//.test(pathname)) return '脚本'
  // 系统设置各子页按页面自身名称展示，与其它菜单组显示叶子页名的规则一致。
  if (pathname === '/settings/users' || pathname.startsWith('/settings/users/')) return '用户管理'
  if (pathname === '/settings/agents' || pathname.startsWith('/settings/agents/')) return '智能体配置'
  if (pathname === '/settings/domains' || pathname.startsWith('/settings/domains/')) return '领域设置'
  if (pathname === '/settings/monitoring' || pathname.startsWith('/settings/monitoring/')) return '运行监控'
  return null
}

/**
 * 组合标签标题：可见标题只展示一层（有页面级描述用后缀，否则用菜单名）；
 * 完整标题保留“菜单名 · 页面”两级，用于悬停提示。
 * label 为 null（未知菜单键）时两者均为 null。
 */
export function buildTabTitles(
  label: string | null,
  suffix: string | null,
): { title: string | null; fullTitle: string | null } {
  if (!label) return { title: null, fullTitle: null }
  if (!suffix) return { title: label, fullTitle: label }
  return { title: suffix, fullTitle: label + ' · ' + suffix }
}

/** 把路径解析为菜单键（叶子菜单项粒度），供标签与权限判断共用。 */
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
  if (pathname.startsWith('/world-model/calls')) return 'world_model.calls'
  if (pathname === '/world-model' || pathname.startsWith('/world-model/')) return 'world_model.models'
  if (pathname.startsWith('/ontologies')) return 'ontologies'
  if (pathname.startsWith('/agent')) return 'agent'
  if (pathname.startsWith('/overview')) return 'overview'
  if (pathname.startsWith('/super-assistant')) return 'super_assistant'
  if (pathname.startsWith('/explore')) return 'explore'
  if (pathname.startsWith('/events')) return 'events'
  if (pathname.startsWith('/models')) return 'models'
  return null
}
