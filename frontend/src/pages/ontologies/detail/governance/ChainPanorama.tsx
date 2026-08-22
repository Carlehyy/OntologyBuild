/* 治理链路全景画布(@xyflow/react):
   七段链路(采集→资产湖→映射→实例→哨兵→待审批→动作)真实节点 + 流动粒子连线;
   点选节点仅高亮整条上下游(其余压暗,点空白复位),不做页面跳转;
   待审批节点持续脉冲(当前瓶颈),点击直接打开审批详情弹窗;
   垂直间距宽松,画布高度按节点数自适应(内容多少就展示多少);
   底部「链路导读」一键定位典型链路。 */
import { useCallback, useMemo, useState } from 'react'
import {
  Background,
  BaseEdge,
  Controls,
  getBezierPath,
  Handle,
  Position,
  ReactFlow,
  type Edge,
  type EdgeProps,
  type Node,
  type NodeProps,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import {
  Boxes, Database, GitBranch, HandMetal, Loader2, Rocket,
  ShieldAlert, Waypoints, Workflow,
} from 'lucide-react'
import {
  CHAIN_COLUMNS,
  collectChainNeighborhood,
  type ChainEdge,
  type ChainGuide,
  type ChainNode,
  type ChainNodeKind,
} from './chainModel'

const COLUMN_X = 300
const NODE_W = 224
const NODE_H = 72
const NODE_GAP = 40
const HEADER_Y = 0
const FIRST_NODE_Y = 52
const MIN_CANVAS_H = 340
const MAX_CANVAS_H = 660

interface ChainFlowData extends Record<string, unknown> {
  chainNode: ChainNode
  dimmed: boolean
  highlighted: boolean
}

interface ChainEdgeData extends Record<string, unknown> {
  stroke: string
  dash?: string
  dimmed: boolean
  highlighted: boolean
}

const KIND_ICON: Record<ChainNodeKind, typeof Database> = {
  pipeline: Workflow,
  dataset: Database,
  mapping: GitBranch,
  instanceHub: Boxes,
  instance: Boxes,
  sentinel: ShieldAlert,
  pending: HandMetal,
  action: Rocket,
}

const BADGE_CLS: Record<string, string> = {
  ok: 'border-emerald-200 bg-emerald-50 text-emerald-600',
  warn: 'border-amber-200 bg-amber-50 text-amber-600',
  danger: 'border-rose-200 bg-rose-50 text-rose-600',
  info: 'border-sky-200 bg-sky-50 text-sky-600',
}

function ChainNodeCard({ data }: NodeProps<Node<ChainFlowData>>) {
  const { chainNode, dimmed, highlighted } = data
  const Icon = KIND_ICON[chainNode.kind]
  return (
    <div
      className={`chain-node chain-tone-${chainNode.kind} ${
        dimmed ? 'chain-node-dim' : ''
      } ${highlighted ? 'chain-node-hot' : ''} ${chainNode.pulse ? 'chain-node-pulse' : ''}`}
      style={{ width: NODE_W, minHeight: NODE_H }}
      data-kind={chainNode.kind}
    >
      <Handle type="target" position={Position.Left} className="!h-2 !w-2 !border-0 !bg-slate-300" />
      <div className="flex items-start gap-2.5">
        <span className="chain-node-icon">
          <Icon size={14} />
        </span>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-[12.5px] font-semibold leading-4 text-slate-800" title={chainNode.title}>
            {chainNode.title}
          </span>
          {chainNode.sub && (
            <span className="mt-1 block truncate text-[10.5px] leading-4 text-slate-500" title={chainNode.sub}>
              {chainNode.sub}
            </span>
          )}
        </span>
        {chainNode.badge && (
          <span className={`shrink-0 rounded border px-1 py-px text-[9.5px] font-medium ${BADGE_CLS[chainNode.badge.tone]}`}>
            {chainNode.badge.text}
          </span>
        )}
      </div>
      <Handle type="source" position={Position.Right} className="!h-2 !w-2 !border-0 !bg-slate-300" />
    </div>
  )
}

function ChainColumnHeader({ data }: NodeProps<Node<{ label: string; count: number } & Record<string, unknown>>>) {
  return (
    <div className="pointer-events-none flex items-center justify-center gap-1.5 text-center" style={{ width: NODE_W }}>
      <span className="text-[13px] font-semibold tracking-wide text-slate-500">
        {data.label}
      </span>
      <span className="rounded-full bg-slate-100 px-1.5 py-px text-[10px] tabular-nums text-slate-400">
        {data.count}
      </span>
    </div>
  )
}

/* 流动粒子连线:基线 + 两个沿线流动的光点(借鉴治理链路全景效果图的动态感),
   高亮时流动加快,压暗时静止;prefers-reduced-motion 下粒子隐藏。 */
function ChainFlowEdge({ id, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, data }: EdgeProps<Edge<ChainEdgeData>>) {
  const [path] = getBezierPath({ sourceX, sourceY, sourcePosition, targetX, targetY, targetPosition })
  const stroke = data?.stroke ?? '#0d9488'
  const highlighted = Boolean(data?.highlighted)
  const dimmed = Boolean(data?.dimmed)
  const dur = highlighted ? '1.6s' : '2.8s'
  const begin = highlighted ? '-0.8s' : '-1.4s'
  return (
    <>
      <BaseEdge
        id={id}
        path={path}
        style={{
          stroke,
          strokeWidth: highlighted ? 2.2 : 1.3,
          strokeDasharray: data?.dash,
          opacity: dimmed ? 0.15 : highlighted ? 0.95 : 0.55,
        }}
      />
      {!dimmed && [0, 1].map(index => (
        <circle
          key={index}
          className="chain-edge-particle"
          r={highlighted ? 3 : 2.4}
          fill={stroke}
          opacity={highlighted ? 0.95 : 0.55}
        >
          <animateMotion
            dur={dur}
            begin={index === 0 ? '0s' : begin}
            repeatCount="indefinite"
            path={path}
          />
        </circle>
      ))}
    </>
  )
}

const nodeTypes = { chainNode: ChainNodeCard, chainHeader: ChainColumnHeader }
const edgeTypes = { chainFlow: ChainFlowEdge }

const EDGE_STYLE: Record<ChainEdge['kind'], { stroke: string; dash?: string }> = {
  flow: { stroke: '#0d9488' },
  hit: { stroke: '#f43f5e' },
  auto: { stroke: '#94a3b8', dash: '5 4' },
}

export default function ChainPanorama({
  nodes,
  edges,
  guides,
  isRefreshing,
  onOpenPending,
}: {
  nodes: ChainNode[]
  edges: ChainEdge[]
  guides: ChainGuide[]
  isRefreshing: boolean
  onOpenPending: (logId: string) => void
}) {
  const [highlightSet, setHighlightSet] = useState<Set<string> | null>(null)

  const byColumn = useMemo(() => {
    const grouped = new Map<number, ChainNode[]>()
    for (const node of nodes) {
      grouped.set(node.column, [...(grouped.get(node.column) || []), node])
    }
    return grouped
  }, [nodes])

  // 画布高度按内容最多的列自适应:卡片实际内容多少,就展示多少
  const canvasHeight = useMemo(() => {
    const maxCount = Math.max(1, ...[...byColumn.values()].map(list => list.length))
    const needed = FIRST_NODE_Y + maxCount * (NODE_H + NODE_GAP) + 28
    return Math.min(MAX_CANVAS_H, Math.max(MIN_CANVAS_H, needed))
  }, [byColumn])

  const flowNodes = useMemo<Node[]>(() => {
    const result: Node[] = CHAIN_COLUMNS.map(col => ({
      id: `header:${col.column}`,
      type: 'chainHeader',
      position: { x: col.column * COLUMN_X, y: HEADER_Y },
      data: { label: col.name, count: (byColumn.get(col.column) || []).length },
      draggable: false,
      selectable: false,
    }))
    for (const [column, columnNodes] of byColumn) {
      columnNodes.forEach((chainNode, index) => {
        const highlighted = Boolean(highlightSet?.has(chainNode.id))
        result.push({
          id: chainNode.id,
          type: 'chainNode',
          position: { x: column * COLUMN_X, y: FIRST_NODE_Y + index * (NODE_H + NODE_GAP) },
          data: {
            chainNode,
            highlighted,
            dimmed: Boolean(highlightSet) && !highlighted,
          } satisfies ChainFlowData,
          draggable: false,
        })
      })
    }
    return result
  }, [byColumn, highlightSet])

  const flowEdges = useMemo<Edge[]>(() => edges.map(edge => {
    const highlighted = Boolean(highlightSet?.has(edge.from) && highlightSet?.has(edge.to))
    const dimmed = Boolean(highlightSet) && !highlighted
    const tone = EDGE_STYLE[edge.kind]
    return {
      id: edge.id,
      source: edge.from,
      target: edge.to,
      type: 'chainFlow',
      data: {
        stroke: tone.stroke,
        dash: tone.dash,
        dimmed,
        highlighted,
      } satisfies ChainEdgeData,
    }
  }), [edges, highlightSet])

  const aggregateIds = useMemo(
    () => new Set(nodes.filter(node => node.kind === 'instanceHub').map(node => node.id)),
    [nodes],
  )

  const handleNodeClick = useCallback((_: unknown, node: Node) => {
    if (node.type !== 'chainNode') return
    const chainNode = (node.data as ChainFlowData).chainNode
    setHighlightSet(collectChainNeighborhood(chainNode.id, edges, aggregateIds))
    // 点击节点只做链路高亮,不跳转其他页面;待审批节点额外打开详情弹窗
    if (chainNode.kind === 'pending' && chainNode.refId) onOpenPending(chainNode.refId)
  }, [edges, aggregateIds, onOpenPending])

  const handleGuideClick = useCallback((guide: ChainGuide) => {
    setHighlightSet(new Set(guide.nodeIds))
    if (guide.pendingLogId) onOpenPending(guide.pendingLogId)
  }, [onOpenPending])

  return (
    <div data-testid="governance-chain-panorama" className="rounded-xl border bg-white p-4">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <Waypoints size={15} className="text-teal-600" />
        <p className="text-[13px] font-semibold text-gray-800">本体执行链 · 数据如何变成动作与事实</p>
        <span className="text-[11px] text-gray-400">点击节点高亮整条上下游,点击空白复位;待审批节点即当前瓶颈,点开看前因后果</span>
        {isRefreshing && (
          <span className="ml-auto inline-flex items-center gap-1 text-[10px] text-teal-600">
            <Loader2 size={10} className="animate-spin" /> 同步中
          </span>
        )}
      </div>
      <div
        className="overflow-hidden rounded-lg border border-slate-100 bg-gradient-to-b from-slate-50/60 to-teal-50/30"
        style={{ height: canvasHeight }}
      >
        <ReactFlow
          nodes={flowNodes}
          edges={flowEdges}
          nodeTypes={nodeTypes}
          edgeTypes={edgeTypes}
          fitView
          fitViewOptions={{ padding: 0.15, maxZoom: 1 }}
          minZoom={0.3}
          nodesConnectable={false}
          elementsSelectable
          zoomOnScroll={false}
          panOnScroll={false}
          panOnDrag
          zoomOnPinch
          preventScrolling={false}
          onNodeClick={handleNodeClick}
          onPaneClick={() => setHighlightSet(null)}
          proOptions={{ hideAttribution: true }}
        >
          <Background gap={22} size={1} color="#e2e8f0" />
          <Controls showInteractive={false} position="bottom-right" />
        </ReactFlow>
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-slate-100 pt-3">
        <span className="text-[11px] font-medium text-gray-400">链路导读</span>
        {guides.length === 0 ? (
          <span className="text-[11px] text-gray-400">当前没有停滞在审批环节的链路,全链路畅通。</span>
        ) : (
          guides.map(guide => (
            <button
              key={guide.id}
              type="button"
              onClick={() => handleGuideClick(guide)}
              className="group inline-flex max-w-full items-center gap-1.5 rounded-lg border border-amber-200 bg-amber-50/70 px-2.5 py-1 text-left transition hover:border-amber-300 hover:bg-amber-50"
            >
              <HandMetal size={11} className="shrink-0 text-amber-500" />
              <span className="truncate text-[11px] font-medium text-amber-700">{guide.title}</span>
              <span className="hidden truncate text-[10px] text-amber-500/80 sm:inline">{guide.sub}</span>
            </button>
          ))
        )}
      </div>
    </div>
  )
}
