import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import { rankOntologyCards } from '../../../pages/agent/components/ontologyCardRanking.ts'

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
