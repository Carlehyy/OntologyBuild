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
export type StructureLevel = 1 | 2
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

interface Point { x: number; y: number }

interface StructureLayoutOptions {
  ignoreSaved?: boolean
}

const stableHash = (value: string) => {
  let hash = 2166136261
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index)
    hash = Math.imul(hash, 16777619)
  }
  return hash >>> 0
}

function childOrbitRadius(childCount: number) {
  if (childCount <= 0) return 128
  let remaining = childCount
  let ring = 0
  let radius = 270
  while (remaining > 0) {
    const capacity = 8 + ring * 4
    remaining -= capacity
    radius = 270 + ring * 210
    ring += 1
  }
  return radius
}

function objectComponents(objectIds: string[], links: StructureLink[]) {
  const known = new Set(objectIds)
  const adjacency = new Map(objectIds.map(id => [id, new Set<string>()]))
  links.forEach(link => {
    if (!known.has(link.sourceObjectTypeId) || !known.has(link.targetObjectTypeId)) return
    if (link.sourceObjectTypeId === link.targetObjectTypeId) return
    adjacency.get(link.sourceObjectTypeId)?.add(link.targetObjectTypeId)
    adjacency.get(link.targetObjectTypeId)?.add(link.sourceObjectTypeId)
  })
  const visited = new Set<string>()
  const components: string[][] = []
  ;[...objectIds].sort().forEach(start => {
    if (visited.has(start)) return
    const queue = [start]
    const component: string[] = []
    visited.add(start)
    while (queue.length) {
      const current = queue.shift()!
      component.push(current)
      ;[...(adjacency.get(current) || [])].sort().forEach(next => {
        if (visited.has(next)) return
        visited.add(next)
        queue.push(next)
      })
    }
    components.push(component.sort())
  })
  return components
}

/**
 * Deterministic force-directed layout for the object backbone.
 *
 * Relations act as springs, every object pair repels and a collision radius
 * reserves room for the L2 property/action orbit. Disconnected components are
 * packed afterwards, so they remain nearby without pretending to be linked.
 */
