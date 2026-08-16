import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import {
  enqueuePrompt,
  loadQueuedPrompts,
  makeQueuedPrompt,
  mergeQueuedPrompts,
  persistQueuedPrompts,
  queuedPromptsKey,
  type QueuedPrompt,
  type StorageLike,
} from '../../../pages/agent/components/queuedPrompts.ts'

function memoryStorage(): StorageLike & { map: Map<string, string> } {
  const map = new Map<string, string>()
  return {
    map,
    getItem: (key: string) => map.get(key) ?? null,
    setItem: (key: string, value: string) => void map.set(key, value),
    removeItem: (key: string) => void map.delete(key),
  }
}

describe('queuedPrompts', () => {
  it('key 区分会话，无会话用 pending 占位', () => {
    assert.ok(queuedPromptsKey('c-1').includes('c-1'))
    assert.ok(queuedPromptsKey(null).includes('pending'))
  })

  it('持久化与装载往返一致，空队列清除存储', () => {
    const storage = memoryStorage()
    const key = queuedPromptsKey('c-1')
    const items: QueuedPrompt[] = [makeQueuedPrompt('问题A'), makeQueuedPrompt('问题B')]
    persistQueuedPrompts(key, items, storage)
    assert.deepEqual(loadQueuedPrompts(key, storage), items)
    persistQueuedPrompts(key, [], storage)
    assert.deepEqual(loadQueuedPrompts(key, storage), [])
    assert.equal(storage.map.has(key), false)
  })

  it('损坏数据返回空队列', () => {
    const storage = memoryStorage()
    const key = queuedPromptsKey('c-1')
    storage.setItem(key, '{"not":"an-array"}')
    assert.deepEqual(loadQueuedPrompts(key, storage), [])
    storage.setItem(key, 'broken json')
    assert.deepEqual(loadQueuedPrompts(key, storage), [])
  })

  it('入队去重并限制最多 8 条', () => {
    let items: QueuedPrompt[] = []
    items = enqueuePrompt(items, '问题A')
    items = enqueuePrompt(items, ' 问题A ')
    assert.equal(items.length, 1)
    for (let i = 0; i < 10; i += 1) items = enqueuePrompt(items, `问题${i}`)
    assert.equal(items.length, 8)
    assert.equal(items[7]?.text, '问题9')
  })

  it('合并 pending 桶时按 id 去重并限量', () => {
    const current = [makeQueuedPrompt('已在会话内的提问')]
    const pending = [
      ...current.map(item => ({ ...item })),
      makeQueuedPrompt('新会话前排队的提问'),
    ]
    const merged = mergeQueuedPrompts(current, pending)
    assert.equal(merged.length, 2)
    assert.ok(merged.some(item => item.text === '新会话前排队的提问'))
  })

  it('无 storage 环境（Node）静默降级', () => {
    assert.deepEqual(loadQueuedPrompts(queuedPromptsKey('c-1'), null), [])
    assert.doesNotThrow(() => persistQueuedPrompts(queuedPromptsKey('c-1'), [makeQueuedPrompt('x')], null))
  })
})
