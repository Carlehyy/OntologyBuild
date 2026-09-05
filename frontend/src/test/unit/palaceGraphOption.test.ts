import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import { palaceGraphCategories, palaceGraphOption } from '../../pages/super-assistant/components/palaceGraphOption.ts'
import { CHART_SERIES_PALETTE } from '../../lib/echartsTheme.ts'
import type { PalaceGraph } from '../../api/superAssistant'

function makeGraph(overrides: Partial<PalaceGraph> = {}): PalaceGraph {
  return {
    available: true,
    nodes: [
      {
        id: 'e-1', name: '张三', type: '人物', aliases: ['老张'],
        source_files: ['简历.md'], file_ids: ['f-1'], mention_count: 4, match_count: 1,
      },
      {
        id: 'e-2', name: 'ACME', type: '组织', aliases: [],
        source_files: ['简历.md', '项目说明.md'], file_ids: ['f-1', 'f-2'], mention_count: 1, match_count: 0,
      },
      {
        id: 'e-3', name: '语义网', type: '未知类别', aliases: [],
        source_files: ['资料.md'], file_ids: ['f-2'], mention_count: 1, match_count: 0,
      },
    ],
    edges: [
      { source: 'e-1', target: 'e-2', name: '任职', source_files: ['简历.md'], file_ids: ['f-1'] },
      { source: 'e-1', target: 'e-404', name: '悬空', source_files: [], file_ids: [] },
    ],
    totals: { entities: 3, relations: 1 },
    truncated: false,
    builtFiles: 2,
    totalFiles: 2,
    updatedAt: '2026-09-05T06:00:00Z',
    ...overrides,
  }
}