function forceObjectLayout(workspace: PublishedWorkspace, level: StructureLevel) {
  const objectIds = workspace.objectTypes.map(item => item.id)
  const childCounts = new Map(objectIds.map(id => [id, 0]))
  if (level === 2) {
    workspace.objectTypes.forEach(item => childCounts.set(item.id, item.properties.length))
    workspace.actions.forEach(action => childCounts.set(action.objectTypeId, (childCounts.get(action.objectTypeId) || 0) + 1))
  }
  const reserveRadius = new Map(objectIds.map(id => [
    id,
    level === 2 && (childCounts.get(id) || 0) > 0
      ? childOrbitRadius(childCounts.get(id) || 0) + 112
      : 128,
  ]))
  const components = objectComponents(objectIds, workspace.linkTypes)
  const componentLayouts: Array<{ points: Map<string, Point>; minX: number; minY: number; width: number; height: number }> = []

  components.forEach(component => {
    const points = new Map<string, Point>()
    const velocity = new Map<string, Point>()
    const count = component.length
    const initialRadius = count <= 1 ? 0 : Math.max(150, Math.sqrt(count) * (level === 1 ? 112 : 148))
    component.forEach((id, index) => {
      const jitter = ((stableHash(id) % 37) - 18) * 0.8
      const angle = -Math.PI / 2 + (index / Math.max(1, count)) * Math.PI * 2
      points.set(id, { x: Math.cos(angle) * (initialRadius + jitter), y: Math.sin(angle) * (initialRadius + jitter) })
      velocity.set(id, { x: 0, y: 0 })
    })

    const componentSet = new Set(component)
    const springs = workspace.linkTypes
      .filter(link => componentSet.has(link.sourceObjectTypeId) && componentSet.has(link.targetObjectTypeId) && link.sourceObjectTypeId !== link.targetObjectTypeId)
      .map(link => [link.sourceObjectTypeId, link.targetObjectTypeId] as const)
    const baseDistance = level === 1 ? 318 : 420
    const iterations = count > 180 ? 70 : count > 100 ? 100 : count > 50 ? 150 : 240

    for (let iteration = 0; iteration < iterations && count > 1; iteration += 1) {
      const forces = new Map(component.map(id => [id, { x: 0, y: 0 }]))
      for (let leftIndex = 0; leftIndex < count; leftIndex += 1) {
        for (let rightIndex = leftIndex + 1; rightIndex < count; rightIndex += 1) {
          const leftId = component[leftIndex]
          const rightId = component[rightIndex]
          const left = points.get(leftId)!
          const right = points.get(rightId)!
          let dx = right.x - left.x
          let dy = right.y - left.y
          let distance = Math.hypot(dx, dy)
          if (distance < 0.01) {
            const angle = ((stableHash(`${leftId}:${rightId}`) % 360) / 180) * Math.PI
            dx = Math.cos(angle)
            dy = Math.sin(angle)
            distance = 1
          }
          const ux = dx / distance
          const uy = dy / distance
          const collisionDistance = level === 1
            ? 270
            : Math.min(1080, (reserveRadius.get(leftId) || 128) + (reserveRadius.get(rightId) || 128) + 56)
          const repulsion = (baseDistance * baseDistance * 1.15) / Math.max(900, distance * distance)
            + (distance < collisionDistance ? (collisionDistance - distance) * 0.11 : 0)
          const leftForce = forces.get(leftId)!
          const rightForce = forces.get(rightId)!
          leftForce.x -= ux * repulsion
          leftForce.y -= uy * repulsion
          rightForce.x += ux * repulsion
          rightForce.y += uy * repulsion
        }
      }
      springs.forEach(([sourceId, targetId]) => {
        const source = points.get(sourceId)!
        const target = points.get(targetId)!
        const dx = target.x - source.x
        const dy = target.y - source.y
        const distance = Math.hypot(dx, dy) || 1
        const desired = level === 1
          ? baseDistance
          : Math.min(1080, Math.max(baseDistance, (reserveRadius.get(sourceId) || 128) + (reserveRadius.get(targetId) || 128) + 56))
        const spring = (distance - desired) * 0.025
        const sx = (dx / distance) * spring
        const sy = (dy / distance) * spring
        forces.get(sourceId)!.x += sx
        forces.get(sourceId)!.y += sy
        forces.get(targetId)!.x -= sx
        forces.get(targetId)!.y -= sy
      })
      const maxStep = 15 - (iteration / iterations) * 12
      component.forEach(id => {
        const point = points.get(id)!
        const force = forces.get(id)!
        const previous = velocity.get(id)!
        force.x -= point.x * 0.004
        force.y -= point.y * 0.004
        let vx = (previous.x + force.x) * 0.76
        let vy = (previous.y + force.y) * 0.76
        const speed = Math.hypot(vx, vy)
        if (speed > maxStep) {
          vx = (vx / speed) * maxStep
          vy = (vy / speed) * maxStep
        }
        velocity.set(id, { x: vx, y: vy })
        points.set(id, { x: point.x + vx, y: point.y + vy })
      })
    }

    // Finish with a deterministic collision pass. Force simulations optimize
    // the whole system and may leave a few near-overlaps in dense cyclic
    // graphs; this pass guarantees that the reserved L2 clusters do not touch.
    for (let pass = 0; pass < 32 && count > 1; pass += 1) {
      let moved = false
      for (let leftIndex = 0; leftIndex < count; leftIndex += 1) {
        for (let rightIndex = leftIndex + 1; rightIndex < count; rightIndex += 1) {
          const leftId = component[leftIndex]
          const rightId = component[rightIndex]
          const left = points.get(leftId)!
          const right = points.get(rightId)!
          let dx = right.x - left.x
          let dy = right.y - left.y
          let distance = Math.hypot(dx, dy)
          if (distance < 0.01) {
            const angle = ((stableHash(`${leftId}:${rightId}:collision`) % 360) / 180) * Math.PI
            dx = Math.cos(angle)
            dy = Math.sin(angle)
            distance = 1
          }
          const minimum = level === 1
            ? 270
            : Math.min(1080, (reserveRadius.get(leftId) || 128) + (reserveRadius.get(rightId) || 128) + 56)
          if (distance >= minimum) continue
          const shift = (minimum - distance) / 2 + 0.5
          const sx = (dx / distance) * shift
          const sy = (dy / distance) * shift
          points.set(leftId, { x: left.x - sx, y: left.y - sy })
          points.set(rightId, { x: right.x + sx, y: right.y + sy })
          moved = true
        }
      }
      if (!moved) break
    }

    let minX = Number.POSITIVE_INFINITY
    let minY = Number.POSITIVE_INFINITY
    let maxX = Number.NEGATIVE_INFINITY
    let maxY = Number.NEGATIVE_INFINITY
    component.forEach(id => {
      const point = points.get(id)!
      const radius = reserveRadius.get(id) || 128
      const horizontal = level === 1 ? NODE_SIZE.object.width / 2 : radius
      const vertical = level === 1 ? NODE_SIZE.object.height / 2 : radius
      minX = Math.min(minX, point.x - horizontal)
      maxX = Math.max(maxX, point.x + horizontal)
      minY = Math.min(minY, point.y - vertical)
      maxY = Math.max(maxY, point.y + vertical)
    })
    componentLayouts.push({ points, minX, minY, width: maxX - minX, height: maxY - minY })
  })

  const totalArea = componentLayouts.reduce((sum, item) => sum + (item.width + 120) * (item.height + 120), 0)
  const targetRowWidth = Math.max(920, Math.sqrt(totalArea) * 1.24)
  const packed = new Map<string, Point>()
  let cursorX = 72
  let cursorY = 72
  let rowHeight = 0
  componentLayouts.forEach(layout => {
    const boxWidth = Math.max(300, layout.width + 120)
    const boxHeight = Math.max(190, layout.height + 120)
    if (cursorX > 72 && cursorX + boxWidth > targetRowWidth) {
      cursorX = 72
      cursorY += rowHeight
      rowHeight = 0
    }
    layout.points.forEach((point, id) => packed.set(id, {
      x: cursorX + 60 + point.x - layout.minX,
      y: cursorY + 60 + point.y - layout.minY,
    }))
    cursorX += boxWidth
    rowHeight = Math.max(rowHeight, boxHeight)
  })
  return packed
}

