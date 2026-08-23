/**
 * 本体网络画布的纯 option 构建（ECharts graph series）：与组件解耦，
 * 可在 node:test 中验证——与治理页 charts.ts 同一范式。
 *
 * 渲染语言对齐 ECharts 官方 graph 示例的力导向观感，取值纪律见 DESIGN.md：
 * - 固定浅色作用域（§5.4）：全部颜色来自 @/lib/echartsTheme 共享常量，
 *   不在页域新造色板、不写裸 hex；
 * - 类别色按本体顺序轮转平台十色板；关系边做两端类别色的低饱和渐变；
 * - 节点默认极浅投影，悬停发光只作为交互信号（克制 chrome）；
 * - 标签胶囊化 + labelLayout.hideOverlap 低缩放防重叠；
 * - 悬停联动直接用 ECharts 原生 emphasis.focus='adjacency' + blur 淡出：
 *   零 option 重建、带过渡动画；分析高亮（路径/影响推演）优先于悬停联动
 *   （分析态下 focus 置 none，压暗语义由数据侧 opacity 承担）。
 */
import type { EChartsOption } from 'echarts'
import {
  CHART_AXIS,
  CHART_BLUE,
  CHART_ORANGE,
  CHART_RED,
  CHART_SPLIT,
  CHART_TEAL,
  CHART_TEXT,
  CHART_TEXT_STRONG,
  CHART_TOOLTIP_BG,
  CHART_TOOLTIP_BORDER,
  CHART_VIOLET,
  baseChartOption,
} from '../../../lib/echartsTheme.ts'
import type {
  NetworkGraphData,
  NetworkGraphEdge,
  NetworkGraphNode,
} from '../../../api/ontologyNetwork'
import {
  degreeMap,
  maxDegreeOf,
  nodeSize,
  ontologyColorMap,
} from './networkModel.ts'

/** 分析态高亮集合（与旧 cytoscape 版画布的 props 契约一致）。 */
export interface NetworkCanvasHighlight {
  pathNodeIds?: Set<string>
  pathEdgeIds?: Set<string>
  directImpactIds?: Set<string>
  indirectImpactIds?: Set<string>
  changeNodeId?: string
  selectedNodeId?: string
}

/** 分析态是否激活（任一集合非空即视为激活）。 */
export function hasActiveAnalysis(highlight?: NetworkCanvasHighlight): boolean {
  if (!highlight) return false
  return (highlight.pathNodeIds?.size ?? 0) > 0
    || (highlight.directImpactIds?.size ?? 0) > 0
    || (highlight.indirectImpactIds?.size ?? 0) > 0
    || !!highlight.changeNodeId
}

// ── 颜色工具（全部以共享主题常量为输入，不引入新的裸 hex） ──

function hexChannels(hex: string): [number, number, number] {
  const value = hex.replace('#', '')
  const full = value.length === 3
    ? value.split('').map(ch => ch + ch).join('')
    : value
  return [
    parseInt(full.slice(0, 2), 16),
    parseInt(full.slice(2, 4), 16),
    parseInt(full.slice(4, 6), 16),
  ]
}

/** 主题色 → rgba(alpha) 字符串。 */
export function withAlpha(hex: string, alpha: number): string {
  const [r, g, b] = hexChannels(hex)
  return `rgba(${r},${g},${b},${Math.round(alpha * 1000) / 1000})`
}

/** 向中性灰（CHART_AXIS）混合，得到低饱和的边用色。 */
export function soften(hex: string, toward = CHART_AXIS, ratio = 0.55): string {
  const [r, g, b] = hexChannels(hex)
  const [tr, tg, tb] = hexChannels(toward)
  const mix = (from: number, to: number) => Math.round(from * (1 - ratio) + to * ratio)
  return `rgb(${mix(r, tr)},${mix(g, tg)},${mix(b, tb)})`
}

// ── 节点 / 边状态解析 ──

type NodeVisualState =
  | 'path' | 'change' | 'direct' | 'indirect' | 'selected' | 'dim' | 'normal'

function resolveNodeState(id: string, highlight?: NetworkCanvasHighlight): NodeVisualState {
  if (highlight?.pathNodeIds?.has(id)) return 'path'
  if (highlight?.directImpactIds?.has(id)) return 'direct'
  if (highlight?.indirectImpactIds?.has(id)) return 'indirect'
  if (highlight?.changeNodeId && highlight.changeNodeId === id) return 'change'
  return 'normal'
}

const NODE_STATE_STYLE: Record<NodeVisualState, { borderColor: string; borderWidth: number; borderType?: 'solid' | 'dashed' }> = {
  path: { borderColor: CHART_BLUE, borderWidth: 3 },
  direct: { borderColor: CHART_ORANGE, borderWidth: 3 },
  indirect: { borderColor: CHART_RED, borderWidth: 2.4, borderType: 'dashed' },
  change: { borderColor: CHART_VIOLET, borderWidth: 3.4 },
  selected: { borderColor: CHART_TEXT_STRONG, borderWidth: 2.6 },
  dim: { borderColor: CHART_SPLIT, borderWidth: 1 },
  normal: { borderColor: CHART_SPLIT, borderWidth: 1.2 },
}

