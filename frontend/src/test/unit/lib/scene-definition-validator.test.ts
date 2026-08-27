/**
 * 场景定义校验器单测 —— 镜像 backend/app/scenes/validation.py 的行为。
 */
import assert from 'node:assert/strict'
import { describe, it } from 'node:test'
import {
  normalizeDefinition,
  validateDefinition,
} from '../../../lib/scene3d/validateDefinition.ts'

function validDefinition() {
  return {
    meta: { id: 'supply-park', name: '供应链园区', version: '1.0.0' },
    objects: [
      { id: 'warehouse', label: '仓库管理', type: 'warehouse', layout: { x: 26, z: -10, w: 14, d: 20, h: 18 }, ontology_concept_id: 'ont-warehouse' },
      { id: 'production', label: '生产管理', type: 'tower', layout: { x: 26, z: -34, w: 13, d: 11, h: 24 }, extras: ['solar'] },
    ],
    relations: [{ from: 'warehouse', to: 'production', kind: 'flow' }],
    dataBindings: [
      {
        target: 'warehouse',
        source: 'client',
        path: 'warehouse.rate',
        metrics: [['库位利用率', '{value}%']],
        rules: [
          { when: '> 95', status: 'alarm', message: '库位告急' },
          { when: 'between 85 95', status: 'warning', message: '库位偏高' },
          { when: 'else', status: 'normal' },
        ],
      },
    ],
    events: [
      { key: 'temp-alarm', label: '库温告警', objectId: 'warehouse', description: '库内温度超限时触发' },
    ],
    sources: { client: { type: 'client' } },
  }
}

