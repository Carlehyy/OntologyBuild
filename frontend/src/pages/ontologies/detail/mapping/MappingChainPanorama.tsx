/* 数据供给全景画布(@xyflow/react):
   「来源数据资产 → 本体元素」两列链路节点卡,与治理推演页链路全景同一设计语言;
   连线为纯视觉元素(interactionWidth=0 + pointer-events:none),不参与命中测试,
   避免边线层重挂载/回流打断节点卡悬停——那正是「悬停卡片一直在闪」的根源;
   高亮集合经稳定字符串参与依赖,hover 状态变化不再重建连线数组;
   流动粒子在悬停检视(未聚焦)期间对非聚焦连线静止(缩放下观感),聚焦链路保持流动;
   自动布局按列垂直居中(矮列相对最高列居中,整图重心均衡),节点卡可拖拽微调,
   位置按本体持久化到 localStorage;
   滚轮缩放(preventScrolling 捕获滚轮,画布上不再透传页面滚动),左键拖空白平移;
   点选节点高亮整条直接上下游、其余压暗,点画布空白复位;
   数据资产节点点击打开数据预览,本体元素节点点击打开血缘详情弹窗。 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Background,
  BaseEdge,
  Controls,
  getBezierPath,
  Handle,
  Position,
  ReactFlow,
  ReactFlowProvider,
  useNodesInitialized,
  useReactFlow,
  type Edge,
  type EdgeProps,
  type Node,
  type NodeProps,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { Boxes, Database, GitBranch } from 'lucide-react'
import { buildFlowModel, resolveFlowNodeClick, type FlowRowInput } from './mapping-review'

/** 行数据在纯函数输入之上附带节点卡所需的展示信息。 */
export interface ChainFlowRow extends FlowRowInput {
  status: string
  statusLabel: string
  mappedFields: number
  totalFields: number
  datasets: Array<{
    id: string
    name: string
    sourceLabel: string
    rows: number | null
    quality: number | null
    reviewLabel: string | null
  }>
}

interface MappingChainPanoramaProps {
  rows: ChainFlowRow[]
  /** 清单行 hover 时下发对应图节点 id,驱动节点高亮。 */
  hoverKey: string | null
  selectedKey: string | null
  onSelectElement: (key: string) => void
  onPreviewDataset: (datasetId: string) => void
  onHoverNode: (key: string | null) => void
  /** 节点拖拽位置按本体持久化（localStorage 新增 key，不改既有存储契约）。 */
  ontologyId: string
}

const chainPositionsKey = (ontologyId: string) => `dmo:chain-positions:${ontologyId}`
function readChainPositions(ontologyId: string): Record<string, { x: number; y: number }> {
  try {
    const raw = window.localStorage.getItem(chainPositionsKey(ontologyId))
    if (!raw) return {}
    const parsed = JSON.parse(raw) as Record<string, { x: number; y: number }>
    return parsed && typeof parsed === 'object' ? parsed : {}
  } catch { return {} }
}
function writeChainPositions(ontologyId: string, positions: Record<string, { x: number; y: number }>) {
  try { window.localStorage.setItem(chainPositionsKey(ontologyId), JSON.stringify(positions)) } catch { /* noop */ }
}

const COLUMN_X = 300
const COLUMN_X_MAX = 620
const NODE_W = 220
const NODE_H = 66
const NODE_GAP = 14
const HEADER_Y = 0
const FIRST_NODE_Y = 48

/** 两列链路的列距随容器宽度自适应：全宽画布下两列拉开（横向链路更舒展），
    窄容器回落到历史默认 300。已持久化的节点拖拽位置不受影响（绝对坐标优先）。 */
const resolveColumnX = (containerWidth: number) => (
  containerWidth > 0 ? Math.max(COLUMN_X, Math.min(COLUMN_X_MAX, containerWidth - NODE_W - 96)) : COLUMN_X
)

type BadgeTone = 'ok' | 'warn' | 'danger' | 'muted'

const STATUS_TONE: Record<string, BadgeTone> = {
  ready: 'ok',
  'no-data': 'warn',
  incomplete: 'warn',
  'type-risk': 'warn',
  unmapped: 'muted',
  'missing-source': 'danger',
}

interface ChainCardFields {
  title: string
  sub: string
  badge: { text: string; tone: BadgeTone } | null
  icon: 'dataset' | 'object' | 'relation'
}

type ChainCardData = ChainCardFields & { dimmed: boolean; highlighted: boolean } & Record<string, unknown>

const CHAIN_ICON = { dataset: Database, object: Boxes, relation: GitBranch } as const

