/** 输入框 slash 命令提示的纯函数匹配（提示条与单测复用）。
 *
 * 命令目录由后端按"已启用的外部集成"下发（当前为 multica 配置的
 * commands 字段）；未配置/未启用时目录为空，输入 / 不出现任何提示，
 * 即超级助手在此状态下不提供该集成的任何命令。
 */
export interface SlashCommand {
  command: string
  title: string
  description: string
  usage: string
  write: boolean
}

/** 命令的完整输入 token：usage 去掉参数占位部分（如 /multica:list_tasks） */
export function slashCommandToken(command: SlashCommand): string {
  return command.usage.split(' ', 1)[0]
}

/** 判断输入是否处于 slash 命令选择态：以 / 开头且尚未出现空白（参数区） */
export function isSlashCommandDraft(input: string): boolean {
  const typed = input.trimStart()
  return typed.startsWith('/') && !/\s/.test(typed)
}

/** 按输入前缀过滤命令：输入 / 即列出全部可用命令，逐字符收窄；
 *  进入参数输入（出现空白）或目录为空时返回空数组 */
export function matchSlashCommands(
  input: string,
  commands: SlashCommand[],
): SlashCommand[] {
  if (commands.length === 0) return []
  const typed = input.trimStart()
  if (!isSlashCommandDraft(typed)) return []
  return commands.filter(item => slashCommandToken(item).startsWith(typed))
}
