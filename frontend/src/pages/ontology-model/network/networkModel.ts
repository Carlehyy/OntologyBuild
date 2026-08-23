/**
 * 本体网络页的纯数据模型工具：本体配色、聚类布局、结果叠加合并。
 *
 * 全部是纯函数（无 DOM / React 依赖），供页面组件与单元测试共用。
 * 配色唯一来源是平台共享图表主题（DESIGN.md §5.1），本模块不再维护页域色板。
 */
import type {
  NetworkGraphData,
  NetworkGraphEdge,
  NetworkGraphNode,
} from '../../../api/ontologyNetwork'
import { CHART_SERIES_PALETTE } from '../../../lib/echartsTheme.ts'

/**
 * 本体簇固定调色板：直接引用平台共享十色板（原 Tableau10 私有副本已收敛），
 * 按本体在响应中的顺序取色，保证同一会话内稳定。保留导出名以稳定图例等调用方。
 */
export const ONTOLOGY_PALETTE: readonly string[] = CHART_SERIES_PALETTE

export function ontologyColorMap(sections: { id: string }[]): Map<string, string> {
  const map = new Map<string, string>()
  sections.forEach((section, index) => {
    map.set(section.id, ONTOLOGY_PALETTE[index % ONTOLOGY_PALETTE.length])
  })
  return map
}

/** graphify 同款节点尺寸策略：点状节点，直径随度数线性放大。 */
export function degreeMap(edges: NetworkGraphEdge[]): Map<string, number> {
  const degrees = new Map<string, number>()
  for (const edge of edges) {
    // 桥接边是展示层启发式，不计入度数，避免虚线装饰干扰大小语义。
    if (edge.kind === 'bridge') continue
    degrees.set(edge.source, (degrees.get(edge.source) || 0) + 1)
    degrees.set(edge.target, (degrees.get(edge.target) || 0) + 1)
  }
  return degrees
}

const TYPE_BASE_SIZE = 26
const TYPE_SIZE_RANGE = 18
const INSTANCE_BASE_SIZE = 13
const INSTANCE_SIZE_RANGE = 8

/** 节点直径（px）：对象类型显著大于实例，同 kind 内按度数归一化放大。 */
export function nodeSize(
  node: Pick<NetworkGraphNode, 'kind'>,
  degree: number,
  maxDegree: number,
): number {
  const normalized = maxDegree > 0 ? Math.min(1, degree / maxDegree) : 0
  return node.kind === 'object_type'
    ? TYPE_BASE_SIZE + TYPE_SIZE_RANGE * normalized
    : INSTANCE_BASE_SIZE + INSTANCE_SIZE_RANGE * normalized
}

/** 全画布的度数上限（用于归一化）。 */
export function maxDegreeOf(edges: NetworkGraphEdge[]): number {
  let max = 0
  for (const degree of degreeMap(edges).values()) max = Math.max(max, degree)
  return max
}

function hashAngle(left: string, right: string): number {
  let hash = 0
  const text = left + '\u0000' + right
  for (let i = 0; i < text.length; i++) hash = (hash * 31 + text.charCodeAt(i)) >>> 0
  return (hash % 3600) / 3600 * Math.PI * 2
}

/**
 * 重叠消解（确定性后处理）：力导向收敛后，把仍然贴得太近的节点对沿连线
 * 方向推开，保证任意两节点间隙 ≥ minGap。完全重合的节点用 id 哈希决定
 * 分离方向，同一输入永远得到同一结果。返回是否有位置被调整。
 */
export function separateOverlaps(
  positions: Map<string, { x: number; y: number }>,
  diameters: Map<string, number>,
  options: { iterations?: number; minGap?: number } = {},
): boolean {
  const iterations = options.iterations ?? 90
  const minGap = options.minGap ?? 8
  const ids = [...positions.keys()]
  let adjusted = false
  for (let iter = 0; iter < iterations; iter++) {
    let movedThisRound = false
    for (let i = 0; i < ids.length; i++) {
      for (let j = i + 1; j < ids.length; j++) {
        const a = positions.get(ids[i])!
        const b = positions.get(ids[j])!
        const need = (diameters.get(ids[i]) ?? 20) / 2 + (diameters.get(ids[j]) ?? 20) / 2 + minGap
        let dx = b.x - a.x
        let dy = b.y - a.y
        let dist = Math.hypot(dx, dy)
        if (dist >= need) continue
        if (dist < 1e-6) {
          const angle = hashAngle(ids[i], ids[j])
          dx = Math.cos(angle)
          dy = Math.sin(angle)
          dist = 1
        }
        const push = (need - dist) / 2
        a.x -= (dx / dist) * push
        a.y -= (dy / dist) * push
        b.x += (dx / dist) * push
        b.y += (dy / dist) * push
        movedThisRound = true
      }
    }
    if (!movedThisRound) break
    adjusted = true
  }
  return adjusted
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
