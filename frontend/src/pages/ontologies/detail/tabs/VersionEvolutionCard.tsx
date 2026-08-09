import {
  useEffect, useMemo, useRef, useState,
  type PointerEvent as ReactPointerEvent,
  type WheelEvent as ReactWheelEvent,
} from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  Focus, Maximize2, Minus, Pause, Play, Plus, RotateCcw, StepForward,
} from 'lucide-react'
import {
  ontologyVersionApi,
  type OntologyVersionNode,
} from '@/api/v2/ontology-versions'

type EvolutionStage = 'draft' | 'trial' | 'release'
type EdgeKind = 'branch' | 'main' | 'merge'
type ViewMode = 'focus' | 'full'

interface LayoutNode extends OntologyVersionNode {
  col: number
  lane: number
  x: number
  y: number
  stage: EvolutionStage
  isCurrent: boolean
}

interface LayoutEdge {
  id: string
  from: string
  to: string
  kind: EdgeKind
  col: number
}

interface EvolutionStep {
  nodeId: string
  text: string
}

interface ViewTransform {
  x: number
  y: number
  k: number
}

const COL_GAP = 88
const LANE_GAP = 54
const FIT_PADDING = 34
const FOCUS_HISTORY_WINDOW = 6

const stageOf = (node: OntologyVersionNode): EvolutionStage => {
  if (node.node_kind === 'release') return 'release'
  if (node.lifecycle_status === 'trial_ready' || node.lifecycle_status === 'superseded') return 'trial'
  return 'draft'
}

const stageLabel: Record<EvolutionStage, string> = {
  draft: '草稿态',
  trial: '试跑态',
  release: '发布态',
}

const timestamp = (node: OntologyVersionNode) => {
  const value = new Date(node.created_at || node.published_at || 0).getTime()
  return Number.isFinite(value) ? value : 0
}

function buildLayout(versions: OntologyVersionNode[], currentReleaseId: string | undefined) {
  const sorted = [...versions].sort((a, b) => timestamp(a) - timestamp(b)
    || a.version_number.localeCompare(b.version_number, undefined, { numeric: true }))
  const sourceNodes = new Map(sorted.map(node => [node.id, node]))
  const siblingOrder = new Map<string, number>()
  const laneById = new Map<string, number>()
  const lanes: number[] = []

  sorted.forEach(node => {
    if (node.node_kind === 'release') {
      laneById.set(node.id, 0)
      lanes.push(0)
      return
    }
    const parentLane = node.parent_version_id ? laneById.get(node.parent_version_id) : undefined
    let lane: number
    if (parentLane && parentLane !== 0) {
      lane = parentLane + Math.sign(parentLane)
    } else {
      const siblingKey = node.parent_version_id || '__root__'
      const index = siblingOrder.get(siblingKey) || 0
      const distance = Math.floor(index / 2) + 1
      lane = index % 2 === 0 ? distance : -distance
      siblingOrder.set(siblingKey, index + 1)
    }
    laneById.set(node.id, lane)
    lanes.push(lane)
  })

  const maxLane = Math.max(1, ...lanes.map(lane => Math.abs(lane)))
  const originY = 54 + maxLane * LANE_GAP
  const nodes: LayoutNode[] = sorted.map((node, col) => ({
    ...node,
    col,
    lane: laneById.get(node.id) || 0,
    x: 54 + col * COL_GAP,
    y: originY - (laneById.get(node.id) || 0) * LANE_GAP,
    stage: stageOf(node),
    isCurrent: node.id === currentReleaseId,
  }))
  const nodeById = new Map(nodes.map(node => [node.id, node]))
  const edges: LayoutEdge[] = []

  nodes.forEach(node => {
    if (node.parent_version_id && sourceNodes.has(node.parent_version_id)) {
      const parent = nodeById.get(node.parent_version_id)
      edges.push({
        id: `parent-${node.parent_version_id}-${node.id}`,
        from: node.parent_version_id,
        to: node.id,
        kind: node.node_kind === 'release' && parent?.node_kind === 'release' ? 'main' : 'branch',
        col: node.col,
      })
    }
    if (node.promoted_from_id && sourceNodes.has(node.promoted_from_id)) {
      edges.push({
        id: `merge-${node.promoted_from_id}-${node.id}`,
        from: node.promoted_from_id,
        to: node.id,
        kind: 'merge',
        col: node.col,
      })
    }
  })

  const nodeNumber = new Map(nodes.map(node => [node.id, node.version_number]))
  const steps: EvolutionStep[] = nodes.map(node => {
    const parentVersion = node.parent_version_id ? nodeNumber.get(node.parent_version_id) : undefined
    if (node.node_kind === 'release') {
      const promoted = node.promoted_from_id ? nodeNumber.get(node.promoted_from_id) : undefined
      return {
        nodeId: node.id,
        text: promoted
          ? `${promoted} 完成验证并晋级，发布 ${node.version_number}${node.isCurrent ? '（当前运行版本）' : ''}`
          : `${node.version_number} 建立稳定发布基线${node.isCurrent ? '（当前运行版本）' : ''}`,
      }
    }
    if (node.stage === 'trial') {
      return { nodeId: node.id, text: `${node.version_number} 已完成隔离试跑，等待发布决策` }
    }
    return {
      nodeId: node.id,
      text: `${node.version_number}${parentVersion ? ` 从 ${parentVersion}` : ''} 派生，进入草稿态`,
    }
  })

  const xs = nodes.map(node => node.x)
  const ys = nodes.map(node => node.y)
  const bounds = nodes.length ? {
    minX: Math.min(...xs) - 38,
    minY: Math.min(...ys) - 38,
    maxX: Math.max(...xs) + 46,
    maxY: Math.max(...ys) + 38,
  } : { minX: 0, minY: 0, maxX: 320, maxY: 180 }

  return { nodes, nodeById, edges, steps, bounds }
}

