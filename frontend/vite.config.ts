import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

// Docker 内代理目标需指向 backend service, 本机直跑则用 localhost
const apiTarget = process.env.VITE_API_PROXY_TARGET || 'http://localhost:8000'

export default defineConfig({
  plugins: [react()],
  resolve: { alias: { '@': path.resolve(__dirname, './src') } },
  server: {
    host: true,
    port: parseInt(process.env.DEPLOY_RUN_PORT || '5173'),
    // 允许通过沙箱/隧道的任意 host 访问 dev server（开发便利）
    allowedHosts: true,
    proxy: { '/api': apiTarget, '/api-hub': apiTarget }
  }
})
