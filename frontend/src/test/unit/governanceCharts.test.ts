import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import {
  buildDailyComboOption,
  buildKpiSparkSeries,
  buildMiniBarOption,
  buildMiniCategoryBarOption,
  buildMiniDonutOption,
  buildMiniLineOption,
  buildMiniSegmentBarOption,
} from '../../pages/ontologies/detail/governance/charts.ts'

const daily7d = [
  { date: '2026-08-16', fired: 1, firedError: 0, runSuccess: 2, runFailed: 0 },
  { date: '2026-08-17', fired: 0, firedError: 1, runSuccess: 0, runFailed: 1 },
  { date: '2026-08-18', fired: 0, firedError: 0, runSuccess: 0, runFailed: 0 },
]

describe('buildDailyComboOption', () => {
  it('执行成功/失败堆叠柱 + 哨兵命中折线面积线,命中含错误', () => {
    const option = buildDailyComboOption(daily7d) as any
    assert.equal(option.series.length, 3)
    const [success, failed, hits] = option.series
    assert.equal(success.type, 'bar')
    assert.equal(success.stack, 'run')
    assert.deepEqual(success.data, [2, 0, 0])
    assert.equal(failed.stack, 'run')
    assert.deepEqual(failed.data, [0, 1, 0])
    assert.equal(hits.type, 'line')
    assert.deepEqual(hits.data, [1, 1, 0])
    assert.ok(hits.areaStyle, '命中折线带面积渐变')
    assert.deepEqual(option.xAxis.data, ['8/16', '8/17', '8/18'])
  })
})

describe('buildMiniBarOption / buildMiniLineOption', () => {
  it('迷你柱无轴无提示,按值渲染', () => {
    const option = buildMiniBarOption([1, 0, 3], '#3b82f6') as any
    assert.equal(option.series[0].type, 'bar')
    assert.deepEqual(option.series[0].data, [1, 0, 3])
    assert.equal(option.series[0].itemStyle.color, '#3b82f6')
    assert.equal(option.xAxis.show, false)
    assert.equal(option.tooltip.show, false)
  })

  it('迷你折线比例轴固定 0~1,空值不连', () => {
    const option = buildMiniLineOption([0.5, null, 1], '#8b5cf6') as any
    assert.equal(option.series[0].type, 'line')
    assert.equal(option.series[0].connectNulls, false)
    assert.deepEqual(option.series[0].data, [0.5, null, 1])
    assert.equal(option.yAxis.max, 1)
  })
})

describe('buildMiniCategoryBarOption / buildMiniDonutOption / buildMiniSegmentBarOption', () => {
  it('迷你分类柱逐柱取色,无轴无提示', () => {
    const option = buildMiniCategoryBarOption([
      { value: 3, color: '#059669' },
      { value: 1, color: '#3B82F6' },
    ]) as any
    assert.equal(option.series[0].type, 'bar')
    assert.deepEqual(option.series[0].data, [
      { value: 3, itemStyle: { color: '#059669', borderRadius: [1.5, 1.5, 0, 0], opacity: 0.85 } },
      { value: 1, itemStyle: { color: '#3B82F6', borderRadius: [1.5, 1.5, 0, 0], opacity: 0.85 } },
    ])
    assert.equal(option.xAxis.show, false)
    assert.equal(option.tooltip.show, false)
  })

  it('迷你环形按值出扇区,空数据退化为灰环占位', () => {
    const option = buildMiniDonutOption([{ value: 2, color: '#059669' }, { value: 1, color: '#3B82F6' }]) as any
    assert.equal(option.series[0].type, 'pie')
    assert.deepEqual(option.series[0].data, [
      { value: 2, itemStyle: { color: '#059669' } },
      { value: 1, itemStyle: { color: '#3B82F6' } },
    ])
    const empty = buildMiniDonutOption([]) as any
    assert.equal(empty.series[0].data.length, 1)
    assert.equal(empty.series[0].data[0].itemStyle.color, '#F1F5F9')
  })

  it('迷你分段条按序堆叠且首尾圆角,空数据整条灰占位', () => {
    const option = buildMiniSegmentBarOption([
      { value: 2, color: '#059669' },
      { value: 0, color: '#3B82F6' },
      { value: 1, color: '#CBD5E1' },
    ]) as any
    // 零值分段被过滤,只保留 2 段
    assert.equal(option.series.length, 2)
    assert.ok(option.series.every((series: any) => series.stack === 'segment'))
    assert.deepEqual(option.series[0].data, [2])
    assert.deepEqual(option.series[0].itemStyle.borderRadius, [4, 0, 0, 4])
    assert.deepEqual(option.series[1].itemStyle.borderRadius, [0, 4, 4, 0])
    assert.equal(option.xAxis.max, 3)
    const empty = buildMiniSegmentBarOption([]) as any
    assert.equal(empty.series.length, 1)
    assert.deepEqual(empty.series[0].data, [1])
    assert.equal(empty.series[0].itemStyle.color, '#CBD5E1')
  })
})

describe('buildKpiSparkSeries', () => {
  it('决策按日归桶(UTC 日期键),批准率空决策日为 null', () => {
    const series = buildKpiSparkSeries({
      daily7d,
      logs: [
        { status: 'approved', executedAt: '2026-08-16T10:00:00Z' },
        { status: 'rejected', executedAt: '2026-08-16T12:00:00Z', dryRun: false },
        { status: 'approved', executedAt: '2026-08-17T01:00:00Z' },
        { status: 'success', executedAt: '2026-08-17T02:00:00Z' },
        { status: 'approved', executedAt: '2026-08-16T03:00:00Z', dryRun: true },
        { status: 'approved', executedAt: '2026-07-01T00:00:00Z' },
        { status: 'approved', executedAt: null },
      ],
    })
    assert.deepEqual(series.decisions, [2, 1, 0])
    assert.deepEqual(series.approvalRate, [0.5, 1, null])
    assert.deepEqual(series.sentinelHits, [1, 1, 0])
    assert.deepEqual(series.actionSuccess, [2, 0, 0])
  })

  it('空 daily7d 时返回空序列', () => {
    const series = buildKpiSparkSeries({ daily7d: [], logs: [] })
    assert.deepEqual(series.decisions, [])
    assert.deepEqual(series.approvalRate, [])
  })
})
