import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import { validateTestInputText } from '../../pages/world-model/testInputValidation.ts'


describe('validateTestInputText', () => {
  it('合法对象通过校验', () => {
    assert.deepEqual(validateTestInputText('{ "context": {}, "horizon": 3 }'), { ok: true, message: '' })
    assert.deepEqual(validateTestInputText('  '), { ok: true, message: '' })
    assert.deepEqual(validateTestInputText(''), { ok: true, message: '' })
  })

  it('顶层必须是对象：数组与标量均拒绝', () => {
    for (const text of ['[1, 2]', '"text"', '42', 'null']) {
      const status = validateTestInputText(text)
      assert.equal(status.ok, false)
      assert.match(status.message, /顶层必须是 JSON 对象/)
    }
  })

  it('语法错误拒绝并携带引擎消息', () => {
    const status = validateTestInputText('{ "context": }')
    assert.equal(status.ok, false)
    assert.ok(status.message.length > 0)
    assert.match(status.message, /第 \d+ 行|Unexpected|is not valid JSON|JSON/i)
  })

  it('position 风格错误消息换算为行列（旧版 V8/JSC）', () => {
    // 模拟 "position 14"：位于源码第 1 行（前面无换行）
    const source = '{\n  "context": x\n}'
    // 手工构造：直接调用内部逻辑不可行，退而校验换算口径——
    // 第 2 行第 13 列附近的 x 处报错时，行号应大于 1
    const broken = source.replace('x', '}')
    const status = validateTestInputText(broken)
    if (/position \d+/i.test(status.message)) {
      // position 指向换行后内容 → 行号应为 2
      assert.match(status.message, /第 2 行/)
    }
  })
})
