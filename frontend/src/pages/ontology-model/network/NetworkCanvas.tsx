/**
 * 本体网络画布：cytoscape 渲染跨本体全局图（graphify 布局语言 + 浅色系）。
 *
 * 渲染语言对齐 Graphify-Labs/graphify 的 HTML 导出（vis.js Network）：
 * - 社区配色点状节点、力导向物理成簇、度数决定大小、边降噪与标签分级；
 * - 配色采用浅色系：浅底深字，关系边用中灰半透明，保证与平台整体观感一致；
 * - 力导向物理布局：用确定性聚类坐标做种子，再由 cose 弹簧-斥力模型收敛，
 *   兼顾"有机生长感"与"同一数据簇不乱飞"的稳定性；
 * - 节点直径随度数放大（graphify 的 size = f(degree)）；
 * - 边默认弱化（低透明度细线 + 小箭头 + 隐藏标签），悬停/路径高亮才显性化；
 * - 实例标签在低缩放下隐藏，避免小节点标签糊成一片。
 * 组件本身无业务状态，高亮集合与选中回调全部由页面注入。
 */
import { useEffect, useMemo, useRef } from 'react'
import cytoscape, { type Core, type ElementDefinition } from 'cytoscape'
import type {
  NetworkGraphData,
  NetworkGraphEdge,
  NetworkGraphNode,
} from '@/api/ontologyNetwork'
import {
  clusterPositions,
  degreeMap,
  maxDegreeOf,
  nodeSize,
  ontologyColorMap,
  separateOverlaps,
} from './networkModel'

const CANVAS_BG = '#f6f8fc'
const FALLBACK_COLORS = ['#4E79A7', '#F28E2B', '#E15759', '#76B7B2', '#59A14F']
/** 实例标签显性化所需的最低缩放（低于此值只显示对象类型标签）。 */
const INSTANCE_LABEL_ZOOM = 0.55

export interface NetworkCanvasHighlight {
  pathNodeIds?: Set<string>
  pathEdgeIds?: Set<string>
  directImpactIds?: Set<string>
  indirectImpactIds?: Set<string>
  changeNodeId?: string
  selectedNodeId?: string
}

interface Props {
  nodes: NetworkGraphNode[]
  edges: NetworkGraphEdge[]
  sections: NetworkGraphData['ontologies']
  highlight?: NetworkCanvasHighlight
  onSelect?: (nodeId: string) => void
  onBackgroundTap?: () => void
  /** 暴露 cytoscape 实例给页面（缩放/聚焦工具条）。 */
  onReady?: (cy: Core | null) => void
  /** 跨重建的位置缓存：数据刷新（搜索/层级切换）时已有节点不重新飞位。 */
  positionsRef?: React.MutableRefObject<Map<string, { x: number; y: number }>>
}

