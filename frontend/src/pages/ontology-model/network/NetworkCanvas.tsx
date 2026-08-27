/**
 * 本体网络画布：ECharts graph 渲染跨本体全局图（固定浅色作用域，取值口径
 * 见根目录 DESIGN.md §5，颜色全部来自 @/lib/echartsTheme）。
 *
 * MYW-58 起采用确定性分区布局（layout:'none'）：
 * - 坐标由 networkModel.clusterLayout 产出、fitLayoutToViewport 按实测画布
 *   尺寸归一化（只压缩间距，不缩放符号/字号），首屏即完整可读；
 * - 数据变化自动回到适应视图（zoom=1 + 外接框居中）；仅高亮/选中变化时
 *   保留用户当前的 roam 视图，不发生跳变；
 * - 悬停：ECharts 原生 emphasis.focus='adjacency'——一跳邻接强亮、其余 blur
 *   淡出，零 option 重建、自带过渡动画（分析态激活时让位给分析高亮）；
 * - 标签：胶囊化白底衬 + labelLayout.hideOverlap 防重叠；
 * - 高亮契约：路径蓝 / 变更紫 / 直接影响橙 / 间接影响红虚线 /
 *   非参与元素压暗 / 选中深描边，全部由页面 props 注入。
 *
 * 组件本身无业务状态，onReady 暴露轻量控制器（缩放/适应/聚焦）供工具条使用。
 * 渲染器自适应：节点数超过阈值切 canvas（大图性能），小图保持 svg 以支持
 * mocked E2E 的 DOM 级文本断言。
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import ReactECharts from 'echarts-for-react'
import type { ECharts } from 'echarts'
import type {
  NetworkGraphData,
} from '@/api/ontologyNetwork'
import { clusterLayout, fitLayoutToViewport, NETWORK_VIEW_INSETS } from './networkModel.ts'
import {
  buildNetworkGraphOption,
  type NetworkCanvasHighlight,
} from './networkGraphOption.ts'

/** 工具条用的最小控制器。 */
export interface NetworkCanvasController {
  /** 相对缩放（>1 放大）。 */
  zoom(factor: number): void
  /** 回到适应视图（zoom=1 + 外接框居中）。 */
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
}

const ZOOM_MIN = 0.04
const ZOOM_MAX = 4
/** 节点数超过该阈值改用 canvas 渲染器：大图 DOM 元素量级显著下降。 */
const RENDERER_CANVAS_THRESHOLD = 120

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

