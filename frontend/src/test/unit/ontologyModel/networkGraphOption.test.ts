import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import {
  baseEdgeStyle,
  buildNetworkGraphOption,
  forceRepulsion,
  hasActiveAnalysis,
  hoverBands,
  soften,
  withAlpha,
  type BuildNetworkGraphOptionInput,
  type NetworkCanvasHighlight,
} from '../../../pages/ontology-model/network/networkGraphOption.ts'
import { CHART_AXIS, CHART_BLUE, CHART_ORANGE, CHART_VIOLET } from '../../../lib/echartsTheme.ts'
import type { NetworkGraphNode, NetworkOntologySection } from '../../../api/ontologyNetwork'

function makeSection(id: string, name: string, published = true): NetworkOntologySection {
  return {
    id, name, domain: name, published, releaseId: published ? `rel-${id}` : null,
    version: published ? 'v2' : null, typeCount: 2, linkTypeCount: 1, instanceCount: 3,
  }
}

function makeNode(overrides: Partial<NetworkGraphNode>): NetworkGraphNode {
  return {
    id: 'instance:x', entityId: 'x', kind: 'instance', label: '节点',
    ontologyId: 'o1', ontologyName: '供应链',
    ...overrides,
  }
}

const chainEdges = [
  { id: 'ab', kind: 'relation' as const, source: 'a', target: 'b', label: 'r' },
  { id: 'bc', kind: 'relation' as const, source: 'b', target: 'c', label: 'r' },
  { id: 'cd', kind: 'relation' as const, source: 'c', target: 'd', label: 'r' },
]

describe('hoverBands（一跳邻接强亮，其余淡出）', () => {
  it('悬停节点自身与一跳邻居进 strong，二跳不进入任何集合', () => {
    const bands = hoverBands(chainEdges, 'b')
    assert.equal(bands.active, true)
    assert.deepEqual([...bands.strongNodeIds].sort(), ['a', 'b', 'c'])
    assert.equal('softNodeIds' in bands, false)
    assert.deepEqual([...bands.incidentEdgeIds].sort(), ['ab', 'bc'])
  })

  it('无悬停目标或孤立节点时返回 inactive 空集', () => {
    for (const hovered of [null, undefined, '', 'ghost']) {
      const bands = hoverBands(chainEdges, hovered)
      assert.equal(bands.active, false)
      assert.equal(bands.strongNodeIds.size, 0)
      assert.equal(bands.incidentEdgeIds.size, 0)
    }
  })

  it('桥接边同样计入连通性（跨本体同名类型互为邻居）', () => {
    const edges = [
      { id: 'br', kind: 'bridge' as const, source: 'x1', target: 'x2', label: '同名类型' },
    ]
    const bands = hoverBands(edges, 'x1')
    assert.equal(bands.active, true)
    assert.ok(bands.strongNodeIds.has('x2'))
    assert.ok(bands.incidentEdgeIds.has('br'))
  })
})

describe('颜色工具', () => {
  it('withAlpha 输出 rgba 字符串', () => {
    assert.equal(withAlpha(CHART_BLUE, 0.5), 'rgba(59,130,246,0.5)')
  })

  it('soften 向中性灰混合且确定', () => {
    assert.equal(soften('#000000'), 'rgb(112,117,124)')
    assert.equal(soften(CHART_BLUE), soften(CHART_BLUE))
  })
})

describe('forceRepulsion / baseEdgeStyle', () => {
  it('斥力随规模自适应并有上下界', () => {
    assert.equal(forceRepulsion(0), 400)
    assert.equal(forceRepulsion(10), 340)
    assert.equal(forceRepulsion(60), Math.min(1600, Math.max(340, 960)))
    assert.equal(forceRepulsion(500), 1600)
  })

  it('五类边各有线型与箭头语义', () => {
    assert.equal(baseEdgeStyle('relation').arrow, true)
    assert.equal(baseEdgeStyle('relation').lineType, 'solid')
    assert.deepEqual(baseEdgeStyle('schema_relation').lineType, [5, 4])
    assert.deepEqual(baseEdgeStyle('contains').lineType, [2, 4])
    assert.equal(baseEdgeStyle('contains').arrow, false)
    assert.deepEqual(baseEdgeStyle('bridge').lineType, [7, 5])
  })
})

