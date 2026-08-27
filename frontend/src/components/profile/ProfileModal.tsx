import { useEffect, useRef, useState } from 'react'
import {
  Braces,
  CircleUserRound,
  KeyRound,
  Loader2,
  LockKeyhole,
  Plus,
  Trash2,
} from 'lucide-react'

import { Button } from '@/components/ui/Button'
import { Modal } from '@/components/ui/Modal'
import { authApi, type UserEnvVar } from '@/api/auth'
import { useAuthStore } from '@/stores/authStore'
/**
 * 个人资料弹窗（用户头像下拉 → 个人资料，MYW-56）。
 *
 * 左侧竖向 tab 导航（验收反馈：按分区组织内容与允许的操作）：
 * - 「账号信息」：用户名（唯一标识，只读）、邮箱自助修改（成功后同步
 *   auth-store）、修改密码（走既有 PUT /auth/password，需验证当前密码）；
 * - 「环境变量」：用户私有环境变量，key/value 均为字符串，全量保存，
 *   服务端加密落库；本期仅做个人配置的保存与维护，不注入任何执行链路。
 */

const ENV_VAR_KEY_PATTERN = /^[A-Za-z0-9_.-]+$/
const ENV_VAR_MAX_ITEMS = 50
const ENV_VAR_VALUE_MAX_LENGTH = 4096

type ProfileTab = 'account' | 'env'

const PROFILE_TABS: Array<{ key: ProfileTab; label: string; icon: typeof CircleUserRound }> = [
  { key: 'account', label: '账号信息', icon: CircleUserRound },
  { key: 'env', label: '环境变量', icon: Braces },
]

type Notice = { kind: 'success' | 'error'; text: string }

function errorMessage(error: any, fallback: string) {
  const detail = error?.detail ?? error?.message
  if (typeof detail === 'string' && detail) return detail
  if (detail && typeof detail === 'object' && typeof detail.message === 'string') return detail.message
  return fallback
}

const inputClass =
  'w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-base)] px-3 py-2 text-sm text-[var(--color-text-primary)] outline-none transition-colors placeholder:text-[var(--color-text-tertiary)] focus:border-[var(--color-primary)] disabled:cursor-not-allowed disabled:bg-[var(--color-muted)] disabled:text-[var(--color-text-secondary)]'

