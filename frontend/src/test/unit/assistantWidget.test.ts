import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import {
  buildChainSteps,
  errorMessage,
  isMenuAccessDenied,
  mapToolStepStatus,
  pickInitialConversationId,
  reduceStreamEvent,
} from '../../components/assistant-widget/logic.ts'

const baseMessage = () => ({
  id: 'assistant-1',
  conversation_id: 'conversation-1',
  role: 'assistant' as const,
  content: '',
  status: 'streaming' as const,
  steps: [],
  token_usage: {},
  created_at: '2026-08-12T00:00:00+00:00',
})

describe('pickInitialConversationId', () => {
  const conversations = [{ id: 'c-1' }, { id: 'c-2' }]

  it('prefers the requested conversation when it exists in the list', () => {
    assert.equal(pickInitialConversationId(conversations, 'c-2'), 'c-2')
  })

  it('falls back to the latest conversation when the requested one is missing', () => {
    assert.equal(pickInitialConversationId(conversations, 'c-404'), 'c-1')
    assert.equal(pickInitialConversationId(conversations, ''), 'c-1')
    assert.equal(pickInitialConversationId(conversations, null), 'c-1')
    assert.equal(pickInitialConversationId(conversations), 'c-1')
  })

  it('returns null for an empty list', () => {
    assert.equal(pickInitialConversationId([], 'c-1'), null)
    assert.equal(pickInitialConversationId([]), null)
  })
})

describe('errorMessage', () => {
  it('reads FastAPI string detail first', () => {
    assert.equal(errorMessage({ detail: '会话不存在' }), '会话不存在')
  })

  it('reads structured detail.message used by menu_guard', () => {
    assert.equal(
      errorMessage({ detail: { code: 'MENU_ACCESS_DENIED', message: '当前角色无权访问此功能' } }),
      '当前角色无权访问此功能',
    )
  })

  it('falls back to Error.message and then the fallback text', () => {
    assert.equal(errorMessage(new Error('网络中断')), '网络中断')
    assert.equal(errorMessage({}, '加载失败'), '加载失败')
    assert.equal(errorMessage(null), '操作失败')
  })
})

describe('isMenuAccessDenied', () => {
  it('matches the menu_guard denial envelope only', () => {
    assert.equal(isMenuAccessDenied({ detail: { code: 'MENU_ACCESS_DENIED', menu_key: 'super_assistant' } }), true)
    assert.equal(isMenuAccessDenied({ detail: 'Not authenticated' }), false)
    assert.equal(isMenuAccessDenied(new Error('x')), false)
    assert.equal(isMenuAccessDenied(undefined), false)
  })
})

