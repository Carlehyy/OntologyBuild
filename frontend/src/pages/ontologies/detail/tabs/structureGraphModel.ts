import dagre from '@dagrejs/dagre'
import { MarkerType, type Edge, type Node } from '@xyflow/react'

export interface StructureProperty {
  id?: string
  name: string
  displayName?: string
  type?: string
  required?: boolean
  description?: string
  source?: string
  functionId?: string
}

export interface StructureObject {
  id: string
  name: string
  displayName: string
  description?: string
  primaryKey?: string | null
  positionX?: number
  positionY?: number
  color?: string
  icon?: string
  properties: StructureProperty[]
}

export interface StructureLink {
  id: string
  name: string
  displayName: string
  description?: string
  sourceObjectTypeId: string
  targetObjectTypeId: string
  cardinality?: string
  sourceRole?: string
  targetRole?: string
  properties?: StructureProperty[]
}

export interface StructureAction {
  id: string
  name: string
  displayName: string
  description?: string
  objectTypeId: string
  requiresApproval?: boolean
  validationFunctionId?: string
  parameters?: Array<Record<string, unknown>>
  rules?: Array<Record<string, unknown>>
}

export interface StructureFunction {
  id: string
  name: string
  displayName: string
  description?: string
  functionType: string
  language: string
  enabled: boolean
  targetObjectTypeId?: string
  targetActionId?: string
}

export interface StructureSentinel {
  id: string
  name: string
  displayName: string
  description?: string
  bindings?: Array<{ alias: string; objectTypeId: string; filter?: string | null }>
  links?: Array<{ from: string; linkTypeId: string; to: string }>
  condition?: string
  conditionRows?: Array<Record<string, unknown>>
  conditionLogic?: string
  primaryAlias?: string
  actionIds?: string[]
  actionParameters?: Record<string, unknown>
  onChange?: boolean
  onSchedule?: boolean
  scanIntervalSeconds?: number
  muted?: boolean
  enabled?: boolean
  status?: string
}

export interface PublishedWorkspace {
  version: string
  versionId: string
  isCurrentRelease: boolean
  publishedAt?: string | null
  objectTypes: StructureObject[]
  linkTypes: StructureLink[]
  actions: StructureAction[]
  functions: StructureFunction[]
  sentinels: StructureSentinel[]
  canvasLayout?: Record<string, { x: number; y: number }>
}

export type StructureKind = 'object' | 'property' | 'action'
export type StructureEmphasis = 'search' | 'path' | 'dependency' | 'context' | 'primary' | null

export interface StructureNodeData extends Record<string, unknown> {
  kind: StructureKind
  entityId: string
  parentObjectId?: string
  label: string
  technicalName: string
  subtitle: string
  color?: string
  dimmed?: boolean
  emphasis?: StructureEmphasis
}

export interface StructureEdgeData extends Record<string, unknown> {
  kind: 'relation' | 'property' | 'action'
  entityId?: string
  label?: string
  offset?: number
  dimmed?: boolean
  emphasis?: StructureEmphasis
}

export type StructureNode = Node<StructureNodeData, 'structure'>
export type StructureEdge = Edge<StructureEdgeData, 'structure'>

export interface HighlightSet {
  nodes: Set<string>
  edges: Set<string>
  contextNodes: Set<string>
  primaryNodes: Set<string>
  summary: string
}

export interface GraphPath {
  nodes: string[]
  edges: string[]
}

export const propertyNodeId = (objectId: string, property: StructureProperty) =>
  `property:${objectId}:${property.id || property.name}`
export const actionNodeId = (actionId: string) => `action:${actionId}`
export const relationEdgeId = (linkId: string) => `link:${linkId}`

const NODE_SIZE: Record<StructureKind, { width: number; height: number }> = {
  object: { width: 224, height: 80 },
  property: { width: 188, height: 60 },
  action: { width: 196, height: 64 },
}

