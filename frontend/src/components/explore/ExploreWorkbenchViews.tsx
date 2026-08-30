import { lazy } from 'react'

/**
 * 业务澄清工作区嵌入视图的懒加载宿主。
 * 图谱编辑器内核位于 palantir-graph（非 pages 域）可直接引用；
 * 数据映射工作台位于 pages/ontologies/mapping，pages/explore 不能直接引用
 * 兄弟 pages 域（feature-boundaries 门禁），经 components 层中转
 * （先例：components/tickets → pages/tickets/shared）。
 */
export const LazyGraphWorkspace = lazy(() => import('@/palantir-graph/GraphWorkspace'))
export const LazyMappingWorkspace = lazy(() =>
  import('@/pages/ontologies/mapping/MappingConfigurationPage').then(m => ({ default: m.MappingWorkspace })),
)
