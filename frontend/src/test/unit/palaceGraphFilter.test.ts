import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import {
  filterPalaceGraph,
  palaceFileNodeIds,
  palaceOneHopNeighbors,
} from '../../pages/super-assistant/components/palaceGraphFilter.ts'
import type { PalaceGraph } from '../../api/superAssistant'

function makeGraph(): PalaceGraph {
  return {
    available: true,
    nodes: [
      {
        id: 'e-1', name: '张三', type: '人物', aliases: ['老张', 'San Zhang'],
        source_files: ['简历.md'], file_ids: ['f-1'], mention_count: 4, match_count: 1,
      },
      {
        id: 'e-2', name: 'ACME Corp', type: '组织', aliases: [],
        source_files: ['简历.md'], file_ids: ['f-1'], mention_count: 2, match_count: 0,
      },
      {
        id: 'e-3', name: '语义网', type: '技术', aliases: [],
        source_files: ['资料.md'], file_ids: ['f-2', 'f-1'], mention_count: 1, match_count: 0,
      },
    ],
    edges: [
      { source: 'e-1', target: 'e-2', name: '任职', source_files: ['简历.md'], file_ids: ['f-1'] },
      { source: 'e-2', target: 'e-3', name: '使用', source_files: ['资料.md'], file_ids: ['f-2'] },
      { source: 'e-1', target: 'e-404', name: '悬空', source_files: [], file_ids: [] },
    ],
    totals: { entities: 3, relations: 2 },
    truncated: false,
  }
}

describe('filterPalaceGraph', () => {
  it('空串与纯空白返回全量节点与边', () => {
    const graph = makeGraph()
    for (const keyword of ['', '   ']) {
      const view = filterPalaceGraph(graph, keyword)
      assert.deepEqual(view.nodes, graph.nodes)
      assert.deepEqual(view.edges, graph.edges)
    }
  })

  it('按 name 命中（大小写不敏感），边仅在两端节点都保留时保留', () => {
    const view = filterPalaceGraph(makeGraph(), 'acme')
    assert.deepEqual(view.nodes.map(node => node.id), ['e-2'])
    // e-2 的两条边分别连向未命中的 e-1 / e-3，全部过滤
    assert.deepEqual(view.edges, [])
  })

  it('按 alias 与 type 命中', () => {
    assert.deepEqual(filterPalaceGraph(makeGraph(), '老张').nodes.map(node => node.id), ['e-1'])
    assert.deepEqual(filterPalaceGraph(makeGraph(), 'San').nodes.map(node => node.id), ['e-1'])
    assert.deepEqual(filterPalaceGraph(makeGraph(), '技术').nodes.map(node => node.id), ['e-3'])
  })

  it('多个节点命中时保留节点间相连的边，悬空边始终被过滤', () => {
    // 'a' 同时命中 e-1（别名 San Zhang）与 e-2（ACME Corp），不命中 e-3
    const view = filterPalaceGraph(makeGraph(), 'a')
    assert.deepEqual(view.nodes.map(node => node.id), ['e-1', 'e-2'])
    assert.deepEqual(view.edges.map(edge => edge.name), ['任职'])
  })

  it('无命中返回空视图（调用方据此渲染空态）', () => {
    const view = filterPalaceGraph(makeGraph(), '不存在的关键词')
    assert.deepEqual(view.nodes, [])
    assert.deepEqual(view.edges, [])
  })
})

describe('palaceOneHopNeighbors', () => {
  it('从已加载边双向推导一跳邻居与关系名', () => {
    const neighbors = palaceOneHopNeighbors(makeGraph(), 'e-2')
    assert.deepEqual(neighbors, [
      { nodeId: 'e-1', name: '张三', relation: '任职' },
      { nodeId: 'e-3', name: '语义网', relation: '使用' },
    ])
  })

  it('忽略端点不在节点集内的边与无关节点', () => {
    assert.deepEqual(palaceOneHopNeighbors(makeGraph(), 'e-1'), [
      { nodeId: 'e-2', name: 'ACME Corp', relation: '任职' },
    ])
    assert.deepEqual(palaceOneHopNeighbors(makeGraph(), 'e-404'), [])
    assert.deepEqual(palaceOneHopNeighbors(makeGraph(), 'e-missing'), [])
  })
})

describe('palaceFileNodeIds', () => {
  it('返回溯源命中该文件的全部节点 id（含多来源节点）', () => {
    assert.deepEqual([...palaceFileNodeIds(makeGraph(), 'f-1')].sort(), ['e-1', 'e-2', 'e-3'])
    assert.deepEqual([...palaceFileNodeIds(makeGraph(), 'f-2')], ['e-3'])
  })

  it('无命中（图片/未建图文件）返回空集，调用方据此不做淡化', () => {
    assert.deepEqual(palaceFileNodeIds(makeGraph(), 'f-img'), new Set())
  })
})
