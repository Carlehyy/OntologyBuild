import type { ColumnDefinition } from '../../api/v2/pipelines.ts'
import { inferColumnTypes } from './script/scriptUtils.ts'

const CONTRACT_TYPES = new Set([
  'string',
  'integer',
  'float',
  'boolean',
  'timestamp',
  'json',
])

const LEGACY_TYPE_MAP: Record<string, string> = {
  int: 'integer',
  bool: 'boolean',
  datetime: 'timestamp',
  str: 'string',
  number: 'float',
}

/** Normalize historical contract spellings to the platform vocabulary. */
export function normalizeContractType(value?: string): string {
  const raw = (value || 'string').toLowerCase()
  const normalized = LEGACY_TYPE_MAP[raw] || raw
  return CONTRACT_TYPES.has(normalized) ? normalized : 'string'
}

/**
 * Build field contracts from a dry-run sample.
 *
 * Existing definitions always win so a user's prior manual choices are never
 * overwritten.  Only newly discovered columns receive a type recommendation.
 */
export function buildInitialColumnDefinitions(
  columns: string[],
  sample: Array<Record<string, unknown>>,
  existing?: ColumnDefinition[] | null,
  declaredPrimaryKeys?: Set<string>,
): ColumnDefinition[] {
  const existingMap = new Map(
    (existing || []).map(definition => [
      definition.source_key || definition.field_key,
      definition,
    ]),
  )
  const inferredTypes = inferColumnTypes(sample, columns)

  return columns.map(column => {
    const previous = existingMap.get(column)
    const definition: ColumnDefinition = previous ? {
      source_key: previous.source_key || previous.field_key,
      field_key: previous.field_key,
      field_name: previous.field_name || previous.field_key,
      field_type: normalizeContractType(previous.field_type),
      is_primary_key: !!previous.is_primary_key,
      nullable: previous.nullable !== false,
    } : {
      source_key: column,
      field_key: column,
      field_name: column,
      field_type: normalizeContractType(inferredTypes[column]),
      is_primary_key: false,
      nullable: true,
    }

    if (declaredPrimaryKeys?.has(definition.field_key)) {
      definition.is_primary_key = true
    }
    if (definition.is_primary_key) definition.nullable = false
    return definition
  })
}
