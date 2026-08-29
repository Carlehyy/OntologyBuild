import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import {
  normalizeRuntimeRange,
  resolveRuntimeRange,
  describeRuntimeRange,
  RUNTIME_RANGE_MAX_SPAN_DAYS,
  type RuntimeRange,
} from '../../../pages/ontologies/detail/tabs/runtimeSummaryRange.ts'

// 固定"现在"：2026-08-27（周四）12:00 UTC，避免用例随真实日期漂移。
const NOW = new Date('2026-08-27T12:00:00Z')

const range = (start: string, end: string): RuntimeRange => ({ start, end })

describe('resolveRuntimeRange', () => {
  it('今天/昨天给单日窗', () => {
    assert.deepEqual(resolveRuntimeRange('today', NOW, range('', '')), range('2026-08-27', '2026-08-27'))
    assert.deepEqual(resolveRuntimeRange('yesterday', NOW, range('', '')), range('2026-08-26', '2026-08-26'))
  })

  it('近7天含今天共 7 天，近30天含今天共 30 天', () => {
    assert.deepEqual(resolveRuntimeRange('last7', NOW, range('', '')), range('2026-08-21', '2026-08-27'))
    assert.deepEqual(resolveRuntimeRange('last30', NOW, range('', '')), range('2026-07-29', '2026-08-27'))
  })

  it('本月从 1 号到今天', () => {
    assert.deepEqual(resolveRuntimeRange('thisMonth', NOW, range('', '')), range('2026-08-01', '2026-08-27'))
  })

  it('上月跨月且覆盖大小月（含闰年二月）', () => {
    assert.deepEqual(resolveRuntimeRange('lastMonth', NOW, range('', '')), range('2026-07-01', '2026-07-31'))
    assert.deepEqual(
      resolveRuntimeRange('lastMonth', new Date('2026-03-15T08:00:00Z'), range('', '')),
      range('2026-02-01', '2026-02-28'),
    )
    // 2024 闰年：3 月的上月是 2/1–2/29
    assert.deepEqual(
      resolveRuntimeRange('lastMonth', new Date('2024-03-15T08:00:00Z'), range('', '')),
      range('2024-02-01', '2024-02-29'),
    )
  })

  it('跨年：1 月的上月是上一年 12 月', () => {
    assert.deepEqual(
      resolveRuntimeRange('lastMonth', new Date('2026-01-09T08:00:00Z'), range('', '')),
      range('2025-12-01', '2025-12-31'),
    )
  })
})

describe('normalizeRuntimeRange', () => {
  it('空值回退近 7 天', () => {
    assert.deepEqual(normalizeRuntimeRange(range('', ''), NOW), range('2026-08-21', '2026-08-27'))
    assert.deepEqual(normalizeRuntimeRange(range('2026-08-10', ''), NOW), range('2026-08-10', '2026-08-27'))
  })

  it('非法格式回退近 7 天对应端点，颠倒后交换', () => {
    // 非法起点回退到近 7 天起点 2026-08-21，与 2026-08-20 颠倒后交换
    assert.deepEqual(normalizeRuntimeRange(range('not-a-date', '2026-08-20'), NOW), range('2026-08-20', '2026-08-21'))
  })

  it('起止颠倒时交换', () => {
    assert.deepEqual(normalizeRuntimeRange(range('2026-08-20', '2026-08-10'), NOW), range('2026-08-10', '2026-08-20'))
  })

  it(`跨度超过 ${RUNTIME_RANGE_MAX_SPAN_DAYS} 天按起点截断`, () => {
    const normalized = normalizeRuntimeRange(range('2026-01-01', '2026-12-31'), NOW)
    assert.equal(normalized.start, '2026-01-01')
    assert.equal(normalized.end, '2026-04-02')
    const span = (Date.parse(`${normalized.end}T00:00:00Z`) - Date.parse(`${normalized.start}T00:00:00Z`)) / 86400000 + 1
    assert.equal(span, RUNTIME_RANGE_MAX_SPAN_DAYS)
  })
})

describe('describeRuntimeRange', () => {
  it('预设维度给标签，自定义给日期区间', () => {
    assert.equal(describeRuntimeRange('last7', range('2026-08-21', '2026-08-27')), '近7天')
    assert.equal(describeRuntimeRange('lastMonth', range('2026-07-01', '2026-07-31')), '上月')
    assert.equal(describeRuntimeRange('custom', range('2026-08-01', '2026-08-15')), '2026-08-01 ~ 2026-08-15')
  })
})
