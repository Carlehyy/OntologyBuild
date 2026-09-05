import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import { isSlashCommandDraft, matchSlashCommands, slashCommandToken } from '../../lib/slashCommands.ts'

const commands = [
  { command: 'list_agents', title: '查看智能体', description: '列出智能体', usage: '/multica:list_agents', write: false },
  { command: 'list_tasks', title: '查看任务清单', description: '查看任务清单', usage: '/multica:list_tasks [过滤条件]', write: false },
  { command: 'create_task', title: '下发任务', description: '创建并指派任务', usage: '/multica:create_task 任务描述…', write: true },
]

describe('isSlashCommandDraft', () => {
  it('斜杠开头且无空白即命令选择态（含仅一个 /）', () => {
    assert.equal(isSlashCommandDraft('/'), true)
    assert.equal(isSlashCommandDraft('/m'), true)
    assert.equal(isSlashCommandDraft('/multica:'), true)
    assert.equal(isSlashCommandDraft('  /multica:l'), true)
  })

  it('参数区或普通输入不是选择态', () => {
    assert.equal(isSlashCommandDraft('/multica:list_agents 进行中的'), false)
    assert.equal(isSlashCommandDraft('你好'), false)
    assert.equal(isSlashCommandDraft('看看 /multica'), false)
  })
})

describe('slashCommandToken', () => {
  it('usage 去掉参数占位得到完整命令 token', () => {
    assert.equal(slashCommandToken(commands[1]), '/multica:list_tasks')
    assert.equal(slashCommandToken(commands[0]), '/multica:list_agents')
  })
})

describe('matchSlashCommands', () => {
  it('输入 / 列出全部，逐字符前缀收窄', () => {
    assert.equal(matchSlashCommands('/', commands).length, 3)
    assert.equal(matchSlashCommands('/m', commands).length, 3)
    assert.deepEqual(
      matchSlashCommands('/multica:l', commands).map(item => item.command),
      ['list_agents', 'list_tasks'],
    )
    assert.deepEqual(
      matchSlashCommands('/multica:list_a', commands).map(item => item.command),
      ['list_agents'],
    )
  })

  it('未知前缀、参数区、非命令输入与空目录（未配置集成）都返回空', () => {
    assert.deepEqual(matchSlashCommands('/x', commands), [])
    assert.deepEqual(matchSlashCommands('/multica:warp', commands), [])
    assert.deepEqual(matchSlashCommands('/multica:list_agents ', commands), [])
    assert.deepEqual(matchSlashCommands('普通消息', commands), [])
    assert.deepEqual(matchSlashCommands('/', []), [])
  })
})
