/**
 * 本体网络画布：cytoscape 渲染跨本体全局图。
 *
 * 与本体助手页的 InstanceKnowledgeGraph 同源视觉，但：
 * - 节点按「本体」着色（簇叠加视图），而不是按对象类型自身颜色；
 * - 支持跨本体同名类型桥接边（虚线，kind=bridge）；
 * - 布局用确定性聚类排布（见 networkModel.clusterPositions），不用随机力导向。
 * 组件本身无业务状态，高亮集合与选中回调全部由页面注入。
 */
import { useEffect, useMemo, useRef } from 'react'
import cytoscape, { type Core, type ElementDefinition } from 'cytoscape'
import type {
  NetworkGraphData,
  NetworkGraphEdge,
  NetworkGraphNode,
} from '@/api/ontologyNetwork'
import { clusterPositions, ontologyColorMap } from './networkModel'

const TYPE_COLORS = ['#0f766e', '#0369a1', '#7c3aed', '#b45309', '#be123c', '#15803d']

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
}

export default function NetworkCanvas(
  { nodes, edges, sections, highlight, onSelect, onBackgroundTap, onReady }: Props,
) {
  const hostRef = useRef<HTMLDivElement>(null)
  const cyRef = useRef<Core | null>(null)
  const colorByOntology = useMemo(() => ontologyColorMap(sections), [sections])
  const hasAnalysis = !!(highlight?.pathNodeIds?.size
    || highlight?.directImpactIds?.size
    || highlight?.indirectImpactIds?.size
    || highlight?.changeNodeId)

  useEffect(() => {
    const host = hostRef.current
    if (!host) return
    const positions = clusterPositions(nodes)
    const pathNodeIds = highlight?.pathNodeIds || new Set<string>()
    const pathEdgeIds = highlight?.pathEdgeIds || new Set<string>()
    const directImpactIds = highlight?.directImpactIds || new Set<string>()
    const indirectImpactIds = highlight?.indirectImpactIds || new Set<string>()
    const changeNodeId = highlight?.changeNodeId || ''

    const elements: ElementDefinition[] = [
      ...nodes.map((node, index) => {
        const isBridgeEndpoint = edges.some(
          edge => edge.kind === 'bridge' && (edge.source === node.id || edge.target === node.id))
        const classes = [
          node.kind.replace('_', '-'),
          pathNodeIds.has(node.id) ? 'path-node' : '',
          directImpactIds.has(node.id) ? 'direct-impact' : '',
          indirectImpactIds.has(node.id) ? 'indirect-impact' : '',
          node.id === changeNodeId ? 'change-node' : '',
          node.id === highlight?.selectedNodeId ? 'selected-node' : '',
          isBridgeEndpoint ? 'bridge-endpoint' : '',
          hasAnalysis
            && !pathNodeIds.has(node.id)
            && !directImpactIds.has(node.id)
            && !indirectImpactIds.has(node.id)
            && node.id !== changeNodeId
            ? 'dimmed' : '',
        ].filter(Boolean).join(' ')
        return {
          group: 'nodes' as const,
          data: {
            id: node.id,
            label: node.label,
            secondary: node.kind === 'object_type'
              ? `${node.count || 0} 个实例`
              : node.secondaryLabel || '',
            kind: node.kind,
            // 全局视图按本体着色；无归属（异常兜底）时退回类型调色板
            color: colorByOntology.get(node.ontologyId) || TYPE_COLORS[index % TYPE_COLORS.length],
          },
          position: positions.get(node.id),
          classes,
        }
      }),
      ...edges.map(edge => ({
        group: 'edges' as const,
        data: {
          id: edge.id,
          source: edge.source,
          target: edge.target,
          label: edge.kind === 'contains' || edge.kind === 'attribute' ? '' : edge.label,
          kind: edge.kind,
        },
        classes: [
          edge.kind.replace('_', '-'),
          pathEdgeIds.has(edge.id) ? 'path-edge' : '',
          hasAnalysis && edge.kind === 'relation' ? 'impact-edge' : '',
          // 分析态下只保留路径/关系/桥接的对比度，装饰性边（contains 等）淡出
          hasAnalysis && !pathEdgeIds.has(edge.id)
            && edge.kind !== 'relation' && edge.kind !== 'bridge' ? 'dimmed' : '',
        ].filter(Boolean).join(' '),
      })),
    ]

    const cy = cytoscape({
      container: host,
      elements,
      layout: { name: 'preset', fit: true, padding: 54 },
      minZoom: 0.06,
      maxZoom: 2.6,
      boxSelectionEnabled: false,
      autoungrabify: false,
      style: [
        {
          selector: 'node',
          style: {
            'background-color': '#ffffff',
            'border-color': '#94a3b8',
            'border-width': 1.5,
            color: '#0f172a',
            label: 'data(label)',
            'font-size': 11,
            'font-weight': 600,
            'text-wrap': 'ellipsis',
            'text-max-width': '112px',
            'text-valign': 'center',
            'text-halign': 'center',
            'overlay-opacity': 0,
            width: 54,
            height: 54,
            'transition-property': 'border-color, border-width, opacity, background-color',
            'transition-duration': 180,
          },
        },
        {
          selector: 'node.object-type',
          style: {
            shape: 'round-rectangle',
            width: 132,
            height: 58,
            'background-color': '#f8fafc',
            'border-color': 'data(color)',
            'border-width': 2.5,
            'text-margin-y': -6,
          },
        },
        {
          selector: 'node.instance',
          style: {
            shape: 'ellipse',
            width: 76,
            height: 76,
            'background-color': '#ffffff',
            'border-color': 'data(color)',
          },
        },
        {
          selector: 'edge',
          style: {
            width: 1.4,
            'line-color': '#94a3b8',
            'target-arrow-color': '#94a3b8',
            'target-arrow-shape': 'triangle',
            'curve-style': 'bezier',
            label: 'data(label)',
            'font-size': 9,
            color: '#64748b',
            'text-background-color': '#ffffff',
            'text-background-opacity': 0.86,
            'text-background-padding': '3px',
            'text-rotation': 'autorotate',
            'overlay-opacity': 0,
            'transition-property': 'line-color, width, opacity',
            'transition-duration': 180,
          },
        },
        {
          selector: 'edge.contains',
          style: {
            'line-style': 'dashed',
            'target-arrow-shape': 'none',
            'line-color': '#cbd5e1',
            width: 1,
          },
        },
        {
          selector: 'edge.bridge',
          style: {
            'line-style': 'dashed',
            'line-color': '#8b5cf6',
            'target-arrow-shape': 'none',
            width: 2,
            'z-index': 10,
          },
        },
        {
          selector: 'edge.schema-relation',
          style: { width: 1.6 },
        },
        {
          selector: '.bridge-endpoint',
          style: { 'underlay-color': '#ddd6fe', 'underlay-opacity': 0.4, 'underlay-padding': 6 },
        },
        {
          selector: '.path-node',
          style: {
            'border-color': '#2563eb',
            'border-width': 4,
            'background-color': '#eff6ff',
          },
        },
        {
          selector: '.path-edge',
          style: {
            'line-color': '#2563eb',
            'target-arrow-color': '#2563eb',
            width: 4,
            'z-index': 20,
          },
        },
        {
          selector: '.change-node',
          style: {
            'border-color': '#7c3aed',
            'border-width': 5,
            'background-color': '#f5f3ff',
          },
        },
        {
          selector: '.direct-impact',
          style: {
            'border-color': '#ea580c',
            'border-width': 4,
            'background-color': '#fff7ed',
          },
        },
        {
          selector: '.indirect-impact',
          style: {
            'border-color': '#dc2626',
            'border-width': 3,
            'border-style': 'dashed',
            'background-color': '#fef2f2',
          },
        },
        {
          selector: '.impact-edge',
          style: {
            'line-color': '#f97316',
            'target-arrow-color': '#f97316',
            width: 2.8,
          },
        },
        { selector: '.dimmed', style: { opacity: 0.16 } },
        {
          selector: '.selected-node',
          style: {
            'border-color': '#0f766e',
            'border-width': 5,
            'underlay-color': '#99f6e4',
            'underlay-opacity': 0.34,
            'underlay-padding': 8,
          },
        },
      ],
    })
    cy.on('tap', 'node', event => onSelect?.(event.target.id()))
    cy.on('tap', event => {
      if (event.target === cy) onBackgroundTap?.()
    })
    cyRef.current = cy
    onReady?.(cy)
    requestAnimationFrame(() => cy.fit(undefined, 54))
    return () => {
      cy.destroy()
      if (cyRef.current === cy) cyRef.current = null
      onReady?.(null)
    }
    // 画布元素整体重建；高亮集合变化通过完整重建保证与 InstanceKnowledgeGraph 一致的语义
  }, [nodes, edges, sections, colorByOntology, hasAnalysis,
    highlight?.pathNodeIds, highlight?.pathEdgeIds, highlight?.directImpactIds,
    highlight?.indirectImpactIds, highlight?.changeNodeId, highlight?.selectedNodeId,
    onSelect, onBackgroundTap])

  return (
    <div className="relative h-full min-h-0 w-full overflow-hidden bg-[radial-gradient(circle_at_1px_1px,#dbe4ee_1px,transparent_0)] [background-size:24px_24px]">
      <div ref={hostRef} className="absolute inset-0" aria-label="本体网络全局画布" />
    </div>
  )
}