/** 关系边的基准样式（kind → 线型/箭头/透明度），颜色由调用方按端点注入。 */
export function baseEdgeStyle(kind: NetworkGraphEdge['kind']): {
  width: number
  lineType: 'solid' | number[]
  arrow: boolean
  opacity: number
} {
  switch (kind) {
    case 'relation':
      return { width: 1.6, lineType: 'solid', arrow: true, opacity: 0.9 }
    case 'schema_relation':
      return { width: 1.1, lineType: [5, 4], arrow: true, opacity: 0.75 }
    case 'contains':
    case 'attribute':
      return { width: 0.9, lineType: [2, 4], arrow: false, opacity: 0.55 }
    case 'bridge':
      return { width: 1.6, lineType: [7, 5], arrow: false, opacity: 0.8 }
  }
}

// ── option 构建 ──

export interface BuildNetworkGraphOptionInput {
  nodes: NetworkGraphNode[]
  edges: NetworkGraphEdge[]
  sections: NetworkGraphData['ontologies']
  highlight?: NetworkCanvasHighlight
  /** 确定性初始坐标（缓存优先、clusterPositions 兜底），供 force initLayout:none 使用。 */
  positions?: Map<string, { x: number; y: number }>
}

/** 力导向参数随规模自适应：节点越多斥力越大，避免挤成一团（MYW-28 教训）。 */
export function forceRepulsion(nodeCount: number): number {
  return nodeCount <= 0 ? 400 : Math.round(Math.min(1600, Math.max(340, nodeCount * 16)))
}

/** 理想边长途经随规模增长，超大图保持簇间呼吸感；始终有界。 */
export function forceEdgeLength(nodeCount: number): number {
  return Math.round(Math.min(160, 100 + Math.max(0, nodeCount) * 0.06))
}


const CAPSULE_LABEL_BASE = {
  backgroundColor: CHART_TOOLTIP_BG,
  borderColor: CHART_TOOLTIP_BORDER,
  borderRadius: 999,
} as const

