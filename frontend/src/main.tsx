import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './styles/tokens.css'
import './styles/animations.css'
import './index.css'
import './i18n'
import App from './App'
import { applyThemeClass } from './lib/theme'
import { useThemeStore } from './stores/themeStore'

// 主题初始化：index.html 的内联脚本已在首帧前应用 .dark，这里在 store 水合后再次对齐，
// 并兜底 index.html 脚本未能覆盖的运行时注入场景。
applyThemeClass(useThemeStore.getState().theme, document.documentElement)

// ReactFlow / Chromium 偶发 ResizeObserver loop 通知属于浏览器布局测量噪音；
// 在 Vite dev overlay 中会被当成未处理错误刷屏，影响本地调试体验。
window.addEventListener('error', (event) => {
  if (/ResizeObserver loop (completed|limit exceeded)/i.test(event.message)) {
    event.stopImmediatePropagation()
  }
})

function reloadForUpdatedAssets() {
  const key = 'openontology:last-asset-reload'
  const now = Date.now()
  const last = Number(sessionStorage.getItem(key) || 0)
  if (now - last < 10_000) return false
  sessionStorage.setItem(key, String(now))
  window.location.reload()
  return true
}

(window as any).__openOntologyReloadForAssets = reloadForUpdatedAssets

window.addEventListener('vite:preloadError', (event) => {
  event.preventDefault()
  reloadForUpdatedAssets()
})

window.addEventListener('unhandledrejection', (event) => {
  const message = String((event.reason as any)?.message || event.reason || '')
  if (/Failed to fetch dynamically imported module|Importing a module script failed|error loading dynamically imported module/i.test(message)) {
    event.preventDefault()
    reloadForUpdatedAssets()
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
