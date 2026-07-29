import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

const centralEnvDir = path.resolve(__dirname, '../config/generated/local')

function validPort(value: string | undefined, fallback: number): number {
  const parsed = Number.parseInt(value || '', 10)
  return Number.isInteger(parsed) && parsed >= 1 && parsed <= 65535
    ? parsed
    : fallback
}

function urlHost(host: string): string {
  return host.includes(':') && !host.startsWith('[') ? `[${host}]` : host
}

export default defineConfig(({ mode }) => {
  // loadEnv is required inside Vite config evaluation.  process.env remains
  // authoritative for Docker/Actions, the central local file overrides the
  // legacy frontend env files, and clean checkouts retain historical defaults.
  const legacyEnv = loadEnv(mode, __dirname, '')
  const centralEnv = loadEnv(mode, centralEnvDir, '')
  const hasProcessBackend = Boolean(
    process.env.LOCAL_BACKEND_HOST || process.env.LOCAL_BACKEND_PORT,
  )
  const hasCentralBackend = Boolean(
    centralEnv.LOCAL_BACKEND_HOST || centralEnv.LOCAL_BACKEND_PORT,
  )
  const backendHost =
    process.env.LOCAL_BACKEND_HOST ||
    centralEnv.LOCAL_BACKEND_HOST ||
    '127.0.0.1'
  const backendPort = validPort(
    process.env.LOCAL_BACKEND_PORT || centralEnv.LOCAL_BACKEND_PORT,
    8000,
  )
  const apiTarget =
    process.env.VITE_API_PROXY_TARGET ||
    (hasProcessBackend ? `http://${urlHost(backendHost)}:${backendPort}` : '') ||
    centralEnv.VITE_API_PROXY_TARGET ||
    (hasCentralBackend ? `http://${urlHost(backendHost)}:${backendPort}` : '') ||
    legacyEnv.VITE_API_PROXY_TARGET ||
    `http://${urlHost(backendHost)}:${backendPort}`

  const frontendHost =
    process.env.LOCAL_FRONTEND_HOST ||
    centralEnv.LOCAL_FRONTEND_HOST ||
    legacyEnv.LOCAL_FRONTEND_HOST ||
    true
  const frontendPort = validPort(
    process.env.DEPLOY_RUN_PORT ||
      process.env.LOCAL_FRONTEND_PORT ||
      centralEnv.LOCAL_FRONTEND_PORT ||
      legacyEnv.DEPLOY_RUN_PORT ||
      legacyEnv.LOCAL_FRONTEND_PORT,
    5173,
  )

  return {
    plugins: [react()],
    resolve: { alias: { '@': path.resolve(__dirname, './src') } },
    server: {
      host: frontendHost,
      port: frontendPort,
      strictPort: true,
      // 允许通过沙箱/隧道的任意 host 访问 dev server（开发便利）
      allowedHosts: true,
      proxy: {
        '/api': { target: apiTarget, ws: true },
        '/api-hub': { target: apiTarget, ws: true },
        '/proxy': { target: apiTarget },
      },
    },
  }
})
