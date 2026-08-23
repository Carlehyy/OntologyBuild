/**
 * parseSseBuffer 单测：分帧、截断容错、CRLF、坏帧跳过。
 */
import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import { parseSseBuffer } from '../../../lib/sse.ts'

const NL = '\n'
const frame = (event: string, data: unknown) =>
  'event: ' + event + NL + 'data: ' + JSON.stringify(data) + NL + NL

describe('parseSseBuffer', () => {
  it('解析完整单帧', () => {
    const { events, rest } = parseSseBuffer(frame('meta', { a: 1 }))
    assert.deepEqual(events, [{ event: 'meta', data: { a: 1 } }])
    assert.equal(rest, '')
  })

  it('解析连续多帧且保序', () => {
    const text = frame('meta', { a: 1 }) + frame('text', { content: 'x' }) + frame('done', {})
    const { events } = parseSseBuffer(text)
    assert.deepEqual(events.map(e => e.event), ['meta', 'text', 'done'])
  })

  it('尾部不完整帧进入 rest 等待后续数据', () => {
    const complete = frame('meta', { a: 1 })
    const partial = 'event: scene_updated' + NL + 'data: {"scene_id":"s1"'
    const { events, rest } = parseSseBuffer(complete + partial)
    assert.equal(events.length, 1)
    assert.equal(rest, partial)
  })

  it('CRLF 帧分隔同样生效', () => {
    const text = 'event: meta\r\ndata: {"ok":true}\r\n\r\n'
    const { events } = parseSseBuffer(text)
    assert.deepEqual(events, [{ event: 'meta', data: { ok: true } }])
  })

  it('缺少 data 行的帧被跳过', () => {
    const text = 'event: lonely' + NL + NL + frame('done', {})
    const { events } = parseSseBuffer(text)
    assert.deepEqual(events.map(e => e.event), ['done'])
  })

  it('非 JSON data 整帧跳过而不抛错', () => {
    const text = 'data: not-json' + NL + NL + frame('done', {})
    const { events } = parseSseBuffer(text)
    assert.deepEqual(events.map(e => e.event), ['done'])
  })

  it('未知事件名原样保留（前向兼容）', () => {
    const text = frame('future_event', { x: 1 })
    const { events } = parseSseBuffer(text)
    assert.equal(events[0].event, 'future_event')
  })

  it('空输入返回空结果', () => {
    assert.deepEqual(parseSseBuffer(''), { events: [], rest: '' })
  })
})
