import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import type { Edge, Node } from '@xyflow/react'

import { computeHandleSides } from '../../../pages/ontologies/mapping/handle-sides.ts'

function makeNode(id: string, type: string, x: number): Node {
  return { id, type, position: { x, y: 0 }, data: {}, measured: { width: 238, height: 200 } }
}

function makeEdge(id: string, source: string, target: string): Edge {
  return { id, source, target, sourceHandle: 'order_id', targetHandle: 'order_id' }
}

describe('computeHandleSides', () => {
  it('无连线时保持默认朝向：数据集在右、对象/关系在左', () => {
    const sides = computeHandleSides(
      [makeNode('dataset:ds-1', 'dataset', 0), makeNode('object:ot-1', 'object', 650)],
      [],
    )
    assert.equal(sides.get('dataset:ds-1'), 'right')
    assert.equal(sides.get('object:ot-1'), 'left')
  })

  it('目标在数据集右侧时保持默认朝向，不换侧', () => {
    const nodes = [makeNode('dataset:ds-1', 'dataset', 0), makeNode('object:ot-1', 'object', 650)]
    const sides = computeHandleSides(nodes, [makeEdge('e1', 'dataset:ds-1', 'object:ot-1')])
    assert.equal(sides.get('dataset:ds-1'), 'right')
    assert.equal(sides.get('object:ot-1'), 'left')
  })

  it('对象被拖到数据集左侧后两侧锚点一起换侧', () => {
    const nodes = [makeNode('dataset:ds-1', 'dataset', 650), makeNode('object:ot-1', 'object', 0)]
    const sides = computeHandleSides(nodes, [makeEdge('e1', 'dataset:ds-1', 'object:ot-1')])
    assert.equal(sides.get('dataset:ds-1'), 'left')
    assert.equal(sides.get('object:ot-1'), 'right')
  })

  it('多方向连线互相抵消时回到默认朝向', () => {
    const nodes = [
      makeNode('object:ot-left', 'object', 0),
      makeNode('dataset:ds-1', 'dataset', 400),
      makeNode('object:ot-right', 'object', 800),
    ]
    const sides = computeHandleSides(nodes, [
      makeEdge('e1', 'dataset:ds-1', 'object:ot-left'),
      makeEdge('e2', 'dataset:ds-1', 'object:ot-right'),
    ])
    // 数据集两侧受力抵消，回到默认右；两个对象的锚点各自朝向数据集
    assert.equal(sides.get('dataset:ds-1'), 'right')
    assert.equal(sides.get('object:ot-left'), 'right')
    assert.equal(sides.get('object:ot-right'), 'left')
  })

  it('端点节点缺失时忽略该连线，不影响其他节点', () => {
    const nodes = [makeNode('dataset:ds-1', 'dataset', 0)]
    const sides = computeHandleSides(nodes, [makeEdge('e1', 'dataset:ds-1', 'object:gone')])
    assert.equal(sides.get('dataset:ds-1'), 'right')
  })
})
