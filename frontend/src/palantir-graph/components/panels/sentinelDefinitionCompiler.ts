import type { ActionParameter } from '../../types/ontology'
import type {
  SentinelConditionRow,
  SentinelParameterMode,
} from './sentinelDefinitionModel'

const NUMERIC_TYPES = [
  'number',
  'integer',
  'int',
  'float',
  'double',
  'decimal',
  'currency',
  'date',
  'datetime',
  'time',
]

export const isSentinelNumericType = (type?: string) =>
  !!type && NUMERIC_TYPES.includes(type.toLowerCase())

export function sentinelOperatorsForType(type?: string): string[] {
  if (!type) return ['==', '!=', '>', '>=', '<', '<=']
  const normalized = type.toLowerCase()
  if (isSentinelNumericType(type)) return ['>', '>=', '<', '<=', '==', '!=']
  if (normalized === 'boolean' || normalized === 'bool') return ['==', '!=']
  return ['==', '!=', 'contains']
}

// 始终使用下标形式。JavaScript 的近似 Unicode 正则无法等价判断 Python
// Identifier；例如「价格€」「状态。码」走点语法会生成无法编译的表达式。
const sentinelPropertyRef = (alias: string, property: string) =>
  `${alias}[${JSON.stringify(property)}]`

export const normalizeSentinelParameterSource = (
  spec: Record<string, unknown>,
) => String(spec.sourceType || spec.source || '')
  .trim()
  .toLowerCase()
  .replaceAll('-', '_')

export function sentinelParameterMode(spec: unknown): SentinelParameterMode {
  if (
    typeof spec === 'string'
    && (spec.includes('{{') || spec.includes('}}'))
  ) {
    return 'template'
  }
  if (!spec || typeof spec !== 'object' || Array.isArray(spec)) {
    return spec === undefined ? 'default' : 'constant'
  }
  const source = normalizeSentinelParameterSource(
    spec as Record<string, unknown>,
  )
  if (!source) return 'constant'
  if (source === 'constant' || source === 'literal') return 'constant'
  if (
    source === 'property'
    || source === 'match'
    || source === 'match_property'
  ) return 'property'
  if (source === 'primary_id' || source === 'target_id') return 'primary_id'
  if (
    source === 'event'
    || source === 'event_property'
    || source === 'edge'
  ) return 'event'
  return 'advanced'
}

export function sentinelConstantValue(spec: unknown): unknown {
  if (spec && typeof spec === 'object' && !Array.isArray(spec)) {
    if ('value' in spec) return (spec as { value: unknown }).value
    if ('sourceValue' in spec) {
      return (spec as { sourceValue: unknown }).sourceValue
    }
  }
  return spec
}

export function coerceSentinelConstant(raw: string, type?: string): unknown {
  const normalized = String(type || '').toLowerCase()
  if (
    [
      'number',
      'integer',
      'int',
      'float',
      'double',
      'decimal',
      'currency',
    ].includes(normalized)
  ) {
    if (raw === '') return ''
    const value = Number(raw)
    // 保留非法原文，让发布/执行类型闸门明确报错；绝不能让 NaN 经 JSON
    // 序列化悄悄变成 null。
    return Number.isFinite(value) ? value : raw
  }
  if (normalized === 'boolean' || normalized === 'bool') return raw === 'true'
  if (['json', 'object', 'array'].includes(normalized)) {
    try {
      return JSON.parse(raw)
    } catch {
      return raw
    }
  }
  return raw
}

export function sentinelParameterOptions(parameter: ActionParameter) {
  const raw = (
    parameter.enum
    ?? parameter.options
    ?? parameter.allowedValues
    ?? []
  )
  return raw.map(option => {
    if (
      option !== null
      && typeof option === 'object'
      && !Array.isArray(option)
      && Object.prototype.hasOwnProperty.call(option, 'value')
    ) {
      const item = option as { label?: string; value: unknown }
      return {
        label: item.label || String(item.value),
        value: item.value,
      }
    }
    return { label: String(option), value: option }
  })
}

export function compileSentinelConditionRow(
  row: SentinelConditionRow,
): string | null {
  if (!row.leftAlias || !row.leftProp) return null
  const left = sentinelPropertyRef(row.leftAlias, row.leftProp)
  let right: string
  if (row.rightKind === 'property') {
    if (!row.rightAlias || !row.rightProp) return null
    right = sentinelPropertyRef(row.rightAlias, row.rightProp)
  } else {
    const value = (row.rightValue ?? '').trim()
    if (value === '') return null
    right = (
      /^-?\d+(\.\d+)?$/.test(value)
      || value === 'true'
      || value === 'false'
    ) ? value : JSON.stringify(value)
  }
  if (row.op === 'contains') return `${right} in ${left}`
  return `${left} ${row.op} ${right}`
}

export function compileSentinelCondition(
  rows: SentinelConditionRow[],
  logic: 'and' | 'or',
) {
  const parts = rows
    .map(compileSentinelConditionRow)
    .filter(Boolean) as string[]
  return parts.join(` ${logic} `)
}