export default function ProfileModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const user = useAuthStore(s => s.user)
  const token = useAuthStore(s => s.token)
  const setAuth = useAuthStore(s => s.setAuth)

  const [activeTab, setActiveTab] = useState<ProfileTab>('account')
  const [emailDraft, setEmailDraft] = useState('')
  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [envVars, setEnvVars] = useState<UserEnvVar[]>([])
  const [envLoading, setEnvLoading] = useState(false)
  const [savingProfile, setSavingProfile] = useState(false)
  const [savingPassword, setSavingPassword] = useState(false)
  const [savingEnvVars, setSavingEnvVars] = useState(false)
  const [notice, setNotice] = useState<Notice | null>(null)
  const tabRefs = useRef<Array<HTMLButtonElement | null>>([])

  const busy = savingProfile || savingPassword || savingEnvVars

  // 仅在弹窗打开时初始化/加载。刻意不把 user 放进依赖：保存邮箱会更新
  // auth-store 里的 user，若依赖它，成功提示会被这次重置立即吞掉。
  useEffect(() => {
    if (!open) return
    setActiveTab('account')
    const currentUser = useAuthStore.getState().user
    setEmailDraft(currentUser?.email ?? '')
    setCurrentPassword('')
    setNewPassword('')
    setNotice(null)
    let cancelled = false
    setEnvLoading(true)
    authApi.listEnvVars()
      .then(items => { if (!cancelled) setEnvVars(Array.isArray(items) ? items : []) })
      .catch(error => {
        if (!cancelled) setNotice({ kind: 'error', text: errorMessage(error, '环境变量加载失败') })
      })
      .finally(() => { if (!cancelled) setEnvLoading(false) })
    return () => { cancelled = true }
  }, [open])

  const showToast = (kind: Notice['kind'], text: string) => {
    setNotice({ kind, text })
    window.setTimeout(() => setNotice(current => current?.text === text ? null : current), 3200)
  }

  // 竖向 tab 的方向键导航：↑/↓ 在分区之间移动焦点并切换
  const onTablistKeyDown = (event: React.KeyboardEvent) => {
    if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return
    event.preventDefault()
    const index = PROFILE_TABS.findIndex(tab => tab.key === activeTab)
    const next = (index + (event.key === 'ArrowDown' ? 1 : -1) + PROFILE_TABS.length) % PROFILE_TABS.length
    const nextTab = PROFILE_TABS[next]
    setActiveTab(nextTab.key)
    tabRefs.current[next]?.focus()
  }

  const saveProfile = async () => {
    const email = emailDraft.trim()
    if (!email) { showToast('error', '请填写邮箱'); return }
    setSavingProfile(true)
    try {
      const updated = await authApi.updateProfile(email)
      if (token && updated) setAuth(updated, token)
      showToast('success', '资料已更新')
    } catch (error) {
      showToast('error', errorMessage(error, '保存资料失败'))
    } finally {
      setSavingProfile(false)
    }
  }

  const savePassword = async () => {
    if (!currentPassword) { showToast('error', '请输入当前密码'); return }
    if (newPassword.length < 6) { showToast('error', '新密码至少需要 6 个字符'); return }
    setSavingPassword(true)
    try {
      await authApi.changePassword(currentPassword, newPassword)
      setCurrentPassword('')
      setNewPassword('')
      showToast('success', '密码已更新')
    } catch (error) {
      showToast('error', errorMessage(error, '修改密码失败'))
    } finally {
      setSavingPassword(false)
    }
  }

  const validateEnvVars = (): UserEnvVar[] | null => {
    const seen = new Set<string>()
    for (const item of envVars) {
      const key = item.key.trim()
      if (!key) { showToast('error', '环境变量名不能为空'); return null }
      if (!ENV_VAR_KEY_PATTERN.test(key)) {
        showToast('error', '变量名仅允许字母、数字、下划线、连字符和点')
        return null
      }
      if (seen.has(key)) { showToast('error', `环境变量名重复：${key}`); return null }
      if (item.value.length > ENV_VAR_VALUE_MAX_LENGTH) {
        showToast('error', `变量 ${key} 的值超过 4096 字符上限`)
        return null
      }
      seen.add(key)
    }
    return envVars.map(item => ({ key: item.key.trim(), value: item.value }))
  }

  const saveEnvVars = async () => {
    const payload = validateEnvVars()
    if (!payload) return
    setSavingEnvVars(true)
    try {
      const saved = await authApi.saveEnvVars(payload)
      setEnvVars(Array.isArray(saved) ? saved : payload)
      showToast('success', '环境变量已保存')
    } catch (error) {
      showToast('error', errorMessage(error, '保存环境变量失败'))
    } finally {
      setSavingEnvVars(false)
    }
  }

  const updateEnvVar = (index: number, patch: Partial<UserEnvVar>) => {
    setEnvVars(current => current.map((item, i) => i === index ? { ...item, ...patch } : item))
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="个人资料"
      description="维护账号信息、登录密码与你的私有环境变量"
      size="xl"
      headerIcon={<CircleUserRound size={19} />}
      disableClose={busy}
    >
      {notice && (
        <div role="status" aria-live="polite" className={`mb-4 flex items-center gap-2 rounded-lg border px-3 py-2 text-xs ${
          notice.kind === 'success'
            ? 'border-[var(--color-success)]/30 bg-[var(--color-success)]/10 text-[var(--color-success)]'
            : 'border-[var(--color-danger)]/30 bg-[var(--color-danger)]/10 text-[var(--color-danger)]'
        }`}>
          {notice.text}
        </div>
      )}

      {/* 双栏区域固定最小高度：两个 tab 内容量差异大（账号信息约为环境变量
          的三倍高），不固定高度时切换 tab 弹窗会忽高忽低。min-h 覆盖账号
          信息面板的完整内容，选中环境变量时同样保持这一高度；内容超出时
          仍随内容增高并由 Modal 整体限高滚动。 */}
      <div className="grid min-h-[30rem] grid-cols-[128px_minmax(0,1fr)] items-start gap-5">
        {/* 左侧竖向 tab 导航 */}
        <nav
          role="tablist"
          aria-label="个人资料分区"
          aria-orientation="vertical"
          className="flex flex-col gap-1 border-r border-[var(--color-border)] pr-3"
          onKeyDown={onTablistKeyDown}
        >
          {PROFILE_TABS.map((tab, index) => {
            const Icon = tab.icon
            const active = activeTab === tab.key
            return (
              <button
                key={tab.key}
                ref={node => { tabRefs.current[index] = node }}
                type="button"
                role="tab"
                id={`profile-tab-${tab.key}`}
                aria-selected={active}
                aria-controls={`profile-panel-${tab.key}`}
                tabIndex={active ? 0 : -1}
                onClick={() => setActiveTab(tab.key)}
                className={`flex items-center gap-2 rounded-lg px-3 py-2 text-left text-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-ring)] ${
                  active
                    ? 'bg-[var(--color-nav-light)] font-medium text-[var(--color-nav-bg)]'
                    : 'text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text-primary)]'
                }`}
              >
                <Icon size={15} className="shrink-0" aria-hidden="true" />
                {tab.label}
              </button>
            )
          })}
        </nav>

        {/* 右侧分区内容 */}
        {activeTab === 'account' && (
          <div role="tabpanel" id="profile-panel-account" aria-labelledby="profile-tab-account">
            {/* 账号信息 */}
            <section aria-label="账号信息">
              <p className="text-xs text-[var(--color-text-tertiary)]">
                用户名是区分不同用户的唯一标识，不支持修改
              </p>
              <div className="mt-3 space-y-3">
                <label className="block">
                  <span className="mb-1.5 flex items-center gap-1 text-xs font-medium text-[var(--color-text-secondary)]">
                    用户名<LockKeyhole size={11} className="text-[var(--color-text-tertiary)]" aria-hidden="true" />
                  </span>
                  <input value={user?.username ?? ''} readOnly disabled aria-readonly className={`${inputClass} font-mono`} />
                </label>
                <label className="block">
                  <span className="mb-1.5 block text-xs font-medium text-[var(--color-text-secondary)]">邮箱</span>
                  <input
                    type="email"
                    value={emailDraft}
                    onChange={event => setEmailDraft(event.target.value)}
                    className={inputClass}
                    placeholder="name@company.com"
                  />
                </label>
                <div className="flex justify-end">
                  <Button size="sm" loading={savingProfile} onClick={() => void saveProfile()}>保存资料</Button>
                </div>
              </div>
            </section>

            {/* 修改密码 */}
            <section aria-label="修改密码" className="mt-5 border-t border-[var(--color-border)] pt-4">
              <h4 className="flex items-center gap-1.5 text-sm font-medium text-[var(--color-text-primary)]">
                <KeyRound size={14} />修改密码
              </h4>
              <p className="mt-0.5 text-xs text-[var(--color-text-tertiary)]">修改后请使用新密码重新登录</p>
              <div className="mt-3 space-y-3">
                <label className="block">
                  <span className="mb-1.5 block text-xs font-medium text-[var(--color-text-secondary)]">当前密码</span>
                  <input
                    type="password"
                    value={currentPassword}
                    onChange={event => setCurrentPassword(event.target.value)}
                    autoComplete="current-password"
                    className={inputClass}
                  />
                </label>
                <label className="block">
                  <span className="mb-1.5 block text-xs font-medium text-[var(--color-text-secondary)]">新密码</span>
                  <input
                    type="password"
                    value={newPassword}
                    onChange={event => setNewPassword(event.target.value)}
                    autoComplete="new-password"
                    placeholder="至少 6 个字符"
                    className={inputClass}
                  />
                </label>
                <div className="flex justify-end">
                  <Button size="sm" loading={savingPassword} onClick={() => void savePassword()}>更新密码</Button>
                </div>
              </div>
            </section>
          </div>
        )}

        {activeTab === 'env' && (
          <div role="tabpanel" id="profile-panel-env" aria-labelledby="profile-tab-env">
            {/* 私有环境变量 */}
            <section aria-label="私有环境变量">
              <p className="text-xs text-[var(--color-text-tertiary)]">
                仅本人可见，值加密存储；本期仅保存配置，暂不注入任何执行链路（最多 {ENV_VAR_MAX_ITEMS} 条）
              </p>
              <div className="mt-3 space-y-2">
                {envLoading ? (
                  <div className="flex items-center gap-2 px-1 py-3 text-xs text-[var(--color-text-tertiary)]">
                    <Loader2 size={14} className="animate-spin" />正在加载环境变量...
                  </div>
                ) : envVars.length === 0 ? (
                  <p className="px-1 py-3 text-xs text-[var(--color-text-tertiary)]">暂无环境变量，点击下方按钮添加</p>
                ) : (
                  <ul className="space-y-2">
                    {envVars.map((item, index) => (
                      <li key={index} className="grid grid-cols-[minmax(0,8rem)_minmax(0,1fr)_auto] items-center gap-2">
                        <input
                          value={item.key}
                          onChange={event => updateEnvVar(index, { key: event.target.value })}
                          aria-label={`第 ${index + 1} 个变量名`}
                          placeholder="变量名，如 API_KEY"
                          className={`${inputClass} font-mono`}
                        />
                        <input
                          value={item.value}
                          onChange={event => updateEnvVar(index, { value: event.target.value })}
                          aria-label={`第 ${index + 1} 个变量值`}
                          placeholder="值（可留空）"
                          className={`${inputClass} font-mono`}
                        />
                        <Button
                          variant="ghost"
                          size="icon-sm"
                          aria-label={`删除变量 ${item.key.trim() || index + 1}`}
                          onClick={() => setEnvVars(current => current.filter((_, i) => i !== index))}
                        >
                          <Trash2 size={14} />
                        </Button>
                      </li>
                    ))}
                  </ul>
                )}
                <div className="flex justify-between">
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={envLoading || envVars.length >= ENV_VAR_MAX_ITEMS}
                    onClick={() => setEnvVars(current => [...current, { key: '', value: '' }])}
                  >
                    <Plus size={13} />添加变量
                  </Button>
                  <Button size="sm" variant="success" loading={savingEnvVars} onClick={() => void saveEnvVars()}>
                    保存变量
                  </Button>
                </div>
              </div>
            </section>
          </div>
        )}
      </div>
    </Modal>
  )
}
