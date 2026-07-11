import { useState, useEffect, useRef } from 'react'
import { Link, useNavigate, useLocation } from 'react-router-dom'
import { useAuthStore } from '@/stores/authStore'
import {
  LayoutDashboard, Network, Cpu, Settings, LogOut,
  Database, ChevronLeft, ChevronRight, GitBranch, Table2,
  Sparkles, ChevronDown, Repeat, Bot, PlugZap, Workflow, Compass,
  Inbox, UserCircle, CheckCircle2, Bell, AlertTriangle, User, Clock, Trash2, ClipboardList, Globe, Waypoints, History, KeyRound,
} from 'lucide-react'
import { Modal } from '@/components/ui/Modal'

interface NavItem {
  to: string
  icon: React.ElementType
  label: string
  subItems?: { to: string; icon: React.ElementType; label: string }[]
}

export default function Layout({ children }: { children: React.ReactNode }) {
  const logout = useAuthStore(s => s.logout)
  const navigate = useNavigate()
  const location = useLocation()
  const [collapsed, setCollapsed] = useState(false)
  const [expandedGroup, setExpandedGroup] = useState<string | null>(null)
  const [inboxOpen, setInboxOpen] = useState(false)
  const [userMenuOpen, setUserMenuOpen] = useState(false)
  const [dismissingIds, setDismissingIds] = useState<number[]>([])
  const [detailItem, setDetailItem] = useState<InboxItem | null>(null)
  const inboxRef = useRef<HTMLDivElement>(null)
  const userMenuRef = useRef<HTMLDivElement>(null)

  interface InboxItem {
    id: number
    type: 'approval' | 'alert' | 'notification'
    title: string
    time: string
    submitter: string
    detail: string
  }

  const [inboxItems, setInboxItems] = useState<InboxItem[]>([
    { id: 1, type: 'approval', title: '数据集审批：供应商数据v2.3', time: '10分钟前', submitter: '张三', detail: '提交了供应商数据集 v2.3 版本进行审批，包含 240 条供应商记录，新增字段"信用评级"和"合作年限"。请在24小时内完成审批，以便后续数据流水线使用。' },
    { id: 2, type: 'alert', title: '数据质量告警：采购订单表', time: '15分钟前', submitter: '系统', detail: '数据质量检测发现采购订单表中有 12 条记录存在字段为空的问题，涉及字段："交货日期"(8条)、"付款方式"(4条)。建议尽快处理以避免影响后续数据映射。' },
    { id: 3, type: 'approval', title: '本体映射审批：采购订单实体', time: '30分钟前', submitter: '李四', detail: '提交了采购订单实体的本体映射配置，新增 6 个属性映射，关联了供应商实体和客户实体。请确认映射关系和基数配置是否正确。' },
    { id: 4, type: 'notification', title: 'MySQL连接器同步完成', time: '1小时前', submitter: '系统', detail: 'MySQL数据库连接器同步任务已完成，本次同步共处理 1,234 条记录，更新 156 条，新增 89 条，无异常。' },
    { id: 5, type: 'alert', title: '同步任务失败：客户数据同步', time: '1.5小时前', submitter: '系统', detail: '客户数据同步任务执行失败，错误原因：数据库连接超时。请检查数据库配置是否正确、网络连接是否正常。失败前已处理 456/1200 条记录。' },
    { id: 6, type: 'notification', title: '新用户注册', time: '2小时前', submitter: '系统', detail: '新用户 editor_role 已完成注册，当前角色为默认编辑者，请管理员分配相应的本体访问权限。' },
    { id: 7, type: 'approval', title: 'Curated 数据集审批：客户清单', time: '3小时前', submitter: '王五', detail: '提交了客户清单 Curated 数据集进行审批，经过清洗和转换后共 567 条客户记录，数据质量评分 92 分，请审核后批准发布。' },
    { id: 8, type: 'notification', title: '流水线构建完成', time: '4小时前', submitter: '系统', detail: '数据处理流水线"供应商数据管道"已成功构建并运行完成，输出结果已写入 Curated 数据集。' },
    { id: 9, type: 'alert', title: '存储空间不足警告', time: '5小时前', submitter: '系统', detail: '服务器上传目录使用率已达 85%，剩余空间约 1.5GB。建议清理不必要的临时文件或扩展存储空间。' },
    { id: 10, type: 'approval', title: '提示词模板审批：医疗领域提取', time: '6小时前', submitter: '赵六', detail: '提交了医疗领域文档信息提取的提示词模板，用于从临床文档中提取疾病、症状、药物等实体信息，请审批后发布使用。' },
    { id: 11, type: 'notification', title: '本体质量审查报告已生成', time: '昨天', submitter: '系统', detail: '本体质量审查 Agent 已完成对当前本体的质量检查，发现 3 个潜在问题（2个孤立实体、1个关系基数异常），请查看详细报告。' },
    { id: 12, type: 'notification', title: '模型配置更新完成', time: '昨天', submitter: '管理员', detail: '系统默认大语言模型已从 gpt-3.5-turbo 切换为 gpt-4o，新配置立即生效，后续所有 LLM 提取任务将使用新模型。' },
  ])

  const handleDismiss = (id: number) => {
    setDismissingIds(prev => [...prev, id])
    // 先让滑出+透明度动画完成，再折叠高度
    setTimeout(() => {
      setInboxItems(prev => prev.filter(i => i.id !== id))
      setDismissingIds(prev => prev.filter(did => did !== id))
    }, 500)
  }

  const pendingCount = inboxItems.filter(i => i.type === 'approval' || i.type === 'alert').length
  // 按优先级排序：待审批 > 告警 > 通知
  const sortedItems = [...inboxItems].sort((a, b) => {
    const order: Record<string, number> = { approval: 0, alert: 1, notification: 2 }
    return order[a.type] - order[b.type]
  })
  const alertItems = inboxItems.filter(i => i.type === 'alert')
  const notificationItems = inboxItems.filter(i => i.type === 'notification')
  const approvalItems = inboxItems.filter(i => i.type === 'approval')
  // 按时间排序（假设已按时间从新到旧排列）

  useEffect(() => {
    if (!expandedGroup) return
    const stillActive = location.pathname === expandedGroup || location.pathname.startsWith(expandedGroup + '/')
    if (!stillActive) {
      const timer = window.setTimeout(() => setExpandedGroup(null), 0)
      return () => window.clearTimeout(timer)
    }
  }, [expandedGroup, location.pathname])

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

  const navItems: NavItem[] = [
    { to: '/overview', icon: LayoutDashboard, label: '平台概览' },
    { to: '/explore', icon: Compass, label: '业务探索' },
    { to: '/ontologies', icon: Network, label: '本体管理' },
    { to: '/agent', icon: Bot, label: '智能助手' },
    { to: '/events', icon: ClipboardList, label: '事件登记' },
    { to: '/data', icon: Database, label: '数据通道', subItems: [
      { to: '/data/pipelines', icon: GitBranch, label: '数据流水线' },
      { to: '/data/pipelines/sync-tasks', icon: Repeat, label: '数据任务池' },
      { to: '/data/structured', icon: Table2, label: '数据资产湖' },
    ]},
    { to: '/api-hub', icon: Waypoints, label: '接口代理', subItems: [
      { to: '/api-hub/interfaces', icon: PlugZap, label: '接口管理' },
      { to: '/api-hub/history', icon: History, label: '调用历史' },
      { to: '/api-hub/operations', icon: KeyRound, label: '登录与发布' },
    ]},
    { to: '/models', icon: Cpu, label: '模型配置' },
    { to: '/settings', icon: Settings, label: '系统设置', subItems: [
      { to: '/settings/extraction', icon: Sparkles, label: '规则设置' },
      { to: '/settings/users', icon: Network, label: '用户管理' },
      { to: '/settings/prompts', icon: Sparkles, label: '提示词模板' },
      { to: '/settings/agents', icon: Bot, label: '智能体配置' },
      { to: '/settings/skills', icon: Sparkles, label: '技能中心' },
      { to: '/settings/workflows', icon: Workflow, label: '工作流配置' },
      { to: '/settings/domains', icon: Globe, label: '领域设置' },
      { to: '/settings/open-interfaces', icon: PlugZap, label: '开放接口' },
    ]},
  ]

  const isActive = (to: string) => location.pathname === to || location.pathname.startsWith(to + '/')
  const isGroupActive = (item: NavItem) => isActive(item.to) || (item.subItems?.some(s => isActive(s.to)) ?? false)
  const isEdgeToEdgePage = isActive('/explore') || isActive('/agent') || isActive('/events') || isActive('/api-hub')
  const isStewardPage = isActive('/data/pipelines/steward')
  // 标签栏独立于所有页面，所有页面统一显示（含业务探索、智能助手等全屏页）
  const showTopTabBar = true

  return (
    <div className="flex h-screen bg-[var(--color-bg-base)]" style={{ fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif" }}>
      {/* Sidebar */}
      <aside className={`bg-[var(--color-bg-elevated)] border-r border-[var(--color-border)] flex flex-col transition-all duration-300 ease-out ${collapsed ? 'w-16' : 'w-56'}`}>
        {/* Logo */}
        <div className={`h-14 border-b border-[var(--color-border)] flex items-center gap-3 transition-all ${collapsed ? 'justify-center px-0' : 'px-4'}`}>
          <div className="w-8 h-8 rounded-lg flex items-center justify-center shrink-0" style={{ background: 'var(--color-nav-bg)' }}>
            <Network size={18} className="text-white" />
          </div>
          {!collapsed && <span className="font-semibold text-[var(--color-text-primary)] tracking-tight text-sm">OpenOntology</span>}
        </div>

        {/* Nav */}
        <nav className="flex-1 py-3 px-2 space-y-1.5 overflow-y-auto">
          {navItems.map((item) => {
            const Icon = item.icon
            const groupActive = isGroupActive(item)

            if (item.subItems) {
              return (
                <div key={item.to}>
                  <button onClick={() => {
                    if (collapsed) return
                    if (expandedGroup === item.to) {
                      setExpandedGroup(null)
                    } else {
                      setExpandedGroup(item.to)
                      if (item.subItems && item.subItems.length > 0) {
                        navigate(item.subItems[0].to)
                      }
                    }
                  }}
                    className={`w-full flex items-center gap-3 px-3 py-2 rounded-lg text-sm transition-all ${groupActive
                        ? 'text-white' : 'text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text-primary)]'}`}
                    style={groupActive ? { background: 'var(--color-nav-bg)' } : {}}>
                    <Icon size={18} className="shrink-0" />
                    {!collapsed && <span className="flex-1 text-left font-medium">{item.label}</span>}
                    {!collapsed && <ChevronDown size={14} className={`transition-transform ${expandedGroup === item.to ? 'rotate-180' : ''}`} />}
                  </button>
                  {expandedGroup === item.to && !collapsed && (
                    <div className="ml-4 mt-1.5 space-y-1.5 border-l border-[var(--color-border)] pl-3 anim-fade-in-down">
                      {item.subItems.map(sub => {
                        const SubIcon = sub.icon
                        // 精确匹配优先：当前路径精确命中某兄弟子项时只高亮它，避免父路径被前缀误激活
                        const exact = item.subItems?.find(s => location.pathname === s.to)
                        const subActive = exact ? sub.to === exact.to : isActive(sub.to)
                        return (
                          <Link key={sub.to} to={sub.to}
                            className={`flex items-center gap-2 px-2 py-1.5 rounded-md text-xs transition-colors ${subActive ? 'bg-[var(--color-nav-light)] text-[var(--color-nav-bg)] font-medium' : 'text-[var(--color-text-tertiary)] hover:text-[var(--color-text-primary)] hover:bg-[var(--color-bg-hover)]'}`}>
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
              <Link key={item.to} to={item.to}
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
            className={`flex items-center gap-2 px-4 h-9 text-sm text-[var(--color-text-tertiary)] hover:text-[var(--color-text-primary)] hover:bg-[var(--color-bg-hover)] transition-colors w-full ${collapsed ? 'justify-center' : ''}`}>
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
      <main className="flex-1 flex flex-col overflow-hidden">
        {/* 通用标签栏 */}
        {showTopTabBar && (
          <div className="h-14 shrink-0 flex items-center justify-between border-b border-[var(--color-border)] bg-[var(--color-bg-elevated)] px-4">
            {/* 左侧预留 */}
            <div className="flex items-center gap-2">
              {/* 后续可放置用户空间等功能 */}
            </div>
            
            {/* 右侧：收件箱 + 用户中心 */}
            <div className="flex items-center gap-1">
              {/* 收件箱 */}
              <div className="relative" ref={inboxRef}>
                <button
                  onClick={() => { setInboxOpen(!inboxOpen); setUserMenuOpen(false) }}
                  className={`relative flex items-center justify-center w-10 h-10 rounded-lg transition-colors ${
                    inboxOpen ? 'bg-[var(--color-bg-hover)] text-[var(--color-text-primary)]' : 'text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text-primary)]'
                  }`}
                  title="收件箱"
                >
                  <Inbox size={22} strokeWidth={1.8} />
                  {pendingCount > 0 && (
                    <span className="absolute -top-0.5 -right-0.5 min-w-[18px] h-[18px] bg-[var(--color-danger)] text-white text-[10px] font-semibold rounded-full flex items-center justify-center px-1 shadow-sm">
                      {pendingCount}
                    </span>
                  )}
                </button>

                {/* 收件箱下拉面板 */}
                {inboxOpen && (
                  <div className="absolute right-0 mt-3 w-[380px] bg-[var(--color-bg-elevated)] rounded-lg shadow-lg border border-[var(--color-border)] z-50 overflow-hidden anim-fade-in-down origin-top">
                    {/* 头部：标题与三个状态同一行，用 whitespace-nowrap 防止中文断行 */}
                    <div className="px-4 py-3 border-b border-[var(--color-border)] flex items-center gap-3">
                      <span className="shrink-0 text-sm font-semibold text-[var(--color-text-primary)]">收件箱</span>
                      <span className="inline-flex items-center gap-1 whitespace-nowrap text-xs text-orange-600 dark:text-orange-400">
                        <span className="w-1.5 h-1.5 rounded-full bg-orange-500"></span>
                        待审批 <span className="font-semibold">{approvalItems.length}</span>
                      </span>
                      <span className="inline-flex items-center gap-1 whitespace-nowrap text-xs text-red-600 dark:text-red-400">
                        <span className="w-1.5 h-1.5 rounded-full bg-red-500"></span>
                        告警 <span className="font-semibold">{alertItems.length}</span>
                      </span>
                      <span className="inline-flex items-center gap-1 whitespace-nowrap text-xs text-blue-600 dark:text-blue-400">
                        <span className="w-1.5 h-1.5 rounded-full bg-blue-500"></span>
                        通知 <span className="font-semibold">{notificationItems.length}</span>
                      </span>
                      {notificationItems.length > 0 && (
                        <button
                          onClick={() => setInboxItems(prev => prev.filter(i => i.type !== 'notification'))}
                          className="ml-auto shrink-0 inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-xs text-[var(--color-text-tertiary)] hover:text-[var(--color-danger)] transition-colors"
                        >
                          <Trash2 size={12} /> 清空通知
                        </button>
                      )}
                    </div>

                    {/* 消息列表 */}
                    <div className="max-h-[420px] overflow-y-auto overflow-x-hidden [&::-webkit-scrollbar]:w-1.5 [&::-webkit-scrollbar-thumb]:rounded-full [&::-webkit-scrollbar-thumb]:bg-[var(--color-border)] [&::-webkit-scrollbar-track]:bg-transparent">
                      {sortedItems.length === 0 ? (
                        <div className="py-16 flex flex-col items-center text-center">
                          <div className="w-12 h-12 rounded-full bg-[var(--color-bg-base)] flex items-center justify-center mb-3">
                            <Inbox size={22} className="text-[var(--color-text-tertiary)]" />
                          </div>
                          <p className="text-sm text-[var(--color-text-tertiary)]">暂无待办消息</p>
                        </div>
                      ) : (
                        <div className="divide-y divide-[var(--color-border)]">
                          {sortedItems.map((item) => {
                            const isApproval = item.type === 'approval'
                            const isAlert = item.type === 'alert'
                            const isDismissing = dismissingIds.includes(item.id)
                            return (
                              <div
                                key={item.id}
                                className={`px-4 py-3 flex items-center gap-3 ${isDismissing ? 'pointer-events-none' : ''} hover:bg-[var(--color-bg-hover)] transition-colors`}
                                style={{
                                  transform: isDismissing ? 'translateX(100%)' : 'translateX(0)',
                                  opacity: isDismissing ? 0 : 1,
                                  height: isDismissing ? 0 : 'auto',
                                  paddingTop: isDismissing ? 0 : undefined,
                                  paddingBottom: isDismissing ? 0 : undefined,
                                  transition: 'transform 0.45s cubic-bezier(0.4, 0, 0.2, 1), opacity 0.3s ease-in-out, height 0.3s ease 0.2s, padding 0.3s ease 0.2s',
                                  overflow: 'hidden',
                                }}
                              >
                                {/* 左侧图标 */}
                                <div className={`shrink-0 w-8 h-8 rounded-lg flex items-center justify-center ${
                                  isApproval
                                    ? 'bg-orange-500/15 text-orange-600 dark:text-orange-400'
                                    : isAlert
                                    ? 'bg-red-500/15 text-red-600 dark:text-red-400'
                                    : 'bg-blue-500/15 text-blue-600 dark:text-blue-400'
                                }`}>
                                  {isApproval ? <CheckCircle2 size={16} /> : isAlert ? <AlertTriangle size={16} /> : <Bell size={16} />}
                                </div>

                                {/* 内容 */}
                                <div className="flex-1 min-w-0">
                                  <p className={`text-sm leading-snug font-medium ${
                                    isApproval || isAlert ? 'text-[var(--color-text-primary)]' : 'text-[var(--color-text-secondary)]'
                                  }`}>
                                    {item.title}
                                  </p>
                                  <div className="flex items-center gap-1.5 mt-1 text-xs text-[var(--color-text-tertiary)]">
                                    <span>{item.submitter}</span>
                                    <span className="text-[var(--color-border)]">·</span>
                                    <span>{item.time}</span>
                                  </div>
                                </div>

                                {/* 操作按钮 */}
                                <div className="flex items-center gap-1.5 shrink-0">
                                  <button
                                    onClick={() => handleDismiss(item.id)}
                                    className="px-2.5 py-1 text-xs text-[var(--color-text-primary)] bg-[var(--color-border)] hover:bg-[var(--color-border-hover)] active:scale-95 rounded transition-all duration-150"
                                  >
                                    忽略
                                  </button>
                                  <button
                                    onClick={() => {
                                      setInboxOpen(false)
                                      setTimeout(() => setDetailItem(item), 150)
                                    }}
                                    className="px-2.5 py-1 text-xs text-white bg-[var(--color-nav-bg)] hover:opacity-90 active:scale-95 rounded transition-all duration-150"
                                  >
                                    处理
                                  </button>
                                </div>
                              </div>
                            )
                          })}
                        </div>
                      )}
                    </div>

                    {/* 底部 */}
                    <button className="w-full px-4 py-2.5 text-sm text-[var(--color-primary)] hover:bg-[var(--color-bg-hover)] transition-colors flex items-center justify-center gap-1 border-t border-[var(--color-border)]">
                      查看全部消息 <span className="text-[var(--color-text-tertiary)]">({inboxItems.length})</span>
                    </button>
                  </div>
                )}
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
                      <div className="w-10 h-10 rounded-full bg-gradient-to-br from-indigo-400 to-purple-500 flex items-center justify-center text-white font-semibold shrink-0">
                        A
                      </div>
                      <div className="min-w-0">
                        <p className="font-medium text-[var(--color-text-primary)] text-sm">admin</p>
                        <p className="text-xs text-[var(--color-text-tertiary)] mt-0.5">管理员</p>
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
        <div className={`flex-1 overflow-auto ${isEdgeToEdgePage || isStewardPage ? 'h-full min-h-0' : 'p-6'}`}>
          {children}
        </div>
      </main>
      
      {/* 消息详情弹窗 */}
      <Modal 
        open={!!detailItem} 
        onClose={() => setDetailItem(null)}
        title={detailItem?.title}
        size="md"
        footer={
          <>
            <button 
              onClick={() => setDetailItem(null)}
              className="px-3 py-1.5 text-sm text-[var(--color-text-secondary)] bg-[var(--color-bg-hover)] hover:bg-[var(--color-border)] active:scale-95 rounded-md transition-all duration-150"
            >
              关闭
            </button>
            <button 
              onClick={() => {
                if (detailItem) {
                  handleDismiss(detailItem.id)
                  setDetailItem(null)
                }
              }}
              className="px-3 py-1.5 text-sm text-white bg-[var(--color-nav-bg)] hover:opacity-90 active:scale-95 rounded-md transition-all duration-150"
            >
              确认处理
            </button>
          </>
        }
      >
        {detailItem && (
          <div className="space-y-4">
            {/* 类型标签 */}
            <div className="flex items-center gap-2">
              <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-medium ${
                detailItem.type === 'approval' 
                  ? 'bg-orange-500/15 text-orange-600 dark:text-orange-400' 
                  : detailItem.type === 'alert'
                  ? 'bg-red-500/15 text-red-600 dark:text-red-400'
                  : 'bg-blue-500/15 text-blue-600 dark:text-blue-400'
              }`}>
                {detailItem.type === 'approval' ? <CheckCircle2 size={12} /> : detailItem.type === 'alert' ? <AlertTriangle size={12} /> : <Bell size={12} />}
                {detailItem.type === 'approval' ? '待审批' : detailItem.type === 'alert' ? '告警' : '通知'}
              </span>
            </div>
            
            {/* 详细内容 */}
            <p className="text-sm text-[var(--color-text-secondary)] leading-relaxed">
              {detailItem.detail}
            </p>
            
            {/* 元信息 */}
            <div className="pt-3 border-t border-[var(--color-border)] flex items-center gap-4 text-xs text-[var(--color-text-tertiary)]">
              <span className="flex items-center gap-1">
                <User size={12} />
                {detailItem.submitter}
              </span>
              <span className="flex items-center gap-1">
                <Clock size={12} />
                {detailItem.time}
              </span>
            </div>
          </div>
        )}
      </Modal>
    </div>
  )
}
