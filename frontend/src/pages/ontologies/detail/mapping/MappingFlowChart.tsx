import { useEffect, useMemo, useRef, useState } from 'react'
import ReactECharts from 'echarts-for-react'
import { useThemeStore } from '@/stores/themeStore'
import { buildFlowModel, resolveFlowNodeClick, type FlowRowInput } from './mapping-review'

/** 行数据在纯函数输入之上附带 tooltip 所需的展示信息。 */
export interface FlowChartRow extends FlowRowInput {
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

interface MappingFlowChartProps {
  rows: FlowChartRow[]
  /** 清单行 hover 时下发对应图节点 id，驱动邻接高亮。 */
  hoverKey: string | null
  selectedKey: string | null
  onSelectElement: (key: string) => void
  onPreviewDataset: (datasetId: string) => void
  onHoverNode: (key: string | null) => void
}

function readCssVar(element: HTMLElement | null, name: string, fallback: string): string {
  if (!element) return fallback
  const value = getComputedStyle(element).getPropertyValue(name).trim()
  return value || fallback
}

export default function MappingFlowChart({
  rows,
  hoverKey,
  selectedKey,
  onSelectElement,
  onPreviewDataset,
  onHoverNode,
}: MappingFlowChartProps) {
  const theme = useThemeStore(state => state.theme)
  const wrapRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<ReactECharts>(null)
  // 首帧 DOM 未挂载时 CSS 变量读不到（用浅色兜底值）；挂载后触发一次重算
  const [mounted, setMounted] = useState(false)
  useEffect(() => setMounted(true), [])
  const model = useMemo(() => buildFlowModel(rows), [rows])
  const rowByKey = useMemo(() => new Map(rows.map(row => [row.key, row])), [rows])

  // CSS 变量取色：随主题切换/挂载完成重建 option
  const option = useMemo(() => {
    void mounted
    const el = wrapRef.current
    const labelColor = readCssVar(el, '--dmo-text', '#354157')
    const tooltipBg = readCssVar(el, '--dmo-surface', '#ffffff')
    const tooltipInk = readCssVar(el, '--dmo-ink', '#172033')
    const tooltipMuted = readCssVar(el, '--dmo-muted', '#728093')
    const tooltipLine = readCssVar(el, '--dmo-line', '#e3e9ee')
    const datasetColor = readCssVar(el, '--dmo-flow-dataset', '#0f8f83')
    const objectColor = readCssVar(el, '--dmo-flow-object', '#5b7ec9')
    const relationColor = readCssVar(el, '--dmo-flow-relation', '#cf8b2e')
    const colorOf = (kind: string) => kind === 'dataset' ? datasetColor : kind === 'object' ? objectColor : relationColor

    const tooltipNode = (data: { id: string; displayName: string; kind: string }) => {
      if (data.kind === 'dataset') {
        const dataset = rows.flatMap(row => row.datasets).find(item => `dataset:${item.id}` === data.id)
        if (!dataset) return data.displayName
        const quality = dataset.quality == null ? null : Math.round(Number(dataset.quality) <= 1 ? Number(dataset.quality) * 100 : Number(dataset.quality))
        const parts = [
          `<b>${dataset.name}</b>`,
          `<span style="color:${tooltipMuted}">${dataset.sourceLabel}${dataset.rows == null ? '' : ` · ${dataset.rows.toLocaleString()} 行`}${quality == null ? '' : ` · 质量 ${quality}%`}</span>`,
        ]
        if (dataset.reviewLabel) parts.push(`<span style="color:#c17b2f">审核状态：${dataset.reviewLabel}</span>`)
        parts.push(`<span style="color:${tooltipMuted}">点击查看数据预览</span>`)
        return `<div style="display:flex;flex-direction:column;gap:2px;font-size:12px;color:${tooltipInk}">${parts.join('')}</div>`
      }
      const row = rowByKey.get(data.id)
      if (!row) return data.displayName
      return `<div style="display:flex;flex-direction:column;gap:2px;font-size:12px;color:${tooltipInk}">`
        + `<b>${row.name}</b>`
        + `<span style="color:${tooltipMuted}">${row.kind === 'object' ? '对象实体' : '实体关系'} · ${row.statusLabel}</span>`
        + `<span style="color:${tooltipMuted}">实例 ${row.instanceCount.toLocaleString()} 条 · 字段 ${row.mappedFields}/${row.totalFields}</span>`
        + `<span style="color:${tooltipMuted}">点击定位到清单与血缘详情</span>`
        + `</div>`
    }

    return {
      animationDuration: 800,
      animationEasing: 'cubicOut',
      tooltip: {
        trigger: 'item',
        backgroundColor: tooltipBg,
        borderColor: tooltipLine,
        padding: [8, 10],
        textStyle: { color: tooltipInk },
        formatter: (params: { dataType: string; data: { id?: string; displayName?: string; kind?: string; realValue?: number } }) => {
          const data = params.data
          if (params.dataType === 'edge') {
            return `<div style="font-size:12px;color:${tooltipInk}">已产出 <b>${(data.realValue ?? 0).toLocaleString()}</b> 条实例</div>`
          }
          return tooltipNode(data as { id: string; displayName: string; kind: string })
        },
      },
      series: [{
        type: 'sankey',
        left: 158,
        right: 150,
        top: 14,
        bottom: 14,
        nodeWidth: 12,
        nodeGap: 14,
        draggable: false,
        emphasis: { focus: 'adjacency' },
        label: {
          color: labelColor,
          fontSize: 11,
          formatter: (params: { data: { displayName?: string } }) => params.data.displayName ?? '',
        },
        lineStyle: { color: 'source', opacity: 0.32, curveness: 0.5 },
        data: model.nodes.map(node => ({
          name: node.id,
          depth: node.depth,
          displayName: node.displayName,
          kind: node.kind,
          id: node.id,
          // 数据集标签放节点左侧、元素标签放右侧，避免长名称压在流上
          label: node.kind === 'dataset'
            ? { position: 'left', overflow: 'truncate', width: 146 }
            : { position: 'right', overflow: 'truncate', width: 138 },
          itemStyle: {
            color: colorOf(node.kind),
            borderRadius: 3,
            opacity: selectedKey && node.id === selectedKey ? 1 : undefined,
          },
        })),
        links: model.links.map(link => ({
          source: link.source,
          target: link.target,
          value: link.value,
          realValue: link.realValue,
        })),
      }],
    }
  }, [model, mounted, rowByKey, rows, selectedKey, theme])

  // 行 hover → 图节点邻接高亮（失败静默：联动是增强体验，不影响主流程）
  useEffect(() => {
    const chart = chartRef.current?.getEchartsInstance()
    if (!chart) return
    try {
      chart.dispatchAction({ type: 'downplay', seriesIndex: 0 })
      if (hoverKey) chart.dispatchAction({ type: 'highlight', seriesIndex: 0, name: hoverKey })
    } catch { /* noop */ }
  }, [hoverKey])

  // 容器尺寸变化 → 重排
  useEffect(() => {
    const element = wrapRef.current
    if (!element || typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(() => {
      try { chartRef.current?.getEchartsInstance().resize() } catch { /* noop */ }
    })
    observer.observe(element)
    return () => observer.disconnect()
  }, [])

  const height = useMemo(() => {
    const datasets = model.nodes.filter(node => node.kind === 'dataset').length
    const elements = model.nodes.length - datasets
    return Math.min(560, Math.max(280, Math.max(datasets, elements) * 52))
  }, [model])

  const events = useMemo(() => ({
    click: (params: { dataType?: string; data?: { id?: string; kind?: string } }) => {
      if (params.dataType !== 'node') return
      const action = resolveFlowNodeClick(params.data)
      if (action?.type === 'preview-dataset') onPreviewDataset(action.datasetId)
      else if (action?.type === 'select-element') onSelectElement(action.key)
    },
    mouseover: (params: { dataType?: string; data?: { id?: string } }) => {
      if (params.dataType === 'node') onHoverNode(params.data?.id ?? null)
    },
    mouseout: () => onHoverNode(null),
  }), [onHoverNode, onPreviewDataset, onSelectElement])

  return (
    <div ref={wrapRef} className="dmo-flow-canvas" data-testid="mapping-flow-chart">
      <ReactECharts
        ref={chartRef}
        option={option}
        style={{ height, width: '100%' }}
        opts={{ renderer: 'svg' }}
        notMerge
        onEvents={events}
      />
    </div>
  )
}
