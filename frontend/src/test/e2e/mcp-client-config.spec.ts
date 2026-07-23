import { expect, test } from '@playwright/test'

import { parseMcpClientConfig } from '../../lib/mcpClientConfig'


test('兼容 VS Code JSONC、多 Server 选择与 stdio 配置', () => {
  const parsed = parseMcpClientConfig(`
    \`\`\`jsonc
    {
      // VS Code 使用 servers，而不是 mcpServers。
      "servers": {
        "Remote Docs": {
          "type": "http",
          "url": "https://docs.example.com/mcp",
          "headers": { "X-Retry": 3 },
        },
        "local-tools": {
          "type": "stdio",
          "command": "uvx",
          "args": ["mcp-server-fetch"],
          "env": { "DEBUG": true },
        },
      },
    }
    \`\`\`
  `)

  expect(parsed.sourceLabel).toBe('VS Code servers')
  expect(parsed.servers).toHaveLength(2)
  expect(parsed.servers[0]).toMatchObject({
    name: 'Remote-Docs',
    transport: 'streamable_http',
    url: 'https://docs.example.com/mcp',
    headers: { 'X-Retry': '3' },
  })
  expect(parsed.servers[1]).toMatchObject({
    name: 'local-tools',
    transport: 'stdio',
    command: 'uvx',
    args: ['mcp-server-fetch'],
    env: { DEBUG: 'true' },
  })
})

test('兼容主流客户端的包装与远程地址字段', () => {
  const cases = [
    {
      input: JSON.stringify({
        mcpServers: { geminiHttp: { httpUrl: 'https://gemini.example.com/mcp' } },
      }),
      expected: { name: 'geminiHttp', transport: 'streamable_http', url: 'https://gemini.example.com/mcp' },
    },
    {
      input: JSON.stringify({
        mcpServers: { geminiSse: { url: 'https://gemini.example.com/sse' } },
      }),
      expected: { name: 'geminiSse', transport: 'sse', url: 'https://gemini.example.com/sse' },
    },
    {
      input: JSON.stringify({
        mcpServers: { windsurf: { serverUrl: 'https://windsurf.example.com/mcp' } },
      }),
      expected: { name: 'windsurf', transport: 'streamable_http', url: 'https://windsurf.example.com/mcp' },
    },
    {
      input: JSON.stringify({
        context_servers: {
          zed: {
            settings: {
              url: 'https://zed.example.com/mcp',
              headers: { Authorization: 'Bearer token' },
            },
          },
        },
      }),
      expected: { name: 'zed', transport: 'streamable_http', url: 'https://zed.example.com/mcp' },
    },
    {
      input: JSON.stringify({
        name: 'JetBrains HTTP',
        type: 'streamable-http',
        url: 'https://jetbrains.example.com/mcp',
      }),
      expected: { name: 'JetBrains-HTTP', transport: 'streamable_http', url: 'https://jetbrains.example.com/mcp' },
    },
  ]

  for (const item of cases) {
    expect(parseMcpClientConfig(item.input).servers[0]).toMatchObject(item.expected)
  }
})

test('兼容 Continue 数组、嵌套 VS Code 配置与 mcp-remote', () => {
  const continueConfig = parseMcpClientConfig(JSON.stringify({
    name: 'MCP bundle',
    version: '1.0.0',
    schema: 'v1',
    mcpServers: [
      { name: 'Browser search', type: 'stdio', command: 'npx', args: ['@playwright/mcp@latest'] },
    ],
  }))
  expect(continueConfig.servers[0]).toMatchObject({
    name: 'Browser-search',
    transport: 'stdio',
    command: 'npx',
  })

  const nestedVscode = parseMcpClientConfig(JSON.stringify({
    customizations: {
      vscode: {
        mcp: {
          servers: {
            playwright: { command: 'npx', args: ['-y', '@playwright/mcp'] },
          },
        },
      },
    },
  }))
  expect(nestedVscode.servers[0]).toMatchObject({
    name: 'playwright',
    transport: 'stdio',
    command: 'npx',
  })

  const remoteProxy = parseMcpClientConfig(JSON.stringify({
    mcpServers: {
      remote: {
        command: 'npx',
        args: [
          '-y',
          'mcp-remote@latest',
          'https://remote.example.com/mcp',
          '--header',
          'Authorization: Bearer token',
        ],
      },
    },
  }))
  expect(remoteProxy.servers[0]).toMatchObject({
    name: 'remote',
    transport: 'streamable_http',
    url: 'https://remote.example.com/mcp',
    command: '',
    args: [],
    headers: { Authorization: 'Bearer token' },
  })
})
