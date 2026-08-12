/**
 * 类型词表归一化与映射兼容判定（纯函数，无依赖）。
 * 从 detail/mapping/mapping-data.ts 抽取为独立模块，供 node 单测直接加载；
 * mapping-data.ts 通过 re-export 保持既有导入路径不变。
 */
export function normalizeType(value?: string): string {
  const type = (value || 'string').trim().toLowerCase().replace(/\(.*\)/, '')
  if (['string', 'text', 'varchar', 'char', 'uuid'].includes(type)) return 'string'
  if (['int', 'integer', 'bigint', 'smallint', 'number', 'float', 'double', 'decimal', 'decimal128'].includes(type)) return 'number'
  if (['date', 'datetime', 'timestamp', 'time'].includes(type)) return 'datetime'
  if (['bool', 'boolean'].includes(type)) return 'boolean'
  if (['array', 'list', 'set'].includes(type)) return 'array'
  if (['json', 'object', 'map'].includes(type)) return 'json'
  return type
}

export function typesCompatible(source?: string, target?: string): boolean {
  const sourceType = normalizeType(source)
  const targetType = normalizeType(target)
  // 人工数据集以 JSON 契约保存结构化值；JSON 数组应能映射到本体 array 属性。
  if (sourceType === 'json' && targetType === 'array') return true
  return sourceType === targetType
}