function edgePath(from: LayoutNode, to: LayoutNode, kind: EdgeKind) {
  if (kind === 'main') return `M ${from.x} ${from.y} L ${to.x} ${to.y}`
  const dx = Math.max(Math.abs(to.x - from.x) * .55, 30)
  return `M ${from.x} ${from.y} C ${from.x + dx} ${from.y}, ${to.x - dx} ${to.y}, ${to.x} ${to.y}`
}

function fittedView(width: number, height: number, bounds: ReturnType<typeof buildLayout>['bounds']): ViewTransform {
  const graphWidth = Math.max(bounds.maxX - bounds.minX, 1)
  const graphHeight = Math.max(bounds.maxY - bounds.minY, 1)
  const k = Math.min(
    Math.max((width - FIT_PADDING * 2) / graphWidth, .2),
    Math.max((height - FIT_PADDING * 2) / graphHeight, .2),
    1.12,
  )
  return {
    k,
    x: (width - graphWidth * k) / 2 - bounds.minX * k,
    y: (height - graphHeight * k) / 2 - bounds.minY * k,
  }
}

export default function VersionEvolutionCard({ ontologyId }: { ontologyId: string }) {
  const treeQuery = useQuery({
    queryKey: ['version-tree', ontologyId],
    queryFn: () => ontologyVersionApi.tree(ontologyId),
  })
  const graph = useMemo(
    () => buildLayout(treeQuery.data?.versions || [], treeQuery.data?.current_release_id),
    [treeQuery.data],
  )
  const graphSignature = graph.nodes
    .map(node => `${node.id}:${node.lifecycle_status}:${node.latest_trial?.status || ''}`)
    .join('|')
  const canvasRef = useRef<HTMLDivElement>(null)
  const dragRef = useRef<{ x: number; y: number } | null>(null)
  const completionTimerRef = useRef<number | null>(null)
  const [canvasSize, setCanvasSize] = useState({ width: 480, height: 188 })
  const [view, setView] = useState<ViewTransform>({ x: 0, y: 0, k: 1 })
  const [visibleStep, setVisibleStep] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [mode, setMode] = useState<ViewMode>('full')
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null)

  const fit = (animate = true) => {
    const element = canvasRef.current
    const width = element?.clientWidth || canvasSize.width
    const height = element?.clientHeight || canvasSize.height
    if (!animate) element?.classList.add('is-direct')
    setView(fittedView(width, height, graph.bounds))
    if (!animate) window.requestAnimationFrame(() => element?.classList.remove('is-direct'))
  }

  const focusNode = (nodeId: string, targetScale = .96) => {
    const node = graph.nodeById.get(nodeId)
    const element = canvasRef.current
    if (!node || !element) return
    setView(current => ({
      k: targetScale || current.k,
      x: element.clientWidth / 2 - node.x * (targetScale || current.k),
      y: element.clientHeight / 2 - node.y * (targetScale || current.k),
    }))
  }

  useEffect(() => {
    const element = canvasRef.current
    if (!element) return
    const observer = new ResizeObserver(entries => {
      const rect = entries[0]?.contentRect
      if (!rect) return
      setCanvasSize({ width: rect.width, height: rect.height })
      setView(fittedView(rect.width, rect.height, graph.bounds))
    })
    observer.observe(element)
    return () => observer.disconnect()
  }, [graph.bounds.minX, graph.bounds.minY, graph.bounds.maxX, graph.bounds.maxY])

  useEffect(() => {
    if (!graph.nodes.length) return
    setPlaying(false)
    setMode('full')
    setVisibleStep(graph.steps.length)
    setSelectedNodeId(null)
    window.requestAnimationFrame(() => fit(false))
  }, [ontologyId, graphSignature])

  useEffect(() => {
    if (!playing) return
    if (visibleStep >= graph.steps.length) {
      setPlaying(false)
      completionTimerRef.current = window.setTimeout(() => {
        setMode('full')
        setSelectedNodeId(null)
        fit()
      }, 900)
      return
    }
    const timer = window.setTimeout(() => setVisibleStep(step => step + 1), 900)
    return () => window.clearTimeout(timer)
  }, [playing, visibleStep, graph.steps.length])

  useEffect(() => () => {
    if (completionTimerRef.current !== null) window.clearTimeout(completionTimerRef.current)
  }, [])

  useEffect(() => {
    if (mode !== 'focus' || visibleStep === 0) return
    const nodeId = graph.steps[Math.min(visibleStep, graph.steps.length) - 1]?.nodeId
    if (nodeId) focusNode(nodeId)
  }, [visibleStep, mode])

  const visibleNodes = graph.nodes.slice(0, visibleStep)
  const visibleIds = new Set(visibleNodes.map(node => node.id))
  const visibleEdges = graph.edges.filter(edge => visibleIds.has(edge.from) && visibleIds.has(edge.to))
  const latestVisibleCol = visibleNodes.at(-1)?.col || 0
  const activeStep = visibleStep > 0 ? graph.steps[Math.min(visibleStep, graph.steps.length) - 1] : null
  const selectedNode = selectedNodeId ? graph.nodeById.get(selectedNodeId) : null
  const statusText = selectedNode
    ? `${selectedNode.version_number} · ${stageLabel[selectedNode.stage]}${selectedNode.version_label ? ` · ${selectedNode.version_label}` : ''}`
    : visibleStep === 0
      ? '准备播放 · 逐步回看真实版本演化'
      : visibleStep >= graph.steps.length && mode === 'full'
        ? `已加载完整版本结构 · ${graph.nodes.length} 个快照节点`
        : activeStep?.text || '版本演化已就绪'
  const viewportWorld = {
    x: -view.x / view.k,
    y: -view.y / view.k,
    width: canvasSize.width / view.k,
    height: canvasSize.height / view.k,
  }
  const miniWidth = graph.bounds.maxX - graph.bounds.minX
  const miniHeight = graph.bounds.maxY - graph.bounds.minY

  const zoomAt = (cx: number, cy: number, factor: number) => {
    setView(current => {
      const nextScale = Math.min(3, Math.max(.2, current.k * factor))
      const ratio = nextScale / current.k
      return {
        k: nextScale,
        x: cx - (cx - current.x) * ratio,
        y: cy - (cy - current.y) * ratio,
      }
    })
  }

  const onPointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    if ((event.target as Element).closest('.evolution-minimap, .evolution-zoom-controls')) return
    dragRef.current = { x: event.clientX, y: event.clientY }
    event.currentTarget.setPointerCapture(event.pointerId)
  }

  const onPointerMove = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (!dragRef.current) return
    const deltaX = event.clientX - dragRef.current.x
    const deltaY = event.clientY - dragRef.current.y
    dragRef.current = { x: event.clientX, y: event.clientY }
    setView(current => ({ ...current, x: current.x + deltaX, y: current.y + deltaY }))
  }

  const onWheel = (event: ReactWheelEvent<HTMLDivElement>) => {
    event.preventDefault()
    const rect = event.currentTarget.getBoundingClientRect()
    zoomAt(event.clientX - rect.left, event.clientY - rect.top, event.deltaY < 0 ? 1.12 : 1 / 1.12)
  }

  const play = () => {
    if (!graph.steps.length) return
    if (completionTimerRef.current !== null) window.clearTimeout(completionTimerRef.current)
    setSelectedNodeId(null)
    setMode('focus')
    if (visibleStep >= graph.steps.length) setVisibleStep(1)
    setPlaying(true)
  }

  const reset = () => {
    if (completionTimerRef.current !== null) window.clearTimeout(completionTimerRef.current)
    setPlaying(false)
    setMode('focus')
    setSelectedNodeId(null)
    setVisibleStep(0)
    const firstNode = graph.nodes[0]
    if (firstNode) window.requestAnimationFrame(() => focusNode(firstNode.id))
  }

  const showFull = () => {
    if (completionTimerRef.current !== null) window.clearTimeout(completionTimerRef.current)
    setPlaying(false)
    setSelectedNodeId(null)
    setVisibleStep(graph.steps.length)
    setMode('full')
    window.requestAnimationFrame(() => fit())
  }

  const showFocus = () => {
    const target = graph.nodes.find(node => node.isCurrent) || visibleNodes.at(-1) || graph.nodes.at(-1)
    if (!target) return
    setMode('focus')
    setSelectedNodeId(target.id)
    focusNode(target.id)
  }

  const onMiniMapPointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    event.stopPropagation()
    const rect = event.currentTarget.getBoundingClientRect()
    const worldX = graph.bounds.minX + (event.clientX - rect.left) / rect.width * miniWidth
    const worldY = graph.bounds.minY + (event.clientY - rect.top) / rect.height * miniHeight
    setMode('full')
    setSelectedNodeId(null)
    setView(current => ({
      ...current,
      x: canvasSize.width / 2 - worldX * current.k,
      y: canvasSize.height / 2 - worldY * current.k,
    }))
  }

  if (treeQuery.isLoading) {
    return (
      <section className="version-evolution-card is-loading" aria-label="版本演化加载中">
        <div className="evolution-skeleton-line" />
        <div className="evolution-skeleton-canvas" />
      </section>
    )
  }

  if (treeQuery.isError || !graph.nodes.length) {
    return (
      <section className="version-evolution-card evolution-empty" aria-label="版本演化">
        <GitBranchFallback />
        <div><strong>{treeQuery.isError ? '版本演化读取失败' : '暂无版本演化记录'}</strong><span>{treeQuery.isError ? '请刷新页面后重试' : '创建版本分支后将在这里形成演化轨迹'}</span></div>
      </section>
    )
  }

  return (
    <section className="version-evolution-card" data-testid="version-evolution-card" aria-label="版本演化">
      <header className="evolution-header">
        <div className="evolution-title"><i />版本演化</div>
        <div className="evolution-legend" aria-label="版本状态图例">
          <span><i className="is-draft" />草稿</span>
          <span><i className="is-trial" />试跑</span>
          <span><i className="is-release" />发布</span>
        </div>
      </header>

      <div
        ref={canvasRef}
        className="evolution-canvas"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={() => { dragRef.current = null }}
        onPointerCancel={() => { dragRef.current = null }}
        onWheel={onWheel}
      >
        <svg className={view.k < .5 ? 'is-low-detail' : ''} aria-label="本体版本分支演化图">
          <g className="evolution-viewport" transform={`translate(${view.x} ${view.y}) scale(${view.k})`}>
            <g className="evolution-edge-layer">
              {visibleEdges.map(edge => {
                const from = graph.nodeById.get(edge.from)!
                const to = graph.nodeById.get(edge.to)!
                const dimmed = mode === 'focus' && edge.col < latestVisibleCol - FOCUS_HISTORY_WINDOW
                return <path key={edge.id} className={`evolution-edge is-${edge.kind}${dimmed ? ' is-dimmed' : ''}`} d={edgePath(from, to, edge.kind)} />
              })}
            </g>
            <g className="evolution-node-layer">
              {visibleNodes.map(node => {
                const radius = node.node_kind === 'release' ? 13 : 10.5
                const dimmed = mode === 'focus' && node.col < latestVisibleCol - FOCUS_HISTORY_WINDOW
                return (
                  <g
                    key={node.id}
                    data-testid={`overview-version-node-${node.version_number}`}
                    className={`evolution-node is-${node.stage}${node.isCurrent ? ' is-current' : ''}${dimmed ? ' is-dimmed' : ''}${selectedNodeId === node.id ? ' is-selected' : ''}`}
                    role="button"
                    tabIndex={0}
                    aria-label={`${node.version_number} ${stageLabel[node.stage]}${node.isCurrent ? ' 当前发布' : ''}`}
                    onClick={event => {
                      event.stopPropagation()
                      setSelectedNodeId(node.id)
                      setMode('focus')
                      focusNode(node.id)
                    }}
                    onKeyDown={event => {
                      if (event.key !== 'Enter' && event.key !== ' ') return
                      event.preventDefault()
                      setSelectedNodeId(node.id)
                      setMode('focus')
                      focusNode(node.id)
                    }}
                  >
                    <title>{`${node.version_number} · ${stageLabel[node.stage]}${node.version_label ? ` · ${node.version_label}` : ''}`}</title>
                    <circle className="evolution-node-halo" cx={node.x} cy={node.y} r={radius + 4} />
                    <circle className="evolution-node-core" cx={node.x} cy={node.y} r={radius} />
                    {node.isCurrent && <text className="evolution-main-tag" x={node.x} y={node.y - radius - 8}>当前</text>}
                    <text className="evolution-node-label" x={node.x} y={node.y + radius + 15}>{node.version_number}</text>
                  </g>
                )
              })}
            </g>
          </g>
        </svg>

        <div
          className="evolution-minimap"
          role="button"
          tabIndex={0}
          title="小地图 · 点击跳转"
          aria-label="版本演化小地图，点击跳转"
          onPointerDown={onMiniMapPointerDown}
          onKeyDown={event => {
            if (event.key !== 'Enter' && event.key !== ' ') return
            event.preventDefault()
            setMode('full')
            setSelectedNodeId(null)
            fit()
          }}
        >
          <svg viewBox={`${graph.bounds.minX} ${graph.bounds.minY} ${miniWidth} ${miniHeight}`} preserveAspectRatio="none" aria-hidden="true">
            {visibleEdges.map(edge => {
              const from = graph.nodeById.get(edge.from)!
              const to = graph.nodeById.get(edge.to)!
              return <path key={edge.id} className={`mini-edge is-${edge.kind}`} d={edgePath(from, to, edge.kind)} />
            })}
            {visibleNodes.map(node => <circle key={node.id} className={`mini-node is-${node.stage}`} cx={node.x} cy={node.y} r={node.node_kind === 'release' ? 6 : 4.5} />)}
            <rect className="mini-viewport" x={viewportWorld.x} y={viewportWorld.y} width={viewportWorld.width} height={viewportWorld.height} rx="3" />
          </svg>
        </div>

        <div className="evolution-zoom-controls" aria-label="画布缩放">
          <button type="button" onClick={() => zoomAt(canvasSize.width / 2, canvasSize.height / 2, 1.25)} title="放大" aria-label="放大版本演化图"><Plus size={13} /></button>
          <button type="button" onClick={() => zoomAt(canvasSize.width / 2, canvasSize.height / 2, .8)} title="缩小" aria-label="缩小版本演化图"><Minus size={13} /></button>
          <button type="button" onClick={() => { setMode('full'); setSelectedNodeId(null); fit() }} title="适应画布" aria-label="适应画布"><Maximize2 size={13} /></button>
        </div>
        <div className="evolution-status" role="status" aria-live="polite">{statusText}</div>
        <div className="evolution-hint">拖拽平移 · 滚轮缩放</div>
      </div>

      {graph.steps.length > 1 && (
      <footer className="evolution-footer">
        <button type="button" className="evolution-play-button" onClick={() => playing ? setPlaying(false) : play()}>
          {playing ? <><Pause size={12} fill="currentColor" />暂停</> : <><Play size={12} fill="currentColor" />播放</>}
        </button>
        <button type="button" onClick={() => {
          setPlaying(false)
          setSelectedNodeId(null)
          setMode('focus')
          setVisibleStep(step => Math.min(step + 1, graph.steps.length))
        }} title="单步推进"><StepForward size={12} />单步</button>
        <button type="button" className={mode === 'focus' ? 'is-active' : ''} onClick={showFocus} title="聚焦当前发布"><Focus size={12} />聚焦</button>
        <button type="button" className={mode === 'full' ? 'is-active' : ''} onClick={showFull} title="展示完整结构"><Maximize2 size={12} />全貌</button>
        <button type="button" onClick={reset} title="重置演示" aria-label="重置版本演化演示"><RotateCcw size={12} /></button>
        <div className="evolution-progress" aria-hidden="true"><i style={{ width: `${graph.steps.length ? visibleStep / graph.steps.length * 100 : 0}%` }} /></div>
        <span className="evolution-step-label">{visibleStep} / {graph.steps.length}</span>
      </footer>
      )}
    </section>
  )
}

function GitBranchFallback() {
  return (
    <svg viewBox="0 0 32 32" aria-hidden="true">
      <circle cx="9" cy="7" r="3" /><circle cx="23" cy="10" r="3" /><circle cx="9" cy="25" r="3" />
      <path d="M9 10v12M12 13c7 0 8-3 8-3" />
    </svg>
  )
}