function orbitPositions(parentId: string, childIds: string[], parentCenter: Point) {
  const positions = new Map<string, Point>()
  let cursor = 0
  let ring = 0
  while (cursor < childIds.length) {
    const capacity = 8 + ring * 4
    const items = childIds.slice(cursor, cursor + capacity)
    const radius = 270 + ring * 210
    const offset = ((stableHash(`${parentId}:${ring}`) % 360) / 180) * Math.PI
    items.forEach((id, index) => {
      const angle = offset + (index / Math.max(1, items.length)) * Math.PI * 2
      positions.set(id, {
        x: parentCenter.x + Math.cos(angle) * radius,
        y: parentCenter.y + Math.sin(angle) * radius,
      })
    })
    cursor += items.length
    ring += 1
  }
  return positions
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

export function buildStructureGraph(
  workspace: PublishedWorkspace,
  level: StructureLevel = 2,
  options: StructureLayoutOptions = {},
) {
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
    if (level === 2) objectType.properties.forEach(property => {
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

  if (level === 2) workspace.actions.forEach(action => {
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

  const objectCenters = forceObjectLayout(workspace, level)
  const generated = new Map<string, Point>()
  const nodeById = new Map(nodes.map(node => [node.id, node]))
  workspace.objectTypes.forEach(objectType => {
    const center = objectCenters.get(objectType.id) || { x: 184, y: 112 }
    generated.set(objectType.id, {
      x: center.x - NODE_SIZE.object.width / 2,
      y: center.y - NODE_SIZE.object.height / 2,
    })
    if (level !== 2) return
    const childIds = [
      ...objectType.properties.map(property => propertyNodeId(objectType.id, property)),
      ...workspace.actions.filter(action => action.objectTypeId === objectType.id).map(action => actionNodeId(action.id)),
    ]
    orbitPositions(objectType.id, childIds, center).forEach((childCenter, id) => {
      const node = nodeById.get(id)
      if (!node) return
      const size = NODE_SIZE[node.data.kind]
      generated.set(id, { x: childCenter.x - size.width / 2, y: childCenter.y - size.height / 2 })
    })
  })
  const layout = workspace.canvasLayout || {}
  const positioned = nodes.map(node => {
    const saved = options.ignoreSaved ? undefined : layout[`l${level}:${node.id}`]
    return {
      ...node,
      position: saved && Number.isFinite(saved.x) && Number.isFinite(saved.y)
        ? saved
        : generated.get(node.id) || { x: 72, y: 72 },
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
