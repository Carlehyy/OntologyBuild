import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import {
  estimateNodeBox,
  layoutKnowledgeGraph,
  seedPositions,
} from '../../../pages/agent/components/instanceGraphLayout.ts'

interface NodeSpec {
  id: string
  kind: 'object_type' | 'instance' | 'property'
  label?: string
  objectTypeId?: string
  instanceId?: string
}

const objectType = (id: string, label: string): NodeSpec => ({
  id: 'type:' + id,
  kind: 'object_type',
  label,
  objectTypeId: id,
})

const instance = (id: string, label: string, objectTypeId: string): NodeSpec => ({
  id: 'instance:' + id,
  kind: 'instance',
  label,
  objectTypeId,
  instanceId: id,
})

const property = (id: string, label: string, instanceId: string): NodeSpec => ({
  id: 'property:' + id,
  kind: 'property',
  label,
  instanceId,
})

const buildL2Graph = (): NodeSpec[] => {
  const nodes: NodeSpec[] = [
    objectType('device', '生产设备'),
    objectType('work_order', '维修工单'),
    objectType('technician', '技师'),
  ]
  ;['设备', '工单', '技师'].forEach((prefix, typeIndex) => {
    const typeId = ['device', 'work_order', 'technician'][typeIndex]
    for (let i = 1; i <= 20; i += 1) {
      nodes.push(instance(`${prefix}-${i}`, `${prefix}-SN-${1000 + i}`, typeId))
    }
  })
  return nodes
}

const collectOverlaps = (nodes: NodeSpec[]) => {
  const positions = layoutKnowledgeGraph(nodes)
  const pairs: string[] = []
  for (let i = 0; i < nodes.length; i += 1) {
    for (let j = i + 1; j < nodes.length; j += 1) {
      const a = estimateNodeBox(nodes[i])
      const b = estimateNodeBox(nodes[j])
      const pa = positions.get(nodes[i].id)!
      const pb = positions.get(nodes[j].id)!
      const overlapX = a.halfW + b.halfW - Math.abs(pa.x - pb.x)
      const overlapY = a.halfH + b.halfH - Math.abs(pa.y - pb.y)
      if (overlapX > 1 && overlapY > 1) {
        pairs.push(`${nodes[i].id} × ${nodes[j].id} (${overlapX.toFixed(1)}×${overlapY.toFixed(1)})`)
      }
    }
  }
  return pairs
}

describe('layoutKnowledgeGraph（MYW-65 数据推演图谱防重叠布局）', () => {
  it('同一输入两次布局结果完全一致（确定性）', () => {
    const nodes = buildL2Graph()
    const first = layoutKnowledgeGraph(nodes)
    const second = layoutKnowledgeGraph(nodes)
    assert.deepEqual([...second.entries()], [...first.entries()])
  })

  it('L2 规模（3 类 × 20 实例）所有节点两两不重叠', () => {
    const overlaps = collectOverlaps(buildL2Graph())
    assert.deepEqual(overlaps, [])
  })

  it('长中文标签实例也不会互相压盖', () => {
    const nodes: NodeSpec[] = [objectType('sensor', '环境传感器')]
    for (let i = 1; i <= 15; i += 1) {
      nodes.push(instance(`s-${i}`, `车间三号温湿度传感器组-${i}`, 'sensor'))
    }
    assert.deepEqual(collectOverlaps(nodes), [])
  })

  it('输出覆盖全部输入节点', () => {
    const nodes = buildL2Graph()
    const positions = layoutKnowledgeGraph(nodes)
    nodes.forEach(node => assert.ok(positions.has(node.id), `missing ${node.id}`))
  })

  it('L3 焦点视图：实例与其字段节点互不重叠', () => {
    const nodes: NodeSpec[] = [
      objectType('pump', '泵'),
      instance('pump-1', 'PUMP-A', 'pump'),
      ...['status', 'pressure', 'temperature', 'vibration', 'location', 'maintainer']
        .map(name => property(name, name, 'pump-1')),
    ]
    const positions = layoutKnowledgeGraph(nodes)
    assert.deepEqual(collectOverlaps(nodes as NodeSpec[]), [])
    nodes.forEach(node => assert.ok(positions.has(node.id)))
  })

  it('种子布局：实例围绕所属类型中心排布', () => {
    const nodes: NodeSpec[] = [objectType('device', '设备')]
    for (let i = 1; i <= 6; i += 1) nodes.push(instance(`d-${i}`, `设备-${i}`, 'device'))
    const positions = seedPositions(nodes)
    const center = positions.get('type:device')!
    const distances = nodes
      .filter(node => node.kind === 'instance')
      .map(node => {
        const point = positions.get(node.id)!
        return Math.hypot(point.x - center.x, point.y - center.y)
      })
    distances.forEach(distance => {
      assert.ok(distance > 100 && distance < 400, `instance ring distance ${distance}`)
    })
  })

  it('空图与单节点图安全返回', () => {
    assert.equal(layoutKnowledgeGraph([]).size, 0)
    const single = layoutKnowledgeGraph([instance('x', '独苗', 't')])
    assert.equal(single.size, 1)
  })
})
