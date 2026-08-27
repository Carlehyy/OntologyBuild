/**
 * 调用链轮次时间格式化（纯函数）。
 *
 * 每轮执行的开始 / 结束时刻来自消息落库时间戳：历史会话用后端
 * AgentMessage.created_at，实时会话由前端在发送与回合终态写入本地时钟。
 * 保持零依赖便于 node --test 单测复用。
 */

const pad2 = (n: number): string => String(n).padStart(2, '0')

const clockOf = (d: Date): string => `${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}`

/** 同一天的两次 ISO 时刻展示为 HH:mm:ss；跨天时两端补「M-DD 」日期前缀。任一缺省或非法返回 '—'。 */
export function formatTurnTimes(startAt?: string | null, endAt?: string | null): { start: string; end: string } {
  const toValid = (value?: string | null): Date | null => {
    const d = value ? new Date(value) : null
    return d && !Number.isNaN(d.getTime()) ? d : null
  }
  const start = toValid(startAt)
  const end = toValid(endAt)
  if (!start || !end) return { start: start ? clockOf(start) : '—', end: end ? clockOf(end) : '—' }
  const crossDay = start.toDateString() !== end.toDateString()
  const prefix = (d: Date) => `${d.getMonth() + 1}-${pad2(d.getDate())} `
  return { start: (crossDay ? prefix(start) : '') + clockOf(start), end: (crossDay ? prefix(end) : '') + clockOf(end) }
}

/** 轮次总耗时：墙钟差值兜底时钟偏移；<1s 用 ms，<1min 用一位小数秒，其余用「X分Y秒」。缺省或非法返回 null。 */
export function formatTurnElapsed(startAt?: string | null, endAt?: string | null): string | null {
  if (!startAt || !endAt) return null
  const ms = Math.max(0, new Date(endAt).getTime() - new Date(startAt).getTime())
  if (!Number.isFinite(ms)) return null
  if (ms < 1000) return `${ms} ms`
  const totalSeconds = ms / 1000
  if (totalSeconds < 60) return `${totalSeconds.toFixed(1).replace(/\.0$/, '')} 秒`
  const minutes = Math.floor(totalSeconds / 60)
  const seconds = Math.round(totalSeconds % 60)
  return seconds === 0 ? `${minutes} 分` : `${minutes} 分 ${seconds} 秒`
}
