import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import {
  Check,
  ChevronRight,
  CircleUserRound,
  KeyRound,
  Loader2,
  LockKeyhole,
  Pencil,
  Plus,
  Search,
  ShieldCheck,
  Trash2,
  UserCheck,
  UserRoundCog,
  UsersRound,
  X,
} from 'lucide-react'
import { usersApi } from '@/api/ontologies'
import { CONFIGURABLE_NAV_ITEMS } from '@/config/navigation'
import { useAuthStore } from '@/stores/authStore'
import type { User } from '@/types/auth'


type ManagedRole = 'editor' | 'viewer' | 'custom'
type UserRole = 'admin' | ManagedRole
type ViewMode = 'accounts' | 'permissions'
type StatusFilter = 'all' | 'active' | 'disabled'

interface UserDraft {
  username: string
  email: string
  password: string
  role: UserRole
}

const EMPTY_DRAFT: UserDraft = { username: '', email: '', password: '', role: 'viewer' }
const EMPTY_USERS: User[] = []
const EMPTY_ROLE_PERMISSIONS: { role: ManagedRole; menu_keys: string[] }[] = []
const ROLE_META: Record<UserRole, { label: string; description: string; badge: string }> = {
  admin: { label: '管理员', description: '拥有平台全部菜单与管理权限', badge: 'bg-accent text-[var(--color-text-inverse)]' },
  editor: { label: '编辑者', description: '按角色配置使用业务与数据功能', badge: 'bg-brand-soft text-brand-ink ring-1 ring-inset ring-ring' },
  viewer: { label: '查看者', description: '按角色配置查看已授权页面', badge: 'bg-muted text-muted-foreground ring-1 ring-inset ring-[var(--color-border-hover)]' },
  custom: { label: '自定义', description: '使用管理员单独配置的菜单访问范围', badge: 'bg-viz-cyan-soft text-viz-cyan ring-1 ring-inset ring-viz-cyan' },
}

function errorMessage(error: any, fallback: string) {
  const detail = error?.detail ?? error?.message
  if (typeof detail === 'string' && detail) return detail
  if (detail && typeof detail === 'object' && typeof detail.message === 'string') return detail.message
  return fallback
}

function Dialog({ title, description, children, onClose }: {
  title: string
  description?: string
  children: React.ReactNode
  onClose: () => void
}) {
  return (
    <div className="fixed inset-0 z-[70] flex items-center justify-center bg-accent p-4 backdrop-blur-[2px]" role="presentation" onMouseDown={event => { if (event.target === event.currentTarget) onClose() }}>
      <section role="dialog" aria-modal="true" aria-labelledby="user-dialog-title" className="w-full max-w-lg overflow-hidden rounded-2xl border border-border bg-card shadow-[0_24px_80px_rgba(15,23,42,0.2)]">
        <header className="flex items-start justify-between border-b border-border px-6 py-5">
          <div>
            <h2 id="user-dialog-title" className="text-base font-semibold text-foreground">{title}</h2>
            {description && <p className="mt-1 text-xs leading-5 text-muted-foreground">{description}</p>}
          </div>
          <button type="button" onClick={onClose} aria-label="关闭" className="rounded-lg p-2 text-[var(--color-text-tertiary)] transition-colors hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
            <X size={16} />
          </button>
        </header>
        {children}
      </section>
    </div>
  )
}

