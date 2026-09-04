/**
 * 记忆宫殿图谱的客户端过滤与邻域推导（纯函数，单测见 test/unit/palaceGraphFilter.test.ts）。
 *
 * 过滤口径：按 name / aliases / type 关键词命中节点（大小写不敏感），
 * 边仅在两端节点都保留时保留；一跳邻居从已加载边推导，供节点详情面板展示。
 */
import type { PalaceGraph, PalaceGraphEdge, PalaceGraphNode } from '../../../api/superAssistant'

export interface PalaceGraphView {
  nodes: PalaceGraphNode[]
  edges: PalaceGraphEdge[]
}

const matchesKeyword = (node: PalaceGraphNode, keyword: string): boolean => {
  if (node.name.toLowerCase().includes(keyword)) return true
  if (node.type.toLowerCase().includes(keyword)) return true
  return (node.aliases || []).some(alias => alias.toLowerCase().includes(keyword))
}

/** 关键词过滤：空串/纯空白返回全量；无命中返回空视图（由调用方渲染空态）。 */
export function filterPalaceGraph(graph: PalaceGraph, keyword: string): PalaceGraphView {
  const trimmed = keyword.trim().toLowerCase()
  if (!trimmed) return { nodes: graph.nodes, edges: graph.edges }
  const nodes = graph.nodes.filter(node => matchesKeyword(node, trimmed))
  const idSet = new Set(nodes.map(node => node.id))
  const edges = graph.edges.filter(edge => idSet.has(edge.source) && idSet.has(edge.target))
  return { nodes, edges }
}

export interface PalaceNeighbor {
  nodeId: string
  name: string
  relation: string
}

/** 一跳邻居：从已加载边推导（双向），任一端点不在节点集内的边直接忽略。 */
export function palaceOneHopNeighbors(graph: PalaceGraph, nodeId: string): PalaceNeighbor[] {
  const nameById = new Map(graph.nodes.map(node => [node.id, node.name]))
  const neighbors: PalaceNeighbor[] = []
  for (const edge of graph.edges) {
    if (!nameById.has(edge.source) || !nameById.has(edge.target)) continue
    if (edge.source === nodeId) {
      neighbors.push({ nodeId: edge.target, name: nameById.get(edge.target) as string, relation: edge.name })
    } else if (edge.target === nodeId) {
      neighbors.push({ nodeId: edge.source, name: nameById.get(edge.source) as string, relation: edge.name })
    }
  }
  return neighbors
}
