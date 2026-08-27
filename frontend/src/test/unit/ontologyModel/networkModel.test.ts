import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import {
  clusterLayout,
  degreeMap,
  fitLayoutToViewport,
  legendItems,
  maxDegreeOf,
  mergeOverlay,
  NETWORK_VIEW_INSETS,
  nodeSize,
  ontologyColorMap,
  ONTOLOGY_PALETTE,
  toGraphNodeId,
} from '../../../pages/ontology-model/network/networkModel.ts'
import type {
  NetworkGraphData,
  NetworkGraphEdge,
  NetworkGraphNode,
} from '../../../api/ontologyNetwork'

function makeNode(overrides: Partial<NetworkGraphNode>): NetworkGraphNode {
  return {
    id: 'instance:x',
    entityId: 'x',
    kind: 'instance',
    label: '节点',
    ontologyId: 'o1',
    ontologyName: '本体一',
    ...overrides,
  }
}

function makeGraph(nodes: NetworkGraphNode[]): NetworkGraphData {
  return {
    level: 2,
    query: null,
    limitPerType: 10,
    ontologies: [],
    errors: [],
    nodes,
    edges: [],
    bridges: { enabled: false, groups: [] },
    meta: {
      nodeBudget: 800, edgeBudget: 2000, truncated: false, droppedEdges: 0,
      nodeCount: nodes.length, edgeCount: 0, selectedOntologies: 1, totalInstances: 0,
    },
  }
}

describe('ontologyColorMap', () => {
  it('按本体顺序稳定取色，超出调色板后循环', () => {
    const colors = ontologyColorMap([{ id: 'a' }, { id: 'b' }, { id: 'c' }])
    assert.equal(colors.get('a'), ONTOLOGY_PALETTE[0])
    assert.equal(colors.get('b'), ONTOLOGY_PALETTE[1])
    const wrap = ontologyColorMap(Array.from({ length: ONTOLOGY_PALETTE.length + 1 }, (_, i) => ({ id: `o${i}` })))
    assert.equal(wrap.get(`o${ONTOLOGY_PALETTE.length}`), ONTOLOGY_PALETTE[0])
  })
})

describe('clusterLayout（确定性本体分区布局）', () => {
  const typeA1 = makeNode({ id: 'type:a1', entityId: 'a1', kind: 'object_type', objectTypeId: 'a1' })
  const typeA2 = makeNode({ id: 'type:a2', entityId: 'a2', kind: 'object_type', objectTypeId: 'a2' })
  const typeALost = makeNode({ id: 'type:a3', entityId: 'a3', kind: 'object_type', objectTypeId: 'a3' })
  const typeB1 = makeNode({
    id: 'type:b1', entityId: 'b1', kind: 'object_type',
    ontologyId: 'o2', ontologyName: '本体二', objectTypeId: 'b1',
  })
  const instanceI1 = makeNode({ id: 'instance:i1', entityId: 'i1', objectTypeId: 'a1' })
  const nodes = [typeA1, typeA2, typeALost, typeB1, instanceI1]
  const edges: NetworkGraphEdge[] = [{
    id: 'schema:s1', kind: 'schema_relation', source: 'type:a1', target: 'type:a2', label: '下游',
  }]

  /** 某本体全部节点的外接框。 */
  const bboxOf = (positions: Map<string, { x: number; y: number }>, ontologyId: string) => {
    const points = [...positions.entries()]
      .filter(([id]) => nodes.find(node => node.id === id)?.ontologyId === ontologyId)
      .map(([, point]) => point)
    return {
      minX: Math.min(...points.map(p => p.x)),
      maxX: Math.max(...points.map(p => p.x)),
      minY: Math.min(...points.map(p => p.y)),
      maxY: Math.max(...points.map(p => p.y)),
    }
  }

  it('为每个节点给出确定性坐标；层级边下游在根的右侧', () => {
    const first = clusterLayout(nodes, edges)
    const second = clusterLayout(nodes, edges)
    assert.equal(first.positions.size, nodes.length)
    for (const [id, pos] of first.positions) {
      assert.deepEqual(second.positions.get(id), pos, '同一输入必须得到同一布局')
      assert.ok(Number.isFinite(pos.x) && Number.isFinite(pos.y))
    }
    assert.ok(first.positions.get('type:a2')!.x > first.positions.get('type:a1')!.x,
      '有结构边的下游类型应排在根类型右侧（层次分列）')
  })

  it('不同本体的簇外接框互不重叠；孤立类型仍在所属本体簇内', () => {
    const { positions } = clusterLayout(nodes, edges)
    const o1 = bboxOf(positions, 'o1')
    const o2 = bboxOf(positions, 'o2')
    const separated = o1.maxX < o2.minX || o2.maxX < o1.minX
      || o1.maxY < o2.minY || o2.maxY < o1.minY
    assert.ok(separated, '两个本体的簇外接框不应重叠')
    const lost = positions.get('type:a3')!
    assert.ok(lost.x >= o1.minX && lost.x <= o1.maxX && lost.y >= o1.minY && lost.y <= o1.maxY,
      '无关系的类型应留在所属本体簇内（簇尾网格），而不是漂到画布边缘')
  })

  it('实例围绕所属类型成环', () => {
    const { positions } = clusterLayout(nodes, edges)
    const type = positions.get('type:a1')!
    const instance = positions.get('instance:i1')!
    const distance = Math.hypot(instance.x - type.x, instance.y - type.y)
    assert.ok(distance > 0 && distance < 260, `实例应环绕类型中心，实际距离 ${distance}`)
  })
})

