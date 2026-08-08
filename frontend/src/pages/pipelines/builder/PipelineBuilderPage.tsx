import { useCallback, useRef, useState, useEffect } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import {
  ReactFlow, MiniMap, Controls, Background,
  useNodesState, useEdgesState, addEdge, ReactFlowProvider,
  type Connection, type Node, type Edge,
  type NodeTypes, type OnNodesChange, type OnEdgesChange,
  MarkerType,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { Save, Play, CheckCircle, X, ArrowLeft, Loader2, AlertTriangle, Pencil, ChevronLeft, ChevronRight, CheckCircle2, XCircle, ArrowRight, Repeat } from 'lucide-react'
import pipelinesApi, { type Pipeline, type ValidateResult } from '@/api/v2/pipelines'
import ConnectorNode from './nodes/ConnectorNode'
import StorageNode from './nodes/StorageNode'
import TransformNode from './nodes/TransformNode'
import OutputNode from './nodes/OutputNode'
import ConnectorInspector from './inspectors/ConnectorInspector'
import StorageInspector from './inspectors/StorageInspector'
import TransformInspector from './inspectors/TransformInspector'
import OutputInspector from './inspectors/OutputInspector'

const nodeTypes: NodeTypes = {
  connector: ConnectorNode, storage: StorageNode,
  transform: TransformNode, output: OutputNode,
}

const NODE_DEFAULTS: Record<string, { label: string; color: string; config: Record<string, unknown> }> = {
  connector: { label: '连接器', color: '#3B82F6', config: { source_type: 'file', config_values: {} } },
  storage: { label: '存储器', color: '#10B981', config: { storage_mode: 'auto' } },
  transform: { label: '转换器', color: '#F59E0B', config: { path: 'auto', steps: [] } },
  output: { label: '输出', color: '#8B5CF6', config: { dataset_type: 'curated_dataset', primary_key: [] } },
}

const TOOLS = [
  { type: 'connector', label: '连接器', desc: '数据源连接' },
  { type: 'storage', label: '存储器', desc: '原始数据存储' },
  { type: 'transform', label: '转换器', desc: '数据转换' },
  { type: 'output', label: '输出', desc: '输出 Curated Dataset' },
]

const TYPE_ORDER: Record<string, number> = { connector: 0, storage: 1, transform: 2, output: 3 }

function layoutDefinitionNodes(rawNodes: any[]) {
  const used = new Set<string>()
  const hasOverlap = rawNodes.some(n => {
    const p = n.position
    if (!p) return true
    const key = `${Math.round(p.x)}:${Math.round(p.y)}`
    if (used.has(key)) return true
    used.add(key)
    return false
  })
  const sorted = [...rawNodes].sort((a, b) => (TYPE_ORDER[a.type] ?? 99) - (TYPE_ORDER[b.type] ?? 99))
  return rawNodes.map((n, index) => {
    const layoutIndex = sorted.findIndex(s => s.id === n.id)
    return {
      id: n.id,
      type: n.type,
      position: hasOverlap ? { x: 120 + layoutIndex * 240, y: 180 + (index % 2) * 20 } : n.position,
      data: { label: n.label || '', config: n.config || {} },
    }
  })
}

interface SelectedNodeData {
  id: string; type: string; label: string; config: Record<string, unknown>
}

export default function PipelineBuilderPage() {
  const { pipelineId } = useParams<{ pipelineId: string }>()
  const navigate = useNavigate()
  const reactFlowWrapper = useRef<HTMLDivElement>(null)
  const inspectorRef = useRef<HTMLDivElement>(null)
  const reactFlowInstanceRef = useRef<any>(null)

  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([])
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([])
  const [selectedNode, setSelectedNode] = useState<SelectedNodeData | null>(null)
  const [pipeline, setPipeline] = useState<Pipeline | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [running, setRunning] = useState(false)
  const [validation, setValidation] = useState<ValidateResult | null>(null)
  const [saveStatus, setSaveStatus] = useState<'saved' | 'unsaved' | 'saving'>('saved')
  const [publishResult, setPublishResult] = useState<{ version: number } | null>(null)
  const [publishError, setPublishError] = useState('')
  const [runResult, setRunResult] = useState<{ status: 'success' | 'failed'; rowsIn: number; rowsOut: number; error?: string } | null>(null)
  const [inspectorWidth, setInspectorWidth] = useState(288)
  const [toolbarCollapsed, setToolbarCollapsed] = useState(false)
  const isDraggingPanel = useRef(false)

  const handlePanelResizeStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault(); isDraggingPanel.current = true
    document.body.style.cursor = 'col-resize'; document.body.style.userSelect = 'none'
  }, [])
  useEffect(() => {
    const h = (e: MouseEvent) => {
      if (!isDraggingPanel.current) return
      setInspectorWidth(Math.max(240, Math.min(600, window.innerWidth - e.clientX - 48)))
    }
    const u = () => { isDraggingPanel.current = false; document.body.style.cursor = ''; document.body.style.userSelect = '' }
    window.addEventListener('mousemove', h); window.addEventListener('mouseup', u)
    return () => { window.removeEventListener('mousemove', h); window.removeEventListener('mouseup', u) }
  }, [])

  useEffect(() => {
    if (!pipelineId) return; setLoading(true)
    Promise.all([pipelinesApi.get(pipelineId), pipelinesApi.runs(pipelineId)])
      .then(([pl, runs]) => {
        setPipeline(pl)
        // runs 接口按 created_at DESC 返回；首项才是最近一次运行。
        const lastRun = Array.isArray(runs) && runs.length > 0 ? runs[0] : null
        if (lastRun) {
          pipelinesApi.getRun(lastRun.id).catch(() => {}).then((detail: any) => {
            const curatedIds = detail?.stats?.curated_dataset_ids || []
            const cid = detail?.stats?.curated_dataset_id || curatedIds[0]
            if (cid) setNodes(nds => nds.map(n => n.type === 'output' ? { ...n, data: { ...n.data, config: { ...(n.data as any).config || {}, curated_dataset_id: cid, curated_dataset_ids: curatedIds } } } : n))
          })
        }
        const def = pl.definition || { nodes: [], edges: [] }
        setNodes(layoutDefinitionNodes(def.nodes as any[] || []))
        setEdges((def.edges as any[] || []).map((e: any) => ({
          id: e.id, source: e.source, target: e.target, type: 'smoothstep', markerEnd: { type: MarkerType.ArrowClosed },
        })))
      }).catch(() => navigate('/data/pipelines')).finally(() => setLoading(false))
  }, [pipelineId])

  const saveDefinition = useCallback(async () => {
    if (!pipelineId) return; setSaving(true); setSaveStatus('saving')
    try {
      await pipelinesApi.update(pipelineId, { definition: { nodes: nodes.map(n => ({
        id: n.id, type: n.type, position: n.position, label: (n.data as any).label || '', config: (n.data as any).config || {},
      })), edges: edges.map(e => ({ id: e.id, source: e.source, target: e.target })) } as any })
      setSaveStatus('saved')
    } catch { setSaveStatus('unsaved') }
    finally { setSaving(false) }
  }, [pipelineId, nodes, edges])

  useEffect(() => { const h = (e: KeyboardEvent) => { if ((e.ctrlKey || e.metaKey) && e.key === 's') { e.preventDefault(); saveDefinition() } }; window.addEventListener('keydown', h); return () => window.removeEventListener('keydown', h) }, [saveDefinition])
  useEffect(() => { if (!loading) setSaveStatus('unsaved') }, [nodes, edges])

  const handleRun = async () => {
    if (!pipelineId) return; setRunning(true)
    setRunResult(null)
    setNodes(nds => nds.map(n => ({ ...n, data: { ...n.data, status: 'idle' } })))
    try {
      await saveDefinition()
      const def = (await pipelinesApi.get(pipelineId)).definition || { nodes: [], edges: [] }
      for (const nid of (def.nodes as any[] || []).map((n: any) => n.id)) {
        setNodes(nds => nds.map(n => n.id === nid ? { ...n, data: { ...n.data, status: 'running' } } : n))
        await new Promise(r => setTimeout(r, 300))
      }
      const result = await pipelinesApi.runSync(pipelineId)
      const nodeStatus = (result as any).stats?.node_status || {}
      const runSucceeded = (result as any).status === 'success'
      const curatedIds = (result as any).stats?.curated_dataset_ids || []
      const curatedId = (result as any).stats?.curated_dataset_id || curatedIds[0] || ''
      setNodes(nds => nds.map(n => {
        const base: any = { ...n.data, status: nodeStatus[n.id] || (runSucceeded ? 'success' : 'failed') }
        if (n.type === 'output' && curatedId) base.config = { ...((base as any).config || {}), curated_dataset_id: curatedId, curated_dataset_ids: curatedIds }
        return { ...n, data: base }
      }))
      setSelectedNode(prev => {
        if (!prev || prev.type !== 'output' || !curatedId) return prev
        return { ...prev, config: { ...prev.config, curated_dataset_id: curatedId, curated_dataset_ids: curatedIds } }
      })
      const stats = ((result as any).stats || {}) as Record<string, unknown>
      setRunResult(runSucceeded
        ? { status: 'success', rowsIn: Number(stats.rows_in ?? 0), rowsOut: Number(stats.rows_out ?? 0) }
        : { status: 'failed', rowsIn: 0, rowsOut: 0, error: String((result as any).error || '运行失败') })
      const pl = await pipelinesApi.get(pipelineId); setPipeline(pl)
    } catch (e: any) {
      setNodes(nds => nds.map(n => ({ ...n, data: { ...n.data, status: 'failed' } })))
      setRunResult({ status: 'failed', rowsIn: 0, rowsOut: 0, error: e?.detail || e?.message || '运行失败' })
    }
    finally { setRunning(false) }
  }

  const handleValidate = async () => { if (!pipelineId) return; try { setValidation(await pipelinesApi.validate(pipelineId)) } catch { setValidation({ valid: false, errors: [], warnings: [{ node_id: '', severity: 'error', message: '校验失败' }] }) } }
  const handlePublish = async () => {
    if (!pipelineId) return
    setPublishError('')
    try {
      await saveDefinition()
      const r = await pipelinesApi.publish(pipelineId)
      setPipeline(await pipelinesApi.get(pipelineId))
      setPublishResult({ version: r.version })
    } catch (e: any) {
      setPublishError(e?.detail || e?.message || '发布失败，请先通过校验')
    }
  }

  const onDragStart = useCallback((event: React.DragEvent, nodeType: string) => { event.dataTransfer.setData('application/reactflow', nodeType); event.dataTransfer.effectAllowed = 'move' }, [])
  const onDrop = useCallback((event: React.DragEvent) => {
    event.preventDefault(); const type = event.dataTransfer.getData('application/reactflow')
    if (!type || !NODE_DEFAULTS[type]) return
    const pos = reactFlowInstanceRef.current?.screenToFlowPosition({ x: event.clientX, y: event.clientY }) || { x: event.clientX - 150, y: event.clientY - 40 }
    const d = NODE_DEFAULTS[type]
    setNodes(nds => nds.concat({ id: `${type}_${Date.now()}`, type, position: pos, data: { label: `${d.label}_${nodes.length + 1}`, config: { ...d.config } } }))
  }, [nodes, setNodes])
  const onDragOver = useCallback((event: React.DragEvent) => { event.preventDefault(); event.dataTransfer.dropEffect = 'move' }, [])
  const onConnect = useCallback((conn: Connection) => { setEdges(eds => addEdge({ ...conn, id: `edge_${Date.now()}`, type: 'smoothstep', markerEnd: { type: MarkerType.ArrowClosed } }, eds)) }, [setEdges])
  const onNodeClick = useCallback((_: React.MouseEvent, node: Node) => { setSelectedNode({ id: node.id, type: node.type || '', label: (node.data as any).label || '', config: (node.data as any).config || {} }) }, [])
  const onPaneClick = useCallback(() => { setSelectedNode(null); setValidation(null) }, [])
  const updateNodeData = useCallback((nodeId: string, data: Record<string, unknown>) => {
    setNodes(nds => nds.map(n => n.id === nodeId ? { ...n, data: { ...n.data as any, ...data } } : n))
    setSelectedNode(prev => prev && prev.id === nodeId ? { ...prev, ...data } as any : prev)
  }, [setNodes])

  if (loading) return <div className="text-gray-400 text-sm p-8 text-center">加载 Pipeline...</div>
  if (!pipeline) return <div className="text-gray-400 text-sm p-8 text-center">Pipeline 未找到</div>
  // 数据管家托管的 n8n 流水线：没有画布 DSL，编排在数据管家对话完成
  if ((pipeline.definition as { engine?: string } | null)?.engine === 'n8n') {
    const rid = (pipeline.definition as { n8n?: { steward_id?: string } } | null)?.n8n?.steward_id
    return (
      <div className="flex flex-col items-center justify-center gap-3 p-16 text-center">
        <p className="text-sm font-medium text-gray-700">「{pipeline.name}」是数据管家托管的 n8n 流水线</p>
        <p className="text-xs text-gray-400">它没有画布节点，创建与编排在数据管家中通过对话完成；发布在编辑向导完成，启用由流水线列表开关控制</p>
        <button
          onClick={() => navigate(rid ? `/data/pipelines/steward?record=${encodeURIComponent(rid)}` : '/data/pipelines/steward')}
          className="mt-1 px-4 py-2 bg-violet-600 text-white text-sm rounded-lg hover:bg-violet-700"
        >
          前往数据管家
        </button>
      </div>
    )
  }
  // Python 脚本流水线：没有画布 DSL，脚本在专属编辑页维护
  if ((pipeline.definition as { engine?: string } | null)?.engine === 'python') {
    return (
      <div className="flex flex-col items-center justify-center gap-3 p-16 text-center">
        <p className="text-sm font-medium text-gray-700">「{pipeline.name}」是 Python 脚本流水线</p>
        <p className="text-xs text-gray-400">它没有画布节点，取数逻辑在脚本编辑页用 Python 编写；发布在编辑向导完成，启用由流水线列表开关控制</p>
        <button
          onClick={() => navigate(`/data/pipelines/script/${pipeline.id}`)}
          className="mt-1 px-4 py-2 bg-indigo-600 text-white text-sm rounded-lg hover:bg-indigo-700"
        >
          前往脚本编辑页
        </button>
      </div>
    )
  }

  return (
    <ReactFlowProvider>
      <div className="h-[calc(100vh-5rem)] flex flex-col -m-6">
        <div className="flex items-center gap-3 px-4 py-2 bg-white border-b shrink-0">
          <button onClick={() => navigate(-1)} className="p-1.5 rounded hover:bg-gray-100 text-gray-500"><ArrowLeft size={16} /></button>
          <span className="font-semibold text-sm">{pipeline.name}</span>
          <span className={`text-xs px-1.5 py-0.5 rounded border ${pipeline.status === 'published' ? 'bg-green-50 text-green-600 border-green-200' : pipeline.status === 'failed' ? 'bg-red-50 text-red-600 border-red-200' : 'bg-gray-100 text-gray-600 border-gray-200'}`}>
            {({ draft: '草稿', editing: '编辑中', running: '运行中', failed: '失败', published: '已发布' } as Record<string, string>)[pipeline.status] || pipeline.status}
          </span>
          <span className="text-xs text-gray-400">v{pipeline.version || 1}</span>
          {saveStatus === 'saving' && <span className="text-xs text-amber-500 ml-2">保存中...</span>}
          {saveStatus === 'saved' && <span className="text-xs text-green-500 ml-2">已保存</span>}
          {saveStatus === 'unsaved' && <span className="text-xs text-gray-400 ml-2">未保存</span>}
          <div className="flex-1" />
          <button onClick={saveDefinition} disabled={saving} className="flex items-center gap-1 px-3 py-1.5 text-xs border rounded-lg hover:bg-gray-50"><Save size={13} />保存</button>
          <button onClick={handleValidate} className="flex items-center gap-1 px-3 py-1.5 text-xs border rounded-lg hover:bg-gray-50"><CheckCircle size={13} />校验</button>
          <button onClick={handleRun} disabled={running} className="flex items-center gap-1 px-3 py-1.5 text-xs bg-gray-800 text-white rounded-lg hover:bg-black disabled:opacity-50">{running ? <Loader2 size={13} className="animate-spin" /> : <Play size={13} />}运行</button>
          <button onClick={handlePublish} className="flex items-center gap-1 px-3 py-1.5 text-xs bg-black text-white rounded-lg hover:bg-gray-800">发布</button>
        </div>
        {validation && !validation.valid && (<div className="bg-red-50 border-b border-red-200 px-4 py-2 shrink-0"><AlertTriangle size={12} className="inline mr-1 text-red-600" /><span className="text-xs text-red-600">校验未通过: {validation.errors.map((e: any) => e.message).join('; ')}</span></div>)}
        {validation && validation.valid && (
          <div className="bg-green-50 border-b border-green-200 px-4 py-2 shrink-0 flex items-center gap-1.5">
            <CheckCircle2 size={12} className="text-green-600" />
            <span className="text-xs text-green-700">
              校验通过{validation.warnings.length > 0 ? `（${validation.warnings.length} 条提醒：${validation.warnings.map((w: any) => w.message).join('；')}）` : '，可以运行或发布'}
            </span>
            <button onClick={() => setValidation(null)} className="ml-auto text-gray-400 hover:text-gray-600"><X size={12} /></button>
          </div>
        )}
        {publishError && (
          <div className="bg-red-50 border-b border-red-200 px-4 py-2 shrink-0 flex items-center gap-1.5">
            <XCircle size={12} className="text-red-600" />
            <span className="text-xs text-red-600">发布失败：{publishError}</span>
            <button onClick={() => setPublishError('')} className="ml-auto text-gray-400 hover:text-gray-600"><X size={12} /></button>
          </div>
        )}
        {runResult && (
          <div className={`border-b px-4 py-2 shrink-0 flex items-center gap-2 ${runResult.status === 'success' ? 'bg-green-50 border-green-200' : 'bg-red-50 border-red-200'}`}>
            {runResult.status === 'success' ? <CheckCircle2 size={13} className="text-green-600 shrink-0" /> : <XCircle size={13} className="text-red-600 shrink-0" />}
            {runResult.status === 'success' ? (
              <span className="text-xs text-green-700">测试运行成功：输入 {runResult.rowsIn} 行 → 输出 {runResult.rowsOut} 行（点击输出节点可预览产物）</span>
            ) : (
              <span className="text-xs text-red-600 truncate" title={runResult.error}>运行失败：{runResult.error}</span>
            )}
            {runResult.status === 'success' && (
              <button onClick={() => navigate('/data/structured')} className="flex items-center gap-1 text-xs px-2 py-0.5 bg-white border border-green-300 rounded hover:bg-green-100 shrink-0">
                去资产湖查看 <ArrowRight size={10} />
              </button>
            )}
            <button onClick={() => setRunResult(null)} className="ml-auto text-gray-400 hover:text-gray-600"><X size={12} /></button>
          </div>
        )}
        <div className="flex flex-1 overflow-hidden">
          <div className={`${toolbarCollapsed ? "w-10" : "w-48"} bg-gray-50 border-r p-1 space-y-1 shrink-0 transition-all duration-200 relative`}>
            <button onClick={() => setToolbarCollapsed(!toolbarCollapsed)}
              className="absolute -right-2 top-2 w-4 h-4 bg-white border rounded-full flex items-center justify-center text-gray-400 hover:text-black z-10">
              {toolbarCollapsed ? <ChevronRight size={10} /> : <ChevronLeft size={10} />}
            </button>
            {!toolbarCollapsed && <p className="text-xs font-medium text-gray-500 px-2 py-1">节点工具</p>}
            {TOOLS.map(t => (
              <div key={t.type} draggable onDragStart={e => onDragStart(e, t.type)}
                className={`flex items-center gap-2 px-3 py-2 rounded-lg text-xs cursor-grab active:cursor-grabbing hover:bg-white border border-transparent hover:border-gray-200 ${toolbarCollapsed ? "justify-center px-1" : ""}`} title={t.label}>
                <span className="w-2.5 h-2.5 rounded-full shrink-0" style={{ backgroundColor: NODE_DEFAULTS[t.type]?.color }} />
                {!toolbarCollapsed && <div><p className="font-medium">{t.label}</p><p className="text-gray-400">{t.desc}</p></div>}
              </div>
            ))}
          </div>
          <div ref={reactFlowWrapper} className="flex-1" onDrop={onDrop} onDragOver={onDragOver}>
            <ReactFlow nodes={nodes} edges={edges} onNodesChange={onNodesChange as OnNodesChange} onEdgesChange={onEdgesChange as OnEdgesChange} onConnect={onConnect} onNodeClick={onNodeClick} onPaneClick={onPaneClick} nodeTypes={nodeTypes} fitView deleteKeyCode={['Backspace', 'Delete']} snapToGrid snapGrid={[15, 15]} onInit={(inst) => { reactFlowInstanceRef.current = inst }}>
              <Controls /><MiniMap nodeStrokeWidth={3} nodeColor={n => NODE_DEFAULTS[n.type || '']?.color || '#666'} style={{ width: 150, height: 100 }} /><Background color="#f0f0f0" gap={15} />
            </ReactFlow>
          </div>
          <div ref={inspectorRef} className="relative bg-white border-l overflow-y-auto shrink-0" style={{ width: inspectorWidth }}>
            <div onMouseDown={handlePanelResizeStart} className="absolute left-0 top-0 bottom-0 w-1.5 cursor-col-resize hover:bg-blue-400 active:bg-blue-500 transition-colors z-10 group"><div className="absolute left-0.5 top-1/2 -translate-y-1/2 w-0.5 h-8 bg-gray-300 rounded-full group-hover:bg-white" /></div>
            {selectedNode ? (<NodeInspector nodeData={selectedNode} onUpdate={(data) => updateNodeData(selectedNode.id, data)} onClose={() => setSelectedNode(null)} pipelineId={pipelineId} />) : (<div className="p-4 text-center text-gray-400 text-xs mt-8">点击节点查看配置</div>)}
          </div>
        </div>

        {/* 发布成功引导弹窗 */}
        {publishResult && (
          <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50" onClick={() => setPublishResult(null)}>
            <div className="bg-white rounded-xl shadow-lg p-6 w-[440px]" onClick={e => e.stopPropagation()}>
              <div className="flex items-center gap-2.5 mb-3">
                <div className="w-9 h-9 rounded-full bg-green-100 flex items-center justify-center shrink-0">
                  <CheckCircle2 size={18} className="text-green-600" />
                </div>
                <div>
                  <h3 className="font-semibold text-gray-900">发布成功（v{publishResult.version}）</h3>
                  <p className="text-xs text-gray-500 mt-0.5">「{pipeline.name}」现在可以被数据任务池触发了</p>
                </div>
              </div>
              <div className="bg-gray-50 rounded-lg p-3 text-xs text-gray-600 leading-relaxed mb-4">
                下一步建议：到<b>数据任务池</b>创建同步任务，把数据源按计划同步进平台，并在任务中挂接这条流水线——同步完成后自动加工数据，产物进入<b>数据资产湖</b>。
              </div>
              <div className="flex justify-end gap-2">
                <button onClick={() => setPublishResult(null)} className="px-4 py-2 border rounded-lg text-sm hover:bg-gray-50">
                  继续编辑
                </button>
                <button
                  onClick={() => navigate(`/data/pipelines/sync-tasks?pipeline=${pipelineId}`)}
                  className="flex items-center gap-1.5 px-4 py-2 bg-[var(--color-nav-bg)] text-white rounded-lg text-sm hover:opacity-90"
                >
                  <Repeat size={13} /> 去任务池创建任务
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </ReactFlowProvider>
  )
}

/** ── NodeInspector: 编辑/确认/取消 三态 ── */
function NodeInspector({ nodeData, onUpdate, onClose, pipelineId }: { nodeData: SelectedNodeData; onUpdate: (data: Record<string, unknown>) => void; onClose: () => void; pipelineId?: string }) {
  const [editing, setEditing] = useState(false)
  const [localConfig, setLocalConfig] = useState<Record<string, unknown>>(nodeData.config || {})
  const [localLabel, setLocalLabel] = useState(nodeData.label)

  useEffect(() => { setLocalConfig(nodeData.config || {}); setLocalLabel(nodeData.label) }, [nodeData.id])

  const handleChange = (key: string, value: unknown) => { setLocalConfig(p => ({ ...p, [key]: value })) }

  const handleConfirm = () => {
    onUpdate({ config: localConfig, label: localLabel })
    setEditing(false)
  }
  const handleCancel = () => {
    setLocalConfig(nodeData.config || {})
    setLocalLabel(nodeData.label)
    setEditing(false)
  }

  const Inspectors: Record<string, any> = {
    connector: ConnectorInspector, storage: StorageInspector,
    transform: TransformInspector, output: OutputInspector,
  }
  const InspectorComponent = Inspectors[nodeData.type]

  return (
    <div className="p-4">
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2"><span className="w-2.5 h-2.5 rounded-full" style={{ backgroundColor: NODE_DEFAULTS[nodeData.type]?.color || '#666' }} /><span className="font-medium text-sm">{nodeData.label}</span></div>
        <button onClick={onClose} className="text-gray-400 hover:text-black"><X size={14} /></button>
      </div>
      <div className="space-y-3">
        <div>
          <label className="text-xs text-gray-500 mb-1 block">节点名称</label>
          {editing ? (
            <input value={localLabel} onChange={e => setLocalLabel(e.target.value)} className="w-full border rounded-lg px-3 py-1.5 text-sm" />
          ) : (
            <p className="text-sm text-gray-700 bg-gray-50 rounded-lg px-3 py-1.5">{localLabel}</p>
          )}
        </div>
        {InspectorComponent && <InspectorComponent config={localConfig} onChange={handleChange} readOnly={!editing} pipelineId={pipelineId} />}
      </div>
      <div className="flex gap-2 mt-4">
        {editing ? (<>
          <button onClick={handleCancel} className="flex-1 px-3 py-2 border rounded-lg text-sm text-gray-600 hover:bg-gray-50">取消</button>
          <button onClick={handleConfirm} className="flex-1 px-3 py-2 bg-black text-white rounded-lg text-sm hover:bg-gray-800">确认</button>
        </>) : (
          <button onClick={() => setEditing(true)} className="w-full flex items-center justify-center gap-1.5 px-3 py-2 border rounded-lg text-sm text-gray-700 hover:bg-gray-50"><Pencil size={13} />编辑</button>
        )}
      </div>
    </div>
  )
}
