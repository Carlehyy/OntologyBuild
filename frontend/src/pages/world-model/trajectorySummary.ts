// 从 simulate 返回值中提取可图表化的轨迹序列与摘要信息。
// 契约建议返回 { trajectory, confidence, boundary }，但脚本作者可能返回任意 JSON，
// 因此提取必须完全防御：形状不满足时返回 null，界面回退为原始 JSON 呈现。

export interface TrajectorySeries {
  name: string
  values: (number | null)[]
}

export interface TrajectorySummary {
  series: TrajectorySeries[]
  pointCount: number
  confidence: number | null
  boundary: string | null
}

function toFiniteOrNull(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

/**
 * 提取规则：
 * - payload 必须是对象且含 trajectory 字段；
 * - trajectory 为一维数组（长度 ≥ 2）→ 单序列；
 * - trajectory 为等宽的数值二维数组 → 多序列（按列拆分）；
 * - 序列内非有限数值（缺测）记为 null（图表断点连接），全序列无有效数值时不生成预览。
 */
export function extractTrajectorySummary(payload: unknown): TrajectorySummary | null {
  if (!payload || typeof payload !== 'object' || Array.isArray(payload)) return null
  const record = payload as Record<string, unknown>
  const raw = record.trajectory
  if (!Array.isArray(raw) || raw.length < 2) return null

  let series: TrajectorySeries[]
  if (!raw.every(item => Array.isArray(item))) {
    // 一维序列：非有限数值元素一律按缺测处理，避免个别脏值让整个预览不可用
    series = [{ name: 'trajectory', values: raw.map(toFiniteOrNull) }]
  } else {
    const rows = raw as unknown[][]
    const width = rows[0].length
    if (width < 1 || !rows.every(row => row.length === width)) return null
    series = Array.from({ length: width }, (_, column) => ({
      name: `序列 ${column + 1}`,
      values: rows.map(row => toFiniteOrNull(row[column])),
    }))
  }

  const hasData = series.some(item => item.values.some(value => value !== null))
  if (!hasData) return null

  const confidenceRaw = record.confidence
  const confidence = typeof confidenceRaw === 'number' && Number.isFinite(confidenceRaw)
    ? confidenceRaw
    : null
  const boundaryRaw = record.boundary
  const boundary = typeof boundaryRaw === 'string' && boundaryRaw.trim() ? boundaryRaw.trim() : null

  return { series, pointCount: raw.length, confidence, boundary }
}
