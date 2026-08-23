/**
 * 本体网络画布：ECharts graph 渲染跨本体全局图（固定浅色作用域，取值口径
 * 见根目录 DESIGN.md §5，颜色全部来自 @/lib/echartsTheme）。
 *
 * 引擎由 cytoscape 切换为 ECharts（官方 graph 示例的力导向观感）：
 * - 布局：force + 确定性聚类种子坐标（clusterPositions）+ 跨重建位置缓存，
 *   数据刷新时已有节点不重新飞位；力参数随规模自适应防挤团；
 * - 悬停：自定义"一跳强亮 + 二跳微亮"带宽联动（分析态激活时让位给分析高亮）；
 * - 标签：胶囊化白底衬 + labelLayout.hideOverlap，低缩放下自动隐藏重叠标签；
 * - 高亮契约与旧版一致：路径蓝 / 变更紫 / 直接影响橙 / 间接影响红虚线 /
 *   非参与元素压暗 / 选中深描边，全部由页面 props 注入。
 *
 * 组件本身无业务状态，onReady 暴露轻量控制器（缩放/适应/聚焦）供工具条使用。
 * 渲染器选 svg 以支持 mocked E2E 的 DOM 级断言；若大规模图出现卡顿，
 * 改 opts.renderer 为 canvas 即可（一行切换，不影响任何逻辑）。
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import ReactECharts from 'echarts-for-react'
import type { ECharts } from 'echarts'
import type {
  NetworkGraphData,
} from '@/api/ontologyNetwork'
import { clusterPositions } from './networkModel.ts'
import {
  buildNetworkGraphOption,
  hasActiveAnalysis,
  type NetworkCanvasHighlight,
} from './networkGraphOption.ts'

/** 工具条用的最小控制器：替代旧版直接暴露 cytoscape Core 实例。 */
export interface NetworkCanvasController {
  /** 相对缩放（>1 放大）。 */
  zoom(factor: number): void
  /** 回到初始适应视图（首帧布局完成后的 zoom/center 快照）。 */
  fit(): void
  /** 居中并适度放大到指定节点。 */
  focusNode(nodeId: string): void
}

interface Props {
  nodes: NetworkGraphData['nodes']
  edges: NetworkGraphData['edges']
  sections: NetworkGraphData['ontologies']
  highlight?: NetworkCanvasHighlight
  onSelect?: (nodeId: string) => void
  onBackgroundTap?: () => void
  /** 暴露控制器给页面（缩放/聚焦工具条）；卸载或重建时回调 null。 */
  onReady?: (controller: NetworkCanvasController | null) => void
  /** 跨重建的位置缓存：数据刷新（搜索/层级切换）时已有节点不重新飞位。 */
  positionsRef?: React.MutableRefObject<Map<string, { x: number; y: number }>>
}

const ZOOM_MIN = 0.04
const ZOOM_MAX = 4

function clampZoom(value: number): number {
  return Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, value))
}

/** 从 option 读当前视图（zoom/center），roam 之后 getOption 反映最新值。 */
function readView(chart: ECharts): { zoom: number; center?: [number, number] } {
  const series = ((chart.getOption() as { series?: { zoom?: number; center?: number[] }[] }).series ?? [])[0]
  const center = Array.isArray(series?.center) && series.center.length === 2
    ? [series.center[0], series.center[1]] as [number, number]
    : undefined
  return { zoom: typeof series?.zoom === 'number' ? series.zoom : 1, center }
}

/** 尽力读取内部布局结果写回位置缓存；内部 API 变化时静默降级为不写回。 */
function readLayoutPositions(chart: ECharts): Map<string, { x: number; y: number }> {
  const out = new Map<string, { x: number; y: number }>()
  try {
    const model = (chart as unknown as {
      getModel?: () => {
        getSeriesByIndex?: (index: number) => {
          getGraph?: () => {
            eachNode?: (visit: (node: {
              id?: string
              getLayout?: () => unknown
            }) => void) => void
          }
        }
      }
    }).getModel?.()
    const graph = model?.getSeriesByIndex?.(0)?.getGraph?.()
    graph?.eachNode?.(node => {
      if (!node.id) return
      const layout = node.getLayout?.()
      if (Array.isArray(layout) && layout.length === 2
        && Number.isFinite(layout[0]) && Number.isFinite(layout[1])) {
        out.set(node.id, { x: layout[0], y: layout[1] })
        return
      }
      const point = layout as { x?: unknown; y?: unknown } | null
      if (point && typeof point.x === 'number' && typeof point.y === 'number') {
        out.set(node.id, { x: point.x, y: point.y })
      }
    })
  } catch {
    // 内部模型不可用时保持旧缓存即可，仅损失"刷新不飞位"的一部分体验。
  }
  return out
}

/** 在内部布局里查节点坐标（聚焦用），失败退回种子坐标。 */
function readNodePosition(chart: ECharts, nodeId: string): { x: number; y: number } | null {
  try {
    const model = (chart as unknown as {
      getModel?: () => {
        getSeriesByIndex?: (index: number) => {
          getGraph?: () => {
            getNodeById?: (id: string) => { getLayout?: () => unknown } | null
          }
        }
      }
    }).getModel?.()
    const node = model?.getSeriesByIndex?.(0)?.getGraph?.()?.getNodeById?.(nodeId)
    const layout = node?.getLayout?.()
    if (Array.isArray(layout) && layout.length === 2
      && Number.isFinite(layout[0]) && Number.isFinite(layout[1])) {
      return { x: layout[0], y: layout[1] }
    }
    const point = layout as { x?: unknown; y?: unknown } | null
    if (point && typeof point.x === 'number' && typeof point.y === 'number') {
      return { x: point.x, y: point.y }
    }
  } catch {
    // 交给调用方的种子坐标兜底。
  }
  return null
}

