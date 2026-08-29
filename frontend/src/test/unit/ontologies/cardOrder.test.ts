import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import {
  ONTOLOGY_CARD_ORDER_KEY,
  applyCardOrder,
  readSavedCardOrder,
  reorderCardIds,
  writeSavedCardOrder,
} from '../../../pages/ontologies/list/cardOrder.ts'

interface Item {
  id: string
  created_at: string
}

function makeItems(): Item[] {
  return [
    { id: 'ont-a', created_at: '2026-08-01T00:00:00Z' },
    { id: 'ont-b', created_at: '2026-08-03T00:00:00Z' },
    { id: 'ont-c', created_at: '2026-08-02T00:00:00Z' },
  ]
}

function makeStorage(initial: string | null = null) {
  let stored = initial
  return {
    getItem: (key: string) => (key === ONTOLOGY_CARD_ORDER_KEY ? stored : null),
    setItem: (key: string, value: string) => {
      if (key === ONTOLOGY_CARD_ORDER_KEY) stored = value
    },
  }
}

describe('readSavedCardOrder · 快照读取容错', () => {
  it('缺失/非法 JSON/非数组/脏元素一律回退空数组', () => {
    assert.deepEqual(readSavedCardOrder(makeStorage(null)), [])
    assert.deepEqual(readSavedCardOrder(makeStorage('not-json{')), [])
    assert.deepEqual(readSavedCardOrder(makeStorage('{"a":1}')), [])
    assert.deepEqual(
      readSavedCardOrder(makeStorage(JSON.stringify(['a', 1, null, 'b', 'a']))),
      ['a', 'b'],
    )
  })
})

describe('applyCardOrder · 手动序应用', () => {
  it('空快照保持创建时间倒序（既有默认行为）', () => {
    assert.deepEqual(
      applyCardOrder(makeItems(), []).map(item => item.id),
      ['ont-b', 'ont-c', 'ont-a'],
    )
  })

  it('快照序优先，快照外的本体（新建）按创建时间倒序插到最前', () => {
    assert.deepEqual(
      applyCardOrder(makeItems(), ['ont-a', 'ont-c']).map(item => item.id),
      ['ont-b', 'ont-a', 'ont-c'],
    )
  })

  it('快照中已删除的 id 自动剔除不报错', () => {
    const result = applyCardOrder(makeItems(), ['ont-gone', 'ont-b', 'ont-a', 'ont-c'])
    assert.deepEqual(result.map(item => item.id), ['ont-b', 'ont-a', 'ont-c'])
  })
})

describe('reorderCardIds · 拖拽落位', () => {
  it('before/after 分别插入目标前后', () => {
    assert.deepEqual(
      reorderCardIds(['a', 'b', 'c'], 'a', 'c', 'after'),
      ['b', 'c', 'a'],
    )
    assert.deepEqual(
      reorderCardIds(['a', 'b', 'c'], 'c', 'a', 'before'),
      ['c', 'a', 'b'],
    )
  })

  it('拖到自身或目标不存在时原样返回', () => {
    assert.deepEqual(reorderCardIds(['a', 'b'], 'a', 'a', 'before'), ['a', 'b'])
    assert.deepEqual(reorderCardIds(['a', 'b'], 'a', 'missing', 'before'), ['a', 'b'])
  })
})

describe('writeSavedCardOrder · 写入与回读', () => {
  it('写入后可回读，写入失败静默不抛错', () => {
    const storage = makeStorage()
    writeSavedCardOrder(storage, ['b', 'a'])
    assert.deepEqual(readSavedCardOrder(storage), ['b', 'a'])

    const broken = {
      getItem: () => null,
      setItem: () => {
        throw new Error('quota exceeded')
      },
    }
    writeSavedCardOrder(broken, ['a'])
  })
})