describe('hasActiveAnalysis', () => {
  it('任一分析集合非空即激活', () => {
    assert.equal(hasActiveAnalysis(undefined), false)
    assert.equal(hasActiveAnalysis({}), false)
    assert.equal(hasActiveAnalysis({ changeNodeId: 'instance:x' }), true)
    assert.equal(hasActiveAnalysis({ indirectImpactIds: new Set(['a']) }), true)
  })
})

// ── option 构建 ──

const sections = [makeSection('o1', '供应链'), makeSection('o2', '设备台账', false)]
const nodes: NetworkGraphNode[] = [
  makeNode({ id: 'type:t1', entityId: 't1', kind: 'object_type', label: '客户', technicalName: 'customer' }),
  makeNode({ id: 'type:t2', entityId: 't2', kind: 'object_type', label: '客户', ontologyId: 'o2', ontologyName: '设备台账' }),
  makeNode({ id: 'instance:i1', entityId: 'i1', label: '华东制造', objectTypeId: 't1', objectTypeLabel: '客户' }),
  makeNode({ id: 'instance:i2', entityId: 'i2', label: '华南贸易', objectTypeId: 't1' }),
  // 无任何关联：悬停联动里充当"两跳之外"的压暗样本。
  makeNode({ id: 'instance:i3', entityId: 'i3', label: '孤立实例' }),
]
const edges = [
  { id: 'e1', kind: 'relation' as const, source: 'type:t1', target: 'instance:i1', label: '拥有' },
  { id: 'e2', kind: 'schema_relation' as const, source: 'type:t1', target: 'type:t2', label: '对齐' },
  { id: 'e3', kind: 'contains' as const, source: 'type:t2', target: 'instance:i2', label: '' },
]

function build(overrides: Partial<BuildNetworkGraphOptionInput> = {}) {
  const input: BuildNetworkGraphOptionInput = { nodes, edges, sections, ...overrides }
  return buildNetworkGraphOption(input)
}

function seriesOf(option: ReturnType<typeof build>) {
  const series = (option as { series?: Record<string, unknown>[] }).series ?? []
  return series[0] as unknown as {
    type: string
    layout: string
    force: { initLayout: string; repulsion: number }
    data: Record<string, unknown>[]
    links: Record<string, unknown>[]
    categories: { name: string }[]
  }
}