export default function NetworkCanvas(
  { nodes, edges, sections, highlight, onSelect, onBackgroundTap, onReady }: Props,
) {
  const chartRef = useRef<ReactECharts | null>(null)
  const instanceRef = useRef<ECharts | null>(null)
  /** 画布实测尺寸：layout 归一化的目标视口（首帧用常见尺寸兜底，ready 后校正）。 */
  const [viewport, setViewport] = useState({ width: 960, height: 620 })
  const [readyTick, setReadyTick] = useState(0)
  /** 用户 roam 后的当前视图：高亮/选中重建 option 时回填，避免视图跳变。 */
  const userViewRef = useRef<{ zoom: number; center?: [number, number] } | null>(null)
  /** 数据身份追踪：仅当 nodes/edges 引用变化时重置视图（高亮变化不重置）。 */
  const prevDataRef = useRef<{ nodes: unknown; edges: unknown }>({ nodes: null, edges: null })
  if (prevDataRef.current.nodes !== nodes || prevDataRef.current.edges !== edges) {
    prevDataRef.current = { nodes, edges }
    userViewRef.current = null
  }

  // 回调经 ref 转发，保证传给 echarts-for-react 的 handler 身份稳定。
  const callbacksRef = useRef({ onSelect, onBackgroundTap, onReady })
  useEffect(() => {
    callbacksRef.current = { onSelect, onBackgroundTap, onReady }
  }, [onSelect, onBackgroundTap, onReady])

  // ---- 确定性分区布局 + 视口归一化（1 数据单位 = 1 物理像素） ----
  const layout = useMemo(() => clusterLayout(nodes, edges), [nodes, edges])
  const fitted = useMemo(
    () => fitLayoutToViewport(layout, viewport.width, viewport.height),
    [layout, viewport])
  const fittedNowRef = useRef(fitted)
  fittedNowRef.current = fitted

  const option = useMemo(
    () => buildNetworkGraphOption({
      nodes,
      edges,
      sections,
      highlight,
      positions: fitted.positions,
      center: userViewRef.current?.center ?? fitted.center,
      zoom: userViewRef.current?.zoom ?? 1,
      viewInsets: NETWORK_VIEW_INSETS,
    }),
    [nodes, edges, sections, highlight, fitted])

  // 卸载清理：通知页面控制器失效。
  useEffect(() => () => {
    callbacksRef.current.onReady?.(null)
  }, [])

  // 画布尺寸测量：ready 后与窗口 resize 时校正归一化视口。
  // getWidth 在实例被 dispose 的瞬间可能抛错（_zr 置空），静默跳过本次测量。
  useEffect(() => {
    if (!readyTick) return
    const measure = () => {
      const instance = instanceRef.current
      if (!instance) return
      let width = 0
      let height = 0
      try {
        width = instance.getWidth()
        height = instance.getHeight()
      } catch {
        return
      }
      if (width > 0 && height > 0) {
        setViewport(previous => previous.width === width && previous.height === height
          ? previous
          : { width, height })
      }
    }
    measure()
    window.addEventListener('resize', measure)
    return () => window.removeEventListener('resize', measure)
  }, [readyTick])

  const applyView = (instance: ECharts, view: { zoom: number; center?: [number, number] }) => {
    userViewRef.current = view
    instance.setOption({
      series: [{ zoom: view.zoom, ...(view.center ? { center: view.center } : {}) }],
    })
  }

  const handleReady = (instance: ECharts | undefined) => {
    if (!instance) return
    instanceRef.current = instance

    // 点空白取消选中（zrender 空目标点击 = 画布空白区域）。
    instance.getZr().on('click', event => {
      if (!event.target) callbacksRef.current.onBackgroundTap?.()
    })

    // roam / 缩放后记录用户视图（重建 option 时回填，避免跳变）。
    instance.on('finished', () => {
      userViewRef.current = readView(instance)
    })

    const controller: NetworkCanvasController = {
      zoom(factor) {
        applyView(instance, { zoom: clampZoom(readView(instance).zoom * factor) })
      },
      fit() {
        applyView(instance, { zoom: 1, center: fittedNowRef.current.center })
      },
      focusNode(nodeId) {
        if (!nodeId) return
        const target = fittedNowRef.current.positions.get(nodeId)
        if (!target) return
        applyView(instance, {
          center: [target.x, target.y],
          zoom: Math.max(readView(instance).zoom, 1.35),
        })
      },
    }
    callbacksRef.current.onReady?.(controller)
    setReadyTick(tick => tick + 1)
  }

  const handleEvents = useMemo(() => ({
    click: (params: { dataType?: string; data?: { id?: string }; name?: string }) => {
      if (params.dataType !== 'node') return
      const id = params.data?.id || params.name
      if (id) callbacksRef.current.onSelect?.(id)
    },
  }), [])

  // opts 必须引用稳定：inline 字面量会让 echarts-for-react 在每次渲染时
  // dispose + 重建实例（componentDidUpdate 按引用比较 opts），导致视图与
  // 已存实例句柄失效。
  const renderer: 'canvas' | 'svg' = nodes.length > RENDERER_CANVAS_THRESHOLD ? 'canvas' : 'svg'
  const chartOpts = useMemo(() => ({ renderer }), [renderer])

  return (
    <div
      className="relative h-full min-h-0 w-full overflow-hidden"
      style={{
        backgroundColor: 'var(--color-bg-base)',
        backgroundImage: 'radial-gradient(circle at 1px 1px, var(--color-border) 1px, transparent 0)',
        backgroundSize: '24px 24px',
      }}
    >
      <div className="absolute inset-0" data-testid="network-chart-host" aria-label="本体网络全局画布">
        <ReactECharts
          ref={chartRef}
          option={option}
          notMerge={false}
          lazyUpdate
          opts={chartOpts}
          style={{ width: '100%', height: '100%' }}
          onEvents={handleEvents}
          onChartReady={handleReady}
        />
      </div>
    </div>
  )
}
