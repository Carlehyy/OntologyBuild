import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import type { Edge, Node } from '@xyflow/react'

import {
  edgeHandleSides,
  routeEdgeHandles,
} from '../../../palantir-graph/utils/routeEdgeHandles.ts'

function makeNode(id: string, x: number, y: number, width = 280, height = 140): Node {
  return { id, position: { x, y }, data: {}, measured: { width, height } }
}

function makeEdge(id: string, source: string, target: string): Edge {
  return { id, source, target }
}

describe('edgeHandleSides', () => {
  it('水平占优时连左右两侧，方向由目标相对位置决定', () => {
    const source = makeNode('a', 0, 0)
    const right = makeNode('b', 600, 40)
    const left = makeNode('c', -600, 40)
    assert.deepEqual(edgeHandleSides(source, right), ['right', 'left'])
    assert.deepEqual(edgeHandleSides(source, left), ['left', 'right'])
  })

  it('垂直占优时连上下两侧', () => {
    const source = makeNode('a', 0, 0)
    const below = makeNode('b', 40, 600)
    const above = makeNode('c', 40, -600)
    assert.deepEqual(edgeHandleSides(source, below), ['bottom', 'top'])
    assert.deepEqual(edgeHandleSides(source, above), ['top', 'bottom'])
  })

  it('自环固定右出左进，配合 MultiConnectionEdge 的自环画法', () => {
    const node = makeNode('a', 100, 100)
    assert.deepEqual(edgeHandleSides(node, node), ['right', 'left'])
  })

  it('measured 缺失时用兜底尺寸估算中心，不影响选侧方向', () => {
    const source: Node = { id: 'a', position: { x: 0, y: 0 }, data: {} }
    const target = makeNode('b', 900, 10)
    assert.deepEqual(edgeHandleSides(source, target), ['right', 'left'])
  })
})

describe('routeEdgeHandles', () => {
  it('按节点实时相对位置重写 sourceHandle/targetHandle', () => {
    const nodes = [makeNode('a', 0, 0), makeNode('b', 600, 40)]
    const [routed] = routeEdgeHandles([makeEdge('e1', 'a', 'b')], nodes)
    assert.equal(routed.sourceHandle, 'source-right')
    assert.equal(routed.targetHandle, 'target-left')

    // 目标被拖到源节点左侧后锚点换侧
    const moved = [makeNode('a', 0, 0), makeNode('b', -600, 40)]
    const [rerouted] = routeEdgeHandles([makeEdge('e1', 'a', 'b')], moved)
    assert.equal(rerouted.sourceHandle, 'source-left')
    assert.equal(rerouted.targetHandle, 'target-right')
  })

  it('端点节点缺失时保持原边不变', () => {
    const edge = makeEdge('e1', 'a', 'missing')
    const [routed] = routeEdgeHandles([edge], [makeNode('a', 0, 0)])
    assert.equal(routed, edge)
  })

  it('保留边上已有字段（选中态、平行边偏移等）', () => {
    const nodes = [makeNode('a', 0, 0), makeNode('b', 600, 0)]
    const edge: Edge = {
      ...makeEdge('e1', 'a', 'b'),
      selected: true,
      type: 'multi',
      data: { __offset: 21 },
    }
    const [routed] = routeEdgeHandles([edge], nodes)
    assert.equal(routed.selected, true)
    assert.equal(routed.type, 'multi')
    assert.equal(routed.data?.__offset, 21)
  })
})
