import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import {
  Archive, ArchiveRestore, Clock, History, LayoutDashboard, LogOut,
  Network, Plus, Search, Trash2, X,
} from 'lucide-react'

import type { SuperConversation } from '@/api/superAssistant'
import { Modal } from '@/components/ui/Modal'
import { hasMenuAccess } from '@/config/navigation'
import { useAuthStore } from '@/stores/authStore'
import {
  CONVERSATION_GROUP_SECTIONS,
  groupConversations,
} from '../conversationGroups'

interface WorkbenchSidebarProps {
  conversations: SuperConversation[]
  selectedId: string | null
  mobileOpen: boolean
  onCloseMobile: () => void
  onCreate: () => void | Promise<void>
  onSelect: (id: string) => void
  onDelete: (id: string) => void
  onSetArchived: (id: string, archived: boolean) => void
}

type PlaceholderFeature = 'search' | 'tasks'

const PLACEHOLDER_COPY: Record<PlaceholderFeature, { title: string; body: string }> = {
  search: {
    title: '全局搜索',
    body: '全局搜索功能即将上线：跨会话、本体与平台内容的一站式检索正在规划中，当前版本请先在历史会话列表中查找。',
  },
  tasks: {
    title: '定时任务',
    body: '定时任务功能即将上线：让超级助手按你设定的计划自动执行任务，当前版本请手动发起对话。',
  },
}