export default function NetworkCanvas(
  { nodes, edges, sections, highlight, onSelect, onBackgroundTap, onReady, positionsRef }: Props,
) {
  const chartRef = useRef<ReactECharts | null>(null)
  const instanceRef = useRef<ECharts | null>(null)
  const controllerRef = useRef<NetworkCanvasController | null>(null)
  const initialViewRef = useRef<{ zoom: number; center?: [number, number] } | null>(null)
  const capturedInitialRef = useRef(false)
  const writeBackTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const [hoveredId, setHoveredId] = useState('')

  // 回调经 ref 转发，保证传给 echarts-for-react 的 handler 身份稳定。
  const callbacksRef = useRef({ onSelect, onBackgroundTap, onReady })
  useEffect(() => {
    callbacksRef.current = { onSelect, onBackgroundTap, onReady }
  }, [onSelect, onBackgroundTap, onReady])

  // ---- 确定性种子 + 缓存合并（与旧版语义一致：已有节点不重新飞位） ----
  const positions = useMemo(() => {
    const seeded = clusterPositions(nodes)
    const cache = positionsRef?.current
    if (!cache) return seeded
    const merged = new Map(seeded)
    for (const [id, position] of cache) merged.set(id, position)
    return merged
  }, [nodes, positionsRef])

  const analysisActive = hasActiveAnalysis(highlight)
  const analysisActiveRef = useRef(false)
  analysisActiveRef.current = analysisActive
  // focusNode 的种子兜底坐标取最新值，避免闭包过期。
  const positionsNowRef = useRef(positions)
  positionsNowRef.current = positions

  const option = useMemo(
    () => buildNetworkGraphOption({
      nodes,
      edges,
      sections,
      highlight,
      hoveredId: analysisActive ? '' : hoveredId,
      positions,
    }),
    [nodes, edges, sections, highlight, hoveredId, analysisActive, positions])

  // 数据变化后重置"首帧快照未采集"标记，fit 语义始终对应当前数据的适应视图。
  useEffect(() => {
    capturedInitialRef.current = false
  }, [nodes, edges])

  // 卸载清理：定时器 + 通知页面控制器失效。
  useEffect(() => () => {
    if (writeBackTimerRef.current) clearTimeout(writeBackTimerRef.current)
    callbacksRef.current.onReady?.(null)
  }, [])

  const handleReady = (instance: ECharts | undefined) => {
    if (!instance) return
    instanceRef.current = instance

    // 点空白取消选中（zrender 空目标点击 = 画布空白区域）。
    instance.getZr().on('click', event => {
      if (!event.target) callbacksRef.current.onBackgroundTap?.()
    })

    // 布局收敛后写回位置缓存 + 采集初始视图快照（fit 的回退目标）。
    instance.on('finished', () => {
      if (!capturedInitialRef.current) {
        capturedInitialRef.current = true
        initialViewRef.current = readView(instance)
      }
      if (!positionsRef?.current) return
      if (writeBackTimerRef.current) clearTimeout(writeBackTimerRef.current)
      writeBackTimerRef.current = setTimeout(() => {
        const latest = readLayoutPositions(instance)
        if (latest.size === 0) return
        const cache = positionsRef.current!
        cache.clear()
        for (const [id, position] of latest) cache.set(id, position)
      }, 260)
    })

    const controller: NetworkCanvasController = {
      zoom(factor) {
        const next = clampZoom(readView(instance).zoom * factor)
        instance.setOption({ series: [{ zoom: next }] })
      },
      fit() {
        const snapshot = initialViewRef.current
        if (!snapshot) return
        instance.setOption({
          series: [{ zoom: snapshot.zoom, ...(snapshot.center ? { center: snapshot.center } : {}) }],
        })
      },
      focusNode(nodeId) {
        if (!nodeId) return
        const target = readNodePosition(instance, nodeId) ?? positionsNowRef.current.get(nodeId) ?? null
        if (!target) return
        const current = readView(instance).zoom
        instance.setOption({
          series: [{ center: [target.x, target.y], zoom: Math.max(current, 1.35) }],
        })
      },
    }
    controllerRef.current = controller
    callbacksRef.current.onReady?.(controller)
  }

  const handleEvents = useMemo(() => ({
    click: (params: { dataType?: string; data?: { id?: string }; name?: string }) => {
      if (params.dataType !== 'node') return
      const id = params.data?.id || params.name
      if (id) callbacksRef.current.onSelect?.(id)
    },
    mouseover: (params: { dataType?: string; data?: { id?: string } }) => {
      if (params.dataType !== 'node' || analysisActiveRef.current) return
      const id = params.data?.id
      if (id) setHoveredId(previous => (previous === id ? previous : id))
    },
    mouseout: (params: { dataType?: string }) => {
      if (params.dataType !== 'node' || analysisActiveRef.current) return
      setHoveredId(previous => (previous === '' ? previous : ''))
    },
  }), [])

  return (
    <div
      className="relative h-full min-h-0 w-full overflow-hidden"
      style={{
        backgroundColor: 'var(--color-bg-base)',
        backgroundImage: 'radial-gradient(circle at 1px 1px, var(--color-border) 1px, transparent 0)',
        backgroundSize: '24px 24px',
      }}
    >
      <ReactECharts
        ref={chartRef}
        option={option}
        notMerge={false}
        lazyUpdate
        opts={{ renderer: 'svg' }}
        style={{ width: '100%', height: '100%' }}
        onEvents={handleEvents}
        onChartReady={handleReady}
      />
      <div className="pointer-events-none absolute inset-0" aria-label="本体网络全局画布" />
    </div>
  )
}
