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
        source_files: ['简历.md'], mention_count: 4,
      },
      {
        id: 'e-2', name: 'ACME', type: '组织', aliases: [],
        source_files: ['简历.md', '项目说明.md'], mention_count: 1,
      },
      {
        id: 'e-3', name: '语义网', type: '未知类别', aliases: [],
        source_files: ['资料.md'], mention_count: 1,
      },
    ],
    edges: [
      { source: 'e-1', target: 'e-2', name: '任职', source_files: ['简历.md'] },
      { source: 'e-1', target: 'e-404', name: '悬空', source_files: [] },
    ],
    totals: { entities: 3, relations: 1 },
    truncated: false,
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

  it('空图谱不渲染 legend 数据之外的内容且 series 仍为 graph', () => {
    const option = palaceGraphOption({
      available: true,
      nodes: [],
      edges: [],
      totals: { entities: 0, relations: 0 },
      truncated: false,
    }) as unknown as {
      legend: { show: boolean }
      series: Array<{ data: unknown[]; links: unknown[] }>
    }
    assert.equal(option.legend.show, false)
    assert.deepEqual(option.series[0].data, [])
    assert.deepEqual(option.series[0].links, [])
  })
})