function offsetParallelRelations(edges: StructureEdge[]): StructureEdge[] {
  const relationGroups = new Map<string, StructureEdge[]>()
  edges.filter(edge => edge.data?.kind === 'relation').forEach(edge => {
    const key = [edge.source, edge.target].sort().join('::')
    relationGroups.set(key, [...(relationGroups.get(key) || []), edge])
  })
  const offsets = new Map<string, number>()
  relationGroups.forEach(items => {
    items.forEach((edge, index) => offsets.set(edge.id, (index - (items.length - 1) / 2) * 34))
  })
  return edges.map((edge): StructureEdge => ({
    ...edge,
    data: { ...edge.data!, offset: offsets.get(edge.id) || 0 },
  }))
}

export function buildStructureGraph(workspace: PublishedWorkspace) {
  const nodes: StructureNode[] = []
  const edges: StructureEdge[] = []
  const actionCount = new Map<string, number>()
  workspace.actions.forEach(action => actionCount.set(action.objectTypeId, (actionCount.get(action.objectTypeId) || 0) + 1))

  workspace.objectTypes.forEach(objectType => {
    nodes.push({
      id: objectType.id,
      type: 'structure',
      position: { x: 0, y: 0 },
      data: {
        kind: 'object', entityId: objectType.id,
        label: objectType.displayName || objectType.name,
        technicalName: objectType.name,
        subtitle: `${objectType.properties.length} 个属性 · ${actionCount.get(objectType.id) || 0} 个动作`,
        color: objectType.color,
      },
    })
    objectType.properties.forEach(property => {
      const id = propertyNodeId(objectType.id, property)
      nodes.push({
        id, type: 'structure', position: { x: 0, y: 0 },
        data: {
          kind: 'property', entityId: property.id || property.name, parentObjectId: objectType.id,
          label: property.displayName || property.name, technicalName: property.name,
          subtitle: `${property.type || 'unknown'}${property.required ? ' · 必填' : ''}${property.source === 'computed' ? ' · 派生' : ''}`,
        },
      })
      edges.push({
        id: `owns:${id}`, source: objectType.id, target: id, type: 'structure', selectable: false,
        data: { kind: 'property' },
      })
    })
  })

  workspace.actions.forEach(action => {
    const id = actionNodeId(action.id)
    nodes.push({
      id, type: 'structure', position: { x: 0, y: 0 },
      data: {
        kind: 'action', entityId: action.id, parentObjectId: action.objectTypeId,
        label: action.displayName || action.name, technicalName: action.name,
        subtitle: `${(action.rules || []).length} 条规则${action.requiresApproval ? ' · 需审批' : ''}`,
      },
    })
    edges.push({
      id: `acts:${id}`, source: action.objectTypeId, target: id, type: 'structure', selectable: false,
      data: { kind: 'action' },
    })
  })

  workspace.linkTypes.forEach(link => {
    edges.push({
      id: relationEdgeId(link.id), source: link.sourceObjectTypeId, target: link.targetObjectTypeId,
      type: 'structure', label: link.displayName || link.name,
      data: { kind: 'relation', entityId: link.id, label: link.displayName || link.name },
      markerEnd: { type: MarkerType.ArrowClosed, width: 16, height: 16, color: '#64748b' },
    })
  })

  const graph = new dagre.graphlib.Graph().setDefaultEdgeLabel(() => ({}))
  graph.setGraph({ rankdir: 'LR', nodesep: 44, ranksep: 112, marginx: 36, marginy: 36 })
  nodes.forEach(node => {
    const size = NODE_SIZE[node.data.kind]
    graph.setNode(node.id, size)
  })
  edges.forEach(edge => graph.setEdge(edge.source, edge.target))
  dagre.layout(graph)

  const layout = workspace.canvasLayout || {}
  const positioned = nodes.map(node => {
    const size = NODE_SIZE[node.data.kind]
    const dagreNode = graph.node(node.id)
    const object = node.data.kind === 'object' ? workspace.objectTypes.find(item => item.id === node.id) : undefined
    const saved = layout[node.id]
      || (object && Number.isFinite(object.positionX) && Number.isFinite(object.positionY)
        ? { x: object.positionX as number, y: object.positionY as number }
        : undefined)
    return {
      ...node,
      position: saved || { x: dagreNode.x - size.width / 2, y: dagreNode.y - size.height / 2 },
    }
  })
  return { nodes: positioned, edges: offsetParallelRelations(edges) }
}

