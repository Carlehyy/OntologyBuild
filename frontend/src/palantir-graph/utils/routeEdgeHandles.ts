import type { Edge, Node } from '@xyflow/react';

type HandleSide = 'top' | 'right' | 'bottom' | 'left';

/** measured 缺失时估算节点中心的兜底尺寸，与 Canvas 只读兜底的 280×140 一致。 */
const FALLBACK_NODE_WIDTH = 280;
const FALLBACK_NODE_HEIGHT = 140;

function nodeCenter(node: Node): { x: number; y: number } {
  const width = node.measured?.width ?? node.width ?? FALLBACK_NODE_WIDTH;
  const height = node.measured?.height ?? node.height ?? FALLBACK_NODE_HEIGHT;
  return { x: node.position.x + width / 2, y: node.position.y + height / 2 };
}

/**
 * 按两节点中心连线的主导轴选边：水平占优连左右，垂直占优连上下。
 * 自环固定「右出左进」，与 MultiConnectionEdge 的自环画法保持一致。
 */
export function edgeHandleSides(source: Node, target: Node): [HandleSide, HandleSide] {
  if (source.id === target.id) return ['right', 'left'];
  const sourceCenter = nodeCenter(source);
  const targetCenter = nodeCenter(target);
  const dx = targetCenter.x - sourceCenter.x;
  const dy = targetCenter.y - sourceCenter.y;
  if (Math.abs(dx) >= Math.abs(dy)) {
    return dx >= 0 ? ['right', 'left'] : ['left', 'right'];
  }
  return dy >= 0 ? ['bottom', 'top'] : ['top', 'bottom'];
}

/**
 * 渲染期派生：按节点实时相对位置为每条边重写 sourceHandle/targetHandle，
 * 拖拽节点时连线端点自动换到更合适的侧面（与本体结构页同一观感）。
 * 只在渲染层生效，不回写 store，也不影响布局/模型持久化；
 * handle id 对应 ObjectTypeNode 渲染的四侧锚点。
 */
export function routeEdgeHandles(edges: Edge[], nodes: Node[]): Edge[] {
  const nodeById = new Map(nodes.map((node) => [node.id, node]));
  return edges.map((edge) => {
    const source = nodeById.get(edge.source);
    const target = nodeById.get(edge.target);
    if (!source || !target) return edge;
    const [sourceSide, targetSide] = edgeHandleSides(source, target);
    return {
      ...edge,
      sourceHandle: `source-${sourceSide}`,
      targetHandle: `target-${targetSide}`,
    };
  });
}
