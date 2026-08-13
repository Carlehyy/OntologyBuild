import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import type { SuperMemory } from '../../api/superAssistant.ts'
import {
  candidateActions,
  filterMemories,
  memoryConflictDescription,
  zoneLabel,
} from '../../pages/super-assistant/components/evolutionLogic.ts'

const memory = (overrides: Partial<SuperMemory>): SuperMemory => ({
  id: overrides.id || 'mem-1',
  content: overrides.content || '',
  zone: overrides.zone || 'general',
  pinned: overrides.pinned ?? false,
  confidence: overrides.confidence || 'medium',
  source: overrides.source || 'user',
  tags: overrides.tags || [],
  supersedes: [],
  superseded: false,
  match_count: 0,
  reference_count: 0,
  last_accessed_at: null,
  created_at: '2026-08-12T00:00:00+00:00',
  updated_at: '2026-08-12T00:00:00+00:00',
})

describe('zoneLabel', () => {
  it('maps built-in zones and falls back to raw value', () => {
    assert.equal(zoneLabel('core'), '身份偏好')
    assert.equal(zoneLabel('general'), '通用')
    assert.equal(zoneLabel('project:alpha'), 'project:alpha')
  })
})

describe('filterMemories', () => {
  const memories = [
    memory({ id: 'm1', content: '用户偏好简洁回答', zone: 'core', tags: ['偏好'] }),
    memory({ id: 'm2', content: 'User prefers dark mode', zone: 'work', tags: ['ui'] }),
    memory({ id: 'm3', content: '上次会话讨论了本体发布', zone: 'episode' }),
  ]

  it('returns everything without filters', () => {
    assert.equal(filterMemories(memories, {}).length, 3)
  })

  it('filters by zone exactly', () => {
    assert.deepEqual(filterMemories(memories, { zone: 'core' }).map(item => item.id), ['m1'])
  })

  it('matches content case-insensitively', () => {
    assert.deepEqual(filterMemories(memories, { query: 'DARK' }).map(item => item.id), ['m2'])
    assert.deepEqual(filterMemories(memories, { query: '简洁' }).map(item => item.id), ['m1'])
  })

  it('matches tags and combines with zone', () => {
    assert.deepEqual(filterMemories(memories, { query: 'ui' }).map(item => item.id), ['m2'])
    assert.deepEqual(filterMemories(memories, { query: '偏好', zone: 'work' }), [])
  })
})

describe('candidateActions', () => {
  it('memory and skill candidates use accept/reject', () => {
    assert.deepEqual(candidateActions('memory').map(action => action.decision), ['accept', 'reject'])
    assert.deepEqual(candidateActions('skill').map(action => action.decision), ['accept', 'reject'])
  })

  it('conflict candidates offer the three-way decision', () => {
    assert.deepEqual(
      candidateActions('conflict').map(action => action.decision),
      ['new_supersedes', 'keep_old', 'skip'],
    )
  })
})

describe('memoryConflictDescription', () => {
  it('formats the 409 conflict payload', () => {
    assert.equal(
      memoryConflictDescription({ existing: { id: 'm1', content: '已有记忆', similarity: 0.82 } }),
      '相似度 82%：已有记忆',
    )
  })

  it('returns null for non-conflict errors', () => {
    assert.equal(memoryConflictDescription({ detail: '其他错误' }), null)
    assert.equal(memoryConflictDescription(new Error('network')), null)
  })
})
