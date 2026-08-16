import { useEffect, useRef } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { X } from 'lucide-react'
import { useAuthStore } from '@/stores/authStore'
import { useTabStore } from '@/stores/tabStore'
import { canAccessPath, defaultLandingPath, navTabForPath } from '@/config/navigation'

/**
 * 顶栏多标签页（tags-view）：按叶子菜单项粒度记录访问过的页面，
 * 点击标签切回该菜单域内最后访问的路径，不做 keep-alive（切换即重新挂载）。
 * 标签列表持久化在 localStorage（nav-tabs），刷新后恢复；仅 md 及以上屏幕显示。
 */
export default function NavTabs() {
  const location = useLocation()
  const navigate = useNavigate()
  const user = useAuthStore(s => s.user)
  const tabs = useTabStore(s => s.tabs)
  const activeKey = useTabStore(s => s.activeKey)
  const recordVisit = useTabStore(s => s.recordVisit)
  const close = useTabStore(s => s.close)
  const listRef = useRef<HTMLDivElement>(null)

  // 把当前授权页面记录为标签；越权页（AccessDenied）与无菜单映射页不产生标签。
  // 依赖整个 location：同路径再次导航（如关闭最后一个标签后跳回落地页）也会重记。
  useEffect(() => {
    if (!user) return
    if (!canAccessPath(user, location.pathname)) return
    const info = navTabForPath(location.pathname)
    if (!info) return
    recordVisit(user.username, {
      key: info.key,
      title: info.title,
      fullTitle: info.fullTitle,
      path: `${location.pathname}${location.search}`,
    })
  }, [user, location, recordVisit])

  // 激活标签滚动到可视区域
  useEffect(() => {
    listRef.current
      ?.querySelector('[role="tab"][aria-selected="true"]')
      ?.scrollIntoView({ block: 'nearest', inline: 'nearest' })
  }, [activeKey, tabs.length])

  const handleClose = (key: string) => {
    const result = close(key)
    if (result.closedActive) {
      navigate(result.nextPath ?? defaultLandingPath(user))
    }
  }

  return (
    <div
      ref={listRef}
      role="tablist"
      aria-label="页面标签"
      className="hidden md:flex min-w-0 flex-1 items-center gap-1 overflow-x-auto [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
    >
      {tabs.map(tab => {
        const selected = tab.key === activeKey
        return (
          <div
            key={tab.key}
            role="tab"
            aria-selected={selected}
            tabIndex={0}
            title={tab.fullTitle ?? tab.title}
            onClick={() => navigate(tab.path)}
            onKeyDown={e => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault()
                navigate(tab.path)
              }
            }}
            className={`group flex shrink-0 cursor-pointer items-center gap-1 rounded-lg border border-dashed py-1.5 pl-3 pr-2 text-xs transition-colors ${selected
              ? 'border-[var(--color-nav-bg)] bg-[var(--color-nav-light)] text-[var(--color-nav-bg)] font-medium'
              : 'border-[var(--color-border)] text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text-primary)]'}`}
          >
            <span className="max-w-36 truncate">{tab.title}</span>
            <button
              type="button"
              aria-label={`关闭 ${tab.title}`}
              onClick={e => {
                e.stopPropagation()
                handleClose(tab.key)
              }}
              className={`flex h-4 w-4 items-center justify-center rounded transition-colors ${selected
                ? 'hover:bg-[var(--color-nav-bg)] hover:text-white'
                : 'text-[var(--color-text-tertiary)] hover:bg-[var(--color-border)] hover:text-[var(--color-text-primary)]'}`}
            >
              <X size={12} />
            </button>
          </div>
        )
      })}
    </div>
  )
}
