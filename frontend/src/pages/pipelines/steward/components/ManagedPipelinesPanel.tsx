import { useEffect, useState } from 'react'
import {
  ChevronDown, ChevronRight, ExternalLink, Pencil, RefreshCw, Workflow,
} from 'lucide-react'
import {
  stewardApi, type StewardPipeline, type StewardPipelineDetail,
} from '@/api/steward'
import {
  Background, ReactFlow, ReactFlowProvider, type Edge, type Node,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import dagre from '@dagrejs/dagre'

const PUBLISH_META: Record<string, { label: string; cls: string }> = {
  draft:     { label: '草稿', cls: 'bg-slate-100 text-slate-600 border-slate-200' },
  published: { label: '已发布', cls: 'bg-teal-50 text-teal-700 border-teal-200' },
}

// ---------- 受管流水线面板 ----------

export default function ManagedPipelinesPanel({ records, loading, expandedId, onExpand, onChanged, n8nApiUrl, onOpenWizard }: {
  records: StewardPipeline[]
  loading: boolean
  expandedId: string | null
  onExpand: (id: string | null) => void
  onChanged: () => void
  n8nApiUrl: string
  onOpenWizard: (pipelineId: string) => void
}) {
  return (
    <aside className="flex shrink-0 flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-[0_8px_30px_rgba(15,23,42,0.04)] max-xl:max-h-[42%] max-xl:min-h-[180px] xl:min-w-0 xl:flex-1">
      <div className="flex items-center justify-between border-b border-slate-100 px-4 py-3.5">
        <div className="min-w-0">
          <h2 className="flex items-center gap-1.5 text-sm font-semibold text-gray-800 shrink-0">
            <Workflow size={15} className="text-teal-700" /> 可编排流水线
          </h2>
          <p className="mt-0.5 whitespace-nowrap text-[10px] text-slate-400">此工作区只展示处于未发布的n8n流水线</p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <button onClick={onChanged}
            className="flex items-center gap-1.5 rounded-lg border border-teal-200 bg-teal-50 px-2.5 py-1.5 text-xs font-medium text-teal-700 transition-colors hover:border-teal-300 hover:bg-teal-100 active:scale-[0.98] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-400">
            <RefreshCw size={13} /> 手动刷新
          </button>
        </div>
      </div>

      <div className="scrollbar-none flex-1 space-y-4 overflow-y-auto px-3 py-3">
        {loading ? (
          <div className="p-6 text-center text-sm text-gray-400">加载中…</div>
        ) : records.length === 0 ? (
          <div className="space-y-2 rounded-xl border border-dashed border-slate-300 bg-slate-50/70 p-8 text-center text-slate-400">
            <Workflow size={28} className="mx-auto opacity-30" />
            <p className="text-sm">还没有可编排的 n8n 流水线</p>
            <p className="text-xs">在左侧对话里让数据管家新建一条试试</p>
          </div>
        ) : (
          <div>
            <div className="space-y-2">
              {records.map(r => (
                <RecordCard
                  key={r.id} record={r}
                  expanded={expandedId === r.id}
                  onToggle={() => onExpand(expandedId === r.id ? null : r.id)}
                  n8nApiUrl={n8nApiUrl}
                  onOpenWizard={onOpenWizard}
                />
              ))}
            </div>
          </div>
        )}
      </div>
    </aside>
  )
}

// ---------- 迷你流水线图 ----------

const NODE_W = 120
const NODE_H = 32

function layoutGraph(nodes: Node[], edges: Edge[]) {
  const g = new dagre.graphlib.Graph()
  g.setDefaultEdgeLabel(() => ({}))
  g.setGraph({ rankdir: 'LR', nodesep: 20, ranksep: 50, marginx: 12, marginy: 12 })
  nodes.forEach(n => g.setNode(n.id, { width: NODE_W, height: NODE_H }))
  edges.forEach(e => g.setEdge(e.source, e.target))
  dagre.layout(g)
  return nodes.map(n => {
    const pos = g.node(n.id)
    return { ...n, position: { x: pos.x - NODE_W / 2, y: pos.y - NODE_H / 2 } }
  })
}

interface MiniWorkflowNode { name: string; type?: string; disabled?: boolean }
interface MiniWorkflowTarget { node: string; type?: string; index?: number }
interface MiniWorkflow {
  nodes?: MiniWorkflowNode[]
  connections?: Record<string, Record<string, MiniWorkflowTarget[][]>>
}

function buildGraph(workflow: unknown): { nodes: Node[]; edges: Edge[] } {
  const nodes: Node[] = []
  const edges: Edge[] = []
  const value = workflow && typeof workflow === 'object' ? workflow as MiniWorkflow : {}
  const wfNodes = value.nodes || []
  const wfConns = value.connections || {}

  wfNodes.forEach(n => {
    nodes.push({
      id: n.name,
      type: 'default',
      data: { label: n.name },
      position: { x: 0, y: 0 },
      style: {
        fontSize: '9px', padding: '4px 10px', borderRadius: '6px',
        border: '1px solid #e5e7eb', background: '#f9fafb', color: '#374151',
        width: NODE_W,
      },
    })
  })

  Object.entries(wfConns).forEach(([source, outputs]) => {
    Object.values(outputs).forEach(targetLanes => {
      targetLanes.forEach(targets => targets.forEach(t => {
        edges.push({
          id: `e-${source}-${t.node}`,
          source,
          target: t.node,
          style: { stroke: '#d1d5db', strokeWidth: 1.5 },
        })
      }))
    })
  })

  return { nodes, edges }
}

function MiniGraph({ workflow }: { workflow: unknown }) {
  const { nodes, edges } = buildGraph(workflow)
  const laidOut = layoutGraph([...nodes], [...edges])

  if (nodes.length === 0) return <div className="flex items-center justify-center h-full text-[11px] text-gray-400">暂无节点</div>

  return (
    <ReactFlowProvider>
      <ReactFlow
        nodes={laidOut}
        edges={edges}
        fitView
        fitViewOptions={{ padding: 0.15 }}
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable={false}
        panOnDrag
        zoomOnScroll
        preventScrolling={false}
        proOptions={{ hideAttribution: true }}
      >
        <Background color="#f3f4f6" gap={16} size={0.5} />
      </ReactFlow>
    </ReactFlowProvider>
  )
}

// ---------- 单条记录卡片 ----------

function RecordCard({ record: r, expanded, onToggle, n8nApiUrl, onOpenWizard }: {
  record: StewardPipeline
  expanded: boolean
  onToggle: () => void
  n8nApiUrl: string
  onOpenWizard: (pipelineId: string) => void
}) {
  const published = r.pipelineStatus === 'published'
  const meta = PUBLISH_META[published ? 'published' : 'draft']
  const [detail, setDetail] = useState<StewardPipelineDetail | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)

  useEffect(() => {
    if (expanded && !detail && !detailLoading) {
      const timer = window.setTimeout(() => {
        setDetailLoading(true)
        stewardApi.pipeline(r.id).then(setDetail).catch(() => {}).finally(() => setDetailLoading(false))
      }, 0)
      return () => window.clearTimeout(timer)
    }
  }, [expanded, detail, detailLoading, r.id])

  // 记录变化后（如发布/编排更新）重置已缓存的详情
  useEffect(() => {
    const timer = window.setTimeout(() => setDetail(null), 0)
    return () => window.clearTimeout(timer)
  }, [r.pipelineStatus, r.updatedAt])

  const n8nWebUrl = n8nApiUrl ? n8nApiUrl.replace(/\/api\/.*$/, '').replace(/\/+$/, '') : ''
  const canJump = !!(n8nWebUrl && r.n8nWorkflowId)

  return (
    <div className={`overflow-hidden rounded-xl border transition ${expanded ? 'border-teal-200 bg-teal-50/20 shadow-sm' : 'border-slate-200 bg-white hover:border-slate-300 hover:shadow-sm'}`}>
      <div className="flex items-start gap-2 px-3.5 py-3">
        <button onClick={onToggle} className="flex min-w-0 flex-1 items-start gap-2 text-left" aria-expanded={expanded}>
          {expanded ? <ChevronDown size={14} className="mt-0.5 shrink-0 text-gray-400" /> : <ChevronRight size={14} className="mt-0.5 shrink-0 text-gray-400" />}
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-1.5">
            <p className="truncate text-sm font-medium text-gray-900">{r.name}</p>
            {published && <span className={`shrink-0 rounded border px-1.5 py-px text-[10px] ${meta.cls}`}>{meta.label}</span>}
            {r.active && <span className="shrink-0 rounded border border-green-200 bg-green-50 px-1.5 py-px text-[10px] text-green-600">n8n 已激活</span>}
            </div>
            {r.description && <p className="mt-0.5 truncate text-[11px] text-gray-500">{r.description}</p>}
          </div>
        </button>
        <button onClick={() => { if (r.pipelineId) onOpenWizard(r.pipelineId) }}
          className="flex shrink-0 items-center gap-1 rounded-lg border border-teal-200 px-2 py-1 text-xs text-teal-700 transition hover:bg-teal-50"
          title="打开编辑向导">
          <Pencil size={11} /> 编辑
        </button>
        {canJump && (
          <button onClick={() => window.open(`${n8nWebUrl}/workflow/${r.n8nWorkflowId}`, '_blank')}
            className="flex shrink-0 items-center gap-1 rounded-lg border border-blue-200 px-2 py-1 text-xs text-blue-600 transition-colors hover:bg-blue-50"
            title="跳转 n8n 工作流">
            <ExternalLink size={11} /> 访问
          </button>
        )}
      </div>

      {expanded && (
        <div className="space-y-2 border-t border-slate-100 px-3 py-2.5">
          {/* 节点连线图 */}
          <div className="h-[180px] w-full rounded-lg overflow-hidden border bg-gray-50/30">
            {detailLoading ? (
              <div className="flex items-center justify-center h-full text-[11px] text-gray-400">加载中…</div>
            ) : (
              <MiniGraph workflow={detail?.workflow ?? r.summary} />
            )}
          </div>

          {/* n8n 可达性（只读；执行观测/试跑已不在管家职权内） */}
          {detail?.n8nError ? (
            <p className="text-[11px] text-amber-600">n8n 暂不可达：{detail.n8nError}</p>
          ) : null}
        </div>
      )}
    </div>
  )
}