/** 构建完整的 graph series option；同一输入永远得到同一输出（可快照回归）。 */
export function buildNetworkGraphOption(input: BuildNetworkGraphOptionInput): EChartsOption {
  const { nodes, edges, sections, highlight, positions } = input
  const analysisActive = hasActiveAnalysis(highlight)

  const colorByOntology = ontologyColorMap(sections)
  const degrees = degreeMap(edges)
  const maxDegree = maxDegreeOf(edges)

  // 类目轴仅承载"本体"语义（图例由页面 DOM 呈现，这里不开 echarts legend）。
  const categories = sections.map(section => ({ name: section.name }))
  const categoryIndex = new Map(sections.map((section, index) => [section.id, index]))

  // 对象类型排前：labelLayout.hideOverlap 依数据顺序占位，保证类型标签优先显示。
  const orderedNodes = [
    ...nodes.filter(node => node.kind === 'object_type'),
    ...nodes.filter(node => node.kind !== 'object_type'),
  ]

  const nodeData = orderedNodes.map(node => {
    const category = categoryIndex.get(node.ontologyId) ?? 0
    const baseColor = colorByOntology.get(node.ontologyId) || CHART_TEAL
    const size = nodeSize(node, degrees.get(node.id) || 0, maxDegree)
    const state = resolveNodeState(node.id, highlight)
    const stateStyle = NODE_STATE_STYLE[state]
    const dimmedByAnalysis = analysisActive && state === 'normal'

    let opacity = 1
    if (dimmedByAnalysis) opacity = 0.12

    const isType = node.kind === 'object_type'
    const position = positions?.get(node.id)
    return {
      id: node.id,
      name: node.id,
      x: position?.x,
      y: position?.y,
      category,
      symbolSize: size,
      itemStyle: {
        color: baseColor,
        borderColor: stateStyle.borderColor,
        borderWidth: stateStyle.borderWidth,
        borderType: stateStyle.borderType ?? 'solid',
        opacity,
        shadowBlur: 5,
        shadowColor: withAlpha(CHART_TEXT_STRONG, 0.14),
        shadowOffsetY: 1,
      },
      label: {
        show: true,
        formatter: node.label,
        fontSize: isType ? 11 : 9.5,
        fontWeight: isType ? 600 : 400,
        color: isType ? CHART_TEXT_STRONG : CHART_TEXT,
        // 胶囊只给对象类型：实例标签纯文本小号，密集画布不吵（对齐官方示例）。
        borderWidth: isType ? 1 : 0,
        padding: isType ? [3, 6] : [0, 1],
        ...(isType ? CAPSULE_LABEL_BASE : { backgroundColor: 'transparent', borderColor: 'transparent' }),
      },
      // 自定义负载：tooltip 展示用。
      nodeLabel: node.label,
      nodeKindName: isType ? '对象类型' : node.kind === 'property' ? '属性' : '实例',
      nodeOntologyName: node.ontologyName,
    }
  })

  const linkData = edges.map(edge => {
    const style = baseEdgeStyle(edge.kind)
    let width = style.width
    let opacity = style.opacity
    let flatColor: string | null = null

    const isPathEdge = !!highlight?.pathEdgeIds?.has(edge.id)
    const isImpactEdge = analysisActive && edge.kind === 'relation'
    if (isPathEdge) {
      width = 3.2
      opacity = 1
      flatColor = CHART_BLUE
    } else if (isImpactEdge) {
      width = 2.6
      opacity = 0.95
      flatColor = CHART_ORANGE
    } else if (analysisActive && edge.kind !== 'bridge') {
      opacity = 0.06
    }

    const sourceColor = colorByOntology.get(
      nodes.find(node => node.id === edge.source)?.ontologyId ?? '') || CHART_AXIS
    const targetColor = colorByOntology.get(
      nodes.find(node => node.id === edge.target)?.ontologyId ?? '') || CHART_AXIS

    return {
      id: edge.id,
      source: edge.source,
      target: edge.target,
      lineStyle: {
        width,
        opacity,
        type: style.lineType,
        color: flatColor ?? (
          edge.kind === 'relation'
            ? {
              type: 'linear',
              x: 0, y: 0, x2: 1, y2: 0,
              colorStops: [
                { offset: 0, color: soften(sourceColor) },
                { offset: 1, color: soften(targetColor) },
              ],
            }
            : edge.kind === 'bridge'
              ? withAlpha(CHART_VIOLET, 0.62)
              : withAlpha(CHART_AXIS, 0.85)
        ),
      },
      label: {
        show: false,
        formatter: edge.label,
        fontSize: 9,
        color: CHART_TEXT,
        backgroundColor: CHART_TOOLTIP_BG,
        borderColor: CHART_TOOLTIP_BORDER,
        borderWidth: 0.8,
        borderRadius: 999,
        padding: [2, 5],
      },
      emphasis: {
        lineStyle: { width: width + 0.9, opacity: 1 },
        label: { show: edge.kind === 'relation' || edge.kind === 'bridge' },
      },
      edgeKind: edge.kind,
      edgeLabel: edge.label,
    }
  })

  const base = baseChartOption()
  const option = {
    ...base,
    aria: {
      enabled: true,
      description: '跨本体全局网络图：节点为本体对象类型与实例，连线为关系、层级或同名桥接',
    },
    tooltip: {
      ...(base.tooltip ?? {}),
      formatter: (params: { dataType?: string; data?: Record<string, unknown> }) => {
        const data = params.data || {}
        if (params.dataType === 'edge') {
          return `${data.edgeLabel || ''}`
        }
        return `${data.nodeLabel || ''}<br/>${data.nodeOntologyName || ''} · ${data.nodeKindName || ''}`
      },
    },
    series: [
      {
        type: 'graph',
        layout: 'force',
        roam: true,
        draggable: true,
        categories,
        data: nodeData,
        links: linkData,
        force: {
          repulsion: forceRepulsion(nodes.length),
          gravity: 0.22,
          edgeLength: forceEdgeLength(nodes.length),
          layoutAnimation: true,
          // 初始坐标来自确定性聚类种子/位置缓存（initLayout:'none' 时吃数据 x/y）。
          initLayout: 'none',
        },
        // 悬停联动：原生 adjacency——一跳邻接强亮、其余进入 blur 淡出，
        // 零 option 重建 + 自带过渡动画；分析态下关闭让位给烘培压暗。
        emphasis: {
          focus: analysisActive ? 'none' : 'adjacency',
          itemStyle: {
            borderColor: CHART_TEAL,
            borderWidth: 2.2,
            shadowBlur: 16,
            shadowColor: withAlpha(CHART_TEAL, 0.3),
          },
        },
        blur: {
          itemStyle: { opacity: 0.15 },
          lineStyle: { opacity: 0.08 },
          label: { opacity: 0.15 },
        },
        scaleLimit: { min: 0.04, max: 4 },
        symbol: 'circle',
        symbolKeepAspect: true,
        edgeSymbol: ['none', 'arrow'],
        edgeSymbolSize: 6.5,
        labelLayout: { hideOverlap: true },
        top: 10,
        bottom: 10,
        left: 10,
        right: 10,
      },
    ],
  }
  // graph series 的自定义负载字段超出内置类型面，这里集中收口一次断言。
  return option as unknown as EChartsOption
}
