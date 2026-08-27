/** 「运行汇总」时间维度 → 显式查询窗的纯函数派生。
 *  后端 runtime-summary 按 UTC 日历日聚合（与 overview 的 daily7d 同一口径），
 *  这里同样以 UTC 日历日取窗，保证下拉选项与按日桶严格对齐。 */

export type RuntimeDimension =
  | 'today'
  | 'yesterday'
  | 'last7'
  | 'last30'
  | 'thisMonth'
  | 'lastMonth'
  | 'custom'

export interface RuntimeRange {
  start: string
  end: string
}

export const RUNTIME_DIMENSION_OPTIONS: Array<{ value: RuntimeDimension; label: string }> = [
  { value: 'today', label: '今天' },
  { value: 'yesterday', label: '昨天' },
  { value: 'last7', label: '近7天' },
  { value: 'last30', label: '近30天' },
  { value: 'thisMonth', label: '本月' },
  { value: 'lastMonth', label: '上月' },
  { value: 'custom', label: '自定义' },
]

/** 与后端一致的窗口跨度上限（天）；超限时按起点截断，不发必败请求。 */
export const RUNTIME_RANGE_MAX_SPAN_DAYS = 92

export const RUNTIME_DIMENSION_DEFAULT: RuntimeDimension = 'last7'

const DAY_MS = 24 * 60 * 60 * 1000

const isoDate = (value: Date) => value.toISOString().slice(0, 10)

const utcDay = (now: Date, offsetDays: number) =>
  new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() + offsetDays))

const isValidIsoDate = (value: string) =>
  /^\d{4}-\d{2}-\d{2}$/.test(value) && !Number.isNaN(Date.parse(`${value}T00:00:00Z`))

export function resolveRuntimeRange(
  dimension: RuntimeDimension,
  now: Date,
  custom: RuntimeRange,
): RuntimeRange {
  switch (dimension) {
    case 'today': {
      const day = isoDate(utcDay(now, 0))
      return { start: day, end: day }
    }
    case 'yesterday': {
      const day = isoDate(utcDay(now, -1))
      return { start: day, end: day }
    }
    case 'last7':
      return { start: isoDate(utcDay(now, -6)), end: isoDate(utcDay(now, 0)) }
    case 'last30':
      return { start: isoDate(utcDay(now, -29)), end: isoDate(utcDay(now, 0)) }
    case 'thisMonth':
      return {
        start: isoDate(new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), 1))),
        end: isoDate(utcDay(now, 0)),
      }
    case 'lastMonth':
      return {
        start: isoDate(new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth() - 1, 1))),
        end: isoDate(new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), 0))),
      }
    case 'custom':
      return normalizeRuntimeRange(custom, now)
  }
}

/** 自定义窗兜底：空/非法日期回退近 7 天；起止颠倒时交换；跨度超限按起点截断。 */
export function normalizeRuntimeRange(custom: RuntimeRange, now: Date = new Date()): RuntimeRange {
  const fallback = resolveRuntimeRange(RUNTIME_DIMENSION_DEFAULT, now, custom)
  let start = isValidIsoDate(custom.start) ? custom.start : fallback.start
  let end = isValidIsoDate(custom.end) ? custom.end : fallback.end
  if (start > end) [start, end] = [end, start]
  const spanDays = Math.round((Date.parse(`${end}T00:00:00Z`) - Date.parse(`${start}T00:00:00Z`)) / DAY_MS) + 1
  if (spanDays > RUNTIME_RANGE_MAX_SPAN_DAYS) {
    end = isoDate(new Date(Date.parse(`${start}T00:00:00Z`) + (RUNTIME_RANGE_MAX_SPAN_DAYS - 1) * DAY_MS))
  }
  return { start, end }
}

/** 面板文案用的可读区间：预设维度给标签，自定义给日期区间。 */
export function describeRuntimeRange(dimension: RuntimeDimension, range: RuntimeRange): string {
  if (dimension === 'custom') return `${range.start} ~ ${range.end}`
  return RUNTIME_DIMENSION_OPTIONS.find(option => option.value === dimension)?.label
    ?? RUNTIME_DIMENSION_OPTIONS.find(option => option.value === RUNTIME_DIMENSION_DEFAULT)!.label
}
