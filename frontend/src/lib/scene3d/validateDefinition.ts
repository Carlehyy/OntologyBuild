/**
 * 场景定义 DSL 校验器（TS 纯函数）。
 *
 * 完整镜像 backend/app/scenes/validation.py 的规则与文案：
 * 词汇基线来自三维白模技能包（threejs-white-twin）的三件套 Schema，
 * 在此之上做平台化收敛（meta.id kebab-case、对象/装饰枚举、
 * relations 兼容旧 flows 写法、绑定规则仅比较表达式 + else 兜底、
 * 可选 events 事件清单与对象 ontology_concept_id 挂载）。
 *
 * 校验结果统一为 issue 列表 [{path, message}]，空列表即合法。
 * normalizeDefinition 在保存前执行，保证库内定义形态唯一。
 */

export interface ValidationIssue {
  path: string
  message: string
}

export const OBJECT_TYPES = ['office', 'tower', 'warehouse', 'podium', 'plant'] as const
export const OBJECT_EXTRAS = ['parking', 'solar'] as const
export const RELATION_KINDS = ['flow', 'dependency', 'hierarchy'] as const
export const BINDING_STATUSES = ['normal', 'warning', 'alarm'] as const
export const SOURCE_TYPES = ['client', 'polling', 'static', 'websocket'] as const

const KEBAB_RE = /^[a-z0-9]+(-[a-z0-9]+)*$/
const WHEN_RE = /^(>=|<=|==|!=|>|<)\s*(-?\d+(?:\.\d+)?)$/
const BETWEEN_RE = /^between\s+(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)$/
const BG_RE = /^#[0-9a-fA-F]{6}$/

const MAX_OBJECTS = 200
const MAX_BINDINGS = 200
const MAX_EVENTS = 50

type Json = Record<string, unknown>

function isNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