describe('reduceStreamEvent', () => {
  it('appends text_delta content', () => {
    const message = { ...baseMessage(), content: '你好' }
    const result = reduceStreamEvent(message, { event: 'text_delta', data: { delta: '，世界' } })
    assert.equal(result.message.content, '你好，世界')
    assert.equal(result.message.status, 'streaming')
  })

  it('pushes a running step on tool_start', () => {
    const result = reduceStreamEvent(baseMessage(), {
      event: 'tool_start',
      data: { toolRunId: 'run-1', toolName: 'use_skill', arguments: { skill: 'a' } },
    })
    assert.deepEqual(result.message.steps, [
      { toolName: 'use_skill', status: 'running', arguments: { skill: 'a' } },
    ])
  })

  it('marks the last step awaiting confirmation and surfaces the pending card', () => {
    const running = reduceStreamEvent(baseMessage(), {
      event: 'tool_start',
      data: { toolRunId: 'run-1', toolName: 'minio.list', arguments: {} },
    }).message
    const result = reduceStreamEvent(running, {
      event: 'tool_confirmation_required',
      data: { toolRunId: 'run-1', toolName: 'minio.list', serverName: 'MinIO', arguments: { bucket: 'b' } },
    })
    assert.equal(result.message.steps[0]?.status, 'awaiting_confirmation')
    assert.deepEqual(result.pendingConfirmation, {
      toolRunId: 'run-1',
      toolName: 'minio.list',
      serverName: 'MinIO',
      arguments: { bucket: 'b' },
    })
  })

  it('updates the last step on tool_result and asks to clear the pending card', () => {
    const withStep = reduceStreamEvent(baseMessage(), {
      event: 'tool_start',
      data: { toolRunId: 'run-1', toolName: 'use_skill', arguments: {} },
    }).message
    const result = reduceStreamEvent(withStep, {
      event: 'tool_result',
      data: { toolRunId: 'run-1', status: 'success', preview: 'ok' },
    })
    assert.deepEqual(result.message.steps[0], { toolName: 'use_skill', status: 'success', arguments: {}, preview: 'ok' })
    assert.equal(result.clearPendingFor, 'run-1')
  })

  it('applies the server-final message on message_end', () => {
    const partial = { ...baseMessage(), content: '部分' }
    const result = reduceStreamEvent(partial, {
      event: 'message_end',
      data: {
        message: {
          id: 'assistant-9',
          content: '完整答复',
          steps: [{ toolName: 'use_skill', status: 'success' }],
          tokenUsage: { inputTokens: 10 },
        },
      },
    })
    assert.equal(result.message.content, '完整答复')
    assert.equal(result.message.status, 'complete')
    assert.equal(result.message.steps.length, 1)
    assert.deepEqual(result.message.token_usage, { inputTokens: 10 })
  })

  it('marks cancelled and error terminal states', () => {
    const cancelled = reduceStreamEvent(baseMessage(), { event: 'cancelled', data: {} })
    assert.equal(cancelled.message.status, 'cancelled')

    const failed = reduceStreamEvent(baseMessage(), { event: 'error', data: { message: '模型超时' } })
    assert.equal(failed.message.status, 'error')
    assert.equal(failed.message.content, '模型超时')
    assert.equal(failed.errorText, '模型超时')
  })

  it('leaves the message untouched for thinking / done events', () => {
    const message = baseMessage()
    assert.equal(reduceStreamEvent(message, { event: 'thinking', data: { round: 2 } }).message, message)
    assert.equal(reduceStreamEvent(message, { event: 'done', data: {} }).message, message)
  })
})

describe('buildChainSteps', () => {
  it('maps tool steps to chain items with status mapping', () => {
    const items = buildChainSteps([
      { toolName: 'use_skill', status: 'success', arguments: { a: 1 }, preview: 'done' },
      { toolName: 'minio.list', status: 'running' },
      { toolName: 'x', status: 'awaiting_confirmation' },
      { toolName: 'y', status: 'cancelled' },
      { toolName: 'z', status: 'error' },
    ], {})
    assert.deepEqual(items.map(item => item.status), ['success', 'loading', 'loading', 'abort', 'error'])
    assert.equal(items[0]?.previewText, 'done')
    assert.equal(items[0]?.argumentsText, JSON.stringify({ a: 1 }, null, 2))
    assert.equal(items[1]?.argumentsText, undefined)
  })

  it('appends a thinking placeholder while streaming before any visible content', () => {
    const items = buildChainSteps([], { streaming: true, thinkingRound: 2, hasContent: false })
    assert.equal(items.length, 1)
    assert.equal(items[0]?.status, 'loading')
    assert.match(String(items[0]?.title), /第 2 轮/)
  })

  it('suppresses the thinking placeholder once content streams or a tool is active', () => {
    assert.equal(buildChainSteps([], { streaming: true, thinkingRound: 1, hasContent: true }).length, 0)
    const active = buildChainSteps([{ toolName: 't', status: 'running' }], { streaming: true, thinkingRound: 1 })
    assert.equal(active.some(item => item.key === 'thinking'), false)
  })

  it('returns an empty chain for a finished plain message', () => {
    assert.deepEqual(buildChainSteps([], {}), [])
  })
})

describe('mapToolStepStatus', () => {
  it('covers every backend tool status bucket', () => {
    assert.equal(mapToolStepStatus('success'), 'success')
    assert.equal(mapToolStepStatus('running'), 'loading')
    assert.equal(mapToolStepStatus('awaiting_confirmation'), 'loading')
    assert.equal(mapToolStepStatus('cancelled'), 'abort')
    assert.equal(mapToolStepStatus('error'), 'error')
    assert.equal(mapToolStepStatus('denied'), 'error')
    assert.equal(mapToolStepStatus('expired'), 'error')
    assert.equal(mapToolStepStatus('unknown-future-status'), 'error')
  })
})
