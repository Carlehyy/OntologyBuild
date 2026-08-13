import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import {
  DIAGRAM_TABS,
  canvasProcessNames,
  diagramTargetOptions,
  diagramTargetPlaceholder,
  elementBadges,
} from '../../../pages/explore/canvasPanelLogic.ts'
import type { BusinessCanvas } from '../../../api/exploration.ts'


describe('elementBadges · 流程模型', () => {
  it('完整流程展示步数/分支/指标，异常分支追加标记', () => {
    const badges = elementBadges('processes', {
      id: 'p1', name: 'procure_to_pay', display_name: '采购到付款',
      steps: [{ seq: 1, name: '提交申请' }, { seq: 2, name: '审批' }],
      branches: [
        { from_step: 2, to_step: 3, condition: '金额不超过五万', kind: 'normal' },
        { from_step: 2, to_step: null, condition: '审批驳回', kind: 'exception' },
      ],
      metrics: [{ name: 'cycle_time', formula: 'avg(付款时间 - 申请时间)' }],
    })
    assert.deepEqual(badges, ['2 步', '2 分支', '1 指标', '含异常路径'])
  })

  it('线性流程无分支无指标时不追加异常标记', () => {
    const badges = elementBadges('processes', {
      id: 'p2', name: 'onboarding',
      steps: [{ seq: 1, name: '登记' }, { seq: 2, name: '开通账号' }],
      branches: [], metrics: [],
    })
    assert.deepEqual(badges, ['2 步'])
  })

  it('空流程与缺省字段不产生徽标', () => {
    assert.deepEqual(elementBadges('processes', { id: 'p3', name: 'empty' }), [])
  })

  it('kind 缺省按 normal 处理，不误报异常路径', () => {
    const badges = elementBadges('processes', {
      id: 'p4', name: 'default_kind',
      steps: [{ seq: 1, name: '提交' }],
      branches: [{ from_step: 1, to_step: null, condition: '无条件退出' }],
    })
    assert.deepEqual(badges, ['1 步', '1 分支'])
  })
})

describe('elementBadges · 存量口径回归', () => {
  it('场景仍只展示步数', () => {
    assert.deepEqual(
      elementBadges('scenarios', { id: 's1', name: 'sc', steps: ['甲', '乙', '丙'] }),
      ['3 步'],
    )
  })

  it('对象展示属性/关系/主键', () => {
    assert.deepEqual(
      elementBadges('objects', {
        id: 'o1', name: 'Order', key_attribute: 'order_no',
        attributes: [{ name: 'order_no' }], relations: [{ target: 'Customer' }],
      }),
      ['1 属性', '1 关系', '主键 order_no'],
    )
  })
})

describe('DIAGRAM_TABS 与 target 选项', () => {
  const names = {
    scenarioNames: ['紧急采购'],
    objectNames: ['工单'],
    processNames: ['采购到付款'],
  }

  it('flow/sequence 保持 scenario 口径，state 为 object，er 无需 target', () => {
    const byKind = Object.fromEntries(DIAGRAM_TABS.map(tab => [tab.kind, tab.needsTarget]))
    assert.equal(byKind.flow, 'scenario')
    assert.equal(byKind.sequence, 'scenario')
    assert.equal(byKind.state, 'object')
    assert.equal(byKind.er, undefined)
  })

  it('scenario 口径合并场景名与流程名（flow/sequence 的 target 两类皆可）', () => {
    assert.deepEqual(diagramTargetOptions('scenario', names), ['紧急采购', '采购到付款'])
  })

  it('object 与 process 分支各自独立', () => {
    assert.deepEqual(diagramTargetOptions('object', names), ['工单'])
    assert.deepEqual(diagramTargetOptions('process', names), ['采购到付款'])
  })

  it('无 target 口径返回空选项', () => {
    assert.deepEqual(diagramTargetOptions(undefined, names), [])
  })

  it('默认文案随口径区分', () => {
    assert.equal(diagramTargetPlaceholder('scenario'), '默认场景或流程（第一个）')
    assert.equal(diagramTargetPlaceholder('object'), '自动选择对象')
    assert.equal(diagramTargetPlaceholder('process'), '默认流程（第一个）')
  })
})

describe('canvasProcessNames', () => {
  it('display_name 优先，缺省回退 name', () => {
    const canvas = {
      objects: [], actors: [], behaviors: [], events: [], rules: [], scenarios: [],
      processes: [
        { id: 'p1', name: 'procure_to_pay', display_name: '采购到付款' },
        { id: 'p2', name: 'onboarding' },
      ],
    }
    assert.deepEqual(canvasProcessNames(canvas), ['采购到付款', 'onboarding'])
  })

  it('空画布与缺失 processes 键的旧画布均回退空数组', () => {
    assert.deepEqual(canvasProcessNames(null), [])
    const legacy = { objects: [], actors: [], behaviors: [], events: [], rules: [], scenarios: [] }
    assert.deepEqual(canvasProcessNames(legacy as unknown as BusinessCanvas), [])
  })
})
