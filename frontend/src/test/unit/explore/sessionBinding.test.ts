import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import {
  parseSessionBinding,
  resolveBoundSession,
  sessionBindingKey,
} from '../../../pages/explore/sessionBinding.ts'


const params = (entries: Record<string, string>) => ({
  get: (key: string) => entries[key] ?? null,
})

const session = (id: string, ontologyId: string | null = null, ontologyVersionId: string | null = null) => ({
  id,
  ontologyId,
  ontologyVersionId,
})

describe('parseSessionBinding', () => {
  it('ontologyId 与 versionId 齐备时解析为绑定锚点', () => {
    assert.deepEqual(
      parseSessionBinding(params({ ontologyId: 'ont-1', versionId: 'ver-2' })),
      { ontologyId: 'ont-1', versionId: 'ver-2' },
    )
  })

  it('缺任一参数按非绑定态处理', () => {
    assert.equal(parseSessionBinding(params({ ontologyId: 'ont-1' })), null)
    assert.equal(parseSessionBinding(params({ versionId: 'ver-2' })), null)
    assert.equal(parseSessionBinding(params({})), null)
  })

  it('参数去空白；全空白视同缺失', () => {
    assert.deepEqual(
      parseSessionBinding(params({ ontologyId: ' ont-1 ', versionId: ' ver-2 ' })),
      { ontologyId: 'ont-1', versionId: 'ver-2' },
    )
    assert.equal(parseSessionBinding(params({ ontologyId: '  ', versionId: 'ver-2' })), null)
  })

  it('sessionBindingKey 以 本体:版本 作为代际标识', () => {
    assert.equal(sessionBindingKey({ ontologyId: 'ont-1', versionId: 'ver-2' }), 'ont-1:ver-2')
  })
})

describe('resolveBoundSession', () => {
  const binding = { ontologyId: 'ont-1', versionId: 'ver-2' }

  it('当前会话已是目标绑定 → 不再动作', () => {
    const sessions = [session('s1', 'ont-1', 'ver-2')]
    assert.deepEqual(resolveBoundSession(sessions, binding, 's1'), { action: 'none' })
  })

  it('列表中存在同绑定会话 → 选中它（即使当前选中了别的会话）', () => {
    const sessions = [
      session('s1'),
      session('s2', 'ont-1', 'ver-2'),
      session('s3', 'ont-1', 'ver-9'),
    ]
    assert.deepEqual(resolveBoundSession(sessions, binding, 's1'), { action: 'select', sessionId: 's2' })
    assert.deepEqual(resolveBoundSession(sessions, binding, ''), { action: 'select', sessionId: 's2' })
  })

  it('无匹配（含绑定到其他版本的会话）→ 创建绑定会话', () => {
    const sessions = [session('s1'), session('s3', 'ont-1', 'ver-9'), session('s4', 'ont-2', 'ver-2')]
    assert.deepEqual(resolveBoundSession(sessions, binding, 's1'), { action: 'create' })
    assert.deepEqual(resolveBoundSession([], binding, ''), { action: 'create' })
  })

  it('历史会话缺绑定字段（旧数据）不参与匹配', () => {
    const legacy = [{ id: 's1' }]
    assert.deepEqual(resolveBoundSession(legacy, binding, 's1'), { action: 'create' })
  })
})
