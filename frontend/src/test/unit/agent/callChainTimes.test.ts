import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import { formatTurnElapsed, formatTurnTimes } from '../../../pages/agent/components/callChainTimes.ts'

/** 本地时间构造 ISO 串，规避测试机时区差异 */
const iso = (y: number, month1: number, day: number, h: number, m: number, s: number) =>
  new Date(y, month1 - 1, day, h, m, s).toISOString()

describe('formatTurnTimes', () => {
  const pad = (n: number) => String(n).padStart(2, '0')
  const clock = (value: string) => {
    const d = new Date(value)
    return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
  }

  it('同一天只展示时分秒', () => {
    const start = iso(2026, 8, 27, 14, 23, 5)
    const end = iso(2026, 8, 27, 14, 23, 41)
    assert.deepEqual(formatTurnTimes(start, end), { start: clock(start), end: clock(end) })
  })

  it('跨天时两端补日期前缀', () => {
    const start = iso(2026, 8, 27, 23, 59, 0)
    const end = iso(2026, 8, 28, 0, 5, 30)
    assert.deepEqual(formatTurnTimes(start, end), { start: '8-27 23:59:00', end: '8-28 00:05:30' })
  })

  it('单侧缺省时展示已就绪一侧并占位另一侧', () => {
    const start = iso(2026, 8, 27, 10, 0, 0)
    assert.deepEqual(formatTurnTimes(start, null), { start: clock(start), end: '—' })
  })

  it('全部缺省或非法时刻展示占位符', () => {
    assert.deepEqual(formatTurnTimes(null, null), { start: '—', end: '—' })
    assert.deepEqual(formatTurnTimes('not-a-date', 'not-a-date'), { start: '—', end: '—' })
  })
})

describe('formatTurnElapsed', () => {
  it('亚秒用毫秒', () => {
    assert.equal(formatTurnElapsed(iso(2026, 8, 27, 10, 0, 0), iso(2026, 8, 27, 10, 0, 0)), '0 ms')
    assert.equal(
      formatTurnElapsed(
        new Date(new Date('2026-08-27T02:00:00Z').getTime() + 240).toISOString(),
        new Date(new Date('2026-08-27T02:00:01Z').getTime()).toISOString(),
      ),
      '760 ms',
    )
  })

  it('分钟内用一位小数秒并去掉 .0', () => {
    const base = new Date(2026, 7, 27, 10, 0, 0)
    assert.equal(
      formatTurnElapsed(base.toISOString(), new Date(base.getTime() + 36200).toISOString()),
      '36.2 秒',
    )
    assert.equal(
      formatTurnElapsed(base.toISOString(), new Date(base.getTime() + 40000).toISOString()),
      '40 秒',
    )
  })

  it('超过一分钟按分秒组合', () => {
    const base = new Date(2026, 7, 27, 10, 0, 0)
    assert.equal(
      formatTurnElapsed(base.toISOString(), new Date(base.getTime() + 75000).toISOString()),
      '1 分 15 秒',
    )
    assert.equal(
      formatTurnElapsed(base.toISOString(), new Date(base.getTime() + 120000).toISOString()),
      '2 分',
    )
  })

  it('缺省或时钟偏移（end < start）不产生负耗时', () => {
    assert.equal(formatTurnElapsed(null, iso(2026, 8, 27, 10, 0, 0)), null)
    assert.equal(formatTurnElapsed(iso(2026, 8, 27, 10, 0, 10), iso(2026, 8, 27, 10, 0, 0)), '0 ms')
  })
})
