// 测试入参 JSON 的即时校验：输入过程中定位语法错误，不等点击「执行」才报错。
// 纯函数，便于单元测试与跨引擎行为一致。

export interface TestInputStatus {
  ok: boolean
  message: string
}

/**
 * 校验测试入参文本：
 * - 空白按 {} 处理（与执行时口径一致）；
 * - 顶层必须是对象（数组/标量均不接受）；
 * - JSON 语法错误尽量定位到行列。不同引擎错误格式不同：
 *   V8 新版含 "(line x column y)"，旧版含 "position n"，其余引擎仅给消息原文。
 */
export function validateTestInputText(text: string): TestInputStatus {
  const source = text.trim() === '' ? '{}' : text
  try {
    const parsed: unknown = JSON.parse(source)
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      return { ok: false, message: '顶层必须是 JSON 对象（context / actions / horizon）' }
    }
    return { ok: true, message: '' }
  } catch (error) {
    const raw = (error as Error)?.message || '不是有效 JSON'
    const lineCol = /line (\d+) column (\d+)/i.exec(raw)
    if (lineCol) {
      return { ok: false, message: `第 ${lineCol[1]} 行第 ${lineCol[2]} 列附近：${raw}` }
    }
    const position = /position (\d+)/i.exec(raw)
    if (position) {
      const offset = Number(position[1])
      const before = source.slice(0, offset)
      const line = before.split('\n').length
      const column = offset - before.lastIndexOf('\n')
      return { ok: false, message: `第 ${line} 行第 ${column} 列附近：${raw}` }
    }
    return { ok: false, message: raw }
  }
}
