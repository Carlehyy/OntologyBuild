import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import { saveStatusLabel } from '../../../pages/ontologies/detail/tabs/saveStatus.ts'

describe('saveStatusLabel', () => {
  it('pending 状态展示剩余秒数倒计时', () => {
    assert.equal(saveStatusLabel('pending', 3), '3 秒后自动保存')
    assert.equal(saveStatusLabel('pending', 2), '2 秒后自动保存')
    assert.equal(saveStatusLabel('pending', 1), '1 秒后自动保存')
  })

  it('倒计时数值钳位在 1..3 秒', () => {
    assert.equal(saveStatusLabel('pending', 0), '1 秒后自动保存')
    assert.equal(saveStatusLabel('pending', -2), '1 秒后自动保存')
    assert.equal(saveStatusLabel('pending', 5), '3 秒后自动保存')
    assert.equal(saveStatusLabel('pending', 2.6), '3 秒后自动保存')
  })

  it('其余状态使用固定文案', () => {
    assert.equal(saveStatusLabel('saving', 3), '正在保存布局')
    assert.equal(saveStatusLabel('saved', 3), '布局已保存')
    assert.equal(saveStatusLabel('error', 3), '保存失败')
    assert.equal(saveStatusLabel('idle', 3), '拖动后自动保存布局')
  })
})
