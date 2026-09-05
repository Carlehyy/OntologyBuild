import { ArrowLeft, LockKeyhole, LogOut } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { useAuthStore } from '@/stores/authStore'


export function AccessDeniedPage({ returnTo }: { returnTo: string }) {
  const navigate = useNavigate()

  return (
    <main className="flex min-h-full items-center justify-center px-6 py-14" aria-labelledby="access-denied-title">
      <section className="w-full max-w-xl overflow-hidden rounded-2xl border border-border bg-card shadow-[0_18px_60px_rgba(15,23,42,0.08)]">
        <div className="h-1 bg-brand" />
        <div className="px-8 py-10 sm:px-11 sm:py-12">
          <div className="mb-6 flex h-12 w-12 items-center justify-center rounded-xl bg-brand-soft text-brand-ink">
            <LockKeyhole size={22} />
          </div>
          <p className="mb-2 text-xs font-semibold tracking-[0.14em] text-brand-ink">ACCESS RESTRICTED</p>
          <h1 id="access-denied-title" className="text-2xl font-semibold tracking-tight text-foreground">当前页面无法访问</h1>
          <p className="mt-3 max-w-md text-sm leading-6 text-muted-foreground">
            你的角色尚未获得此页面权限。若工作需要，请联系管理员在“用户管理 → 角色权限”中调整可见范围。
          </p>
          <button
            type="button"
            onClick={() => navigate(returnTo, { replace: true })}
            className="mt-8 inline-flex items-center gap-2 rounded-lg bg-brand-deep px-4 py-2.5 text-sm font-medium text-[var(--color-text-inverse)] shadow-sm transition-all hover:bg-brand-deep active:translate-y-px focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
          >
            <ArrowLeft size={15} /> 返回上一级
          </button>
        </div>
      </section>
    </main>
  )
}


export function NoAssignedPagesPage() {
  const logout = useAuthStore(state => state.logout)
  const navigate = useNavigate()

  return (
    <main className="flex min-h-full items-center justify-center px-6 py-14">
      <section className="w-full max-w-lg rounded-2xl border border-border bg-card p-10 text-center shadow-[0_18px_60px_rgba(15,23,42,0.08)]">
        <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-xl bg-muted text-muted-foreground">
          <LockKeyhole size={22} />
        </div>
        <h1 className="mt-5 text-xl font-semibold text-foreground">暂未分配可访问页面</h1>
        <p className="mt-2 text-sm leading-6 text-muted-foreground">请联系管理员为当前角色配置菜单权限，配置后重新登录即可生效。</p>
        <button
          type="button"
          onClick={() => { logout(); navigate('/login', { replace: true }) }}
          className="mt-7 inline-flex items-center gap-2 rounded-lg border border-border bg-card px-4 py-2.5 text-sm font-medium text-foreground transition-colors hover:bg-muted"
        >
          <LogOut size={15} /> 返回登录
        </button>
      </section>
    </main>
  )
}