export default function UserManagementPanel() {
  const queryClient = useQueryClient()
  const currentUser = useAuthStore(state => state.user)
  const [view, setView] = useState<ViewMode>('accounts')
  const [search, setSearch] = useState('')
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all')
  const [editing, setEditing] = useState<User | null>(null)
  const [creating, setCreating] = useState(false)
  const [draft, setDraft] = useState<UserDraft>(EMPTY_DRAFT)
  const [deleteTarget, setDeleteTarget] = useState<User | null>(null)
  const [selectedRole, setSelectedRole] = useState<ManagedRole>('editor')
  const [permissionDraft, setPermissionDraft] = useState<string[]>([])
  const [notice, setNotice] = useState<{ kind: 'success' | 'error'; text: string } | null>(null)

  const { data: usersData, isLoading: usersLoading, error: usersError } = useQuery({
    queryKey: ['users'],
    queryFn: usersApi.list,
  })
  const { data: rolePermissionsData, isLoading: permissionsLoading } = useQuery({
    queryKey: ['role-menu-permissions'],
    queryFn: usersApi.listRoleMenuPermissions,
  })
  const users = usersData ?? EMPTY_USERS
  const rolePermissions = rolePermissionsData ?? EMPTY_ROLE_PERMISSIONS

  const showNotice = (kind: 'success' | 'error', text: string) => {
    setNotice({ kind, text })
    window.setTimeout(() => setNotice(current => current?.text === text ? null : current), 3200)
  }

  const saveUser = useMutation({
    mutationFn: async () => {
      const username = draft.username.trim()
      const email = draft.email.trim()
      if (!username || !email || (!editing && !draft.password)) throw new Error('请完整填写用户名、邮箱和密码')
      if (draft.password && draft.password.length < 6) throw new Error('密码至少需要 6 个字符')
      if (editing) {
        const payload: Parameters<typeof usersApi.update>[1] = { username, email, role: draft.role }
        if (draft.password) payload.password = draft.password
        return usersApi.update(editing.id, payload)
      }
      return usersApi.create({ username, email, password: draft.password, role: draft.role })
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['users'] })
      showNotice('success', editing ? '用户资料已更新' : '用户已创建')
      closeEditor()
    },
    onError: error => showNotice('error', errorMessage(error, '保存用户失败')),
  })

  const updateStatus = useMutation({
    mutationFn: ({ id, is_active }: { id: string; is_active: boolean }) => usersApi.update(id, { is_active }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['users'] })
      showNotice('success', '账号状态已更新')
    },
    onError: error => showNotice('error', errorMessage(error, '更新账号状态失败')),
  })

  const deleteUser = useMutation({
    mutationFn: (id: string) => usersApi.delete(id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['users'] })
      setDeleteTarget(null)
      showNotice('success', '用户已删除')
    },
    onError: error => showNotice('error', errorMessage(error, '删除用户失败')),
  })

  const savePermissions = useMutation({
    mutationFn: () => usersApi.updateRoleMenuPermissions(selectedRole, permissionDraft),
    onSuccess: result => {
      queryClient.setQueryData(['role-menu-permissions'], (current: typeof rolePermissions | undefined) => {
        const existing = current ?? []
        return existing.some(item => item.role === result.role)
          ? existing.map(item => item.role === result.role ? result : item)
          : [...existing, result]
      })
      showNotice('success', `${ROLE_META[selectedRole].label}的菜单权限已保存`)
    },
    onError: error => showNotice('error', errorMessage(error, '保存角色权限失败')),
  })

  useEffect(() => {
    const current = rolePermissions.find(item => item.role === selectedRole)
    setPermissionDraft(current?.menu_keys ?? [])
  }, [rolePermissions, selectedRole])

  const filteredUsers = useMemo(() => {
    const keyword = search.trim().toLowerCase()
    return users.filter(user => {
      const matchesSearch = !keyword || user.username.toLowerCase().includes(keyword) || user.email.toLowerCase().includes(keyword)
      const matchesStatus = statusFilter === 'all' || (statusFilter === 'active' ? user.is_active : !user.is_active)
      return matchesSearch && matchesStatus
    })
  }, [search, statusFilter, users])

  const stats = {
    total: users.length,
    active: users.filter(user => user.is_active).length,
    admins: users.filter(user => user.role === 'admin' && user.is_active).length,
  }

  const closeEditor = () => {
    setCreating(false)
    setEditing(null)
    setDraft(EMPTY_DRAFT)
  }
  const openCreate = () => {
    setEditing(null)
    setDraft(EMPTY_DRAFT)
    setCreating(true)
  }
  const openEdit = (user: User) => {
    setCreating(false)
    setEditing(user)
    setDraft({ username: user.username, email: user.email, password: '', role: user.role })
  }

  const allPermissionKeys = CONFIGURABLE_NAV_ITEMS.flatMap(item => [item.key, ...(item.subItems?.map(child => child.key) ?? [])])
  const togglePermissionBranch = (key: string, childKeys: string[]) => {
    const branch = [key, ...childKeys]
    const allSelected = branch.every(item => permissionDraft.includes(item))
    setPermissionDraft(current => allSelected
      ? current.filter(item => !branch.includes(item))
      : Array.from(new Set([...current, ...branch])))
  }
  const toggleLeaf = (parentKey: string | null, key: string) => {
    setPermissionDraft(current => {
      if (current.includes(key)) {
        const next = current.filter(item => item !== key)
        if (!parentKey) return next
        const siblings = CONFIGURABLE_NAV_ITEMS.find(item => item.key === parentKey)?.subItems ?? []
        return siblings.some(item => next.includes(item.key))
          ? next
          : next.filter(item => item !== parentKey)
      }
      return Array.from(new Set([...current, key, ...(parentKey ? [parentKey] : [])]))
    })
  }

  return (
    <div className="min-h-full">
      {notice && (
        <div role="status" className={`fixed right-6 top-20 z-[80] flex max-w-sm items-center gap-2 rounded-xl border bg-card px-4 py-3 text-sm shadow-[0_18px_48px_rgba(15,23,42,0.14)] ${notice.kind === 'success' ? 'border-brand-line text-brand-ink' : 'border-[color-mix(in_srgb,var(--color-danger)_35%,transparent)] text-[var(--color-danger)]'}`}>
          {notice.kind === 'success' ? <Check size={15} /> : <X size={15} />}{notice.text}
        </div>
      )}

      <div className="mb-5 flex flex-wrap items-center gap-3 rounded-xl border border-border bg-card px-4 py-3 shadow-sm/50">
        <div className="flex items-center gap-1 rounded-lg border border-border bg-muted p-0.5">
          {([
            { value: 'accounts', label: '用户账号', icon: UsersRound },
            { value: 'permissions', label: '角色权限', icon: KeyRound },
          ] as const).map(tab => {
            const Icon = tab.icon
            return <button key={tab.value} type="button" onClick={() => setView(tab.value)} className={`inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium transition-all ${view === tab.value ? 'bg-brand text-[var(--color-text-inverse)] shadow-sm' : 'text-muted-foreground hover:bg-card hover:text-foreground'}`}><Icon size={13} />{tab.label}</button>
          })}
        </div>

        {view === 'accounts' && <>
          <div className="relative w-full sm:w-64">
            <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-[var(--color-text-tertiary)]" />
            <input value={search} onChange={event => setSearch(event.target.value)} placeholder="搜索用户名或邮箱..." className="w-full rounded-lg border border-border py-1.5 pl-8 pr-3 text-sm text-foreground outline-none transition-all placeholder:text-[var(--color-text-tertiary)] focus:border-brand focus:ring-2 focus:ring-ring" />
          </div>
          <Select value={statusFilter} onValueChange={value => setStatusFilter(value as StatusFilter)}>
            <SelectTrigger className="w-32 cursor-pointer rounded-lg bg-card px-3 py-1.5 text-xs font-medium" aria-label="按账号状态筛选">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">全部状态</SelectItem>
              <SelectItem value="active">已启用</SelectItem>
              <SelectItem value="disabled">已停用</SelectItem>
            </SelectContent>
          </Select>
          <button type="button" onClick={openCreate} className="ml-auto inline-flex items-center gap-1.5 rounded-lg bg-[var(--color-nav-bg)] px-3.5 py-2 text-sm font-medium text-[var(--color-text-inverse)] shadow-sm transition-opacity hover:opacity-90"><Plus size={14} />新增用户</button>
        </>}

        {view === 'permissions' && <div className="ml-auto flex items-center gap-2 text-xs text-muted-foreground"><ShieldCheck size={14} className="text-brand-ink" />管理员始终拥有全部页面权限</div>}
      </div>

      {view === 'accounts' ? (
        <>
          <div className="mb-4 grid grid-cols-1 gap-3 sm:grid-cols-3">
            {[
              { label: '账号总数', value: stats.total, icon: UsersRound, tone: 'bg-muted text-muted-foreground' },
              { label: '启用账号', value: stats.active, icon: UserCheck, tone: 'bg-brand-soft text-brand-ink' },
              { label: '启用管理员', value: stats.admins, icon: ShieldCheck, tone: 'bg-[var(--color-warning-bg)] text-[var(--color-warning)]' },
            ].map(item => { const Icon = item.icon; return <div key={item.label} className="flex items-center gap-3 rounded-xl border border-border bg-card px-4 py-3"><div className={`flex h-9 w-9 items-center justify-center rounded-lg ${item.tone}`}><Icon size={17} /></div><div><p className="text-xs text-muted-foreground">{item.label}</p><p className="mt-0.5 text-lg font-semibold tabular-nums text-foreground">{item.value}</p></div></div> })}
          </div>

          <section className="overflow-hidden rounded-xl border border-border bg-card">
            {usersLoading ? <div className="flex items-center justify-center gap-2 px-6 py-14 text-sm text-[var(--color-text-tertiary)]"><Loader2 size={16} className="animate-spin" />正在加载用户...</div>
              : usersError ? <div className="px-6 py-14 text-center text-sm text-[var(--color-danger)]">{errorMessage(usersError, '用户列表加载失败')}</div>
                : filteredUsers.length === 0 ? <div className="px-6 py-14 text-center"><CircleUserRound size={28} className="mx-auto text-[var(--color-text-tertiary)]" /><p className="mt-3 text-sm font-medium text-muted-foreground">没有匹配的用户</p><p className="mt-1 text-xs text-[var(--color-text-tertiary)]">调整搜索或状态筛选后再试</p></div>
                  : <div className="overflow-x-auto"><table className="w-full min-w-[820px] text-left text-sm"><thead className="border-b border-border bg-muted"><tr>{['用户', '角色', '状态', '创建时间', '操作'].map((heading, index) => <th key={heading} className={`px-5 py-3 text-xs font-medium text-muted-foreground ${index === 4 ? 'text-right' : ''}`}>{heading}</th>)}</tr></thead><tbody className="divide-y border-border">{filteredUsers.map(user => {
                    const role = ROLE_META[user.role]
                    const isSelf = user.id === currentUser?.id
                    return <tr key={user.id} className="transition-colors hover:bg-muted"><td className="px-5 py-3.5"><div className="flex items-center gap-3"><div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-muted text-sm font-semibold text-muted-foreground">{user.username.slice(0, 1).toUpperCase()}</div><div><p className="font-medium text-foreground">{user.username}{isSelf && <span className="ml-2 text-[10px] font-medium text-brand-ink">当前账号</span>}</p><p className="mt-0.5 text-xs text-[var(--color-text-tertiary)]">{user.email}</p></div></div></td><td className="px-5 py-3.5"><span className={`inline-flex rounded-md px-2 py-1 text-[11px] font-medium ${role.badge}`}>{role.label}</span></td><td className="px-5 py-3.5"><button type="button" disabled={isSelf || updateStatus.isPending} onClick={() => updateStatus.mutate({ id: user.id, is_active: !user.is_active })} className={`inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-[11px] font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-60 ${user.is_active ? 'bg-[var(--color-success-bg)] text-[var(--color-success)] hover:bg-[var(--color-success-bg)]' : 'bg-muted text-muted-foreground hover:bg-[var(--color-bg-active)]'}`}><span className={`h-1.5 w-1.5 rounded-full ${user.is_active ? 'bg-[var(--color-success)]' : 'bg-accent'}`} />{user.is_active ? '已启用' : '已停用'}</button></td><td className="px-5 py-3.5 text-xs tabular-nums text-muted-foreground">{new Date(user.created_at).toLocaleDateString('zh-CN')}</td><td className="px-5 py-3.5"><div className="flex justify-end gap-1"><button type="button" onClick={() => openEdit(user)} aria-label={`编辑 ${user.username}`} className="rounded-lg p-2 text-[var(--color-text-tertiary)] transition-colors hover:bg-brand-soft hover:text-brand-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"><Pencil size={14} /></button><button type="button" disabled={isSelf} onClick={() => setDeleteTarget(user)} aria-label={`删除 ${user.username}`} className="rounded-lg p-2 text-[var(--color-text-tertiary)] transition-colors hover:bg-[var(--color-danger-bg)] hover:text-[var(--color-danger)] disabled:cursor-not-allowed disabled:opacity-30"><Trash2 size={14} /></button></div></td></tr>
                  })}</tbody></table></div>}
          </section>
        </>
      ) : (
        <section className="grid gap-4 lg:grid-cols-[260px_minmax(0,1fr)]">
          <aside className="space-y-2 rounded-xl border border-border bg-card p-3">
            <div className="px-2 pb-2 pt-1"><p className="text-xs font-semibold text-foreground">选择可配置角色</p><p className="mt-1 text-[11px] leading-5 text-[var(--color-text-tertiary)]">每位用户仅归属一个角色；菜单权限作用于该角色下的全部用户</p></div>
            {(['editor', 'viewer', 'custom'] as ManagedRole[]).map(role => {
              const meta = ROLE_META[role]
              const count = users.filter(user => user.role === role).length
              return <button key={role} type="button" onClick={() => setSelectedRole(role)} className={`flex w-full items-center gap-3 rounded-lg border p-3 text-left transition-all ${selectedRole === role ? 'border-brand-line bg-brand-soft shadow-sm' : 'border-transparent hover:border-border hover:bg-muted'}`}><div className={`flex h-9 w-9 items-center justify-center rounded-lg ${selectedRole === role ? 'bg-brand text-[var(--color-text-inverse)]' : 'bg-muted text-muted-foreground'}`}><UserRoundCog size={17} /></div><div className="min-w-0 flex-1"><p className="text-sm font-medium text-foreground">{meta.label}</p><p className="mt-0.5 text-[11px] text-[var(--color-text-tertiary)]">{count} 个账号</p></div><ChevronRight size={14} className={selectedRole === role ? 'text-brand-ink' : 'text-[var(--color-text-tertiary)]'} /></button>
            })}
          </aside>

          <div className="overflow-hidden rounded-xl border border-border bg-card">
            <header className="flex flex-wrap items-center justify-between gap-3 border-b border-border px-5 py-4"><div><h2 className="text-sm font-semibold text-foreground">{ROLE_META[selectedRole].label}的菜单范围</h2><p className="mt-1 text-xs text-muted-foreground">勾选一级或二级菜单；内部详情页会继承所属菜单权限。</p></div><div className="flex items-center gap-2"><button type="button" onClick={() => setPermissionDraft([])} className="rounded-lg border border-border px-3 py-2 text-xs font-medium text-muted-foreground hover:bg-muted">清空</button><button type="button" onClick={() => setPermissionDraft(allPermissionKeys)} className="rounded-lg border border-border px-3 py-2 text-xs font-medium text-muted-foreground hover:bg-muted">全选</button><button type="button" disabled={savePermissions.isPending} onClick={() => savePermissions.mutate()} className="inline-flex items-center gap-1.5 rounded-lg bg-brand-deep px-3.5 py-2 text-xs font-medium text-[var(--color-text-inverse)] transition-colors hover:bg-brand-deep disabled:opacity-60">{savePermissions.isPending ? <Loader2 size={13} className="animate-spin" /> : <Check size={13} />}保存权限</button></div></header>
            <div className="p-5">
              <div className="mb-4 flex items-start gap-3 rounded-lg border border-[color-mix(in_srgb,var(--color-warning)_35%,transparent)] bg-[var(--color-warning-bg)] px-4 py-3"><LockKeyhole size={16} className="mt-0.5 shrink-0 text-[var(--color-warning)]" /><div><p className="text-xs font-medium text-[var(--color-warning)]">系统设置不参与分配</p><p className="mt-0.5 text-[11px] leading-5 text-[var(--color-warning)]">用户、智能体和领域设置始终仅管理员可见与访问。</p></div></div>
              {permissionsLoading ? <div className="flex items-center justify-center gap-2 py-14 text-sm text-[var(--color-text-tertiary)]"><Loader2 size={16} className="animate-spin" />正在加载角色权限...</div> : <div className="grid gap-3 xl:grid-cols-2">{CONFIGURABLE_NAV_ITEMS.map(item => {
                const children = item.subItems ?? []
                const branch = [item.key, ...children.map(child => child.key)]
                const checkedCount = branch.filter(key => permissionDraft.includes(key)).length
                const branchChecked = checkedCount === branch.length
                const partiallyChecked = checkedCount > 0 && !branchChecked
                const Icon = item.icon
                return <article key={item.key} className={`rounded-xl border p-4 transition-colors ${checkedCount ? 'border-brand-line bg-brand-soft' : 'border-border bg-card'}`}><button type="button" role="checkbox" aria-checked={partiallyChecked ? 'mixed' : branchChecked} onClick={() => togglePermissionBranch(item.key, children.map(child => child.key))} className="flex w-full items-start gap-3 text-left"><span className={`mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded border ${checkedCount ? 'border-brand bg-brand text-[var(--color-text-inverse)]' : 'border-border bg-card'}`}>{branchChecked ? <Check size={12} strokeWidth={3} /> : partiallyChecked ? <span className="h-0.5 w-2 rounded bg-card" /> : null}</span><span className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-lg ${checkedCount ? 'bg-brand-soft text-brand-ink' : 'bg-muted text-muted-foreground'}`}><Icon size={17} /></span><span className="min-w-0 flex-1"><span className="block text-sm font-medium text-foreground">{item.label}</span><span className="mt-0.5 block text-[11px] leading-5 text-[var(--color-text-tertiary)]">{item.description}</span></span></button>{children.length > 0 && <div className="ml-8 mt-3 space-y-1 border-l border-border pl-4">{children.map(child => { const ChildIcon = child.icon; const checked = permissionDraft.includes(child.key); return <button key={child.key} type="button" role="checkbox" aria-checked={checked} onClick={() => toggleLeaf(item.key, child.key)} className="flex w-full items-center gap-2 rounded-lg px-2 py-2 text-left transition-colors hover:bg-card"><span className={`flex h-4 w-4 shrink-0 items-center justify-center rounded border ${checked ? 'border-brand bg-brand text-[var(--color-text-inverse)]' : 'border-border bg-card'}`}>{checked && <Check size={10} strokeWidth={3} />}</span><ChildIcon size={14} className={checked ? 'text-brand-ink' : 'text-[var(--color-text-tertiary)]'} /><span className={`text-xs ${checked ? 'font-medium text-foreground' : 'text-muted-foreground'}`}>{child.label}</span></button> })}</div>}</article>
              })}</div>}
            </div>
          </div>
        </section>
      )}

      {(creating || editing) && <Dialog title={editing ? '编辑用户' : '新增用户'} description={editing ? '修改账号资料；密码留空时保持原密码。' : '创建后，账号会继承所选角色的菜单权限。'} onClose={closeEditor}>
        <form onSubmit={event => { event.preventDefault(); saveUser.mutate() }} className="space-y-4 px-6 py-5">
          <div className="grid gap-4 sm:grid-cols-2"><label className="text-xs font-medium text-muted-foreground">用户名 *</label><label className="hidden text-xs font-medium text-muted-foreground sm:block">角色 *</label><input autoFocus value={draft.username} onChange={event => setDraft(current => ({ ...current, username: event.target.value }))} className="rounded-lg border border-border px-3 py-2.5 text-sm outline-none focus:border-brand focus:ring-2 focus:ring-ring" placeholder="例如：zhangsan" /><div><label className="mb-1.5 block text-xs font-medium text-muted-foreground sm:hidden">角色 *</label><Select value={draft.role} disabled={editing?.id === currentUser?.id} onValueChange={value => setDraft(current => ({ ...current, role: value as UserRole }))}><SelectTrigger className="w-full rounded-lg bg-card px-3 py-2.5 text-sm"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="viewer">查看者</SelectItem><SelectItem value="editor">编辑者</SelectItem><SelectItem value="custom">自定义</SelectItem><SelectItem value="admin">管理员</SelectItem></SelectContent></Select></div></div>
          <label className="block"><span className="mb-1.5 block text-xs font-medium text-muted-foreground">邮箱 *</span><input type="email" value={draft.email} onChange={event => setDraft(current => ({ ...current, email: event.target.value }))} className="w-full rounded-lg border border-border px-3 py-2.5 text-sm outline-none focus:border-brand focus:ring-2 focus:ring-ring" placeholder="name@company.com" /></label>
          <label className="block"><span className="mb-1.5 block text-xs font-medium text-muted-foreground">{editing ? '新密码' : '初始密码 *'}</span><input type="password" value={draft.password} onChange={event => setDraft(current => ({ ...current, password: event.target.value }))} className="w-full rounded-lg border border-border px-3 py-2.5 text-sm outline-none focus:border-brand focus:ring-2 focus:ring-ring" placeholder={editing ? '留空表示不修改' : '至少 6 个字符'} /></label>
          <div className="rounded-lg bg-muted px-3.5 py-3 text-xs leading-5 text-muted-foreground"><span className="font-medium text-foreground">{ROLE_META[draft.role].label}：</span>{ROLE_META[draft.role].description}</div>
          <footer className="flex justify-end gap-2 border-t border-border pt-4"><button type="button" onClick={closeEditor} className="rounded-lg border border-border px-4 py-2 text-sm font-medium text-muted-foreground hover:bg-muted">取消</button><button type="submit" disabled={saveUser.isPending} className="inline-flex items-center gap-1.5 rounded-lg bg-brand-deep px-4 py-2 text-sm font-medium text-[var(--color-text-inverse)] hover:bg-brand-deep disabled:opacity-60">{saveUser.isPending && <Loader2 size={14} className="animate-spin" />}{editing ? '保存修改' : '创建用户'}</button></footer>
        </form>
      </Dialog>}

      {deleteTarget && <Dialog title="删除用户" description="此操作会立即撤销该账号的登录与访问能力。" onClose={() => setDeleteTarget(null)}><div className="px-6 py-5"><p className="text-sm leading-6 text-muted-foreground">确认删除用户 <span className="font-semibold text-foreground">{deleteTarget.username}</span>？该操作不可恢复。</p><footer className="mt-6 flex justify-end gap-2"><button type="button" onClick={() => setDeleteTarget(null)} className="rounded-lg border border-border px-4 py-2 text-sm font-medium text-muted-foreground hover:bg-muted">取消</button><button type="button" disabled={deleteUser.isPending} onClick={() => deleteUser.mutate(deleteTarget.id)} className="inline-flex items-center gap-1.5 rounded-lg bg-[var(--color-danger)] px-4 py-2 text-sm font-medium text-[var(--color-text-inverse)] hover:bg-[var(--color-danger)] disabled:opacity-60">{deleteUser.isPending && <Loader2 size={14} className="animate-spin" />}确认删除</button></footer></div></Dialog>}
    </div>
  )
}
