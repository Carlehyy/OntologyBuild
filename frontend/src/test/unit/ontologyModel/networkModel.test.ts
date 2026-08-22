import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import {
  clusterPositions,
  legendItems,
  mergeOverlay,
  ontologyColorMap,
  ONTOLOGY_PALETTE,
  toGraphNodeId,
} from '../../../pages/ontology-model/network/networkModel.ts'
import type {
  NetworkGraphData,
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

describe('clusterPositions', () => {
  it('为每个节点给出确定性坐标，不同本体的簇互不重叠', () => {
    const nodes = [
      makeNode({ id: 'type:a1', entityId: 'a1', kind: 'object_type', objectTypeId: 'a1' }),
      makeNode({ id: 'type:b1', entityId: 'b1', kind: 'object_type', ontologyId: 'o2', ontologyName: '本体二', objectTypeId: 'b1' }),
      makeNode({ id: 'instance:i1', entityId: 'i1', objectTypeId: 'a1' }),
    ]
    const first = clusterPositions(nodes)
    const second = clusterPositions(nodes)
    assert.equal(first.size, 3)
    for (const [id, pos] of first) {
      assert.deepEqual(second.get(id), pos, '同一输入必须得到同一布局')
      assert.ok(Number.isFinite(pos.x) && Number.isFinite(pos.y))
    }
    // 实例围绕所属类型：距离类型中心在第一环半径范围内
    const type = first.get('type:a1')!
    const instance = first.get('instance:i1')!
    const distance = Math.hypot(instance.x - type.x, instance.y - type.y)
    assert.ok(distance > 0 && distance < 260, `实例应环绕类型中心，实际距离 ${distance}`)
    // 两个本体的簇中心相距足够远
    const other = first.get('type:b1')!
    const clusterDistance = Math.hypot(other.x - type.x, other.y - type.y)
    assert.ok(clusterDistance > 600, `不同本体簇应分开，实际距离 ${clusterDistance}`)
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
