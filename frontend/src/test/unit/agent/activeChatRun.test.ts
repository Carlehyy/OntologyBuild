import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import {
  loadActiveChatRun,
  patchActiveChatRunConversationId,
  persistActiveChatRun,
  type ActiveChatRun,
  type StorageLike,
} from '../../../pages/agent/components/activeChatRun.ts'

function memoryStorage(): StorageLike & { store: Map<string, string> } {
  const store = new Map<string, string>()
  return {
    store,
    getItem: key => (store.has(key) ? store.get(key)! : null),
    setItem: (key, value) => void store.set(key, value),
    removeItem: key => void store.delete(key),
  }
}

const run: ActiveChatRun = {
  runId: 'run-1',
  ontologyId: 'ontology-1',
  conversationId: null,
  question: '查询订单数量',
  startedAt: '2026-08-27T14:00:00.000Z',
}

describe('loadActiveChatRun', () => {
  it('空存储返回 null', () => {
    assert.equal(loadActiveChatRun(memoryStorage()), null)
  })

  it('读取合法登记', () => {
    const storage = memoryStorage()
    persistActiveChatRun(run, storage)
    assert.deepEqual(loadActiveChatRun(storage), run)
  })

  it('字段缺失/类型不符的脏数据返回 null', () => {
    const storage = memoryStorage()
    storage.setItem('ontoagent:active-chat-run:v1', JSON.stringify({ runId: '' }))
    assert.equal(loadActiveChatRun(storage), null)
    storage.setItem('ontoagent:active-chat-run:v1', 'not-json{')
    assert.equal(loadActiveChatRun(storage), null)
  })
})

describe('persistActiveChatRun', () => {
  it('传 null 清除登记', () => {
    const storage = memoryStorage()
    persistActiveChatRun(run, storage)
    persistActiveChatRun(null, storage)
    assert.equal(storage.store.size, 0)
    assert.equal(loadActiveChatRun(storage), null)
  })
})

describe('patchActiveChatRunConversationId', () => {
  it('meta 事件后回填 conversationId', () => {
    const storage = memoryStorage()
    persistActiveChatRun(run, storage)
    patchActiveChatRunConversationId('conv-1', storage)
    assert.deepEqual(loadActiveChatRun(storage), { ...run, conversationId: 'conv-1' })
  })

  it('登记不存在时忽略', () => {
    const storage = memoryStorage()
    patchActiveChatRunConversationId('conv-1', storage)
    assert.equal(storage.store.size, 0)
  })

  it('重复回填同一会话不重复写入', () => {
    const storage = memoryStorage()
    persistActiveChatRun({ ...run, conversationId: 'conv-1' }, storage)
    const before = storage.store.get('ontoagent:active-chat-run:v1')
    patchActiveChatRunConversationId('conv-1', storage)
    assert.equal(storage.store.get('ontoagent:active-chat-run:v1'), before)
  })
})
