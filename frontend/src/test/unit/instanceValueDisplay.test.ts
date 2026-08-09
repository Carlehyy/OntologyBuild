import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import {
  instanceFactKindLabel,
  instanceSourceLabel,
  resolveInstanceValueDisplay,
} from '../../pages/ontologies/detail/tabs/instanceValueDisplay.ts'


describe('resolveInstanceValueDisplay', () => {
  it('renders empty values as empty kind', () => {
    assert.equal(resolveInstanceValueDisplay(null).kind, 'empty')
    assert.equal(resolveInstanceValueDisplay(undefined).kind, 'empty')
    assert.equal(resolveInstanceValueDisplay('').kind, 'empty')
  })

  it('renders pure-date columns without phantom time', () => {
    // 纯日期没有时刻,过去按本地时区渲染会捏造出 08:00:00(UTC 午夜平移)
    const display = resolveInstanceValueDisplay('2026-07-20', 'date')
    assert.equal(display.kind, 'date')
    assert.equal(display.text, '2026/07/20')
    assert.equal(display.raw, '2026-07-20')
  })

  it('trims time part for values that carry one in a date column', () => {
    const display = resolveInstanceValueDisplay('2026-07-20T08:00:00Z', 'date')
    assert.equal(display.kind, 'date')
    assert.equal(display.text, '2026/07/20')
  })

  it('keeps non-ISO text in date columns untouched', () => {
    const display = resolveInstanceValueDisplay('2026年7月', 'date')
    assert.equal(display.kind, 'text')
    assert.equal(display.text, '2026年7月')
  })

  it('localizes datetime columns as moment values', () => {
    const display = resolveInstanceValueDisplay('2026-07-27T08:00:00Z', 'datetime')
    assert.equal(display.kind, 'datetime')
    assert.equal(display.raw, '2026-07-27T08:00:00Z')
  })

  it('treats parametrized timestamp types as moment values', () => {
    assert.equal(resolveInstanceValueDisplay('2026-07-27T08:00:00Z', 'timestamp(3)').kind, 'datetime')
  })

  it('does not convert pure-date strings in plain string columns', () => {
    // 字符串列里的 '2026-07-20' 可能只是编号或文本,不能误转
    const display = resolveInstanceValueDisplay('2026-07-20', 'string')
    assert.equal(display.kind, 'text')
    assert.equal(display.text, '2026-07-20')
  })

  it('still converts full ISO moment strings in untyped columns', () => {
    assert.equal(resolveInstanceValueDisplay('2026-07-27T08:00:00Z').kind, 'datetime')
    assert.equal(resolveInstanceValueDisplay('2026-07-27 08:00:00', 'string').kind, 'datetime')
  })

  it('formats numbers with thousand separators', () => {
    const display = resolveInstanceValueDisplay(120000, 'number')
    assert.equal(display.kind, 'number')
    assert.equal(display.text, '120,000')
  })

  it('serializes arrays inline and objects pretty-printed', () => {
    assert.deepEqual(resolveInstanceValueDisplay(['a', 'b']), { kind: 'array', text: '["a","b"]' })
    assert.deepEqual(resolveInstanceValueDisplay({ complete: true }), {
      kind: 'object',
      text: '{\n  "complete": true\n}',
    })
  })

  it('passes through booleans and other text as-is', () => {
    assert.deepEqual(resolveInstanceValueDisplay(true), { kind: 'text', text: 'true' })
  })
})

describe('instanceSourceLabel', () => {
  it('maps known sources to user-facing labels', () => {
    assert.equal(instanceSourceLabel('pipeline'), '管道灌入')
    assert.equal(instanceSourceLabel('collector'), '采集器')
    assert.equal(instanceSourceLabel('action'), '动作执行')
    assert.equal(instanceSourceLabel('manual'), '手工录入')
  })

  it('falls back to raw value or placeholder for unknown sources', () => {
    assert.equal(instanceSourceLabel('custom-etl'), 'custom-etl')
    assert.equal(instanceSourceLabel(null), '来源未知')
    assert.equal(instanceSourceLabel(undefined), '来源未知')
  })
})

describe('instanceFactKindLabel', () => {
  it('maps fact kinds to user-facing labels', () => {
    assert.equal(instanceFactKindLabel('property'), '属性')
    assert.equal(instanceFactKindLabel('derived'), '派生')
    assert.equal(instanceFactKindLabel('decision'), '决策')
    assert.equal(instanceFactKindLabel('link'), '关系')
  })

  it('falls back to raw kind for unknown values', () => {
    assert.equal(instanceFactKindLabel('audit'), 'audit')
  })
})
