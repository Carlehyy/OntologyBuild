// 后端 SQLAlchemy DateTime 列不带时区，序列化出的 ISO 串没有 Z 后缀但语义是 UTC。
// 直接 new Date(value) 会被 JS 按本地时区解析，中国上海（UTC+8）下时间慢 8 小时。
// 已有显式时区（Z 或 ±HH:MM）的串原样解析，否则按 UTC 补齐。
// 与 pages/pipelines/sync-tasks/SyncTasksTab.tsx 的既有先例同规则。
const EXPLICIT_TIMEZONE_RE = /(Z|[+-]\d\d:?\d\d)$/

export function parseServerTime(value: string): Date | null {
  const date = new Date(EXPLICIT_TIMEZONE_RE.test(value) ? value : `${value}Z`)
  return Number.isNaN(date.getTime()) ? null : date
}

export function formatSessionTime(value: string): string {
  const date = parseServerTime(value)
  if (!date) return '时间未知'
  return date.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}