describe('scene definition validator', () => {
  it('合法定义通过（空 issue 列表）', () => {
    assert.deepEqual(validateDefinition(validDefinition()), [])
  })

  it('非对象输入报「场景定义必须是 JSON 对象」', () => {
    assert.deepEqual(validateDefinition([1, 2]), [
      { path: '', message: '场景定义必须是 JSON 对象' },
    ])
    assert.deepEqual(validateDefinition('nope'), validateDefinition(null))
  })

  it('meta.id 非 kebab-case 报错', () => {
    const def = validDefinition()
    def.meta.id = 'Supply Park!'
    const issues = validateDefinition(def)
    assert.ok(issues.some(i => i.path === 'meta.id' && i.message.includes('kebab-case')))
  })

  it('对象类型越界报错且文案列出五种枚举', () => {
    const def = validDefinition()
    ;(def.objects[0] as Record<string, unknown>).type = 'castle'
    const issues = validateDefinition(def)
    const hit = issues.find(i => i.path === 'objects[0].type')
    assert.ok(hit)
    assert.equal(hit.message, '必须是 office/tower/warehouse/podium/plant 之一')
  })

  it('重复对象 id 报错', () => {
    const def = validDefinition()
    ;(def.objects[1] as Record<string, unknown>).id = 'warehouse'
    const issues = validateDefinition(def)
    assert.ok(issues.some(i => i.path === 'objects[1].id' && i.message === '对象 id 重复：warehouse'))
  })

  it('relation 悬空引用 from/to 均报错', () => {
    const def = validDefinition()
    def.relations = [{ from: 'ghost-a', to: 'ghost-b', kind: 'flow' }]
    const issues = validateDefinition(def)
    assert.ok(issues.some(i => i.path === 'relations[0].from' && i.message.includes('ghost-a')))
    assert.ok(issues.some(i => i.path === 'relations[0].to' && i.message.includes('ghost-b')))
  })

  it('binding target 不存在报错', () => {
    const def = validDefinition()
    ;(def.dataBindings![0] as Record<string, unknown>).target = 'missing'
    const issues = validateDefinition(def)
    assert.ok(issues.some(i => i.path === 'dataBindings[0].target' && i.message.includes('missing')))
  })

  it('缺 else 兜底报错；else 不在末尾不报兜底错误', () => {
    const def = validDefinition()
    const rules = (def.dataBindings![0] as Record<string, unknown>).rules as unknown[]
    rules.pop()
    let issues = validateDefinition(def)
    assert.ok(issues.some(i => i.path === 'dataBindings[0].rules' && i.message === '最后一条规则必须是 else 兜底'))

    rules.push({ when: 'else', status: 'normal' })
    rules.unshift({ when: 'else', status: 'normal' }) // else 在中间：合法（仅校验末条）
    issues = validateDefinition(def)
    assert.ok(!issues.some(i => i.path === 'dataBindings[0].rules'))
  })

  it('between 表达式合法，非法表达式与非法 status 报错', () => {
    const def = validDefinition()
    const rules = (def.dataBindings![0] as Record<string, unknown>).rules as Record<string, unknown>[]
    rules[1].when = 'between 60 85'
    rules[1].status = 'critical'
    let issues = validateDefinition(def)
    assert.ok(!issues.some(i => i.path === 'dataBindings[0].rules[1].when'))
    assert.ok(issues.some(i =>
      i.path === 'dataBindings[0].rules[1].status' && i.message === '必须是 normal/warning/alarm 之一'))

    rules[1].when = '~ 60'
    issues = validateDefinition(def)
    assert.ok(issues.some(i =>
      i.path === 'dataBindings[0].rules[1].when'
      && i.message === '必须是 else 或形如 "> 95" / "between 60 85" 的表达式'))
  })

  it('flows 二元组归一化为 relations(kind=flow)；非二元组静默丢弃', () => {
    const def = validDefinition() as Record<string, unknown>
    delete def.relations
    def.flows = [['warehouse', 'production'], ['bad']]
    const normalized = normalizeDefinition(def)
    assert.deepEqual(normalized.relations, [
      { from: 'warehouse', to: 'production', kind: 'flow' },
    ])
    assert.ok(!('flows' in normalized))
    // 归一化后的定义应通过校验
    assert.deepEqual(validateDefinition(normalized), [])
  })

  it('已有 relations 时 flows 被丢弃且不影响 relations', () => {
    const def = validDefinition() as Record<string, unknown>
    def.flows = [['a', 'b']]
    const normalized = normalizeDefinition(def)
    assert.deepEqual(normalized.relations, [{ from: 'warehouse', to: 'production', kind: 'flow' }])
    assert.ok(!('flows' in normalized))
  })

  it('polling interval 小于 500 报错并要求 url；未知 source type 报错', () => {
    const def = validDefinition() as Record<string, unknown>
    def.sources = {
      slow: { type: 'polling', interval: 499, url: '/api/x' },
      noUrl: { type: 'polling', interval: 1000 },
      weird: { type: 'grpc' },
    }
    const issues = validateDefinition(def)
    assert.ok(issues.some(i => i.path === 'sources.slow.interval' && i.message === '轮询间隔不能低于 500ms'))
    assert.ok(issues.some(i => i.path === 'sources.noUrl.url' && i.message === '轮询源必须提供 url'))
    assert.ok(issues.some(i => i.path === 'sources.weird.type' && i.message === '必须是 client/polling/static/websocket 之一'))
  })

  it('stage 校验：camera 向量长度、fov 区间、background 颜色', () => {
    const def = validDefinition() as Record<string, unknown>
    def.stage = {
      camera: { pos: [1, 2], target: [0, 0, -4], fov: 200 },
      background: 'white',
    }
    const issues = validateDefinition(def)
    assert.ok(issues.some(i => i.path === 'stage.camera.pos' && i.message === '必须是长度为 3 的数字数组'))
    assert.ok(issues.some(i => i.path === 'stage.camera.fov' && i.message === '必须是 10~100 的数字'))
    assert.ok(issues.some(i => i.path === 'stage.background' && i.message === '必须是 #RRGGBB 颜色'))

    ;(def.stage as Record<string, unknown>).background = '#EDF0F5'
    ;((def.stage as Record<string, unknown>).camera as Record<string, unknown>).pos = [92, 78, 92]
    ;((def.stage as Record<string, unknown>).camera as Record<string, unknown>).fov = 30
    assert.deepEqual(validateDefinition(def), [])
  })

  it('layout 缺失 / 非正数报错；extras 越界报错', () => {
    const def = validDefinition()
    delete (def.objects[0] as Record<string, unknown>).layout
    ;((def.objects[1] as Record<string, unknown>).layout as Record<string, number>).h = 0
    ;(def.objects[1] as Record<string, unknown>).extras = ['fountain']
    const issues = validateDefinition(def)
    assert.ok(issues.some(i => i.path === 'objects[0].layout' && i.message === '缺少 layout 布局信息'))
    assert.ok(issues.some(i => i.path === 'objects[1].layout.h' && i.message === '必须是正数'))
    assert.ok(issues.some(i => i.path.startsWith('objects[1].extras') && i.message.includes('fountain')))
  })

  it('events 与 ontology_concept_id 均合法时通过（空 issue 列表）', () => {
    assert.deepEqual(validateDefinition(validDefinition()), [])
  })

  it('事件 key 非 kebab-case 报错', () => {
    const def = validDefinition()
    ;(def.events![0] as Record<string, unknown>).key = 'Bad Key'
    const issues = validateDefinition(def)
    assert.ok(issues.some(i => i.path === 'events[0].key' && i.message.includes('kebab-case')))
  })

  it('事件 key 重复与未知 objectId 报错', () => {
    const def = validDefinition() as Record<string, unknown>
    def.events = [
      { key: 'first-hit', label: '首个事件', objectId: 'warehouse' },
      { key: 'first-hit', label: '重复事件', objectId: 'ghost' },
    ]
    const issues = validateDefinition(def)
    assert.ok(issues.some(i =>
      i.path === 'events[1].key' && i.message === '事件 key 重复：first-hit'))
    assert.ok(issues.some(i =>
      i.path === 'events[1].objectId' && i.message === '引用了不存在的对象 id：ghost'))
  })

  it('事件 label 非空且不超过 80；description 不超过 200', () => {
    const def = validDefinition()
    const event = def.events![0] as Record<string, unknown>
    event.label = ''
    let issues = validateDefinition(def)
    assert.ok(issues.some(i => i.path === 'events[0].label' && i.message === '必须是非空字符串'))

    event.label = 'a'.repeat(81)
    event.description = 'b'.repeat(201)
    issues = validateDefinition(def)
    assert.ok(issues.some(i => i.path === 'events[0].label' && i.message === '长度不能超过 80'))
    assert.ok(issues.some(i =>
      i.path === 'events[0].description' && i.message === '长度不能超过 200'))

    event.label = '库温告警'
    event.description = 123
    issues = validateDefinition(def)
    assert.ok(issues.some(i =>
      i.path === 'events[0].description' && i.message === '必须是字符串'))
  })

  it('events 非数组报错；超过 50 条报错', () => {
    const def = validDefinition() as Record<string, unknown>
    def.events = 'oops'
    let issues = validateDefinition(def)
    assert.ok(issues.some(i => i.path === 'events' && i.message === '必须是数组'))

    def.events = Array.from({ length: 51 }, (_, index) => ({ key: 'ev-' + index, label: '事件' + index }))
    issues = validateDefinition(def)
    assert.ok(issues.some(i => i.path === 'events' && i.message === '事件数量不能超过 50'))
  })

  it('ontology_concept_id 空串/超长报错，合法值通过', () => {
    const def = validDefinition()
    ;(def.objects[0] as Record<string, unknown>).ontology_concept_id = ''
    let issues = validateDefinition(def)
    assert.ok(issues.some(i =>
      i.path === 'objects[0].ontology_concept_id' && i.message === '必须是非空字符串'))

    ;(def.objects[0] as Record<string, unknown>).ontology_concept_id = 'c'.repeat(129)
    issues = validateDefinition(def)
    assert.ok(issues.some(i =>
      i.path === 'objects[0].ontology_concept_id' && i.message === '长度不能超过 128'))

    ;(def.objects[0] as Record<string, unknown>).ontology_concept_id = 'ont-warehouse'
    assert.deepEqual(validateDefinition(def), [])
  })
})