function MappingChainNodeCard({ data }: NodeProps<Node<ChainCardData>>) {
  const Icon = CHAIN_ICON[data.icon]
  return (
    <div
      className={`dmo-chain-node${data.dimmed ? ' dmo-chain-node-dim' : ''}${data.highlighted ? ' dmo-chain-node-hot' : ''}`}
      style={{ width: NODE_W, minHeight: NODE_H }}
      data-icon={data.icon}
    >
      <Handle type="target" position={Position.Left} className="!h-2 !w-2 !border-0 !bg-slate-300" />
      <div className="dmo-chain-row">
        <span className={`dmo-chain-icon dmo-chain-icon--${data.icon}`}><Icon size={13} /></span>
        <span className="dmo-chain-text">
          <b title={data.title}>{data.title}</b>
          <small title={data.sub}>{data.sub}</small>
        </span>
        {data.badge && <em className="dmo-chain-badge" data-tone={data.badge.tone}>{data.badge.text}</em>}
      </div>
      <Handle type="source" position={Position.Right} className="!h-2 !w-2 !border-0 !bg-slate-300" />
    </div>
  )
}

function MappingChainColumnHeader({ data }: NodeProps<Node<{ label: string; count: number } & Record<string, unknown>>>) {
  return (
    <div className="dmo-chain-colhead" style={{ width: NODE_W }}>
      <span>{data.label}</span>
      <em>{data.count}</em>
    </div>
  )
}

const nodeTypes = { mappingChainNode: MappingChainNodeCard, mappingChainHeader: MappingChainColumnHeader }

interface MappingEdgeData extends Record<string, unknown> {
  highlighted: boolean
  dimmed: boolean
  /** 悬停检视期间静止非聚焦连线（防缩放闪烁）；聚焦连线保持流动。 */
  still?: boolean
}

/* 流动粒子连线：基线 + 两个沿线流动的光点，视觉口径对齐治理推演·本体执行链
   （ChainFlowEdge）——选中聚焦时连线加粗提亮、粒子变大且流速加快（1.6s），
   其余压暗近乎隐去，视线自然聚焦到选中链路。 */
