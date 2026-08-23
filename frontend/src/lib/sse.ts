/**
 * SSE 帧解析（纯函数）：把任意累积的流式文本切分为完整事件与不完整尾部。
 *
 * 帧格式与后端 assistant_service.sse() 对齐：
 *   event: 名称\ndata: {JSON}\n\n
 * 容忍 CRLF、帧内多行 data 取最后一行、非 JSON data 整帧跳过。
 */
export interface SseFrame {
  event: string
  data: unknown
}

export function parseSseBuffer(buffer: string): { events: SseFrame[]; rest: string } {
  const events: SseFrame[] = []
  const parts = buffer.split(/\r?\n\r?\n/)
  const rest = parts.pop() ?? ''
  for (const part of parts) {
    if (!part.trim()) continue
    let event = 'message'
    let dataLine: string | null = null
    for (const line of part.split(/\r?\n/)) {
      if (line.startsWith('event:')) event = line.slice(6).trim()
      else if (line.startsWith('data:')) dataLine = line.slice(5).trim()
    }
    if (dataLine == null) continue
    try {
      events.push({ event, data: JSON.parse(dataLine) })
    } catch {
      // 非 JSON data：整帧丢弃（与后端契约不符时不应让页面崩溃）
    }
  }
  return { events, rest }
}
