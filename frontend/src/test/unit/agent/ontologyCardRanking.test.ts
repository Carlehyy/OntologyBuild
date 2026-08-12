import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import { rankOntologyCards } from '../../../pages/agent/components/ontologyCardRanking.ts'
import {
  circularCardPosition,
  maxVisibleSideRings,
  normalizeCardIndex,
} from '../../../pages/agent/components/ontologyCarouselMath.ts'

const card = (
  name: string,
  clicks: number | undefined,
  updatedAt = '2026-08-01T00:00:00+00:00',
) => ({ name, assistant_card_clicks: clicks, updated_at: updatedAt })

describe('rankOntologyCards', () => {
  it('按全局选用次数降序排列', () => {
    const ranked = rankOntologyCards([
      card('低频', 1),
      card('高频', 9),
      card('中频', 4),
    ])
    assert.deepEqual(ranked.map(item => item.name), ['高频', '中频', '低频'])
  })

  it('缺省或空计数按 0 处理', () => {
    const ranked = rankOntologyCards([
      card('无计数字段', undefined),
      { name: '空计数', assistant_card_clicks: null, updated_at: '2026-08-01T00:00:00+00:00' },
      card('有计数', 1),
    ])
    assert.equal(ranked[0].name, '有计数')
  })

  it('次数并列时按最近更新时间降序', () => {
    const ranked = rankOntologyCards([
      card('较早', 2, '2026-07-01T00:00:00+00:00'),
      card('较新', 2, '2026-08-01T00:00:00+00:00'),
    ])
    assert.deepEqual(ranked.map(item => item.name), ['较新', '较早'])
  })

  it('次数与时间并列时按名称稳定排序，且不改动原数组', () => {
    const source = [card('乙本体', 0), card('甲本体', 0)]
    const ranked = rankOntologyCards(source)
    assert.deepEqual(ranked.map(item => item.name), ['甲本体', '乙本体'])
    assert.deepEqual(source.map(item => item.name), ['乙本体', '甲本体'])
  })
})


describe('circularCardPosition', () => {
  it('焦点为 0 时左侧环绕展示排名末尾的卡', () => {
    // 5 张卡：0 居中，1/2 在右（热度递减），4/3 环绕到左。
    const positions = [0, 1, 2, 3, 4].map(index => circularCardPosition(index, 0, 5))
    assert.deepEqual(positions, [0, 1, 2, -2, -1])
  })

  it('焦点推进后保持环形连续', () => {
    const positions = [0, 1, 2, 3, 4].map(index => circularCardPosition(index, 2, 5))
    assert.deepEqual(positions, [-2, -1, 0, 1, 2])
  })

  it('焦点越界时位置仍落在环形窗口内（无限轮播）', () => {
    const positions = [0, 1, 2, 3, 4].map(index => circularCardPosition(index, 7, 5))
    assert.deepEqual(positions, [-2, -1, 0, 1, 2])
  })

  it('1~2 张卡退化为线性位置，不环绕', () => {
    assert.equal(circularCardPosition(1, 0, 2), 1)
    assert.equal(circularCardPosition(0, 1, 2), -1)
    assert.equal(circularCardPosition(0, 0, 1), 0)
  })

  it('偶数张卡时接缝卡固定在正半侧', () => {
    const positions = [0, 1, 2, 3].map(index => circularCardPosition(index, 0, 4))
    assert.deepEqual(positions, [0, 1, 2, -1])
  })
})

describe('normalizeCardIndex', () => {
  it('越界焦点规整到合法索引', () => {
    assert.equal(normalizeCardIndex(5, 5), 0)
    assert.equal(normalizeCardIndex(-1, 5), 4)
    assert.equal(normalizeCardIndex(2.4, 5), 2)
    assert.equal(normalizeCardIndex(2.6, 5), 3)
  })

  it('空列表安全返回 0', () => {
    assert.equal(normalizeCardIndex(3, 0), 0)
  })
})


describe('maxVisibleSideRings', () => {
  it('宽度足够时展示两环侧卡', () => {
    // 第二环需要 2*210+123+4=547 的半宽 → 舞台约 1100+
    assert.equal(maxVisibleSideRings(1120, 300, 210), 2)
  })

  it('中等宽度只展示一环侧卡', () => {
    // 第一环需要 210+141+4=355 的半宽 → 舞台约 710+
    assert.equal(maxVisibleSideRings(818, 300, 210), 1)
    assert.equal(maxVisibleSideRings(720, 300, 210), 1)
  })

  it('窄宽度时不展示侧卡', () => {
    assert.equal(maxVisibleSideRings(626, 300, 210), 0)
    assert.equal(maxVisibleSideRings(0, 300, 210), 0)
  })
})
