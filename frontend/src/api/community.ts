import { apiClientV2 } from '@/api/client'
import type {
  McpTool,
  McpTransport,
  SuperMcpServer,
} from '@/api/superAssistant'

export interface McpServerCreateInput {
  name: string
  transport: McpTransport
  url: string
  headers: Record<string, string>
  command?: string | null
  args?: string[]
  env?: Record<string, string>
  enabled: boolean
  require_confirmation: boolean
}

export type McpServerUpdateInput = Partial<Omit<McpServerCreateInput, 'name'>>

export interface McpManagementClient {
  createMcpServer: (body: McpServerCreateInput) => Promise<SuperMcpServer>
  updateMcpServer: (id: string, body: McpServerUpdateInput) => Promise<SuperMcpServer>
}

export const communityApi = {
  mcpServers: () => apiClientV2.get<SuperMcpServer[]>('/community/mcp-servers'),
  createMcpServer: (body: McpServerCreateInput) =>
    apiClientV2.post<SuperMcpServer>('/community/mcp-servers', body),
  updateMcpServer: (id: string, body: McpServerUpdateInput) =>
    apiClientV2.patch<SuperMcpServer>(`/community/mcp-servers/${id}`, body),
  deleteMcpServer: (id: string) => apiClientV2.delete(`/community/mcp-servers/${id}`),
  testMcpServer: (id: string) => apiClientV2.post<{ ok: boolean; message: string; tools: McpTool[] }>(
    `/community/mcp-servers/${id}/test`,
  ),
  installPlatformMinio: () => apiClientV2.post<SuperMcpServer>(
    '/community/mcp-servers/platform-minio',
  ),
}
