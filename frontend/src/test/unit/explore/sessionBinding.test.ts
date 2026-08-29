import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import {
  mergeDraftBindingOptions,
  parsePendingNewSession,
  parseSessionBinding,
  resolveBoundSession,
  sessionBindingKey,
  shouldAutoSelectLatestSession,
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

describe('parsePendingNewSession', () => {
  it('session=new 识别为待建新会话意图', () => {
    assert.equal(parsePendingNewSession(params({ session: 'new' })), true)
    assert.equal(parsePendingNewSession(params({ session: ' new ' })), true)
  })

  it('无参数或取值不符按普通进入处理', () => {
    assert.equal(parsePendingNewSession(params({})), false)
    assert.equal(parsePendingNewSession(params({ session: '' })), false)
    assert.equal(parsePendingNewSession(params({ session: 'old' })), false)
    assert.equal(parsePendingNewSession(params({ ontologyId: 'ont-1' })), false)
  })
})

describe('shouldAutoSelectLatestSession', () => {
  it('普通进入且未选中会话 → 自动恢复最近会话', () => {
    assert.equal(shouldAutoSelectLatestSession(false, false), true)
  })

  it('已选中会话时不自动切换', () => {
    assert.equal(shouldAutoSelectLatestSession(false, true), false)
  })

  it('待建新会话态保持空白，不恢复最近会话', () => {
    assert.equal(shouldAutoSelectLatestSession(true, false), false)
    assert.equal(shouldAutoSelectLatestSession(true, true), false)
  })
})

describe('mergeDraftBindingOptions', () => {
  const item = (ontologyId: string, versionId: string, draftCreatedAt: string | null, ontologyName = `本体-${ontologyId}`) => ({
    ontologyId,
    ontologyName,
    domain: '测试领域',
    versionId,
    versionNumber: 'v1.1',
    versionLabel: '业务探索合并',
    draftCreatedAt,
  })

  it('空列表与缺失字段：空输入返回空数组，缺 ontologyId/versionId 的行被丢弃', () => {
    assert.deepEqual(mergeDraftBindingOptions([]), [])
    assert.deepEqual(mergeDraftBindingOptions([
      item('', 'ver-1', '2026-01-01T00:00:00Z'),
      item('ont-1', '', '2026-01-01T00:00:00Z'),
    ]), [])
  })

  it('同一本体只保留最新编辑中草稿（draftCreatedAt 降序取首个）', () => {
    const options = mergeDraftBindingOptions([
      item('ont-1', 'ver-old', '2026-01-01T00:00:00Z'),
      item('ont-1', 'ver-new', '2026-02-01T00:00:00Z'),
    ])
    assert.equal(options.length, 1)
    assert.equal(options[0].versionId, 'ver-new')
  })

  it('整体按最近草稿活动倒序；缺失时间或同刻保持后端返回顺序', () => {
    const options = mergeDraftBindingOptions([
      item('ont-a', 'ver-a', null),
      item('ont-b', 'ver-b', '2026-03-01T00:00:00Z'),
      item('ont-c', 'ver-c', '2026-02-01T00:00:00Z'),
    ])
    assert.deepEqual(options.map(o => o.ontologyId), ['ont-b', 'ont-c', 'ont-a'])
    const tied = mergeDraftBindingOptions([
      item('ont-x', 'ver-x', '2026-01-01T00:00:00Z'),
      item('ont-y', 'ver-y', '2026-01-01T00:00:00Z'),
    ])
    assert.deepEqual(tied.map(o => o.ontologyId), ['ont-x', 'ont-y'])
  })

  it('选项只携带渲染所需字段，不含时间戳', () => {
    const [option] = mergeDraftBindingOptions([item('ont-1', 'ver-1', '2026-01-01T00:00:00Z')])
    assert.deepEqual(Object.keys(option).sort(), [
      'domain', 'ontologyId', 'ontologyName', 'versionId', 'versionLabel', 'versionNumber',
    ])
  })
})
