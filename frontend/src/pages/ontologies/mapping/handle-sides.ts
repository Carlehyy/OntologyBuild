import type { Edge, Node } from '@xyflow/react'

export type HandleSide = 'left' | 'right'

/** measured 缺失时估算节点中心的兜底宽度，与 .dmc-node 的固定宽度一致。 */
const FALLBACK_NODE_WIDTH = 238

function nodeCenterX(node: Node): number {
  const width = node.measured?.width ?? node.width ?? FALLBACK_NODE_WIDTH
  return node.position.x + width / 2
}

/**
 * 计算每个节点字段行锚点的朝向（纯视觉派生，不改边数据）。
 *
 * 字段行的 handle id 即映射语义（列名→属性名），保存时从边反推 field_mapping，
 * 因此锚点身份不能随拖拽重选；这里只按连线对端节点的平均水平方向，
 * 把整节点圆点切到更近的一侧，让拖拽跨侧时连线始终走短边。
 * 无连线或方向互相抵消时保持默认朝向（数据集在右、对象/关系在左）。
 */
export function computeHandleSides(nodes: Node[], edges: Edge[]): Map<string, HandleSide> {
  const nodeById = new Map(nodes.map(node => [node.id, node]))
  const votes = new Map<string, number>()
  for (const edge of edges) {
    const source = nodeById.get(edge.source)
    const target = nodeById.get(edge.target)
    if (!source || !target) continue
    const dx = nodeCenterX(target) - nodeCenterX(source)
    if (dx === 0) continue
    votes.set(source.id, (votes.get(source.id) ?? 0) + dx)
    votes.set(target.id, (votes.get(target.id) ?? 0) - dx)
  }
  return new Map(nodes.map((node) => {
    const vote = votes.get(node.id)
    const fallback: HandleSide = node.type === 'dataset' ? 'right' : 'left'
    return [node.id, vote === undefined || vote === 0 ? fallback : vote > 0 ? 'right' : 'left']
  }))
}