describe('palaceGraphOption', () => {
  it('graph series：力导向布局 + 邻接高亮 + 箭头', () => {
    const option = palaceGraphOption(makeGraph()) as unknown as {
      color: string[]
      series: Array<Record<string, unknown>>
    }
    assert.equal(option.color, CHART_SERIES_PALETTE)
    const series = option.series[0]
    assert.equal(series.type, 'graph')
    assert.equal(series.layout, 'force')
    assert.deepEqual(series.edgeSymbol, ['none', 'arrow'])
    assert.deepEqual((series.emphasis as { focus: string }).focus, 'adjacency')
  })

  it('节点不可拖拽（拖拽一律平移画布）；漫游视图默认复位', () => {
    const option = palaceGraphOption(makeGraph()) as unknown as {
      series: Array<{ draggable: boolean; roam: boolean; zoom: number; center?: unknown }>
    }
    const series = option.series[0]
    assert.equal(series.draggable, false)
    assert.equal(series.roam, true)
    assert.equal(series.zoom, 1)
    assert.equal(series.center, undefined)
  })

  it('view 注入：重建 option 时沿用用户的缩放与平移，不被重置', () => {
    const option = palaceGraphOption(makeGraph(), undefined, {
      view: { zoom: 1.6, center: [180, 220] },
    }) as unknown as {
      series: Array<{ zoom: number; center: [number, number] }>
    }
    assert.equal(option.series[0].zoom, 1.6)
    assert.deepEqual(option.series[0].center, [180, 220])
  })

  it('节点按类型归入 category，未知类型归入「其他」，提及数驱动尺寸', () => {
    const option = palaceGraphOption(makeGraph()) as unknown as {
      series: Array<{ data: Array<Record<string, unknown>> }>
    }
    const data = option.series[0].data
    assert.equal(data.length, 3)
    assert.equal(data[0].category, palaceGraphCategories().findIndex(item => item.name === '人物'))
    assert.equal(data[2].category, palaceGraphCategories().findIndex(item => item.name === '其他'))
    assert.ok(Number(data[0].symbolSize) > Number(data[1].symbolSize))
  })

  it('端点不在节点集内的边被过滤，避免悬空连线', () => {
    const option = palaceGraphOption(makeGraph()) as unknown as {
      series: Array<{ links: Array<Record<string, unknown>> }>
    }
    const links = option.series[0].links
    assert.equal(links.length, 1)
    assert.equal(links[0].edgeLabel, '任职')
  })

  it('默认展示节点标签且顶部预留图例行高度；compactLabels 开启后标签默认隐藏（emphasis 仍展示）', () => {
    const normal = palaceGraphOption(makeGraph()) as unknown as {
      series: Array<{ top: string | number; label: { show: boolean } }>
    }
    assert.equal(normal.series[0].label.show, true)
    assert.equal(normal.series[0].top, 44)

    const compact = palaceGraphOption(makeGraph(), undefined, { compactLabels: true }) as unknown as {
      series: Array<{ label: { show: boolean }; emphasis: { label: { show: boolean } } }>
    }
    assert.equal(compact.series[0].label.show, false)
    assert.equal(compact.series[0].emphasis.label.show, true)
  })

  it('compactLabels + 高亮：命中节点强制展示标签，非命中节点隐藏并降透明', () => {
    const option = palaceGraphOption(makeGraph(), ['e-1'], { compactLabels: true }) as unknown as {
      series: Array<{ data: Array<{ name: string; label?: { show: boolean } }>; links: Array<{ lineStyle: { opacity: number } }> }>
    }
    const data = option.series[0].data
    const hit = data.find(item => item.name === '张三') as { label?: { show: boolean } }
    const miss = data.find(item => item.name === 'ACME') as { label?: { show: boolean } }
    assert.equal(hit.label?.show, true)
    assert.equal(miss.label?.show, false)
    assert.equal(option.series[0].links[0].lineStyle.opacity, 0.08)
  })

  it('空图谱不渲染 legend 数据之外的内容且 series 仍为 graph', () => {
    const option = palaceGraphOption({
      available: true,
      nodes: [],
      edges: [],
      totals: { entities: 0, relations: 0 },
      truncated: false,
      builtFiles: 0,
      totalFiles: 0,
      updatedAt: null,
    }) as unknown as {
      legend: { show: boolean }
      series: Array<{ data: unknown[]; links: unknown[] }>
    }
    assert.equal(option.legend.show, false)
    assert.deepEqual(option.series[0].data, [])
    assert.deepEqual(option.series[0].links, [])
  })

  it('tooltip 节点展示提及与被引用（match_count）次数', () => {
    const option = palaceGraphOption(makeGraph()) as unknown as {
      tooltip: { formatter: (params: { dataType?: string; data?: Record<string, unknown> }) => string }
      series: Array<{ data: Array<Record<string, unknown>> }>
    }
    const html = option.tooltip.formatter({ dataType: 'node', data: option.series[0].data[0] })
    assert.match(html, /提及 4 次/)
    assert.match(html, /被引用 1 次/)
    assert.match(html, /张三（人物）/)
  })

  it('highlightIds（数组）：非命中节点静态降透明并隐藏 label，命中节点不受影响', () => {
    const option = palaceGraphOption(makeGraph(), ['e-1']) as unknown as {
      series: Array<{ data: Array<Record<string, any>>; links: Array<Record<string, any>> }>
    }
    const [hit, miss, other] = option.series[0].data
    assert.equal(hit.itemStyle, undefined)
    assert.equal(hit.label, undefined)
    assert.deepEqual(miss.itemStyle, { opacity: 0.15 })
    assert.equal(miss.label.show, false)
    assert.deepEqual(other.itemStyle, { opacity: 0.15 })
    // 边的两端未全部命中（e-2 未在集合内）→ 静态降透明
    assert.equal(option.series[0].links[0].lineStyle.opacity, 0.08)
  })

  it('highlightIds（Set）：两端全部命中的边保持正常透明度', () => {
    const option = palaceGraphOption(makeGraph(), new Set(['e-1', 'e-2'])) as unknown as {
      series: Array<{ data: Array<Record<string, any>>; links: Array<Record<string, any>> }>
    }
    const [zhang, acme] = option.series[0].data
    assert.equal(zhang.itemStyle, undefined)
    assert.equal(acme.itemStyle, undefined)
    assert.equal(option.series[0].links[0].lineStyle.opacity, 0.55)
  })

  it('highlightIds 为空集时不进入高亮模式，样式与无参数一致', () => {
    const withEmptySet = palaceGraphOption(makeGraph(), new Set<string>()) as unknown as {
      series: Array<{ data: Array<Record<string, any>> }>
    }
    assert.equal(withEmptySet.series[0].data.every(node => node.itemStyle === undefined), true)
  })
})
