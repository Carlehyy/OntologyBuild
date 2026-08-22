/**
 * 试调用 / 调试入参的 JSON 校验工具：解析失败时尽力定位到行列。
 *
 * 各 JS 引擎的 SyntaxError 消息格式不一：
 * - 新版 V8（Chrome 114+ / Node 20+）：`... at line 3 column 5 of the JSON data`
 * - 旧版 V8：`Unexpected token } in JSON at position 42`
 * - 其他引擎：可能只有消息没有位置
 * 定位失败时 line/column 为 null，调用方只展示原始消息即可。
 */

export interface JsonParseIssue {
  message: string
  line: number | null
  column: number | null
}

export type JsonObjectValidation =
  | { value: Record<string, unknown>; issue: null }
  | { value: null; issue: JsonParseIssue }

/** 从 JSON.parse 抛出的错误中提取行列号；提取不到时为 null。 */
export function describeJsonParseError(text: string, error: unknown): JsonParseIssue {
  const message = error instanceof Error ? error.message : String(error)
  const lineCol = /line (\d+) column (\d+)/i.exec(message)
  if (lineCol) {
    return { message, line: Number(lineCol[1]), column: Number(lineCol[2]) }
  }
  const position = /position (\d+)/i.exec(message)
  if (position) {
    // position 是字符串下标：换行累计得行，行内累计得列
    const index = Math.min(Number(position[1]), Math.max(text.length - 1, 0))
    let line = 1
    let column = 1
    for (let i = 0; i < index; i += 1) {
      if (text[i] === '\n') {
        line += 1
        column = 1
      } else {
        column += 1
      }
    }
    return { message, line, column }
  }
  return { message, line: null, column: null }
}

/** 校验入参必须是 JSON 对象（{...}），返回解析值或带定位的错误。 */
export function validateJsonObject(text: string): JsonObjectValidation {
  let parsed: unknown
  try {
    parsed = JSON.parse(text || '{}')
  } catch (error) {
    return { value: null, issue: describeJsonParseError(text, error) }
  }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    return {
      value: null,
      issue: { message: '入参必须是 JSON 对象（以 { 开头、} 结尾）', line: null, column: null },
    }
  }
  return { value: parsed as Record<string, unknown>, issue: null }
}
