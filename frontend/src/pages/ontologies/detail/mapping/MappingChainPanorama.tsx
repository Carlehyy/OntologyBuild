/* 数据供给全景画布(@xyflow/react):
   「来源数据资产 → 本体元素」两列链路节点卡,与治理推演页链路全景同一设计语言;
   点选节点高亮整条直接上下游、其余压暗,点画布空白复位;
   数据资产节点点击打开数据预览,本体元素节点点击打开血缘详情弹窗。 */
import { useCallback, useMemo, useState } from 'react'
import {
  Background,
  Controls,
  Handle,
  Position,
  ReactFlow,
  type Edge,
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
}

const COLUMN_X = 300
const NODE_W = 220
const NODE_H = 66
const NODE_GAP = 14
const HEADER_Y = 0
const FIRST_NODE_Y = 48

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

/** 二分图的直接上下游:节点自身 + 与其相连的边两端。 */
function collectNeighborhood(nodeId: string, links: Array<{ source: string; target: string }>): Set<string> {
  const ids = new Set<string>([nodeId])
  for (const link of links) {
    if (link.source === nodeId) ids.add(link.target)
    if (link.target === nodeId) ids.add(link.source)
  }
  return ids
}

export default function MappingChainPanorama({
  rows,
  hoverKey,
  selectedKey,
  onSelectElement,
  onPreviewDataset,
  onHoverNode,
}: MappingChainPanoramaProps) {
  const model = useMemo(() => buildFlowModel(rows), [rows])
  const [highlightSet, setHighlightSet] = useState<Set<string> | null>(null)

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
    const result: Node[] = [
      {
        id: 'header:dataset', type: 'mappingChainHeader', draggable: false, selectable: false,
        position: { x: 0, y: HEADER_Y }, data: { label: '来源数据资产', count: datasets.length },
      },
      {
        id: 'header:element', type: 'mappingChainHeader', draggable: false, selectable: false,
        position: { x: COLUMN_X, y: HEADER_Y }, data: { label: '本体元素', count: elements.length },
      },
    ]
    const push = (nodeId: string, column: number, index: number, card: ChainCardFields) => {
      const highlighted = Boolean(highlightSet?.has(nodeId)) || hoverKey === nodeId || selectedKey === nodeId
      result.push({
        id: nodeId,
        type: 'mappingChainNode',
        position: { x: column * COLUMN_X, y: FIRST_NODE_Y + index * (NODE_H + NODE_GAP) },
        data: { ...card, highlighted, dimmed: highlightSet ? !highlightSet.has(nodeId) : false } satisfies ChainCardData,
        draggable: false,
      })
    }
    datasets.forEach((node, index) => {
      const card = datasetCards.get(node.id.replace(/^dataset:/, ''))
      push(node.id, 0, index, {
        title: node.displayName,
        sub: card?.sub ?? '',
        badge: card?.badge ?? null,
        icon: 'dataset',
      })
    })
    elements.forEach((node, index) => {
      const row = rows.find(candidate => candidate.key === node.id)
      push(node.id, 1, index, {
        title: node.displayName,
        sub: row ? `实例 ${row.instanceCount.toLocaleString()} 条 · 字段 ${row.mappedFields}/${row.totalFields}` : '',
        badge: row ? { text: row.statusLabel, tone: STATUS_TONE[row.status] ?? 'muted' } : null,
        icon: node.kind === 'object' ? 'object' : 'relation',
      })
    })
    return result
  }, [model, rows, datasetCards, highlightSet, hoverKey, selectedKey])

  const flowEdges = useMemo<Edge[]>(() => model.links.map((link, index) => {
    const highlighted = Boolean(highlightSet?.has(link.source) && highlightSet?.has(link.target))
    const dimmed = Boolean(highlightSet) && !highlighted
    return {
      id: `edge:${index}:${link.source}->${link.target}`,
      source: link.source,
      target: link.target,
      type: 'bezier',
      style: {
        stroke: 'var(--dmo-teal)',
        strokeWidth: highlighted ? 2.2 : 1.3,
        opacity: dimmed ? 0.12 : highlighted ? 0.95 : 0.5,
      },
    }
  }), [model, highlightSet])

  const handleNodeClick = useCallback((_: unknown, node: Node) => {
    if (node.type !== 'mappingChainNode') return
    setHighlightSet(collectNeighborhood(node.id, model.links))
    const action = resolveFlowNodeClick({ id: node.id, kind: node.data.icon as string })
    if (action?.type === 'preview-dataset') onPreviewDataset(action.datasetId)
    else if (action?.type === 'select-element') onSelectElement(action.key)
  }, [model, onPreviewDataset, onSelectElement])

  const height = useMemo(() => {
    const datasets = model.nodes.filter(node => node.kind === 'dataset').length
    const elements = model.nodes.length - datasets
    return Math.min(400, Math.max(240, Math.max(datasets, elements) * (NODE_H + NODE_GAP) + FIRST_NODE_Y))
  }, [model])

  return (
    <div className="dmo-flow-canvas dmo-chain-canvas" data-testid="mapping-chain-panorama" style={{ height }}>
      <ReactFlow
        nodes={flowNodes}
        edges={flowEdges}
        nodeTypes={nodeTypes}
        fitView
        fitViewOptions={{ padding: 0.15, maxZoom: 1 }}
        minZoom={0.3}
        nodesConnectable={false}
        elementsSelectable
        zoomOnScroll={false}
        panOnScroll={false}
        // 左键按下会触发面板拖拽(panOnDrag=true 的副作用),吞掉节点 click;
        // 平移交给中/右键与 Controls,左键专用于节点点选
        panOnDrag={[1, 2]}
        zoomOnPinch
        preventScrolling={false}
        onNodeClick={handleNodeClick}
        onPaneClick={() => setHighlightSet(null)}
        onNodeMouseEnter={(_, node) => { if (node.type === 'mappingChainNode') onHoverNode(node.id) }}
        onNodeMouseLeave={() => onHoverNode(null)}
        proOptions={{ hideAttribution: true }}
      >
        <Background gap={22} size={1} color="var(--dmo-line-soft)" />
        <Controls showInteractive={false} position="bottom-right" />
      </ReactFlow>
    </div>
  )
}