describe('fitLayoutToViewport（视口归一化）', () => {
  it('把布局拉伸到恰好填满视图盒：中心即盒中心，节点都落在留白内', () => {
    const nodes = [
      makeNode({ id: 'type:a1', entityId: 'a1', kind: 'object_type', objectTypeId: 'a1' }),
      makeNode({ id: 'type:b1', entityId: 'b1', kind: 'object_type', ontologyId: 'o2', ontologyName: '本体二', objectTypeId: 'b1' }),
    ]
    const layout = clusterLayout(nodes, [])
    const width = 800
    const height = 600
    const fitted = fitLayoutToViewport(layout, width, height)
    const insets = NETWORK_VIEW_INSETS
    // bbox 与视图盒重合时，视图中心对应的数据坐标 = 盒中心
    assert.deepEqual(fitted.center, [
      insets.left + (width - insets.left - insets.right) / 2,
      insets.top + (height - insets.top - insets.bottom) / 2,
    ])
    for (const point of fitted.positions.values()) {
      assert.ok(point.x >= insets.left && point.x <= width - insets.right, `x=${point.x} 应落在视图盒内`)
      assert.ok(point.y >= insets.top && point.y <= height - insets.bottom, `y=${point.y} 应落在视图盒内`)
    }
    // 同一输入永远得到同一结果（可快照回归）
    const again = fitLayoutToViewport(layout, width, height)
    assert.deepEqual([...fitted.positions], [...again.positions])
  })
})

describe('mergeOverlay', () => {
  it('分析结果叠加到基础图：已有节点被覆盖、新节点补本体归属、边去重合并', () => {
    const base = makeGraph([
      makeNode({ id: 'instance:i1', entityId: 'i1' }),
      makeNode({ id: 'type:t1', entityId: 't1', kind: 'object_type' }),
    ])
    const overlayNodes = [
      makeNode({ id: 'instance:i1', entityId: 'i1', label: '更新后的标签' }),
      makeNode({ id: 'instance:i9', entityId: 'i9', label: '窗口外的路径节点', ontologyId: '' }),
    ]
    const overlayEdges = [{
      id: 'link:l9', kind: 'relation' as const, source: toGraphNodeId('i9'),
      target: toGraphNodeId('i1'), label: '关联',
    }]
    const merged = mergeOverlay(base, overlayNodes, overlayEdges, 'o1')
    const byId = new Map(merged.nodes.map(node => [node.id, node]))
    assert.equal(byId.get('instance:i1')!.label, '更新后的标签')
    assert.equal(byId.get('instance:i9')!.ontologyId, 'o1')
    assert.equal(merged.edges.length, 1)
  })
})

describe('legendItems', () => {
  it('把本体清单映射为带发布徽标信息的图例条目', () => {
    const items = legendItems([
      { id: 'o1', name: '供应链', published: true },
      { id: 'o2', name: '设备台账', published: false },
    ] as any)
    assert.deepEqual(items.map(item => item.label), ['供应链', '设备台账'])
    assert.deepEqual(items.map(item => item.published), [true, false])
    assert.notEqual(items[0].color, items[1].color)
  })
})

describe('degreeMap / maxDegreeOf', () => {
  const edges: NetworkGraphEdge[] = [
    { id: 'e1', kind: 'relation', source: 'a', target: 'b', label: '关联' },
    { id: 'e2', kind: 'relation', source: 'a', target: 'c', label: '关联' },
    { id: 'e3', kind: 'bridge', source: 'b', target: 'd', label: '同名类型' },
  ]

  it('统计无向度数，桥接边不计入（展示层装饰不干扰大小语义）', () => {
    const degrees = degreeMap(edges)
    assert.equal(degrees.get('a'), 2)
    assert.equal(degrees.get('b'), 1)
    assert.equal(degrees.get('c'), 1)
    assert.equal(degrees.get('d'), undefined)
    assert.equal(maxDegreeOf(edges), 2)
  })
})

describe('nodeSize（graphify 度数映射）', () => {
  it('对象类型直径显著大于实例，且随度数单调放大、有上界', () => {
    assert.ok(nodeSize({ kind: 'object_type' }, 0, 4) < nodeSize({ kind: 'object_type' }, 4, 4))
    assert.ok(nodeSize({ kind: 'instance' }, 0, 4) < nodeSize({ kind: 'instance' }, 4, 4))
    // 同度数下类型始终大于实例
    assert.ok(nodeSize({ kind: 'object_type' }, 0, 4) > nodeSize({ kind: 'instance' }, 4, 4))
    // 越界度数被夹紧，不产生无限大节点
    assert.equal(nodeSize({ kind: 'object_type' }, 99, 4), nodeSize({ kind: 'object_type' }, 4, 4))
    // 无边图（maxDegree=0）时退化为基准尺寸而非 NaN
    assert.ok(Number.isFinite(nodeSize({ kind: 'instance' }, 0, 0)))
    // 直径上限受控：防止枢纽节点过大导致相邻节点挤压重叠（MYW-28 验收意见）
    assert.ok(nodeSize({ kind: 'object_type' }, 4, 4) <= 50)
    assert.ok(nodeSize({ kind: 'instance' }, 4, 4) <= 24)
  })
})
