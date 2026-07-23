import type { McpTransport } from '@/api/superAssistant'


type JsonObject = Record<string, unknown>

export interface ParsedMcpClientServer {
  name: string
  transport: McpTransport
  url: string
  command: string
  args: string[]
  headers: Record<string, string>
  env: Record<string, string>
  warnings: string[]
}

export interface ParsedMcpClientConfig {
  sourceLabel: string
  servers: ParsedMcpClientServer[]
}

const isObject = (value: unknown): value is JsonObject =>
  !!value && !Array.isArray(value) && typeof value === 'object'

const stringValue = (value: unknown) =>
  typeof value === 'string' && value.trim() ? value.trim() : ''

const stripJsonComments = (value: string) => {
  let result = ''
  let inString = false
  let escaped = false
  let lineComment = false
  let blockComment = false

  for (let index = 0; index < value.length; index += 1) {
    const current = value[index]
    const next = value[index + 1]

    if (lineComment) {
      if (current === '\n') {
        lineComment = false
        result += current
      }
      continue
    }
    if (blockComment) {
      if (current === '*' && next === '/') {
        blockComment = false
        index += 1
      } else if (current === '\n') {
        result += current
      }
      continue
    }
    if (inString) {
      result += current
      if (escaped) {
        escaped = false
      } else if (current === '\\') {
        escaped = true
      } else if (current === '"') {
        inString = false
      }
      continue
    }
    if (current === '"') {
      inString = true
      result += current
      continue
    }
    if (current === '/' && next === '/') {
      lineComment = true
      index += 1
      continue
    }
    if (current === '/' && next === '*') {
      blockComment = true
      index += 1
      continue
    }
    result += current
  }

  return result
}

const stripTrailingCommas = (value: string) => {
  let result = ''
  let inString = false
  let escaped = false

  for (let index = 0; index < value.length; index += 1) {
    const current = value[index]
    if (inString) {
      result += current
      if (escaped) {
        escaped = false
      } else if (current === '\\') {
        escaped = true
      } else if (current === '"') {
        inString = false
      }
      continue
    }
    if (current === '"') {
      inString = true
      result += current
      continue
    }
    if (current === ',') {
      let lookahead = index + 1
      while (lookahead < value.length && /\s/.test(value[lookahead])) lookahead += 1
      if (value[lookahead] === '}' || value[lookahead] === ']') continue
    }
    result += current
  }

  return result
}

const parseJsonLike = (input: string): unknown => {
  const fenced = input.match(/```(?:jsonc?)?\s*([\s\S]*?)```/i)
  const source = (fenced?.[1] || input).replace(/^\uFEFF/, '').trim()
  if (!source) throw new Error('请粘贴 MCP 客户端配置')
  try {
    return JSON.parse(stripTrailingCommas(stripJsonComments(source)))
  } catch {
    throw new Error('JSON 格式无效，请检查引号、括号或逗号')
  }
}

const normalizeStringMap = (value: unknown): Record<string, string> => {
  if (!isObject(value)) return {}
  return Object.fromEntries(
    Object.entries(value)
      .filter(([, item]) => item !== null && item !== undefined)
      .map(([key, item]) => [
        key,
        typeof item === 'string' ? item : typeof item === 'object' ? JSON.stringify(item) : String(item),
      ]),
  )
}

const normalizeName = (value: string, fallback: string) => {
  const normalized = value
    .normalize('NFKD')
    .replace(/[\u0300-\u036f]/g, '')
    .replace(/[^a-zA-Z0-9_-]+/g, '-')
    .replace(/^[-_]+|[-_]+$/g, '')
  return normalized || fallback
}