export function findPaths(
  links: StructureLink[], sourceId: string, targetId: string, direction: 'outgoing' | 'both',
  maxDepth?: number, maxPaths = 5,
): GraphPath[] {
  if (!sourceId || !targetId || sourceId === targetId) return []
  // A simple path can contain at most N-1 edges. Deriving the limit from the
  // actual graph avoids silently missing valid long paths while still making
  // the breadth-first enumeration finite.
  const objectIds = new Set([sourceId, targetId])
  links.forEach(link => {
    objectIds.add(link.sourceObjectTypeId)
    objectIds.add(link.targetObjectTypeId)
  })
  const depthLimit = maxDepth ?? Math.max(1, objectIds.size - 1)
  const adjacency = new Map<string, Array<{ next: string; edge: string }>>()
  links.forEach(link => {
    adjacency.set(link.sourceObjectTypeId, [
      ...(adjacency.get(link.sourceObjectTypeId) || []),
      { next: link.targetObjectTypeId, edge: relationEdgeId(link.id) },
    ])
    if (direction === 'both') {
      adjacency.set(link.targetObjectTypeId, [
        ...(adjacency.get(link.targetObjectTypeId) || []),
        { next: link.sourceObjectTypeId, edge: relationEdgeId(link.id) },
      ])
    }
  })
  const queue: GraphPath[] = [{ nodes: [sourceId], edges: [] }]
  const results: GraphPath[] = []
  while (queue.length && results.length < maxPaths) {
    const path = queue.shift()!
    if (path.edges.length >= depthLimit) continue
    const current = path.nodes[path.nodes.length - 1]
    for (const candidate of adjacency.get(current) || []) {
      if (path.nodes.includes(candidate.next)) continue
      const nextPath = { nodes: [...path.nodes, candidate.next], edges: [...path.edges, candidate.edge] }
      if (candidate.next === targetId) results.push(nextPath)
      else queue.push(nextPath)
      if (results.length >= maxPaths) break
    }
  }
  return results
}

function containsFunctionReference(value: unknown, functionId: string): boolean {
  if (value === functionId) return true
  if (Array.isArray(value)) return value.some(item => containsFunctionReference(item, functionId))
  if (!value || typeof value !== 'object') return false
  return Object.entries(value as Record<string, unknown>).some(([key, child]) =>
    (key.toLowerCase().includes('function') && child === functionId) || containsFunctionReference(child, functionId))
}

export function functionUsage(workspace: PublishedWorkspace, functionId: string): HighlightSet {
  const nodes = new Set<string>()
  const edges = new Set<string>()
  const contextNodes = new Set<string>()
  let objectCount = 0
  let propertyCount = 0
  let actionCount = 0
  const fn = workspace.functions.find(item => item.id === functionId)
  if (!fn) return { nodes, edges, contextNodes, primaryNodes: new Set(), summary: '' }

  if (fn.targetObjectTypeId) {
    nodes.add(fn.targetObjectTypeId)
    objectCount += 1
  }
  workspace.objectTypes.forEach(objectType => objectType.properties.forEach(property => {
    if (property.functionId !== functionId) return
    const nodeId = propertyNodeId(objectType.id, property)
    nodes.add(nodeId)
    contextNodes.add(objectType.id)
    edges.add(`owns:${nodeId}`)
    propertyCount += 1
  }))
  workspace.actions.forEach(action => {
    if (action.id !== fn.targetActionId && action.validationFunctionId !== functionId && !containsFunctionReference(action.rules, functionId)) return
    const nodeId = actionNodeId(action.id)
    nodes.add(nodeId)
    contextNodes.add(action.objectTypeId)
    edges.add(`acts:${nodeId}`)
    actionCount += 1
  })
  return {
    nodes, edges, contextNodes, primaryNodes: new Set(),
    summary: `${fn.displayName || fn.name}：${objectCount} 个对象、${propertyCount} 个属性、${actionCount} 个动作直接使用`,
  }
}

