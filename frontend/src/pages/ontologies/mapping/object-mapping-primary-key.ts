import type { MappingObjectType, MappingProperty } from '../detail/mapping/mapping-data'

export type ObjectMappingPrimaryKeyIssue =
  | 'object_primary_key_missing'
  | 'primary_key_property_missing'
  | 'primary_key_edge_missing'

export type ObjectMappingPrimaryKeyResolution =
  | {
      ok: true
      column: string
      property: MappingProperty
      source: 'explicit' | 'edge'
    }
  | {
      ok: false
      issue: ObjectMappingPrimaryKeyIssue
      property?: MappingProperty
    }

/**
 * Resolve the source column that provides the stable identity for one object
 * mapping. The primary-key property must remain visibly mapped so the object's
 * formal property is populated. Existing explicit metadata may represent a
 * deliberate/composite identity, so it takes precedence over the edge column.
 */
export function resolveObjectMappingPrimaryKey(
  object: MappingObjectType,
  visibleFieldMapping: Record<string, string>,
  existingFieldMapping?: Record<string, unknown>,
): ObjectMappingPrimaryKeyResolution {
  const primaryKey = object.primaryKey
  if (!primaryKey) return { ok: false, issue: 'object_primary_key_missing' }

  const property = object.properties.find(candidate => (
    candidate.id === primaryKey || candidate.name === primaryKey
  ))
  if (!property) return { ok: false, issue: 'primary_key_property_missing' }

  const edge = Object.entries(visibleFieldMapping).find(([source, target]) => (
    !source.startsWith('__')
    && (target === property.name || target === property.id)
  ))
  if (!edge) return { ok: false, issue: 'primary_key_edge_missing', property }

  const explicit = existingFieldMapping?.__primary_key__
  if (typeof explicit === 'string' && explicit.trim()) {
    return { ok: true, column: explicit, property, source: 'explicit' }
  }
  return { ok: true, column: edge[0], property, source: 'edge' }
}