function formatSessionTime(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '时间未知'
  return date.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

interface ConversationRowProps {
  item: SuperConversation
  current: boolean
  archived: boolean
  onSelect: (id: string) => void
  onDelete: (id: string) => void
  onSetArchived: (id: string, archived: boolean) => void
}

function ConversationRow({ item, current, archived, onSelect, onDelete, onSetArchived }: ConversationRowProps) {
  const title = item.title.trim() || '未命名会话'
  return (
    <div
      data-workbench-conversation={item.id}
      className={`group flex items-center gap-1 rounded-lg px-2 py-1.5 transition-colors ${current
        ? 'bg-teal-50/70'
        : 'hover:bg-[var(--color-bg-hover)]'}`}
    >
      <button
        type="button"
        onClick={() => onSelect(item.id)}
        title={title}
        className="flex min-w-0 flex-1 items-center gap-2 text-left focus-visible:outline-none"
      >
        <span className={`block min-w-0 flex-1 truncate text-sm ${current
          ? 'font-medium text-teal-900'
          : 'text-[var(--color-text-primary)]'}`}
        >
          {title}
        </span>
      </button>
      <span className="shrink-0 text-[10px] tabular-nums text-[var(--color-text-tertiary)] group-hover:hidden">
        {formatSessionTime(item.updated_at)}
      </span>
      <span className="hidden shrink-0 items-center gap-0.5 group-hover:flex">
        <button
          type="button"
          onClick={() => onSetArchived(item.id, !archived)}
          title={archived ? `恢复会话 ${title}` : `归档会话 ${title}`}
          aria-label={archived ? `恢复会话 ${title}` : `归档会话 ${title}`}
          className="flex h-6 w-6 items-center justify-center rounded-md text-slate-400 transition-colors hover:bg-amber-50 hover:text-amber-600 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-300"
        >
          {archived ? <ArchiveRestore size={13} /> : <Archive size={13} />}
        </button>
        <button
          type="button"
          onClick={() => onDelete(item.id)}
          title={`删除会话 ${title}`}
          aria-label={`删除会话 ${title}`}
          className="flex h-6 w-6 items-center justify-center rounded-md text-slate-400 transition-colors hover:bg-red-50 hover:text-red-600 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-300"
        >
          <Trash2 size={13} />
        </button>
      </span>
    </div>
  )
}

export default function WorkbenchSidebar({
  conversations,
  selectedId,
  mobileOpen,
  onCloseMobile,
  onCreate,
  onSelect,
  onDelete,
  onSetArchived,
}: WorkbenchSidebarProps) {
  const user = useAuthStore(state => state.user)
  const logout = useAuthStore(state => state.logout)
  const navigate = useNavigate()
  const [placeholder, setPlaceholder] = useState<PlaceholderFeature | null>(null)
  const [archivedOpen, setArchivedOpen] = useState(false)

  const groups = groupConversations(conversations)
  const activeCount = groups.today.length + groups.yesterday.length + groups.earlier.length

  const handleSelect = (id: string) => {
    onSelect(id)
    onCloseMobile()
  }

  const actionItemClass = 'flex w-full items-center gap-3 rounded-lg px-3 py-2 text-sm text-[var(--color-text-secondary)] transition-colors hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text-primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-ring)]'

  const content = (
    <>
      {/* 品牌 */}
      <div className="flex h-14 shrink-0 items-center gap-3 border-b border-[var(--color-border)] px-4">
        <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg" style={{ background: 'var(--color-nav-bg)' }}>
          <Network size={18} className="text-white" />
        </div>
        <span className="text-sm font-semibold tracking-tight text-[var(--color-text-primary)]">OpenOntology</span>
        <button
          type="button"
          onClick={onCloseMobile}
          aria-label="关闭工作台导航"
          className="ml-auto flex h-10 w-10 items-center justify-center rounded-lg text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)] md:hidden"
        >
          <X size={17} />
        </button>
      </div>

      {/* 新建任务 */}
      <div className="shrink-0 px-3 pt-3">
        <button
          type="button"
          onClick={() => { void onCreate(); onCloseMobile() }}
          className="flex h-10 w-full items-center justify-center gap-2 rounded-xl text-sm font-medium text-white shadow-sm transition-all hover:opacity-95 active:scale-[0.98] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-ring)]"
          style={{ background: 'var(--color-nav-bg)' }}
        >
          <Plus size={16} /> 新建任务
        </button>
      </div>

      {/* 功能项 */}
      <nav className="shrink-0 space-y-1 px-3 py-3" aria-label="工作台功能">
        <button type="button" onClick={() => setPlaceholder('search')} className={actionItemClass}>
          <Search size={17} className="shrink-0" /> 全局搜索
        </button>
        <button type="button" onClick={() => setPlaceholder('tasks')} className={actionItemClass}>
          <Clock size={17} className="shrink-0" /> 定时任务
        </button>
        {hasMenuAccess(user, 'overview') && (
          <Link to="/overview" onClick={onCloseMobile} className={actionItemClass} data-workbench-governance>
            <LayoutDashboard size={17} className="shrink-0" /> 本体治理
          </Link>
        )}
      </nav>

      {/* 历史会话分组时间线 */}
      <div className="flex min-h-0 flex-1 flex-col border-t border-[var(--color-border)]">
        <div className="flex shrink-0 items-center gap-2 px-4 pb-1 pt-3">
          <History size={13} className="text-[var(--color-text-tertiary)]" />
          <span className="text-xs font-medium text-[var(--color-text-secondary)]">历史会话</span>
          <span className="text-[10px] tabular-nums text-[var(--color-text-tertiary)]">共 {activeCount} 个</span>
        </div>
        <div className="scrollbar-thin min-h-0 flex-1 overflow-y-auto px-2 pb-2">
          {activeCount === 0 && groups.archived.length === 0 && (
            <p className="px-3 py-6 text-center text-xs leading-5 text-[var(--color-text-tertiary)]">
              还没有会话，点击上方「新建任务」开始。
            </p>
          )}
          {CONVERSATION_GROUP_SECTIONS.map(section => {
            const items = groups[section.key]
            if (items.length === 0) return null
            return (
              <div key={section.key} data-workbench-group={section.key} className="pt-2">
                <p className="px-2 pb-1 text-[10px] font-medium uppercase tracking-wide text-[var(--color-text-tertiary)]">
                  {section.label}
                </p>
                <div className="space-y-0.5">
                  {items.map(item => (
                    <ConversationRow
                      key={item.id}
                      item={item}
                      current={item.id === selectedId}
                      archived={false}
                      onSelect={handleSelect}
                      onDelete={onDelete}
                      onSetArchived={onSetArchived}
                    />
                  ))}
                </div>
              </div>
            )
          })}
          {groups.archived.length > 0 && (
            <div className="pt-2" data-workbench-group="archived">
              <button
                type="button"
                onClick={() => setArchivedOpen(value => !value)}
                aria-expanded={archivedOpen}
                className="flex w-full items-center gap-1.5 px-2 pb-1 text-[10px] font-medium uppercase tracking-wide text-[var(--color-text-tertiary)] transition-colors hover:text-[var(--color-text-secondary)] focus-visible:outline-none"
              >
                <Archive size={11} />
                归档会话（{groups.archived.length}）
              </button>
              {archivedOpen && (
                <div className="space-y-0.5">
                  {groups.archived.map(item => (
                    <ConversationRow
                      key={item.id}
                      item={item}
                      current={item.id === selectedId}
                      archived
                      onSelect={handleSelect}
                      onDelete={onDelete}
                      onSetArchived={onSetArchived}
                    />
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      {/* 底部用户区 */}
      <div className="flex h-12 shrink-0 items-center gap-2 border-t border-[var(--color-border)] px-3">
        <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg text-xs font-semibold text-white" style={{ background: 'var(--color-nav-bg)' }}>
          {(user?.username || 'U').slice(0, 1).toUpperCase()}
        </div>
        <span className="min-w-0 flex-1 truncate text-xs text-[var(--color-text-secondary)]">{user?.username || '未知用户'}</span>
        <button
          type="button"
          onClick={() => { logout(); navigate('/login') }}
          className="flex shrink-0 items-center gap-1 rounded-lg px-2 py-1.5 text-xs text-[var(--color-text-tertiary)] transition-colors hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-danger)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-ring)]"
        >
          <LogOut size={13} /> 退出登录
        </button>
      </div>
    </>
  )

  return (
    <>
      {/* 与 Layout 侧栏同款模式：单个 aside，移动端固定抽屉、桌面端静态栏（CSS 切换，避免双份 DOM） */}
      {mobileOpen && (
        <button
          type="button"
          aria-label="关闭工作台导航"
          onClick={onCloseMobile}
          className="fixed inset-0 z-40 bg-black/30 md:hidden"
        />
      )}
      <aside className={`${mobileOpen ? 'translate-x-0' : '-translate-x-full'} fixed inset-y-0 left-0 z-50 flex w-64 shrink-0 flex-col border-r border-[var(--color-border)] bg-[var(--color-bg-elevated)] transition-transform duration-300 md:static md:z-auto md:translate-x-0`}>
        {content}
      </aside>

      <Modal
        open={placeholder !== null}
        onClose={() => setPlaceholder(null)}
        title={placeholder ? PLACEHOLDER_COPY[placeholder].title : undefined}
        size="sm"
      >
        <p className="text-sm leading-6 text-[var(--color-text-secondary)]">
          {placeholder ? PLACEHOLDER_COPY[placeholder].body : ''}
        </p>
      </Modal>
    </>
  )
}
