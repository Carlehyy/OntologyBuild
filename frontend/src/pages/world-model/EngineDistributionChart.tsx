import { useMemo } from 'react'
import ReactECharts from 'echarts-for-react'
import type { EChartsOption } from 'echarts'
import { baseWorldModelChartOption } from './worldModelChartTheme'

export interface EngineDistributionSlice {
  name: string
  value: number
  color: string
}

/**
 * 推演模型页引擎类型分布环形图（嵌在模型卡片网格中，占一个卡片位）。
 * 图例由下方 HTML 渲染（echarts 图例在窄卡片内会被裁切），这里只画环形。
 */
export default function EngineDistributionChart({ slices }: { slices: EngineDistributionSlice[] }) {
  const option = useMemo<EChartsOption>(() => ({
    ...baseWorldModelChartOption(),
    aria: {
      enabled: true,
      description: '推演模型引擎类型分布环形图',
    },
    series: [
      {
        type: 'pie',
        radius: ['50%', '74%'],
        center: ['50%', '50%'],
        avoidLabelOverlap: true,
        label: { show: false },
        emphasis: {
          scaleSize: 4,
          label: { show: false },
        },
        itemStyle: { borderColor: '#FFFFFF', borderWidth: 2 },
        data: slices.map(slice => ({
          name: slice.name,
          value: slice.value,
          itemStyle: { color: slice.color },
        })),
      },
    ],
  }), [slices])

  return (
    <ReactECharts
      option={option}
      style={{ width: '100%', height: '100%' }}
      opts={{ renderer: 'canvas' }}
      notMerge
    />
  )
}
