import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import { extractTrajectorySummary } from '../../pages/world-model/trajectorySummary.ts'


describe('extractTrajectorySummary', () => {
  it('payload 非对象 / 数组 / 缺 trajectory 时返回 null', () => {
    assert.equal(extractTrajectorySummary(null), null)
    assert.equal(extractTrajectorySummary('text'), null)
    assert.equal(extractTrajectorySummary([1, 2, 3]), null)
    assert.equal(extractTrajectorySummary({}), null)
    assert.equal(extractTrajectorySummary({ confidence: 0.9 }), null)
  })

  it('trajectory 少于 2 点时返回 null（无法成线）', () => {
    assert.equal(extractTrajectorySummary({ trajectory: [1] }), null)
    assert.equal(extractTrajectorySummary({ trajectory: [] }), null)
  })

  it('数值数组 → 单序列，并携带 confidence / boundary 摘要', () => {
    const summary = extractTrajectorySummary({
      trajectory: [100, 102, 105],
      confidence: 0.87,
      boundary: '仅适用于平稳期短期预测。',
    })
    assert.ok(summary)
    assert.equal(summary.series.length, 1)
    assert.equal(summary.series[0].name, 'trajectory')
    assert.deepEqual(summary.series[0].values, [100, 102, 105])
    assert.equal(summary.pointCount, 3)
    assert.equal(summary.confidence, 0.87)
    assert.equal(summary.boundary, '仅适用于平稳期短期预测。')
  })

  it('数值序列中的缺测点记为 null，不阻断预览', () => {
    const summary = extractTrajectorySummary({ trajectory: [1, null, 'x', 4] })
    assert.ok(summary)
    // 'x' 不是数值 → 记为 null；null 字面量保留
    assert.deepEqual(summary.series[0].values, [1, null, null, 4])
  })

  it('等宽数值二维数组 → 多序列（按列拆分）', () => {
    const summary = extractTrajectorySummary({
      trajectory: [[1, 10], [2, 20], [3, 30]],
    })
    assert.ok(summary)
    assert.equal(summary.pointCount, 3)
    assert.deepEqual(summary.series.map(item => item.name), ['序列 1', '序列 2'])
    assert.deepEqual(summary.series[0].values, [1, 2, 3])
    assert.deepEqual(summary.series[1].values, [10, 20, 30])
  })

  it('参差不齐的二维数组返回 null', () => {
    assert.equal(extractTrajectorySummary({ trajectory: [[1, 2], [3]] }), null)
    assert.equal(extractTrajectorySummary({ trajectory: [[1], 'x'] }), null)
  })

  it('对象元素按缺测处理，不阻断其余数值成线', () => {
    const summary = extractTrajectorySummary({ trajectory: [1, { value: 2 }, 3] })
    assert.ok(summary)
    assert.deepEqual(summary.series[0].values, [1, null, 3])
  })

  it('全序列无有效数值时不生成预览', () => {
    assert.equal(extractTrajectorySummary({ trajectory: ['a', 'b'] }), null)
  })

  it('confidence 非有限数值、boundary 为空白时摘要记为 null 而非报错', () => {
    const summary = extractTrajectorySummary({ trajectory: [1, 2], confidence: 'high', boundary: '   ' })
    assert.ok(summary)
    assert.equal(summary.confidence, null)
    assert.equal(summary.boundary, null)
  })
})
