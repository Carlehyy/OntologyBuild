import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import { formatDurationMs, formatSuccessRate } from '../../pages/world-model/statsFormat.ts'


describe('formatSuccessRate', () => {
  it('total 为 0 时返回占位符（不产生 NaN/Infinity）', () => {
    assert.equal(formatSuccessRate(0, 0), '—')
  })

  it('整数百分比去掉多余的 .0', () => {
    assert.equal(formatSuccessRate(4, 4), '100%')
    assert.equal(formatSuccessRate(0, 5), '0%')
  })

  it('保留一位小数', () => {
    assert.equal(formatSuccessRate(3, 4), '75%')
    assert.equal(formatSuccessRate(1, 3), '33.3%')
    assert.equal(formatSuccessRate(2, 3), '66.7%')
  })

  it('异常输入被钳制在 0~100% 区间', () => {
    assert.equal(formatSuccessRate(5, 4), '100%')
    assert.equal(formatSuccessRate(-1, 4), '0%')
  })
})

describe('formatDurationMs', () => {
  it('统一带 ms 单位', () => {
    assert.equal(formatDurationMs(0), '0 ms')
    assert.equal(formatDurationMs(1072), '1072 ms')
  })
})
