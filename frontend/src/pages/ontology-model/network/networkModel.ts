/**
 * 本体网络页的纯数据模型工具：本体配色、聚类布局、结果叠加合并。
 *
 * 全部是纯函数（无 DOM / React 依赖），供页面组件与单元测试共用。
 */
import type {
  NetworkGraphData,
  NetworkGraphEdge,
  NetworkGraphNode,
} from '@/api/ontologyNetwork'

/** 本体簇固定调色板：按本体在响应中的顺序取色，保证同一会话内稳定。 */
export const ONTOLOGY_PALETTE = [
  '#0f766e', '#0369a1', '#7c3aed', '#b45309',
  '#be123c', '#15803d', '#6d28d9', '#0e7490',
  '#a16207', '#9f1239', '#1d4ed8', '#047857',
] as const

export function ontologyColorMap(sections: { id: string }[]): Map<string, string> {
  const map = new Map<string, string>()
  sections.forEach((section, index) => {
    map.set(section.id, ONTOLOGY_PALETTE[index % ONTOLOGY_PALETTE.length])
  })
  return map
}

/**
 * 确定性聚类布局：每个本体一个簇，簇内对象类型摆网格、实例围绕类型成环。
 * 同一输入永远得到同一布局（无随机力导向），便于测试与回归对比。
 */
export function clusterPositions(nodes: NetworkGraphNode[]): Map<string, { x: number; y: number }> {
  const positions = new Map<string, { x: number; y: number }>()

  const ontologyIds = [...new Set(nodes.map(node => node.ontologyId))]
  const columns = Math.max(1, Math.ceil(Math.sqrt(ontologyIds.length)))
  const clusterGapX = 1500
  const clusterGapY = 1100

  ontologyIds.forEach((ontologyId, clusterIndex) => {
    const column = clusterIndex % columns
    const row = Math.floor(clusterIndex / columns)
    const originX = 300 + column * clusterGapX
    const originY = 240 + row * clusterGapY

    const typeNodes = nodes.filter(
      node => node.ontologyId === ontologyId && node.kind === 'object_type')
    const instanceNodes = nodes.filter(
      node => node.ontologyId === ontologyId && node.kind === 'instance')
    const propertyNodes = nodes.filter(
      node => node.ontologyId === ontologyId && node.kind === 'property')

    // 簇标题占位：类型网格从簇原点铺开
    const typeColumns = Math.max(1, Math.ceil(Math.sqrt(typeNodes.length)))
    const gapX = 430
    const gapY = 330
    const typeCenters = new Map<string, { x: number; y: number }>()
    typeNodes.forEach((node, index) => {
      const center = {
        x: originX + (index % typeColumns) * gapX,
        y: originY + Math.floor(index / typeColumns) * gapY,
      }
      positions.set(node.id, center)
      if (node.objectTypeId) typeCenters.set(node.objectTypeId, center)
    })

    // 实例围绕所属类型节点成环；找不到类型时退回簇原点附近
    const instancesByType = new Map<string, NetworkGraphNode[]>()
    instanceNodes.forEach(node => {
      const key = node.objectTypeId || ''
      instancesByType.set(key, [...(instancesByType.get(key) || []), node])
    })
    instancesByType.forEach((items, typeId) => {
      const anchor = typeCenters.get(typeId) || { x: originX, y: originY }
      items.forEach((node, index) => {
        const ring = Math.floor(index / 10)
        const itemInRing = index % 10
        const countInRing = Math.min(10, items.length - ring * 10)
        const angle = -Math.PI / 2 + (itemInRing / Math.max(1, countInRing)) * Math.PI * 2
        const radiusX = 145 + ring * 75
        const radiusY = 105 + ring * 55
        positions.set(node.id, {
          x: anchor.x + Math.cos(angle) * radiusX,
          y: anchor.y + Math.sin(angle) * radiusY,
        })
      })
    })

    // 属性节点围绕所属实例（全局视图默认不展开属性层，保留以备 L3）
    propertyNodes.forEach((node, index) => {
      const anchor = node.objectTypeId
        ? typeCenters.get(node.objectTypeId) || { x: originX, y: originY }
        : { x: originX, y: originY }
      const angle = -Math.PI / 2 + (index / Math.max(1, propertyNodes.length)) * Math.PI * 2
      positions.set(node.id, {
        x: anchor.x + Math.cos(angle) * 160,
        y: anchor.y + Math.sin(angle) * 120,
      })
    })
  })

  return positions
}

const overlayNodeId = (id: string) => 'instance:' + id

/**
 * 把路径/影响分析返回的实例与关系边叠加到基础图上（按节点/边 id 去重覆盖）。
 * 分析结果节点可能不在基础图加载窗口内，缺本体归属时用 fallbackOntologyId 补齐。
 */
export function mergeOverlay(
  base: NetworkGraphData,
  overlayNodes: NetworkGraphNode[],
  overlayEdges: NetworkGraphEdge[],
  fallbackOntologyId = '',
): { nodes: NetworkGraphNode[]; edges: NetworkGraphEdge[] } {
  const nodeMap = new Map(base.nodes.map(node => [node.id, node]))
  const edgeMap = new Map(base.edges.map(edge => [edge.id, edge]))
  overlayNodes.forEach(node => {
    const merged = node.ontologyId ? node : { ...node, ontologyId: fallbackOntologyId }
    nodeMap.set(merged.id, merged)
  })
  overlayEdges.forEach(edge => edgeMap.set(edge.id, edge))
  return { nodes: [...nodeMap.values()], edges: [...edgeMap.values()] }
}

/** 路径/影响结果的实体 id → 图节点 id（后端返回裸实例 id）。 */
export const toGraphNodeId = overlayNodeId

export interface NetworkLegendItem {
  id: string
  label: string
  color: string
  published?: boolean
}

/** 右侧面板/图例共用的本体条目（含发布徽标所需信息）。 */
export function legendItems(
  sections: NetworkGraphData['ontologies'],
): NetworkLegendItem[] {
  const colors = ontologyColorMap(sections)
  return sections.map(section => ({
    id: section.id,
    label: section.name,
    color: colors.get(section.id) || ONTOLOGY_PALETTE[0],
    published: section.published,
  }))
}