export default function NetworkCanvas(
  { nodes, edges, sections, highlight, onSelect, onBackgroundTap, onReady, positionsRef }: Props,
) {
  const hostRef = useRef<HTMLDivElement>(null)
  const cyRef = useRef<Core | null>(null)
  const colorByOntology = useMemo(() => ontologyColorMap(sections), [sections])
  const degrees = useMemo(() => degreeMap(edges), [edges])
  const maxDegree = useMemo(() => maxDegreeOf(edges), [edges])

  // ---- 画布构建：仅在图数据（节点/边/本体清单）变化时整体重建 ----
  useEffect(() => {
    const host = hostRef.current
    if (!host) return
    const cache = positionsRef?.current
    const seeded = clusterPositions(nodes)
    const positions = new Map(seeded)
    if (cache) {
      // 缓存优先：数据刷新时保留用户已经"盘熟"的布局，只让新增节点参与收敛
      for (const [id, position] of cache) positions.set(id, position)
    }
    const cachedRatio = nodes.length === 0 ? 0
      : nodes.filter(node => cache?.has(node.id)).length / nodes.length

    const sizeById = new Map<string, number>()

    const elements: ElementDefinition[] = [
      ...nodes.map(node => {
        const size = nodeSize(node, degrees.get(node.id) || 0, maxDegree)
        sizeById.set(node.id, size)
        return {
        group: 'nodes' as const,
        data: {
          id: node.id,
          label: node.label,
          kind: node.kind,
          size,
          color: colorByOntology.get(node.ontologyId) || FALLBACK_COLORS[0],
        },
        position: positions.get(node.id),
        classes: node.kind === 'object_type' ? 'object-type' : 'instance',
        }
      }),
      ...edges.map(edge => ({
        group: 'edges' as const,
        data: {
          id: edge.id,
          source: edge.source,
          target: edge.target,
          label: edge.label,
          kind: edge.kind,
        },
        classes: edge.kind.replace('_', '-'),
      })),
    ]

    const cy = cytoscape({
      container: host,
      elements,
      minZoom: 0.06,
      maxZoom: 3,
      boxSelectionEnabled: false,
      wheelSensitivity: 0.25,
      style: [
        {
          selector: 'node',
          style: {
            shape: 'ellipse',
            width: 'data(size)',
            height: 'data(size)',
            'background-color': 'data(color)',
            'border-color': 'data(color)',
            'border-width': 1.2,
            'border-opacity': 0.9,
            color: '#334155',
            label: 'data(label)',
            'font-size': 11,
            'font-weight': 600,
            'text-wrap': 'ellipsis',
            'text-max-width': '110px',
            'text-valign': 'bottom',
            'text-margin-y': 5,
            'text-background-color': CANVAS_BG,
            'text-background-opacity': 0.72,
            'text-background-padding': '2px',
            'text-background-shape': 'roundrectangle',
            'overlay-opacity': 0,
            'transition-property': 'border-color, border-width, opacity, background-color',
            'transition-duration': 160,
          },
        },
        {
          // graphify：只有高度数节点默认带标签；这里对象类型始终显示
          selector: 'node.instance',
          style: { 'font-size': 10, 'font-weight': 500 },
        },
        {
          selector: 'node.label-muted',
          style: { label: '', },
        },
        {
          selector: 'edge',
          style: {
            width: 1.4,
            'line-color': 'rgba(100,116,139,0.34)',
            'target-arrow-color': 'rgba(100,116,139,0.34)',
            'target-arrow-shape': 'triangle',
            'arrow-scale': 0.55,
            'curve-style': 'bezier',
            'control-point-step-size': 36,
            label: '',
            'font-size': 9,
            color: '#b9b9d6',
            'text-background-color': CANVAS_BG,
            'text-background-opacity': 0.85,
            'text-background-padding': '2px',
            'text-rotation': 'autorotate',
            'overlay-opacity': 0,
            'transition-property': 'line-color, width, opacity',
            'transition-duration': 160,
          },
        },
        {
          selector: 'edge.relation',
          style: { width: 1.7, 'line-color': 'rgba(71,85,105,0.42)', 'target-arrow-color': 'rgba(71,85,105,0.42)' },
        },
        {
          selector: 'edge.schema-relation',
          style: {
            width: 1.2,
            'line-style': 'dashed',
            'line-dash-pattern': [5, 4],
            'line-color': 'rgba(100,116,139,0.30)',
            'target-arrow-color': 'rgba(100,116,139,0.30)',
          },
        },
        {
          // 层级归属线：只做极淡的"聚拢暗示"，不与关系边抢视觉
          selector: 'edge.contains, edge.attribute',
          style: {
            width: 0.8,
            'line-style': 'dashed',
            'line-dash-pattern': [2, 4],
            'line-color': 'rgba(100,116,139,0.16)',
            'target-arrow-shape': 'none',
          },
        },
        {
          selector: 'edge.bridge',
          style: {
            'line-style': 'dashed',
            'line-dash-pattern': [7, 5],
            'line-color': 'rgba(124,58,237,0.55)',
            'target-arrow-shape': 'none',
            width: 1.8,
            label: 'data(label)',
            color: '#6d28d9',
            'font-size': 9,
            'z-index': 10,
          },
        },
        {
          selector: 'edge.peek, edge.path-edge, edge.impact-edge',
          style: { label: 'data(label)', width: 2.6 },
        },
        {
          selector: '.dimmed',
          style: { opacity: 0.1 },
        },
        {
          selector: '.path-node',
          style: {
            'border-color': '#2563eb',
            'border-width': 3,
            'underlay-color': '#2563eb',
            'underlay-opacity': 0.22,
            'underlay-padding': 4,
          },
        },
        {
          selector: '.path-edge',
          style: {
            'line-color': '#2563eb',
            'target-arrow-color': '#2563eb',
            width: 3.4,
            'z-index': 20,
          },
        },
        {
          selector: '.change-node',
          style: {
            'border-color': '#7c3aed',
            'border-width': 3.5,
            'underlay-color': '#7c3aed',
            'underlay-opacity': 0.24,
            'underlay-padding': 5,
          },
        },
        {
          selector: '.direct-impact',
          style: { 'border-color': '#ea580c', 'border-width': 3 },
        },
        {
          selector: '.indirect-impact',
          style: { 'border-color': '#dc2626', 'border-width': 2.4, 'border-style': 'dashed' },
        },
        {
          selector: '.impact-edge',
          style: {
            'line-color': '#ea580c',
            'target-arrow-color': '#ea580c',
            width: 2.8,
            'z-index': 18,
          },
        },
        {
          selector: '.selected-node',
          style: {
            'border-color': '#0f172a',
            'border-width': 2.6,
            'underlay-color': '#0f172a',
            'underlay-opacity': 0.14,
            'underlay-padding': 6,
          },
        },
        {
          selector: 'node.hover-ring',
          style: {
            'border-color': '#0f172a',
            'border-width': 2,
          },
        },
      ],
      layout: {
        name: 'cose',
        // 聚类种子坐标 + 缓存：已有节点不随机重排，只做局部收敛。
        // 斥力/重叠参数必须保持 cytoscape 默认量级（nodeRepulsion≈4e5），
        // 压低会让节点互相穿透挤成一团（MYW-28 验收意见的根因）。
        randomize: cachedRatio < 0.5,
        transform: (node: any) => positions.get(node.id()) || { x: 0, y: 0 },
        nodeRepulsion: () => 400000,
        nodeOverlap: 14,
        idealEdgeLength: (edge: any) => {
          const a = sizeById.get(edge.source().id()) || 20
          const b = sizeById.get(edge.target().id()) || 20
          return a / 2 + b / 2 + 105
        },
        edgeElasticity: () => 0.05,
        nestingFactor: 1.2,
        gravity: 0.3,
        numIter: cachedRatio < 0.5 ? 800 : 240,
        initialTemp: 300,
        coolingFactor: 0.99,
        minTemp: 1,
        componentSpacing: 150,
        animate: true,
        animationDuration: cachedRatio < 0.5 ? 680 : 280,
        fit: false,
        padding: 60,
      },
    } as any)

    const applyZoomLabels = () => {
      const showAll = cy.zoom() >= INSTANCE_LABEL_ZOOM
      cy.batch(() => {
        cy.nodes('node.instance').toggleClass('label-muted', !showAll)
      })
    }
    applyZoomLabels()
    cy.on('zoom', applyZoomLabels)

    cy.on('mouseover', 'edge.relation', event => event.target.addClass('peek'))
    cy.on('mouseout', 'edge.relation', event => event.target.removeClass('peek'))
    cy.on('mouseover', 'node', event => event.target.addClass('hover-ring'))
    cy.on('mouseout', 'node', event => event.target.removeClass('hover-ring'))

    cy.on('tap', 'node', event => onSelect?.(event.target.id()))
    cy.on('tap', event => {
      if (event.target === cy) onBackgroundTap?.()
    })

    const savePositions = () => {
      if (!cache) return
      cache.clear()
      cy.nodes().forEach(node => { cache.set(node.id(), { x: node.position().x, y: node.position().y }) })
    }
    cy.one('layoutstop', () => {
      // 确定性重叠消解：物理收敛后仍贴在一起的节点对被推开（不依赖物理参数手感）
      const finalPositions = new Map<string, { x: number; y: number }>()
      const diameters = new Map<string, number>()
      cy.nodes().forEach(node => {
        finalPositions.set(node.id(), { x: node.position().x, y: node.position().y })
        diameters.set(node.id(), node.data('size') || 20)
      })
      if (separateOverlaps(finalPositions, diameters, { iterations: 90, minGap: 8 })) {
        cy.batch(() => cy.nodes().forEach(node => {
          const position = finalPositions.get(node.id())
          if (position) node.position(position)
        }))
      }
      savePositions()
      cy.fit(undefined, 64)
    })

    cyRef.current = cy
    onReady?.(cy)
    return () => {
      savePositions()
      cy.destroy()
      if (cyRef.current === cy) cyRef.current = null
      onReady?.(null)
    }
    // 仅图数据变化才重建；高亮/选中由下方轻量 effect 处理，避免力导向重放
  }, [nodes, edges, sections, colorByOntology, degrees, maxDegree, positionsRef])

  // ---- 高亮应用：不重建画布，只批量切换类 ----
  useEffect(() => {
    const cy = cyRef.current
    if (!cy || cy.destroyed()) return
    const pathNodeIds = highlight?.pathNodeIds || new Set<string>()
    const pathEdgeIds = highlight?.pathEdgeIds || new Set<string>()
    const directImpactIds = highlight?.directImpactIds || new Set<string>()
    const indirectImpactIds = highlight?.indirectImpactIds || new Set<string>()
    const changeNodeId = highlight?.changeNodeId || ''
    const selectedNodeId = highlight?.selectedNodeId || ''
    const hasAnalysis = pathNodeIds.size > 0 || directImpactIds.size > 0
      || indirectImpactIds.size > 0 || !!changeNodeId

    cy.batch(() => {
      cy.nodes().forEach(node => {
        const id = node.id()
        const classes = [
          pathNodeIds.has(id) ? 'path-node' : '',
          directImpactIds.has(id) ? 'direct-impact' : '',
          indirectImpactIds.has(id) ? 'indirect-impact' : '',
          id === changeNodeId ? 'change-node' : '',
          id === selectedNodeId ? 'selected-node' : '',
          hasAnalysis && !pathNodeIds.has(id) && !directImpactIds.has(id)
            && !indirectImpactIds.has(id) && id !== changeNodeId ? 'dimmed' : '',
        ].filter(Boolean).join(' ')
        node.classes(node.hasClass('object-type') ? `object-type ${classes}` : `instance ${classes}`)
      })
      cy.edges().forEach(edge => {
        const kindClass = edge.data('kind').replace('_', '-')
        const id = edge.id()
        const classes = [
          pathEdgeIds.has(id) ? 'path-edge' : '',
          hasAnalysis && edge.data('kind') === 'relation' ? 'impact-edge' : '',
          hasAnalysis && !pathEdgeIds.has(id) && edge.data('kind') !== 'relation'
            && edge.data('kind') !== 'bridge' ? 'dimmed' : '',
        ].filter(Boolean).join(' ')
        edge.classes(`${kindClass} ${classes}`)
      })
    })
  }, [highlight])

  return (
    <div
      className="relative h-full min-h-0 w-full overflow-hidden"
      style={{
        backgroundColor: CANVAS_BG,
        backgroundImage: 'radial-gradient(circle at 1px 1px, #dde5f0 1px, transparent 0)',
        backgroundSize: '24px 24px',
      }}
    >
      <div ref={hostRef} className="absolute inset-0" aria-label="本体网络全局画布" />
    </div>
  )
}
