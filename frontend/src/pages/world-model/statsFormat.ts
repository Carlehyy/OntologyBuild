/** 世界模型统计展示的纯格式化函数（便于单测与三页复用） */

/** 成功率百分比：total 为 0 时返回占位符；保留一位小数并去掉多余的 .0 */
export function formatSuccessRate(success: number, total: number): string {
  if (total <= 0) return '—'
  const rate = Math.max(0, Math.min(1, success / total))
  return `${(rate * 100).toFixed(1).replace(/\.0$/, '')}%`
}

/** 耗时展示：统一带 ms 单位 */
export function formatDurationMs(value: number): string {
  return `${value} ms`
}
