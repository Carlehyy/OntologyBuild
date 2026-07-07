import { apiClient } from './client'

export type McpParameter = {
  name: string
  location: string
  required: boolean
  schema?: Record<string, unknown>
}

export type McpInterface = {
  operation_id: string
  method: string
  path: string
  summary: string
  description: string
  tags: string[]
  parameters: McpParameter[]
  request_body?: Record<string, unknown> | null
  enabled: boolean
  supported: boolean
  unsupported_reason?: string | null
  excluded: boolean
  exclude_reason?: string | null
  display_name?: string | null
  config_description?: string | null
}

export type McpInterfaceList = {
  items: McpInterface[]
  total: number
  enabled_count: number
}

export type McpInfo = {
  endpoint: string
  transport: 'streamable-http'
  server_name: string
  token_required: boolean
  auth: string
  published_count: number
}

export const mcpApi = {
  listInterfaces: () => apiClient.get<McpInterfaceList>('/mcp/interfaces'),
  setOpen: (operationId: string, open: boolean) =>
    apiClient.post<McpInterface>(`/mcp/interfaces/${encodeURIComponent(operationId)}/open`, { open }),
  info: () => apiClient.get<McpInfo>('/mcp/info'),
}
