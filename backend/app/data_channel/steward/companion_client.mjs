#!/usr/bin/env node
// OpenOntology Data Steward browser companion (Node.js 22+).
// It binds Chrome CDP to loopback only and makes one authenticated outbound WSS connection.
import childProcess from 'node:child_process'
import fs from 'node:fs'
import net from 'node:net'
import os from 'node:os'
import path from 'node:path'

const args = Object.fromEntries(process.argv.slice(2).map((value, index, all) =>
  value.startsWith('--') ? [value.slice(2), all[index + 1]?.startsWith('--') ? '' : all[index + 1]] : null
).filter(Boolean))
const server = args.server
const source = args.source
const token = args.token
const port = Number(args['cdp-port'] || 0) || 19333
if (!server || !source || !token) {
  console.error('用法: node companion_client.mjs --server https://平台地址 --source 来源ID --token 一次性配对令牌')
  process.exit(2)
}
const wsUrl = `${server.replace(/\/$/, '').replace(/^http:/, 'ws:').replace(/^https:/, 'wss:')}/api/v2/steward/browser/companion/connect`
if (process.env.NODE_ENV === 'production' && !wsUrl.startsWith('wss://')) {
  console.error('生产环境的本机浏览器助手只允许连接 HTTPS/WSS 平台。')
  process.exit(2)
}

function browserCandidates() {
  if (process.platform === 'darwin') return [
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge',
    '/Applications/Chromium.app/Contents/MacOS/Chromium',
  ]
  if (process.platform === 'win32') {
    const roots = [process.env.PROGRAMFILES, process.env['PROGRAMFILES(X86)'], process.env.LOCALAPPDATA].filter(Boolean)
    return roots.flatMap(root => [
      path.join(root, 'Google', 'Chrome', 'Application', 'chrome.exe'),
      path.join(root, 'Microsoft', 'Edge', 'Application', 'msedge.exe'),
    ])
  }
  return ['/usr/bin/google-chrome', '/usr/bin/google-chrome-stable', '/usr/bin/microsoft-edge', '/usr/bin/chromium', '/usr/bin/chromium-browser']
}

async function ready() {
  try {
    const response = await fetch(`http://127.0.0.1:${port}/json/version`, { signal: AbortSignal.timeout(800) })
    return response.ok
  } catch { return false }
}

async function ensureBrowser() {
  if (await ready()) return
  const executable = args.browser || browserCandidates().find(candidate => fs.existsSync(candidate))
  if (!executable) throw new Error('未找到 Chrome、Edge 或 Chromium；可用 --browser 指定可执行文件')
  const profile = args.profile || path.join(os.homedir(), '.openontology', 'browser-companion', source)
  fs.mkdirSync(profile, { recursive: true })
  childProcess.spawn(executable, [
    `--remote-debugging-port=${port}`,
    '--remote-debugging-address=127.0.0.1',
    '--remote-allow-origins=*',
    `--user-data-dir=${profile}`,
    '--no-first-run', '--no-default-browser-check',
  ], { detached: true, stdio: 'ignore' }).unref()
  for (let i = 0; i < 40; i += 1) {
    await new Promise(resolve => setTimeout(resolve, 250))
    if (await ready()) return
  }
  throw new Error('浏览器已启动，但 CDP 在 10 秒内未就绪')
}

const streams = new Map()
const header = streamId => { const value = Buffer.allocUnsafe(4); value.writeUInt32BE(streamId); return value }

function connect() {
  const websocket = new WebSocket(wsUrl)
  websocket.binaryType = 'arraybuffer'
  websocket.onopen = () => {
    websocket.send(JSON.stringify({ type: 'auth', sourceId: source, token }))
    console.log(`浏览器助手已连接：${server}（CDP 仅监听 127.0.0.1:${port}）`)
  }
  websocket.onmessage = event => {
    if (typeof event.data === 'string') {
      let message
      try { message = JSON.parse(event.data) } catch { return }
      const streamId = Number(message.streamId || 0)
      if (message.type === 'open' && streamId) {
        const socket = net.connect({ host: '127.0.0.1', port })
        streams.set(streamId, socket)
        socket.on('data', chunk => {
          if (websocket.readyState === WebSocket.OPEN) websocket.send(Buffer.concat([header(streamId), chunk]))
        })
        socket.on('close', () => {
          streams.delete(streamId)
          if (websocket.readyState === WebSocket.OPEN) websocket.send(JSON.stringify({ type: 'close', streamId }))
        })
        socket.on('error', () => socket.destroy())
      } else if (message.type === 'close') {
        streams.get(streamId)?.destroy(); streams.delete(streamId)
      }
      return
    }
    const packet = Buffer.from(event.data)
    if (packet.length < 4) return
    const streamId = packet.readUInt32BE(0)
    streams.get(streamId)?.write(packet.subarray(4))
  }
  websocket.onclose = event => {
    for (const socket of streams.values()) socket.destroy()
    streams.clear()
    console.error(`连接已断开 (${event.code})，3 秒后重连…`)
    setTimeout(connect, 3000)
  }
  websocket.onerror = () => websocket.close()
}

try {
  await ensureBrowser()
  connect()
} catch (error) {
  console.error(error?.message || error)
  process.exit(1)
}