describe('buildNetworkGraphOption', () => {
  it('构建 graph force series：类目按本体、类型节点排前、种子坐标注入', () => {
    const option = build({
      positions: new Map([['type:t1', { x: 500, y: 300 }]]),
    })
    const series = seriesOf(option)
    assert.equal(series.type, 'graph')
    assert.equal(series.layout, 'force')
    assert.equal(series.force.initLayout, 'none')
    assert.deepEqual(series.categories.map(category => category.name), ['供应链', '设备台账'])
    assert.equal(series.data.length, nodes.length)
    // 对象类型排前：labelLayout.hideOverlap 依序占位，类型标签优先显示
    assert.equal(series.data[0].id, 'type:t1')
    const positioned = series.data.find(datum => datum.id === 'type:t1') as { x?: number; y?: number }
    assert.equal(positioned.x, 500)
    assert.equal(positioned.y, 300)
  })

  it('关系边用端点类别色的低饱和渐变，其余边用主题常量色', () => {
    const series = seriesOf(build())
    const relation = series.links.find(link => link.id === 'e1') as { lineStyle: { color: { type: string; colorStops: unknown[] } } }
    assert.equal(relation.lineStyle.color.type, 'linear')
    assert.equal(relation.lineStyle.color.colorStops.length, 2)

    const schema = series.links.find(link => link.id === 'e2') as { lineStyle: { color: string } }
    assert.match(schema.lineStyle.color, /^rgba\(/)

    const contains = series.links.find(link => link.id === 'e3') as { lineStyle: { color: string } }
    assert.equal(contains.lineStyle.color, withAlpha(CHART_AXIS, 0.85))
  })

  it('节点尺寸有界：同度数下对象类型大于实例', () => {
    const series = seriesOf(build())
    const sizeOf = (id: string) => (series.data.find(datum => datum.id === id) as { symbolSize: number }).symbolSize
    const typeSize = sizeOf('type:t1')
    const instanceSize = sizeOf('instance:i1')
    assert.ok(typeSize >= 26 && typeSize <= 46, `type size ${typeSize}`)
    assert.ok(instanceSize >= 13 && instanceSize <= 21, `instance size ${instanceSize}`)
    assert.ok(typeSize > instanceSize)
  })

  it('分析高亮：路径蓝/直接影响橙/变更紫/其余压暗，影响边橙色加粗', () => {
    const highlight: NetworkCanvasHighlight = {
      pathNodeIds: new Set(['type:t1']),
      pathEdgeIds: new Set(['e2']),
      directImpactIds: new Set(['instance:i1']),
      indirectImpactIds: new Set(),
      changeNodeId: 'instance:i2',
    }
    const series = seriesOf(build({ highlight }))
    const byId = new Map(series.data.map(datum => [datum.id as string, datum]))

    const path = byId.get('type:t1') as { itemStyle: { borderColor: string; borderWidth: number }; label?: unknown }
    assert.equal(path.itemStyle.borderColor, CHART_BLUE)
    assert.equal(path.itemStyle.borderWidth, 3)

    const direct = byId.get('instance:i1') as { itemStyle: { borderColor: string } }
    assert.equal(direct.itemStyle.borderColor, CHART_ORANGE)

    const change = byId.get('instance:i2') as { itemStyle: { borderColor: string; borderWidth: number } }
    assert.equal(change.itemStyle.borderColor, CHART_VIOLET)
    assert.ok((change.itemStyle.borderWidth as number) >= 3)

    const outsider = byId.get('type:t2') as { itemStyle: { opacity: number } }
    assert.equal(outsider.itemStyle.opacity, 0.12)

    const impactEdge = series.links.find(link => link.id === 'e1') as { lineStyle: { color: string; width: number } }
    assert.equal(impactEdge.lineStyle.color, CHART_ORANGE)
    assert.equal(impactEdge.lineStyle.width, 2.6)

    const pathEdge = series.links.find(link => link.id === 'e2') as { lineStyle: { color: string; width: number } }
    assert.equal(pathEdge.lineStyle.color, CHART_BLUE)
    assert.equal(pathEdge.lineStyle.width, 3.2)
  })

  it('悬停联动：悬停节点与一跳全亮、二跳及其余压暗、关联边加粗', () => {
    const series = seriesOf(build({ hoveredId: 'type:t1' }))
    const byId = new Map(series.data.map(datum => [datum.id as string, datum]))
    const hovered = byId.get('type:t1') as { itemStyle: { opacity: number } }
    const neighbor = byId.get('instance:i1') as { itemStyle: { opacity: number } }
    const bridgePeer = byId.get('type:t2') as { itemStyle: { opacity: number } }
    const twoHop = byId.get('instance:i2') as { itemStyle: { opacity: number } }
    const unrelated = byId.get('instance:i3') as { itemStyle: { opacity: number } }
    assert.equal(hovered.itemStyle.opacity, 1)
    assert.equal(neighbor.itemStyle.opacity, 1)
    assert.equal(bridgePeer.itemStyle.opacity, 1)
    assert.equal(twoHop.itemStyle.opacity, 0.18)
    assert.equal(unrelated.itemStyle.opacity, 0.18)

    const incident = series.links.find(link => link.id === 'e1') as { lineStyle: { width: number; opacity: number } }
    assert.equal(incident.lineStyle.opacity, 1)
    assert.ok(incident.lineStyle.width > 1.6)

    const far = series.links.find(link => link.id === 'e3') as { lineStyle: { opacity: number } }
    assert.equal(far.lineStyle.opacity, 0.08)
  })

  it('分析态激活时忽略悬停联动（分析高亮优先）', () => {
    const series = seriesOf(build({
      hoveredId: 'type:t1',
      highlight: { changeNodeId: 'instance:i2' },
    }))
    const byId = new Map(series.data.map(datum => [datum.id as string, datum]))
    const neighbor = byId.get('instance:i1') as { itemStyle: { opacity: number } }
    assert.equal(neighbor.itemStyle.opacity, 0.12)
  })

  it('同一输入得到同一输出（确定性回归）', () => {
    const positions = new Map([['type:t1', { x: 100, y: 80 }]])
    assert.equal(
      JSON.stringify(build({ positions })),
      JSON.stringify(build({ positions })),
    )
  })

  it('空图安全：不抛错且数据为空', () => {
    const series = seriesOf(buildNetworkGraphOption({ nodes: [], edges: [], sections: [] }))
    assert.equal(series.data.length, 0)
    assert.equal(series.links.length, 0)
  })
})
