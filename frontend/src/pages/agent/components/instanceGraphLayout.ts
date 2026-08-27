/**
 * 数据推演图谱的确定性防重叠布局（MYW-65）
 *
 * 旧版把每类前 10 个实例摆在同一个小椭圆上（10 × Ø76 刚好挤满周长），
 * 节点与标签互相压盖，相邻类型簇的环带也会互相穿插。
 * 这里保留「按类型聚簇」的语义，但分三步消除重叠：
 *   1. 种子层：按簇体量自适应分配画布网格与环带半径，环容量按节点实际占地推算；
 *   2. 相位层：逐圈错开起始角（黄金角），避免不同圈的节点径向堆叠；
 *   3. 松弛层：以种子坐标为锚做有限次确定性松弛 —— AABB 碰撞分离 +
 *      弱锚定回拉，最终再跑若干轮全量分离兜底，保证估计包围盒两两不叠。
 * 全程无随机数：相同输入必然得到相同输出（可单测、可回放）。
 */

export interface LayoutInputNode {
  id: string
  kind: string
  label?: string
  objectTypeId?: string
  instanceId?: string
}

export interface Point {
  x: number
  y: number
}

/** 节点估计占地（半宽/半高）。画布样式：类型 132×58、实例 Ø76（标签截宽 112）、字段 118×44。 */
export function estimateNodeBox(node: LayoutInputNode): { halfW: number; halfH: number } {
  if (node.kind === 'object_type') return { halfW: 66 + 8, halfH: 29 + 6 }
  if (node.kind === 'property') {
    return { halfW: Math.max(59, labelHalfWidth(node.label)) + 4, halfH: 22 + 5 }
  }
  // 实例标签内嵌居中显示，长标签会横向溢出圆外，占地按标签实际宽度估计
  return { halfW: Math.max(38, labelHalfWidth(node.label)) + 5, halfH: 38 + 5 }
}

/** font-size 11 / weight 600 下标签的半宽估计：CJK ≈ 11.5px/字，拉丁/数字 ≈ 6.6px/字，上限 112px。 */
function labelHalfWidth(label: string | undefined): number {
  if (!label) return 0
  let width = 0
  for (const ch of label) width += ch.charCodeAt(0) > 0x2e7f ? 11.5 : 6.6
  return Math.min(112, width) / 2
}

/** 椭圆周长（Ramanujan 近似）。 */
function ellipsePerimeter(a: number, b: number): number {
  const h = ((a - b) / (a + b)) ** 2
  return Math.PI * (a + b) * (1 + (3 * h) / (10 + Math.sqrt(4 - 3 * h)))
}

/** 黄金角：逐圈错相，避免相邻圈的节点落在同一射线上互相遮挡。 */
const GOLDEN_ANGLE = 2.39996323

const RING_BASE_X = 150
const RING_BASE_Y = 118
/** 相邻环的径向步长 ≥ 节点占地直径，保证相邻环的节点不会径向压盖。 */
const RING_STEP_X = 95
const RING_STEP_Y = 95
/** 同环相邻节点的最小弧间距（按占地直径推算后再加余量）。 */
const RING_GAP = 34
/** 簇之间、行与行之间的留白。 */
const CLUSTER_MARGIN_X = 150
const CLUSTER_MARGIN_Y = 120
const CANVAS_PADDING = 200

function groupBy<T>(items: T[], keyOf: (item: T) => string): Map<string, T[]> {
  const map = new Map<string, T[]>()
  items.forEach(item => {
    const key = keyOf(item)
    map.set(key, [...(map.get(key) || []), item])
  })
  return map
}

function ringCapacity(a: number, b: number, spacing: number): number {
  return Math.max(1, Math.round(ellipsePerimeter(a, b) / spacing))
}

/** 某类型簇需要的环数（用于推算簇在网格中的占地）。 */
function ringCountFor(itemCount: number, spacing: number): number {
  let placed = 0
  let ring = 0
  while (placed < itemCount) {
    placed += ringCapacity(RING_BASE_X + ring * RING_STEP_X, RING_BASE_Y + ring * RING_STEP_Y, spacing)
    ring += 1
  }
  return Math.max(1, ring)
}

function clusterSpacing(items: LayoutInputNode[]): number {
  const widest = items.reduce((max, item) => Math.max(max, estimateNodeBox(item).halfW), 44)
  return widest * 2 + RING_GAP
}