function propertyByAlias(
  workspace: PublishedWorkspace, aliasMap: Map<string, string>, alias: unknown, propertyName: unknown,
) {
  if (typeof alias !== 'string' || typeof propertyName !== 'string') return null
  const objectId = aliasMap.get(alias)
  const object = workspace.objectTypes.find(item => item.id === objectId)
  const property = object?.properties.find(item => item.id === propertyName || item.name === propertyName)
  return object && property ? propertyNodeId(object.id, property) : null
}

export function sentinelUsage(workspace: PublishedWorkspace, sentinelId: string): HighlightSet {
  const sentinel = workspace.sentinels.find(item => item.id === sentinelId)
  const nodes = new Set<string>()
  const edges = new Set<string>()
  const contextNodes = new Set<string>()
  const primaryNodes = new Set<string>()
  if (!sentinel) return { nodes, edges, contextNodes, primaryNodes, summary: '' }

  const aliasMap = new Map<string, string>()
  const bindingFilters: string[] = []
  const primaryAlias = sentinel.primaryAlias || sentinel.bindings?.[0]?.alias
  ;(sentinel.bindings || []).forEach(binding => {
    aliasMap.set(binding.alias, binding.objectTypeId)
    nodes.add(binding.objectTypeId)
    if (binding.filter) bindingFilters.push(binding.filter)
    if (binding.alias === primaryAlias) primaryNodes.add(binding.objectTypeId)
  })
  ;(sentinel.links || []).forEach(link => edges.add(relationEdgeId(link.linkTypeId)))

  const propertyNodes = new Set<string>()
  const addProperty = (alias: unknown, property: unknown) => {
    const id = propertyByAlias(workspace, aliasMap, alias, property)
    if (!id) return
    propertyNodes.add(id)
    nodes.add(id)
    const parent = id.split(':')[1]
    contextNodes.add(parent)
    edges.add(`owns:${id}`)
  }
  ;(sentinel.conditionRows || []).forEach(row => {
    addProperty(row.leftAlias, row.leftProp)
    if (row.rightKind === 'property') addProperty(row.rightAlias, row.rightProp)
  })

  const condition = [sentinel.condition || '', ...bindingFilters].join(' ')
  aliasMap.forEach((_objectId, alias) => {
    const object = workspace.objectTypes.find(item => item.id === aliasMap.get(alias))
    object?.properties.forEach(property => {
      const escapedAlias = alias.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
      const escapedProperty = property.name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
      if (new RegExp(`\\b${escapedAlias}\\.${escapedProperty}\\b`).test(condition)) addProperty(alias, property.name)
    })
  })

  const visitParameter = (value: unknown) => {
    if (typeof value === 'string') {
      const match = value.match(/^\{\{\s*([A-Za-z_][A-Za-z0-9_]*|primary|target)\.([A-Za-z_][A-Za-z0-9_]*|id)\s*\}\}$/)
      if (match && match[2] !== 'id') addProperty(match[1] === 'primary' || match[1] === 'target' ? primaryAlias : match[1], match[2])
      return
    }
    if (Array.isArray(value)) return value.forEach(visitParameter)
    if (!value || typeof value !== 'object') return
    const record = value as Record<string, unknown>
    const source = String(record.sourceType || record.source || '')
    if (source === 'property' || source === 'match' || source === 'match_property') {
      addProperty(record.alias, record.property || record.sourceValue)
    }
    Object.values(record).forEach(visitParameter)
  }
  visitParameter(sentinel.actionParameters)

  ;(sentinel.actionIds || []).forEach(actionId => {
    const action = workspace.actions.find(item => item.id === actionId)
    if (!action) return
    const id = actionNodeId(action.id)
    nodes.add(id)
    contextNodes.add(action.objectTypeId)
    edges.add(`acts:${id}`)
  })
  return {
    nodes, edges, contextNodes, primaryNodes,
    summary: `${sentinel.displayName || sentinel.name}：覆盖 ${aliasMap.size} 个对象、${edges.size - propertyNodes.size} 个关系/动作连接、${propertyNodes.size} 个条件属性、${(sentinel.actionIds || []).length} 个动作`,
  }
}
