/** multica /multica: 命令提示的纯函数匹配（输入框提示条与单测复用）。
 *
 * 命令目录由后端 GET /super-assistant/multica/config 下发；未配置/未启用
 * 时后端返回空 commands，输入框不提供任何 multica 命令提示。
 */
export interface MulticaCommandHint {
  command: string
  title: string
  description: string
  usage: string
  write: boolean
}

/** 判断输入是否处于 /multica 命令输入态（含刚敲完 /multica 尚未加冒号） */
export function isMulticaCommandDraft(input: string): boolean {
  return /^\/multica(?![a-zA-Z0-9_])/i.test(input.trimStart())
}

/** 按输入前缀过滤可用命令；命令态之外或目录为空时返回空数组 */
export function matchMulticaCommands(
  input: string,
  commands: MulticaCommandHint[],
): MulticaCommandHint[] {
  if (commands.length === 0) return []
  const trimmed = input.trimStart()
  if (!isMulticaCommandDraft(trimmed)) return []
  const fragment = trimmed
    .slice('/multica'.length)
    .replace(/^[:：]\s*/, '')
    .split(/\s+/, 1)[0]
    .toLowerCase()
  if (!fragment) return commands
  return commands.filter(item => item.command.toLowerCase().startsWith(fragment))
}