/**
 * 按环容量比例把 itemCount 个实例分摊到各环（最大整数余数法）：
 * 外环不会只剩孤零零一个节点，且每环分摊数不超过该环容量。
 */
function distributeToRings(itemCount: number, spacing: number): number[] {
  if (itemCount <= 0) return []
  const rings = ringCountFor(itemCount, spacing)
  const caps = Array.from({ length: rings }, (_, ring) =>
    ringCapacity(RING_BASE_X + ring * RING_STEP_X, RING_BASE_Y + ring * RING_STEP_Y, spacing))
  const totalCap = caps.reduce((sum, cap) => sum + cap, 0)
  const exact = caps.map(cap => (itemCount * cap) / totalCap)
  const assigned = exact.map(value => Math.floor(value))
  let remaining = itemCount - assigned.reduce((sum, value) => sum + value, 0)
  const order = exact
    .map((value, ring) => ({ ring, frac: value - Math.floor(value) }))
    .sort((a, b) => b.frac - a.frac || a.ring - b.ring)
  for (let pass = 0; pass < 2 && remaining > 0; pass += 1) {
    order.forEach(({ ring }) => {
      if (remaining <= 0) return
      if (pass === 0 && assigned[ring] >= caps[ring]) return
      assigned[ring] += 1
      remaining -= 1
    })
  }
  return assigned.filter(value => value > 0)
}

/**
 * 种子层：类型网格 + 实例环带 + 字段环带。
 * 类型/实例/字段都按输入顺序处理，输出与输入顺序无关但确定。
 */
export function seedPositions(nodes: LayoutInputNode[]): Map<string, Point> {
  const positions = new Map<string, Point>()
  if (nodes.length === 0) return positions

  const types = nodes.filter(node => node.kind === 'object_type')
  const instances = nodes.filter(node => node.kind === 'instance')
  const properties = nodes.filter(node => node.kind === 'property')
  const instancesByType = groupBy(instances, node => node.objectTypeId || '')
  const typeCenters = new Map<string, Point>()

  // -- 类型网格：行列尺寸按相邻簇的实际外接半径累计，簇大给的空间就大 --
  const columns = Math.max(1, Math.ceil(Math.sqrt(Math.max(1, types.length))))
  const rows = Math.ceil(types.length / columns)
  const clusterExtent = new Map<string, number>()
  types.forEach(node => {
    const items = instancesByType.get(node.objectTypeId || '') || []
    const spacing = clusterSpacing(items)
    const rings = ringCountFor(items.length, spacing)
    const outerX = RING_BASE_X + (rings - 1) * RING_STEP_X
    const outerY = RING_BASE_Y + (rings - 1) * RING_STEP_Y
    const widestHalf = items.reduce((max, item) => Math.max(max, estimateNodeBox(item).halfW), 44)
    clusterExtent.set(node.id, Math.max(outerX, outerY) + widestHalf + 40)
  })
  const rowHeights = Array.from({ length: rows }, (_, row) =>
    Math.max(120, ...types
      .filter((_, index) => Math.floor(index / columns) === row)
      .map(node => (clusterExtent.get(node.id) || 120) * 2)) + CLUSTER_MARGIN_Y)
  const columnWidths = Array.from({ length: columns }, (_, column) =>
    Math.max(140, ...types
      .filter((_, index) => index % columns === column)
      .map(node => (clusterExtent.get(node.id) || 140) * 2)) + CLUSTER_MARGIN_X)

  const rowOffsets: number[] = []
  const columnOffsets: number[] = []
  rowHeights.reduce((acc, height) => { rowOffsets.push(acc); return acc + height }, 0)
  columnWidths.reduce((acc, width) => { columnOffsets.push(acc); return acc + width }, 0)

  types.forEach((node, index) => {
    const column = index % columns
    const row = Math.floor(index / columns)
    const center = {
      x: CANVAS_PADDING + columnOffsets[column] + columnWidths[column] / 2,
      y: CANVAS_PADDING + rowOffsets[row] + rowHeights[row] / 2,
    }
    positions.set(node.id, center)
    if (node.objectTypeId) typeCenters.set(node.objectTypeId, center)
  })

  // -- 实例环带：各环按容量比例分摊，逐圈黄金角错相，避免径向堆叠 --
  instancesByType.forEach((items, typeId) => {
    const center = typeCenters.get(typeId) || { x: CANVAS_PADDING, y: CANVAS_PADDING }
    const spacing = clusterSpacing(items)
    const ringCounts = distributeToRings(items.length, spacing)
    let index = 0
    ringCounts.forEach((count, ring) => {
      const a = RING_BASE_X + ring * RING_STEP_X
      const b = RING_BASE_Y + ring * RING_STEP_Y
      const phase = ring * GOLDEN_ANGLE
      for (let slot = 0; slot < count; slot += 1) {
        const angle = phase + ((slot + 0.5) / count) * Math.PI * 2
        positions.set(items[index + slot].id, {
          x: center.x + Math.cos(angle) * a,
          y: center.y + Math.sin(angle) * b,
        })
      }
      index += count
    })
  })

  // -- 字段环带：按所属实例分组，围绕各自实例小环展开（旧版按全局序号取角，簇间失衡） --
  const propertyGroups = groupBy(properties, node => node.instanceId || '')
  propertyGroups.forEach((items, instanceEntityId) => {
    const anchorNode = nodes.find(node => node.id === instanceNodeId(instanceEntityId))
    const anchor = positions.get(instanceNodeId(instanceEntityId))
      || typeCenters.get(anchorNode?.objectTypeId || '')
      || { x: CANVAS_PADDING, y: CANVAS_PADDING }
    const anchorBox = anchorNode ? estimateNodeBox(anchorNode) : { halfW: 43, halfH: 43 }
    // 用实例 id 的字符码做确定性相位，让相邻实例的字段环不同向
    let hash = 0
    for (const ch of instanceEntityId) hash = (hash * 31 + ch.charCodeAt(0)) % 997
    const phase = (hash / 997) * Math.PI * 2
    items.forEach((node, index) => {
      const box = estimateNodeBox(node)
      // 层间距 ≥ 字段占地高度，多圈不互相压盖
      const radius = anchorBox.halfW + box.halfW + 40 + Math.floor(index / 4) * 58
      const angle = phase + ((index % 4) + 0.5) * (Math.PI / 2) + Math.floor(index / 4) * 0.35
      positions.set(node.id, {
        x: anchor.x + Math.cos(angle) * radius,
        y: anchor.y + Math.sin(angle) * radius,
      })
    })
  })

  return positions
}