const inferName = (config: JsonObject, index: number) => {
  const directName = stringValue(config.name) || stringValue(config.title) || stringValue(config.id)
  if (directName) return directName

  const url = stringValue(config.httpUrl)
    || stringValue(config.http_url)
    || stringValue(config.serverUrl)
    || stringValue(config.server_url)
    || stringValue(config.url)
    || stringValue(config.endpoint)
  if (url) {
    try {
      const parsedUrl = new URL(url)
      return `${parsedUrl.hostname}${parsedUrl.pathname === '/' ? '' : parsedUrl.pathname}`
    } catch {
      // The form will surface an invalid URL after import; keep deriving a safe name here.
    }
  }

  const args = Array.isArray(config.args) ? config.args.map(String) : []
  const packageName = args.find(item => !item.startsWith('-') && !/^https?:\/\//i.test(item))
  if (packageName) return packageName.split('/').pop()?.replace(/@[^@]+$/, '') || packageName

  const command = stringValue(config.command)
  if (command) return command.split(/[\\/]/).pop() || command
  return `mcp-server-${index + 1}`
}

const hasConnectionFields = (value: unknown): value is JsonObject => {
  if (!isObject(value)) return false
  const settings = isObject(value.settings) ? value.settings : {}
  const config = isObject(value.config) ? value.config : {}
  const merged = { ...settings, ...config, ...value }
  return ['command', 'url', 'httpUrl', 'http_url', 'serverUrl', 'server_url', 'endpoint']
    .some(key => merged[key] !== undefined)
}

const collectionEntries = (collection: unknown): Array<[string | undefined, JsonObject]> => {
  if (Array.isArray(collection)) {
    return collection.map((item, index) => {
      if (!isObject(item)) throw new Error(`第 ${index + 1} 个 MCP Server 配置无效`)
      return [undefined, item]
    })
  }
  if (!isObject(collection)) throw new Error('MCP Server 集合必须是对象或数组')
  if (hasConnectionFields(collection)) return [[undefined, collection]]
  return Object.entries(collection).map(([name, item]) => {
    if (!isObject(item)) throw new Error(`「${name}」不是有效的 MCP Server 配置`)
    return [name, item]
  })
}

const findCollection = (parsed: unknown): {
  sourceLabel: string
  entries: Array<[string | undefined, JsonObject]>
} => {
  if (Array.isArray(parsed)) {
    return { sourceLabel: 'Server 数组', entries: collectionEntries(parsed) }
  }
  if (!isObject(parsed)) throw new Error('MCP 客户端配置必须是 JSON 对象或数组')

  const nestedVscode = isObject(parsed.customizations)
    && isObject(parsed.customizations.vscode)
    && isObject(parsed.customizations.vscode.mcp)
    ? parsed.customizations.vscode.mcp.servers
    : undefined
  const nestedMcp = isObject(parsed.mcp) ? parsed.mcp.servers : undefined
  const candidates: Array<[string, unknown]> = [
    ['mcpServers', parsed.mcpServers],
    ['VS Code servers', parsed.servers],
    ['Zed context_servers', parsed.context_servers],
    ['contextServers', parsed.contextServers],
    ['VS Code customizations.vscode.mcp.servers', nestedVscode],
    ['mcp.servers', nestedMcp],
  ]
  const matched = candidates.find(([, collection]) => collection !== undefined)
  if (matched) {
    return { sourceLabel: matched[0], entries: collectionEntries(matched[1]) }
  }
  if (hasConnectionFields(parsed)) {
    return { sourceLabel: '单个 Server 对象', entries: [[undefined, parsed]] }
  }

  const directEntries = Object.entries(parsed)
    .filter(([, item]) => hasConnectionFields(item))
    .map(([name, item]) => [name, item] as [string, JsonObject])
  if (directEntries.length) return { sourceLabel: 'Server 映射对象', entries: directEntries }
  throw new Error('未识别到 MCP Server；配置需要 command、url、httpUrl 或 serverUrl')
}

const normalizeTransport = (
  value: unknown,
  url: string,
  urlField: 'url' | 'httpUrl' | 'serverUrl',
): McpTransport => {
  const normalized = stringValue(value).toLowerCase().replace(/[^a-z0-9]/g, '')
  if (normalized === 'stdio' || normalized === 'standardio') return 'stdio'
  if (normalized === 'sse' || normalized.includes('serversentevent')) return 'sse'
  if (normalized.includes('http')) return 'streamable_http'
  if (urlField === 'httpUrl' || urlField === 'serverUrl') return 'streamable_http'
  return /(?:^|[/_-])sse(?:[/_?&#-]|$)/i.test(url) ? 'sse' : 'streamable_http'
}

const remoteProxyDetails = (command: string, args: string[]) => {
  if (!/^(?:.*[\\/])?npx(?:\.cmd)?$/i.test(command)) return null
  const remoteIndex = args.findIndex(item => {
    const packageName = item.split('/').pop() || item
    return /^mcp-remote(?:@|$)/i.test(packageName)
  })
  if (remoteIndex < 0) return null
  const url = args.slice(remoteIndex + 1).find(item => /^https?:\/\//i.test(item))
  if (!url) return null

  const headers: Record<string, string> = {}
  for (let index = remoteIndex + 1; index < args.length; index += 1) {
    const item = args[index]
    const inlineHeader = item.match(/^(?:--header|-H)=(.+)$/)
    const headerText = inlineHeader?.[1]
      || ((item === '--header' || item === '-H') ? args[index + 1] : '')
    if (!headerText) continue
    const separator = headerText.indexOf(':')
    if (separator > 0) headers[headerText.slice(0, separator).trim()] = headerText.slice(separator + 1).trim()
    if (!inlineHeader) index += 1
  }
  return { url, headers }
}

const parseServer = (
  raw: JsonObject,
  nameHint: string | undefined,
  index: number,
): ParsedMcpClientServer => {
  const settings = isObject(raw.settings) ? raw.settings : {}
  const nestedConfig = isObject(raw.config) ? raw.config : {}
  const config = { ...settings, ...nestedConfig, ...raw }
  const commandObject = isObject(config.command) ? config.command : {}
  const command = typeof config.command === 'string'
    ? config.command.trim()
    : stringValue(commandObject.path) || stringValue(commandObject.command) || stringValue(commandObject.executable)
  const rawArgs = Array.isArray(config.args)
    ? config.args
    : Array.isArray(commandObject.args)
      ? commandObject.args
      : Array.isArray(config.commandArgs)
        ? config.commandArgs
        : []
  const args = rawArgs.map(String)

  const httpUrl = stringValue(config.httpUrl) || stringValue(config.http_url)
  const serverUrl = stringValue(config.serverUrl) || stringValue(config.server_url)
  const regularUrl = stringValue(config.url) || stringValue(config.endpoint)
  let url = httpUrl || serverUrl || regularUrl
  let urlField: 'url' | 'httpUrl' | 'serverUrl' = httpUrl ? 'httpUrl' : serverUrl ? 'serverUrl' : 'url'
  const proxy = command ? remoteProxyDetails(command, args) : null
  if (!url && proxy) {
    url = proxy.url
    urlField = 'url'
  }

  const env = {
    ...normalizeStringMap(commandObject.env),
    ...normalizeStringMap(config.env),
    ...normalizeStringMap(config.environment),
  }
  const headers = {
    ...(proxy?.headers || {}),
    ...normalizeStringMap(config.requestHeaders),
    ...normalizeStringMap(config.httpHeaders),
    ...normalizeStringMap(config.headers),
  }
  const explicitTransport = config.transportType ?? config.transport ?? config.type
  const transport = proxy
    ? normalizeTransport(undefined, proxy.url, 'url')
    : url
      ? normalizeTransport(explicitTransport, url, urlField)
      : command
        ? 'stdio'
        : normalizeTransport(explicitTransport, '', 'url')

  if (transport === 'stdio' && !command) throw new Error('stdio 配置缺少 command')
  if (transport !== 'stdio' && !url) throw new Error('远程配置缺少 url、httpUrl 或 serverUrl')

  const rawName = nameHint || inferName(config, index)
  const name = normalizeName(rawName, `mcp-server-${index + 1}`)
  const serializedValues = [...Object.values(headers), ...Object.values(env)]
  const warnings: string[] = []
  if (serializedValues.some(value => /\$\{[^}]+}|\$[A-Z_][A-Z0-9_]*|%[A-Z_][A-Z0-9_]*%/i.test(value))) {
    warnings.push('变量占位符已原样保留，请在保存前替换或确认后端环境可用')
  }
  if (isObject(config.oauth)) {
    warnings.push('客户端 OAuth 配置无法自动迁移，请确认该服务可直接访问或改用请求头')
  }

  return {
    name,
    transport,
    url: transport === 'stdio' ? '' : url,
    command: transport === 'stdio' ? command : '',
    args: transport === 'stdio' ? args : [],
    headers: transport === 'stdio' ? {} : headers,
    env: transport === 'stdio' ? env : {},
    warnings,
  }
}

export const parseMcpClientConfig = (input: string): ParsedMcpClientConfig => {
  const parsed = parseJsonLike(input)
  const { sourceLabel, entries } = findCollection(parsed)
  if (!entries.length) throw new Error('配置中没有 MCP Server')

  const usedNames = new Set<string>()
  const servers = entries.map(([nameHint, raw], index) => {
    let server: ParsedMcpClientServer
    try {
      server = parseServer(raw, nameHint, index)
    } catch (error) {
      const label = nameHint ? `「${nameHint}」` : `第 ${index + 1} 个 Server`
      throw new Error(`${label}配置无效：${error instanceof Error ? error.message : '无法解析'}`, { cause: error })
    }
    const baseName = server.name
    let uniqueName = baseName
    let suffix = 2
    while (usedNames.has(uniqueName)) {
      uniqueName = `${baseName}-${suffix}`
      suffix += 1
    }
    usedNames.add(uniqueName)
    return { ...server, name: uniqueName }
  })

  return { sourceLabel, servers }
}
