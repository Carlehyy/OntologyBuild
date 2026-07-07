import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './styles/tokens.css'
import './styles/animations.css'
import './index.css'
import './i18n'
import App from './App'

// ReactFlow / Chromium 偶发 ResizeObserver loop 通知属于浏览器布局测量噪音；
// 在 Vite dev overlay 中会被当成未处理错误刷屏，影响本地调试体验。
window.addEventListener('error', (event) => {
  if (/ResizeObserver loop (completed|limit exceeded)/i.test(event.message)) {
    event.stopImmediatePropagation()
  }
})

// 开发便利：支持通过 URL 注入登录态（沙箱/隧道环境冒烟测试用）
// 例如  https://host/?token=JWT#/ontologies/<id>/graph
try {
  const sp = new URLSearchParams(window.location.search)
  const t = sp.get('token')
  if (t) {
    localStorage.setItem('token', t)
    const raw = localStorage.getItem('auth-store')
    const parsed = raw ? JSON.parse(raw) : { state: {}, version: 0 }
    parsed.state = { ...(parsed.state || {}), token: t }
    localStorage.setItem('auth-store', JSON.stringify(parsed))
  }
} catch { /* noop */ }

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