function instanceNodeId(entityId: string): string {
  return 'instance:' + entityId
}

const ITERATIONS = 220
const SWEEPS = 80
const SEPARATION_GAP = 14
const ANCHOR_PULL = 0.06
const CELL = 150
/** 半邻域扫描的偏移：当前格 + 右/下三格，每对节点恰好被访问一次。 */
const FORWARD_OFFSETS: Array<[number, number]> = [[1, 0], [-1, 1], [0, 1], [1, 1]]

function massOf(kind: string): number {
  if (kind === 'object_type') return 2.5
  return kind === 'property' ? 0.7 : 1
}

/**
 * 松弛层：网格加速的 AABB 碰撞分离 + 向种子位置的弱锚定。
 * 热循环使用扁平数组与半邻域格扫描，避免逐迭代分配；结束后再全量兜底，
 * 保证输出两两不重叠。
 */
export function relaxPositions(
  seed: Map<string, Point>,
  nodes: LayoutInputNode[],
): Map<string, Point> {
  const positions = new Map<string, Point>()
  nodes.forEach(node => {
    const point = seed.get(node.id) || { x: CANVAS_PADDING, y: CANVAS_PADDING }
    positions.set(node.id, { x: point.x, y: point.y })
  })
  if (nodes.length < 2) return positions

  const count = nodes.length
  const xs = new Float64Array(count)
  const ys = new Float64Array(count)
  const halfW = new Float64Array(count)
  const halfH = new Float64Array(count)
  const mass = new Float64Array(count)
  const anchors = new Float64Array(count * 2)
  nodes.forEach((node, index) => {
    const point = positions.get(node.id)!
    xs[index] = point.x
    ys[index] = point.y
    const box = estimateNodeBox(node)
    halfW[index] = box.halfW
    halfH[index] = box.halfH
    mass[index] = massOf(node.kind)
    const anchor = seed.get(node.id) || point
    anchors[index * 2] = anchor.x
    anchors[index * 2 + 1] = anchor.y
  })

  const resolvePair = (i: number, j: number, minGapX: number, minGapY: number, strength: number) => {
    const dx = xs[j] - xs[i]
    const dy = ys[j] - ys[i]
    const overlapX = minGapX - Math.abs(dx)
    const overlapY = minGapY - Math.abs(dy)
    if (overlapX <= 0 || overlapY <= 0) return false
    const total = mass[i] + mass[j]
    const shareI = (mass[j] * 2) / total
    const shareJ = (mass[i] * 2) / total
    const dist = Math.sqrt(dx * dx + dy * dy)
    if (dist < 0.001) {
      // 完全重合：沿 x 轴拆开
      const push = (Math.min(overlapX, overlapY) + 1) * strength / 2
      xs[i] -= push * shareI
      xs[j] += push * shareJ
      return true
    }
    // 沿中心线推开：推量按“恰好清空某一轴的重叠”精确计算。
    // 对环带结构，最小轴推力只会让节点沿环切向滑动、互相转嫁重叠；
    // 中心线推力含径向分量，能把环撑开并打破这种极限环。
    const cosX = Math.abs(dx) / dist
    const cosY = Math.abs(dy) / dist
    const required = Math.min(
      cosX > 1e-9 ? overlapX / cosX : Number.POSITIVE_INFINITY,
      cosY > 1e-9 ? overlapY / cosY : Number.POSITIVE_INFINITY,
    )
    const push = (Number.isFinite(required) ? required + 1 : Math.min(overlapX, overlapY) + 1) * strength / 2
    xs[i] -= (dx / dist) * push * shareI
    ys[i] -= (dy / dist) * push * shareI
    xs[j] += (dx / dist) * push * shareJ
    ys[j] += (dy / dist) * push * shareJ
    return true
  }

  const scanOverlaps = (strength: number) => {
    // 负坐标安全编码：格索引统一偏移后合成数值键
    const keyOf = (cx: number, cy: number) => (cx + 5000) * 10001 + (cy + 5000)
    const buckets = new Map<number, number[]>()
    for (let i = 0; i < count; i += 1) {
      const key = keyOf(Math.floor(xs[i] / CELL), Math.floor(ys[i] / CELL))
      const bucket = buckets.get(key)
      if (bucket) bucket.push(i)
      else buckets.set(key, [i])
    }
    let anyMove = false
    for (const [key, bucket] of buckets) {
      const cx = Math.floor(key / 10001) - 5000
      const cy = (key % 10001) - 5000
      for (let a = 0; a < bucket.length; a += 1) {
        for (let b = a + 1; b < bucket.length; b += 1) {
          anyMove = resolvePair(bucket[a], bucket[b],
            halfW[bucket[a]] + halfW[bucket[b]] + SEPARATION_GAP,
            halfH[bucket[a]] + halfH[bucket[b]] + SEPARATION_GAP, strength) || anyMove
        }
        for (const [ox, oy] of FORWARD_OFFSETS) {
          const neighbor = buckets.get(keyOf(cx + ox, cy + oy))
          if (!neighbor) continue
          for (const j of neighbor) {
            anyMove = resolvePair(bucket[a], j,
              halfW[bucket[a]] + halfW[j] + SEPARATION_GAP,
              halfH[bucket[a]] + halfH[j] + SEPARATION_GAP, strength) || anyMove
          }
        }
      }
    }
    return anyMove
  }

  // -- 迭代松弛：碰撞推开 + 锚定回拉（锚定强度逐轮退火，拥挤区域可以外扩） --
  for (let step = 0; step < ITERATIONS; step += 1) {
    scanOverlaps(1)
    const pull = ANCHOR_PULL * (1 - step / ITERATIONS) + 0.015
    for (let i = 0; i < count; i += 1) {
      xs[i] += (anchors[i * 2] - xs[i]) * pull
      ys[i] += (anchors[i * 2 + 1] - ys[i]) * pull
    }
  }

  // -- 兜底：反复全量分离，直到没有 AABB 相交（或达到轮数上限） --
  for (let sweep = 0; sweep < SWEEPS; sweep += 1) {
    if (!scanOverlaps(2)) break
  }

  nodes.forEach((node, index) => {
    positions.set(node.id, { x: xs[index], y: ys[index] })
  })
  return positions
}

/**
 * 数据推演图谱布局入口：种子 + 松弛，输出每个节点确定的画布坐标。
 * 保证：任意两节点的估计包围盒互不重叠；相同输入输出完全一致。
 */
export function layoutKnowledgeGraph(nodes: LayoutInputNode[]): Map<string, Point> {
  if (nodes.length === 0) return new Map()
  return relaxPositions(seedPositions(nodes), nodes)
}
