import { useState, useEffect, useRef } from 'react'
import { Link, useNavigate, useLocation } from 'react-router-dom'
import { useAuthStore } from '@/stores/authStore'
import {
  Network, Settings, LogOut, ChevronLeft, ChevronRight, ChevronDown,
  UserCircle, User, Menu, X,
} from 'lucide-react'
import InboxPopover from '@/components/inbox/InboxPopover'
import { visibleNavigation, type PlatformNavItem } from '@/config/navigation'

export default function Layout({ children }: { children: React.ReactNode }) {
  const logout = useAuthStore(s => s.logout)
  const user = useAuthStore(s => s.user)
  const navigate = useNavigate()
  const location = useLocation()
  const [collapsed, setCollapsed] = useState(false)
  const [mobileNavOpen, setMobileNavOpen] = useState(false)
  const [expandedGroup, setExpandedGroup] = useState<string | null>(null)
  const [collapsedActiveGroup, setCollapsedActiveGroup] = useState<string | null>(null)
  const [inboxOpen, setInboxOpen] = useState(false)
  const [userMenuOpen, setUserMenuOpen] = useState(false)
  const inboxRef = useRef<HTMLDivElement>(null)
  const userMenuRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (expandedGroup) {
      const stillActive = location.pathname === expandedGroup || location.pathname.startsWith(expandedGroup + '/')
      if (!stillActive) {
        const timer = window.setTimeout(() => setExpandedGroup(null), 0)
        return () => window.clearTimeout(timer)
      }
    }
    if (collapsedActiveGroup) {
      const stillActive = location.pathname === collapsedActiveGroup || location.pathname.startsWith(collapsedActiveGroup + '/')
      if (stillActive) return
      const timer = window.setTimeout(() => setCollapsedActiveGroup(null), 0)
      return () => window.clearTimeout(timer)
    }
  }, [collapsedActiveGroup, expandedGroup, location.pathname])

  // 点击外部关闭下拉面板
  useEffect(() => {
    if (!inboxOpen && !userMenuOpen) return
    const handleClickOutside = (e: MouseEvent) => {
      const target = e.target as Node
      if (inboxOpen && inboxRef.current && !inboxRef.current.contains(target)) setInboxOpen(false)
      if (userMenuOpen && userMenuRef.current && !userMenuRef.current.contains(target)) setUserMenuOpen(false)
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [inboxOpen, userMenuOpen])

  const navItems = visibleNavigation(user)

  const isActive = (to: string) => location.pathname === to || location.pathname.startsWith(to + '/')
  const isGroupActive = (item: PlatformNavItem) => isActive(item.to) || (item.subItems?.some(s => isActive(s.to)) ?? false)
  const isMappingWorkspace = /^\/ontologies\/[^/]+\/mapping-config$/.test(location.pathname)
  // 本体详情的顶部导航必须拥有稳定的布局上下文。若只在“本体结构”
  // 切换 overflow，页面滚动条的出现/消失会改变可用宽度，造成导航横移。
  const isOntologyDetailPage = /^\/ontologies\/[^/]+$/.test(location.pathname)
  const isEdgeToEdgePage = isActive('/explore') || isActive('/agent') || isActive('/super-assistant') || isActive('/events') || isActive('/api-hub') || isMappingWorkspace
  const isStewardPage = isActive('/data/pipelines/steward')
  // 标签栏独立于所有页面，所有页面统一显示（含业务探索、智能助手等全屏页）
  const showTopTabBar = true

  return (
    <div className="flex h-screen bg-[var(--color-bg-base)]" style={{ fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif" }}>
      {mobileNavOpen && (
        <button
          type="button"
          aria-label="关闭平台导航"
          onClick={() => setMobileNavOpen(false)}
          className="fixed inset-0 z-40 bg-black/30 md:hidden"
        />
      )}
      {/* Sidebar */}
      <aside className={`${mobileNavOpen ? 'translate-x-0' : '-translate-x-full'} fixed inset-y-0 left-0 z-50 flex w-56 flex-col border-r border-[var(--color-border)] bg-[var(--color-bg-elevated)] transition-all duration-300 ease-out md:static md:z-auto md:translate-x-0 ${collapsed ? 'md:w-16' : 'md:w-56'}`}>
        {/* Logo */}
        <div className={`h-14 border-b border-[var(--color-border)] flex items-center gap-3 transition-all ${collapsed ? 'justify-center px-0' : 'px-4'}`}>
          <div className="w-8 h-8 rounded-lg flex items-center justify-center shrink-0" style={{ background: 'var(--color-nav-bg)' }}>
            <Network size={18} className="text-white" />
          </div>
          {!collapsed && <span className="font-semibold text-[var(--color-text-primary)] tracking-tight text-sm">OpenOntology</span>}
          <button
            type="button"
            onClick={() => setMobileNavOpen(false)}
            aria-label="关闭平台导航"
            className="ml-auto flex h-10 w-10 items-center justify-center rounded-lg text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)] md:hidden"
          >
            <X size={17} />
          </button>
        </div>

        {/* Nav */}
        <nav className="flex-1 py-3 px-2 space-y-1.5 overflow-y-auto">
          {navItems.map((item) => {
            const Icon = item.icon
            const groupActive = isGroupActive(item)
            const groupExpanded = expandedGroup === item.to || (groupActive && collapsedActiveGroup !== item.to)

            if (item.subItems) {
              return (
                <div key={item.to}>
                  <button
                    type="button"
                    aria-expanded={groupExpanded}
                    onClick={() => {
                      if (collapsed) return
                      if (groupExpanded) {
                        setExpandedGroup(null)
                        setCollapsedActiveGroup(item.to)
                      } else {
                        setCollapsedActiveGroup(null)
                        setExpandedGroup(item.to)
                        if (!groupActive && item.subItems && item.subItems.length > 0) {
                          navigate(item.subItems[0].to)
                          setMobileNavOpen(false)
                        }
                      }
                    }}
                    className={`w-full flex items-center gap-3 px-3 py-2 rounded-lg text-sm transition-all ${groupActive
                        ? 'text-white' : 'text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text-primary)]'}`}
                    style={groupActive ? { background: 'var(--color-nav-bg)' } : {}}>
                    <Icon size={18} className="shrink-0" />
                    {!collapsed && <span className="flex-1 text-left font-medium">{item.label}</span>}
                    {!collapsed && <ChevronDown size={14} className={`transition-transform ${groupExpanded ? 'rotate-180' : ''}`} />}
                  </button>
                  {groupExpanded && !collapsed && (
                    <div className="ml-4 mt-1.5 space-y-1.5 border-l border-[var(--color-border)] pl-3 anim-fade-in-down">
                      {item.subItems.map(sub => {
                        const SubIcon = sub.icon
                        // 精确匹配优先：当前路径精确命中某兄弟子项时只高亮它，避免父路径被前缀误激活
                        const exact = item.subItems?.find(s => location.pathname === s.to)
                        const subActive = exact ? sub.to === exact.to : isActive(sub.to)
                        return (
                          <Link key={sub.to} to={sub.to} onClick={() => setMobileNavOpen(false)}
                            className={`flex items-center gap-2 px-2 py-1.5 rounded-md text-xs transition-colors ${subActive ? 'bg-[#b5f3e6] text-[var(--color-nav-bg)] font-medium' : 'text-[var(--color-text-tertiary)] hover:text-[var(--color-text-primary)] hover:bg-[var(--color-bg-hover)]'}`}>
                            <SubIcon size={14} />
                            <span>{sub.label}</span>
                          </Link>
                        )
                      })}
                    </div>
                  )}
                </div>
              )
            }

            return (
              <Link key={item.to} to={item.to} onClick={() => setMobileNavOpen(false)}
                className={`flex items-center gap-3 px-3 py-2 rounded-lg text-sm transition-all ${isActive(item.to)
                    ? 'text-white shadow-sm' : 'text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text-primary)]'}`}
                style={isActive(item.to) ? { background: 'var(--color-nav-bg)' } : {}}
                title={collapsed ? item.label : undefined}>
                <Icon size={18} className="shrink-0" />
                {!collapsed && <span className="font-medium">{item.label}</span>}
              </Link>
            )
          })}
        </nav>

        {/* Footer */}
        <div className="border-t border-[var(--color-border)]">
          <button onClick={() => setCollapsed(!collapsed)}
            className={`hidden md:flex items-center gap-2 px-4 h-9 text-sm text-[var(--color-text-tertiary)] hover:text-[var(--color-text-primary)] hover:bg-[var(--color-bg-hover)] transition-colors w-full ${collapsed ? 'justify-center' : ''}`}>
            {collapsed ? <ChevronRight size={16} /> : <ChevronLeft size={16} />}
            {!collapsed && <span>折叠起来</span>}
          </button>
          <button onClick={() => { logout(); navigate('/login') }}
            className={`flex items-center gap-2 px-4 h-9 text-sm text-[var(--color-text-tertiary)] hover:text-[var(--color-danger)] hover:bg-[var(--color-bg-hover)] transition-colors w-full ${collapsed ? 'justify-center' : ''}`}>
            <LogOut size={16} />
            {!collapsed && <span>退出登录</span>}
          </button>
        </div>
      </aside>

      {/* Main Content */}
      <main className="min-w-0 flex-1 flex flex-col overflow-hidden">
        {/* 通用标签栏 */}
        {showTopTabBar && (
          <div className="h-14 shrink-0 flex items-center justify-between border-b border-[var(--color-border)] bg-[var(--color-bg-elevated)] px-4">
            {/* 左侧预留 */}
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => { setCollapsed(false); setMobileNavOpen(true) }}
                aria-label="打开平台导航"
                className="flex h-10 w-10 items-center justify-center rounded-lg text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-500 md:hidden"
              >
                <Menu size={18} />
              </button>
            </div>
            
            {/* 右侧：收件箱 + 用户中心 */}
            <div className="flex items-center gap-1">
              {/* 收件箱 */}
              <div className="relative" ref={inboxRef}>
                <InboxPopover
                  open={inboxOpen}
                  onOpenChange={nextOpen => {
                    setInboxOpen(nextOpen)
                    if (nextOpen) setUserMenuOpen(false)
                  }}
                  onNavigate={navigate}
                />
              </div>

              {/* 用户中心 */}
              <div className="relative" ref={userMenuRef}>
                <button
                  onClick={() => { setUserMenuOpen(!userMenuOpen); setInboxOpen(false) }}
                  className={`flex items-center justify-center w-10 h-10 rounded-lg transition-colors ${
                    userMenuOpen ? 'bg-[var(--color-bg-hover)] text-[var(--color-text-primary)]' : 'text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text-primary)]'
                  }`}
                  title="用户中心"
                >
                  <UserCircle size={22} strokeWidth={1.8} />
                </button>

                {/* 用户中心下拉面板 */}
                {userMenuOpen && (
                  <div className="absolute right-0 mt-3 w-64 bg-[var(--color-bg-elevated)] rounded-lg shadow-lg border border-[var(--color-border)] z-50 overflow-hidden anim-fade-in-down origin-top">
                    {/* 用户信息 */}
                    <div className="px-4 py-4 border-b border-[var(--color-border)] flex items-center gap-3">
                      <div className="w-10 h-10 rounded-lg bg-teal-600 flex items-center justify-center text-white font-semibold shrink-0">
                        {(user?.username || 'U').slice(0, 1).toUpperCase()}
                      </div>
                      <div className="min-w-0">
                        <p className="truncate font-medium text-[var(--color-text-primary)] text-sm">{user?.username || '未知用户'}</p>
                        <p className="text-xs text-[var(--color-text-tertiary)] mt-0.5">
                          {user?.role === 'admin' ? '管理员' : user?.role === 'editor' ? '编辑者' : user?.role === 'custom' ? '自定义' : '查看者'}
                        </p>
                      </div>
                    </div>

                    {/* 菜单项 */}
                    <div className="py-1">
                      {[
                        { icon: User, label: '个人资料', desc: '查看和修改个人信息' },
                        { icon: Settings, label: '偏好设置', desc: '主题、语言、通知设置' },
                      ].map(item => {
                        const Icon = item.icon
                        return (
                          <button key={item.label} className="w-full flex items-center gap-3 px-4 py-2.5 hover:bg-[var(--color-bg-hover)] transition-colors text-left group">
                            <div className="w-7 h-7 rounded-md bg-[var(--color-bg-base)] flex items-center justify-center text-[var(--color-text-secondary)] group-hover:text-[var(--color-nav-bg)] transition-colors shrink-0">
                              <Icon size={14} />
                            </div>
                            <div className="flex-1 min-w-0">
                              <p className="text-sm text-[var(--color-text-primary)]">{item.label}</p>
                              <p className="text-xs text-[var(--color-text-tertiary)]">{item.desc}</p>
                            </div>
                          </button>
                        )
                      })}
                    </div>

                    {/* 退出登录 */}
                    <div className="border-t border-[var(--color-border)]">
                      <button
                        onClick={() => {
                          logout()
                          navigate('/login')
                        }}
                        className="w-full flex items-center gap-3 px-4 py-2.5 hover:bg-red-500/10 text-[var(--color-danger)] transition-colors text-left"
                      >
                        <div className="w-7 h-7 rounded-md bg-red-500/10 flex items-center justify-center shrink-0">
                          <LogOut size={14} />
                        </div>
                        <span className="text-sm">退出登录</span>
                      </button>
                    </div>
                  </div>
                )}
              </div>
            </div>
          </div>
        )}
        <div className={`flex-1 ${isEdgeToEdgePage || isStewardPage || isOntologyDetailPage ? 'h-full min-h-0 overflow-hidden' : 'overflow-auto p-6'} ${isOntologyDetailPage ? 'p-6' : ''}`}>
          {children}
        </div>
      </main>
    </div>
  )
}