function isPlainObject(value: unknown): value is Json {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function issue(path: string, message: string): ValidationIssue {
  return { path, message }
}

function isKebab(value: unknown): value is string {
  return typeof value === 'string' && KEBAB_RE.test(value)
}

function isNonEmptyStr(value: unknown): value is string {
  return typeof value === 'string' && value.trim().length > 0
}

/** 校验平台场景定义，返回 issue 列表（空列表 = 合法）。 */
export function validateDefinition(definition: unknown): ValidationIssue[] {
  const issues: ValidationIssue[] = []
  if (!isPlainObject(definition)) {
    return [issue('', '场景定义必须是 JSON 对象')]
  }
  const def = definition

  // —— meta ——
  const meta = def.meta
  if (!isPlainObject(meta)) {
    issues.push(issue('meta', '缺少 meta（场景身份信息）'))
  } else {
    if (!isKebab(meta.id)) {
      issues.push(issue('meta.id', '必须是非空 kebab-case 字符串'))
    }
    if (!isNonEmptyStr(meta.name)) {
      issues.push(issue('meta.name', '必须是非空字符串'))
    }
    if (!isNonEmptyStr(meta.version)) {
      issues.push(issue('meta.version', '必须是非空字符串'))
    }
  }

  // —— stage（可选）——
  const stage = def.stage
  if (stage !== null && stage !== undefined) {
    if (!isPlainObject(stage)) {
      issues.push(issue('stage', '必须是 JSON 对象'))
    } else {
      const camera = stage.camera
      if (camera !== null && camera !== undefined) {
        if (!isPlainObject(camera)) {
          issues.push(issue('stage.camera', '必须是 JSON 对象'))
        } else {
          for (const key of ['pos', 'target'] as const) {
            const vec = camera[key]
            if (
              !Array.isArray(vec) || vec.length !== 3 ||
              !vec.every(v => isNumber(v))
            ) {
              issues.push(issue(`stage.camera.${key}`, '必须是长度为 3 的数字数组'))
            }
          }
          const fov = camera.fov
          if (fov !== null && fov !== undefined &&
              (!isNumber(fov) || fov < 10 || fov > 100)) {
            issues.push(issue('stage.camera.fov', '必须是 10~100 的数字'))
          }
        }
      }
      const background = stage.background
      if (background !== null && background !== undefined &&
          !(typeof background === 'string' && BG_RE.test(background))) {
        issues.push(issue('stage.background', '必须是 #RRGGBB 颜色'))
      }
    }
  }

  // —— objects ——
  const rawObjects = def.objects
  if (!Array.isArray(rawObjects) || rawObjects.length === 0) {
    issues.push(issue('objects', '必须是非空数组'))
  } else if (rawObjects.length > MAX_OBJECTS) {
    issues.push(issue('objects', `对象数量不能超过 ${MAX_OBJECTS}`))
  }
  const objects: unknown[] = Array.isArray(rawObjects) ? rawObjects : []
  const seenIds = new Set<string>()
  objects.forEach((obj, index) => {
    const base = `objects[${index}]`
    if (!isPlainObject(obj)) {
      issues.push(issue(base, '必须是 JSON 对象'))
      return
    }
    const objId = obj.id
    if (!isKebab(objId)) {
      issues.push(issue(`${base}.id`, '必须是非空 kebab-case 字符串'))
    } else if (seenIds.has(objId)) {
      issues.push(issue(`${base}.id`, `对象 id 重复：${objId}`))
    } else {
      seenIds.add(objId)
    }
    if (!isNonEmptyStr(obj.label)) {
      issues.push(issue(`${base}.label`, '必须是非空字符串'))
    }
    if (!(OBJECT_TYPES as readonly unknown[]).includes(obj.type)) {
      issues.push(issue(`${base}.type`, `必须是 ${OBJECT_TYPES.join('/')} 之一`))
    }
    const layout = obj.layout
    if (!isPlainObject(layout)) {
      issues.push(issue(`${base}.layout`, '缺少 layout 布局信息'))
    } else {
      for (const key of ['x', 'z'] as const) {
        if (!isNumber(layout[key])) {
          issues.push(issue(`${base}.layout.${key}`, '必须是数字'))
        }
      }
      for (const key of ['w', 'd', 'h'] as const) {
        const value = layout[key]
        if (!isNumber(value) || value <= 0) {
          issues.push(issue(`${base}.layout.${key}`, '必须是正数'))
        }
      }
    }
    const extras = obj.extras
    if (extras !== null && extras !== undefined) {
      if (!Array.isArray(extras)) {
        issues.push(issue(`${base}.extras`, '必须是数组'))
      } else {
        for (const extra of extras) {
          if (!(OBJECT_EXTRAS as readonly unknown[]).includes(extra)) {
            issues.push(
              issue(`${base}.extras`, `未知装饰项 ${String(extra)}，可选：${OBJECT_EXTRAS.join('/')}`))
          }
        }
      }
    }
    const conceptId = obj.ontology_concept_id
    if (conceptId !== null && conceptId !== undefined) {
      if (!isNonEmptyStr(conceptId)) {
        issues.push(issue(`${base}.ontology_concept_id`, '必须是非空字符串'))
      } else if (conceptId.length > 128) {
        issues.push(issue(`${base}.ontology_concept_id`, '长度不能超过 128'))
      }
    }
  })

  // —— relations（兼容旧 flows 写法）——
  let relations = def.relations ?? null
  const flows = def.flows ?? null
  if (relations === null && flows !== null) {
    relations = flowsToRelations(flows)
  }
  if (relations !== null) {
    if (!Array.isArray(relations)) {
      issues.push(issue('relations', '必须是数组'))
    } else {
      relations.forEach((rel, index) => {
        const base = `relations[${index}]`
        if (!isPlainObject(rel)) {
          issues.push(issue(base, '必须是 JSON 对象'))
          return
        }
        for (const endpointKey of ['from', 'to'] as const) {
          const endpoint = rel[endpointKey]
          if (!seenIds.has(endpoint as string)) {
            issues.push(issue(
              `${base}.${endpointKey}`,
              `引用了不存在的对象 id：${String(endpoint)}`))
          }
        }
        const kind = rel.kind === undefined || rel.kind === null ? 'flow' : rel.kind
        if (!(RELATION_KINDS as readonly unknown[]).includes(kind)) {
          issues.push(issue(
            `${base}.kind`,
            `必须是 ${RELATION_KINDS.join('/')} 之一`))
        }
      })
    }
  }

  // —— dataBindings ——
  const bindings = def.dataBindings
  if (bindings !== null && bindings !== undefined) {
    if (!Array.isArray(bindings)) {
      issues.push(issue('dataBindings', '必须是数组'))
    } else if (bindings.length > MAX_BINDINGS) {
      issues.push(issue('dataBindings', `绑定数量不能超过 ${MAX_BINDINGS}`))
    } else {
      bindings.forEach((binding, index) => {
        const base = `dataBindings[${index}]`
        if (!isPlainObject(binding)) {
          issues.push(issue(base, '必须是 JSON 对象'))
          return
        }
        if (!seenIds.has(binding.target as string)) {
          issues.push(issue(
            `${base}.target`,
            `引用了不存在的对象 id：${String(binding.target)}`))
        }
        const rules = binding.rules
        if (!Array.isArray(rules) || rules.length === 0) {
          issues.push(issue(`${base}.rules`, '必须是非空规则数组'))
          return
        }
        rules.forEach((rule, ruleIndex) => {
          const ruleBase = `${base}.rules[${ruleIndex}]`
          if (!isPlainObject(rule)) {
            issues.push(issue(ruleBase, '必须是 JSON 对象'))
            return
          }
          const when = rule.when
          const whenOk = typeof when === 'string' &&
            (when === 'else' || WHEN_RE.test(when.trim()) || BETWEEN_RE.test(when.trim()))
          if (!whenOk) {
            issues.push(issue(
              `${ruleBase}.when`,
              '必须是 else 或形如 "> 95" / "between 60 85" 的表达式'))
          }
          if (!(BINDING_STATUSES as readonly unknown[]).includes(rule.status)) {
            issues.push(issue(
              `${ruleBase}.status`,
              `必须是 ${BINDING_STATUSES.join('/')} 之一`))
          }
        })
        const lastRule = rules[rules.length - 1]
        if (isPlainObject(lastRule) && lastRule.when !== 'else') {
          issues.push(issue(`${base}.rules`, '最后一条规则必须是 else 兜底'))
        }
      })
    }
  }

  // —— events（可选）——
  const events = def.events
  if (events !== null && events !== undefined) {
    if (!Array.isArray(events)) {
      issues.push(issue('events', '必须是数组'))
    } else if (events.length > MAX_EVENTS) {
      issues.push(issue('events', `事件数量不能超过 ${MAX_EVENTS}`))
    } else {
      const seenEventKeys = new Set<string>()
      events.forEach((event, index) => {
        const base = `events[${index}]`
        if (!isPlainObject(event)) {
          issues.push(issue(base, '必须是 JSON 对象'))
          return
        }
        const key = event.key
        if (!isKebab(key)) {
          issues.push(issue(`${base}.key`, '必须是非空 kebab-case 字符串'))
        } else if (seenEventKeys.has(key)) {
          issues.push(issue(`${base}.key`, `事件 key 重复：${key}`))
        } else {
          seenEventKeys.add(key)
        }
        if (!isNonEmptyStr(event.label)) {
          issues.push(issue(`${base}.label`, '必须是非空字符串'))
        } else if (event.label.length > 80) {
          issues.push(issue(`${base}.label`, '长度不能超过 80'))
        }
        const objectId = event.objectId
        if (objectId !== null && objectId !== undefined &&
            !seenIds.has(objectId as string)) {
          issues.push(issue(
            `${base}.objectId`,
            `引用了不存在的对象 id：${String(objectId)}`))
        }
        const description = event.description
        if (description !== null && description !== undefined) {
          // 与后端镜像一致：非字符串先报类型，再校验长度上限
          if (typeof description !== 'string') {
            issues.push(issue(`${base}.description`, '必须是字符串'))
          } else if (description.length > 200) {
            issues.push(issue(`${base}.description`, '长度不能超过 200'))
          }
        }
      })
    }
  }

  // —— sources（可选）——
  const sources = def.sources
  if (sources !== null && sources !== undefined) {
    if (!isPlainObject(sources)) {
      issues.push(issue('sources', '必须是 JSON 对象'))
    } else {
      for (const [sourceName, source] of Object.entries(sources)) {
        const base = `sources.${sourceName}`
        if (!isPlainObject(source)) {
          issues.push(issue(base, '必须是 JSON 对象'))
          continue
        }
        if (!(SOURCE_TYPES as readonly unknown[]).includes(source.type)) {
          issues.push(issue(
            `${base}.type`,
            `必须是 ${SOURCE_TYPES.join('/')} 之一`))
        }
        if (source.type === 'polling') {
          const interval = source.interval
          if (!isNumber(interval) || interval < 500) {
            issues.push(issue(`${base}.interval`, '轮询间隔不能低于 500ms'))
          }
          if (typeof source.url !== 'string' || !source.url) {
            issues.push(issue(`${base}.url`, '轮询源必须提供 url'))
          }
        }
      }
    }
  }

  return issues
}

function flowsToRelations(flows: unknown): Json[] {
  const normalized: Json[] = []
  if (Array.isArray(flows)) {
    for (const flow of flows) {
      if (
        Array.isArray(flow) && flow.length === 2 &&
        flow.every(v => typeof v === 'string')
      ) {
        normalized.push({ from: flow[0], to: flow[1], kind: 'flow' })
      }
    }
  }
  return normalized
}

/** 保存前的形态归一：flows → relations(kind=flow)。其余键原样保留。 */
export function normalizeDefinition(definition: Json): Json {
  const normalized = { ...definition }
  if (!('relations' in normalized) && 'flows' in normalized) {
    normalized.relations = flowsToRelations(normalized.flows)
    delete normalized.flows
  } else {
    delete normalized.flows
  }
  return normalized
}
