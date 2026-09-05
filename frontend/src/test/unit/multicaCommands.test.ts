import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import { isMulticaCommandDraft, matchMulticaCommands } from '../../lib/multicaCommands.ts'

const commands = [
  { command: 'list_agents', title: '查看智能体', description: '列出智能体', usage: '/multica:list_agents', write: false },
  { command: 'list_tasks', title: '查看任务清单', description: '查看任务清单', usage: '/multica:list_tasks', write: false },
  { command: 'create_task', title: '下发任务', description: '创建并指派任务', usage: '/multica:create_task 任务描述…', write: true },
]

describe('isMulticaCommandDraft', () => {
  it('识别命令输入态：前缀、冒号前后、全角冒号', () => {
    assert.equal(isMulticaCommandDraft('/multica'), true)
    assert.equal(isMulticaCommandDraft('/multica:'), true)
    assert.equal(isMulticaCommandDraft('/multica:list_a'), true)
    assert.equal(isMulticaCommandDraft('/Multica：create_task xxx'), true)
    assert.equal(isMulticaCommandDraft('  /multica:'), true)
  })

  it('非命令输入不误伤：普通消息、其它斜杠命令、前缀近似词', () => {
    assert.equal(isMulticaCommandDraft('你好'), false)
    assert.equal(isMulticaCommandDraft('/skills'), false)
    assert.equal(isMulticaCommandDraft('/multicasomething'), false)
    assert.equal(isMulticaCommandDraft('看看 /multica:list_agents'), false)
  })
})

describe('matchMulticaCommands', () => {
  it('命令态无片段返回全部命令，按片段前缀过滤', () => {
    assert.equal(matchMulticaCommands('/multica', commands).length, 3)
    assert.equal(matchMulticaCommands('/multica:', commands).length, 3)
    assert.deepEqual(
      matchMulticaCommands('/multica:list_a', commands).map(item => item.command),
      ['list_agents'],
    )
    assert.deepEqual(matchMulticaCommands('/multica:create_task 修复登录', commands).map(item => item.command), ['create_task'])
  })

  it('未命中片段、非命令输入与空目录都返回空（未配置即不提供命令）', () => {
    assert.deepEqual(matchMulticaCommands('/multica:warp', commands), [])
    assert.deepEqual(matchMulticaCommands('普通消息', commands), [])
    assert.deepEqual(matchMulticaCommands('/multica:list_agents', []), [])
  })
})
