import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import { groupConversations } from '../../pages/super-assistant/conversationGroups.ts'

// 固定「现在」为 2026-09-03 15:00 本地时间，保证分组断言确定
const NOW = new Date(2026, 8, 3, 15, 0, 0)

function item(id: string, updatedAt: string, status = 'active') {
  return { id, status, updated_at: updatedAt }
}

describe('groupConversations', () => {
  it('按本地日期分入今日/昨日/更早，归档单独成组', () => {
    const groups = groupConversations([
      item('today-1', new Date(2026, 8, 3, 9, 30).toISOString()),
      item('today-2', new Date(2026, 8, 3, 0, 5).toISOString()),
      item('yesterday-1', new Date(2026, 8, 2, 23, 59).toISOString()),
      item('earlier-1', new Date(2026, 8, 1, 12, 0).toISOString()),
      item('archived-today', new Date(2026, 8, 3, 10, 0).toISOString(), 'archived'),
    ], NOW)
    assert.deepEqual(groups.today.map(i => i.id), ['today-1', 'today-2'])
    assert.deepEqual(groups.yesterday.map(i => i.id), ['yesterday-1'])
    assert.deepEqual(groups.earlier.map(i => i.id), ['earlier-1'])
    assert.deepEqual(groups.archived.map(i => i.id), ['archived-today'])
  })

  it('空列表返回四个空组', () => {
    const groups = groupConversations([], NOW)
    assert.deepEqual(groups, { today: [], yesterday: [], earlier: [], archived: [] })
  })

  it('无法解析的日期归入更早', () => {
    const groups = groupConversations([item('bad', 'not-a-date')], NOW)
    assert.deepEqual(groups.earlier.map(i => i.id), ['bad'])
  })

  it('组内保持传入顺序', () => {
    const groups = groupConversations([
      item('a', new Date(2026, 8, 3, 14, 0).toISOString()),
      item('b', new Date(2026, 8, 3, 8, 0).toISOString()),
    ], NOW)
    assert.deepEqual(groups.today.map(i => i.id), ['a', 'b'])
  })
})