function MappingFlowEdge({ id, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, data, interactionWidth }: EdgeProps<Edge<MappingEdgeData>>) {
  const [path] = getBezierPath({ sourceX, sourceY, sourcePosition, targetX, targetY, targetPosition })
  const highlighted = Boolean(data?.highlighted)
  const dimmed = Boolean(data?.dimmed)
  const showParticles = !dimmed && (!data?.still || highlighted)
  const dur = highlighted ? '1.6s' : '2.8s'
  const begin = highlighted ? '-0.8s' : '-1.4s'
  return (
    <>
      <BaseEdge
        id={id}
        path={path}
        /* BaseEdge 默认 interactionWidth=20 会渲染透明命中条，显式透传 0 保持
           连线纯视觉（防悬停振荡，见组件头注释与 MYW-60 用例） */
        interactionWidth={interactionWidth}
        style={{
          stroke: 'var(--dmo-teal)',
          strokeWidth: highlighted ? 2.2 : 1.3,
          opacity: dimmed ? 0.15 : highlighted ? 0.95 : 0.5,
        }}
      />
      {showParticles && [0, 1].map(index => (
        <circle
          key={index}
          className="dmo-chain-particle"
          r={highlighted ? 3 : 2.4}
          fill="var(--dmo-teal)"
          opacity={highlighted ? 0.95 : 0.5}
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

const edgeTypes = { mappingFlowEdge: MappingFlowEdge }

/** 二分图的直接上下游:节点自身 + 与其相连的边两端。 */
function collectNeighborhood(nodeId: string, links: Array<{ source: string; target: string }>): Set<string> {
  const ids = new Set<string>([nodeId])
  for (const link of links) {
    if (link.source === nodeId) ids.add(link.target)
    if (link.target === nodeId) ids.add(link.source)
  }
  return ids
}

/** 外层仅提供 ReactFlow 上下文（useReactFlow/useNodesInitialized 需在 Provider 内使用）。 */
export default function MappingChainPanorama(props: MappingChainPanoramaProps) {
  return (
    <ReactFlowProvider>
      <MappingChainPanoramaInner {...props} />
    </ReactFlowProvider>
  )
}

function MappingChainPanoramaInner({
  rows,
  hoverKey,
  selectedKey,
  onSelectElement,
  onPreviewDataset,
  onHoverNode,
  ontologyId,
}: MappingChainPanoramaProps) {
  const model = useMemo(() => buildFlowModel(rows), [rows])
  const [highlightSet, setHighlightSet] = useState<Set<string> | null>(null)
  // 节点拖拽位置按本体持久化：重进/刷新后保持手动调整的布局
  const [dragPositions, setDragPositions] = useState<Record<string, { x: number; y: number }>>(() => readChainPositions(ontologyId))
  // 指针悬停在图内节点卡上时置真：检视期间非聚焦连线不渲染流动粒子，
  // 避免粒子在 fitView 缩放(<1)下逐帧重绘使卡片边缘闪烁（见 mapping-overview.css）。
  const [hoveringNode, setHoveringNode] = useState(false)

  // 内容水平居中：fitView 只在初始化时执行一次，彼时节点尺寸尚未测量完成，
  // 生产页面上常得到「整块内容靠左、右侧大片留白」的陈旧视口；这里在全部节点
  // 测量完成后（以及模型/容器尺寸变化时）重新 fitView 居中，保证列抬头与卡片
  // 列整体水平居中。拖拽布局不触发 refit（模型未变），手动布局不被打断。
  const containerRef = useRef<HTMLDivElement>(null)
  // 容器宽高都参与 refit 依赖：双栏布局下画布随左栏高度拉伸（flex 填充），
  // 只观察宽度会漏掉高度变化后的重新适配。
  const [containerSize, setContainerSize] = useState({ width: 0, height: 0 })
  useEffect(() => {
    const element = containerRef.current
    if (!element) return
    const update = () => setContainerSize({ width: element.clientWidth, height: element.clientHeight })
    update()
    const observer = new ResizeObserver(update)
    observer.observe(element)
    return () => observer.disconnect()
  }, [])
  const flowInstance = useReactFlow()
  const nodesInitialized = useNodesInitialized()
  useEffect(() => {
    if (!nodesInitialized) return
    void flowInstance.fitView({ padding: 0.15, maxZoom: 1 })
  }, [flowInstance, nodesInitialized, model, containerSize])

  const datasetCards = useMemo(() => {
    const cards = new Map<string, { title: string; sub: string; badge: ChainCardData['badge'] }>()
    for (const row of rows) {
      for (const dataset of row.datasets) {
        if (cards.has(dataset.id)) continue
        const quality = dataset.quality == null ? null : Math.round(Number(dataset.quality) <= 1 ? Number(dataset.quality) * 100 : Number(dataset.quality))
        cards.set(dataset.id, {
          title: dataset.name,
          sub: `${dataset.sourceLabel}${dataset.rows == null ? '' : ` · ${dataset.rows.toLocaleString()} 行`}${quality == null ? '' : ` · 质量 ${quality}%`}`,
          badge: dataset.reviewLabel ? { text: dataset.reviewLabel, tone: 'danger' } : null,
        })
      }
    }
    return cards
  }, [rows])

  const flowNodes = useMemo<Node[]>(() => {
    const datasets = model.nodes.filter(node => node.kind === 'dataset')
    const elements = model.nodes.filter(node => node.kind !== 'dataset')
    const columnX = resolveColumnX(containerSize.width)
    // 按列垂直居中：矮列相对最高列的卡片堆居中，整图重心均衡，不再都从顶部堆起
    const stackHeight = (count: number) => count * NODE_H + Math.max(count - 1, 0) * NODE_GAP
    const centerOffset = (count: number) =>
      (stackHeight(Math.max(datasets.length, elements.length)) - stackHeight(count)) / 2
    const result: Node[] = [
      {
        id: 'header:dataset', type: 'mappingChainHeader', draggable: false, selectable: false,
        position: { x: 0, y: HEADER_Y }, data: { label: '来源数据资产', count: datasets.length },
      },
      {
        id: 'header:element', type: 'mappingChainHeader', draggable: false, selectable: false,
        position: { x: columnX, y: HEADER_Y }, data: { label: '本体元素', count: elements.length },
      },
    ]
    const push = (nodeId: string, column: number, index: number, card: ChainCardFields, offsetY: number) => {
      const highlighted = Boolean(highlightSet?.has(nodeId)) || hoverKey === nodeId || selectedKey === nodeId
      result.push({
        id: nodeId,
        type: 'mappingChainNode',
        position: dragPositions[nodeId] ?? {
          x: column * columnX,
          y: FIRST_NODE_Y + offsetY + index * (NODE_H + NODE_GAP),
        },
        data: { ...card, highlighted, dimmed: highlightSet ? !highlightSet.has(nodeId) : false } satisfies ChainCardData,
      })
    }
    const datasetOffsetY = centerOffset(datasets.length)
    const elementOffsetY = centerOffset(elements.length)
    datasets.forEach((node, index) => {
      const card = datasetCards.get(node.id.replace(/^dataset:/, ''))
      push(node.id, 0, index, {
        title: node.displayName,
        sub: card?.sub ?? '',
        badge: card?.badge ?? null,
        icon: 'dataset',
      }, datasetOffsetY)
    })
    elements.forEach((node, index) => {
      const row = rows.find(candidate => candidate.key === node.id)
      push(node.id, 1, index, {
        title: node.displayName,
        sub: row ? `实例 ${row.instanceCount.toLocaleString()} 条 · 字段 ${row.mappedFields}/${row.totalFields}` : '',
        badge: row ? { text: row.statusLabel, tone: STATUS_TONE[row.status] ?? 'muted' } : null,
        icon: node.kind === 'object' ? 'object' : 'relation',
      }, elementOffsetY)
    })
    return result
  }, [model, rows, datasetCards, highlightSet, hoverKey, selectedKey, dragPositions, containerSize.width])

  // 高亮集合以稳定字符串参与依赖：成员不变时重建的 Set 不再触发连线数组重算
  const highlightSetKey = useMemo(
    () => (highlightSet ? Array.from(highlightSet).sort().join('|') : ''),
    [highlightSet],
  )
  // 悬停检视（未聚焦）期间非聚焦连线静止防闪烁；点选/选中聚焦时保持流动（MYW-77 二轮）
  const still = (hoveringNode || hoverKey != null) && !highlightSet && selectedKey == null
  const flowEdges = useMemo<Edge[]>(() => {
    const members = highlightSetKey ? new Set(highlightSetKey.split('|')) : null
    return model.links.map((link, index) => {
      const highlighted = Boolean(members?.has(link.source) && members?.has(link.target))
      const dimmed = Boolean(members) && !highlighted
      return {
        id: `edge:${index}:${link.source}->${link.target}`,
        source: link.source,
        target: link.target,
        type: 'mappingFlowEdge',
        // 连线不承载任何点击/悬停交互：interactionWidth=0 去掉 20px 宽的透明
        // 命中条，配合样式层 pointer-events:none，杜绝边线层反复卸载/重挂时
        // 打断节点卡悬停命中测试（悬停卡片闪烁问题的根源）
        interactionWidth: 0,
        data: { highlighted, dimmed, still } satisfies MappingEdgeData,
      }
    })
  }, [model, highlightSetKey, still])

  const handleNodeClick = useCallback((_: unknown, node: Node) => {
    if (node.type !== 'mappingChainNode') return
    setHighlightSet(collectNeighborhood(node.id, model.links))
    const action = resolveFlowNodeClick({ id: node.id, kind: node.data.icon as string })
    if (action?.type === 'preview-dataset') onPreviewDataset(action.datasetId)
    else if (action?.type === 'select-element') onSelectElement(action.key)
  }, [model, onPreviewDataset, onSelectElement])

  // 内容高度下限仍按节点规模计算；双栏布局下左栏若有富余高度，画布经 CSS
  // flex 拉伸填满（minHeight 只是下限，不设固定高度），更大的图区对全景更有利。
  const minHeight = useMemo(() => {
    const datasets = model.nodes.filter(node => node.kind === 'dataset').length
    const elements = model.nodes.length - datasets
    return Math.min(400, Math.max(240, Math.max(datasets, elements) * (NODE_H + NODE_GAP) + FIRST_NODE_Y))
  }, [model])

  return (
    <div
      ref={containerRef}
      className="dmo-flow-canvas dmo-chain-canvas"
      data-testid="mapping-chain-panorama"
      style={{ minHeight }}
    >
      {/* 绝对定位填充层：画布盒只有 minHeight 下限（无确定高度）时，
          React Flow 根节点的 height:100% 内联样式会塌缩为 0 */}
      <div style={{ position: 'absolute', inset: 0 }}>
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
          // 滚轮缩放（preventScrolling 捕获滚轮，画布上不再透传页面滚动）；
          // 节点可拖拽后保留左键全景交互：拖节点=调整布局，拖空白=平移画布；
          // 静止点按不会被识别为拖拽，节点 click 不受影响
          panOnDrag
          zoomOnPinch
          preventScrolling
          onNodeClick={handleNodeClick}
          onNodeDragStop={(_, node) => {
            if (node.type !== 'mappingChainNode') return
            setDragPositions(prev => {
              const next = { ...prev, [node.id]: { x: node.position.x, y: node.position.y } }
              writeChainPositions(ontologyId, next)
              return next
            })
          }}
          onPaneClick={() => setHighlightSet(null)}
          onNodeMouseEnter={(_, node) => {
            if (node.type !== 'mappingChainNode') return
            setHoveringNode(true)
            onHoverNode(node.id)
          }}
          onNodeMouseLeave={() => {
            setHoveringNode(false)
            onHoverNode(null)
          }}
          proOptions={{ hideAttribution: true }}
        >
          <Background gap={22} size={1} color="var(--dmo-line-soft)" />
          <Controls showInteractive={false} position="bottom-right" />
        </ReactFlow>
      </div>
    </div>
  )
}
