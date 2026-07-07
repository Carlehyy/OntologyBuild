import { useForm } from 'react-hook-form'
import { useNavigate } from 'react-router-dom'
import { useAuthStore } from '@/stores/authStore'
import { authApi } from '@/api/auth'
import { useTranslation } from 'react-i18next'
import { useState, useEffect, useRef } from 'react'
import { Network, Layers, Brain, Zap, Eye, EyeOff, BookOpen, GitBranch } from 'lucide-react'

/* ── 动态背景粒子 ── */
function ParticleBackground() {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    let w = canvas.width = canvas.offsetWidth
    let h = canvas.height = canvas.offsetHeight
    const particles: { x: number; y: number; vx: number; vy: number; r: number }[] = []
    const PARTICLE_COUNT = 40

    for (let i = 0; i < PARTICLE_COUNT; i++) {
      particles.push({
        x: Math.random() * w, y: Math.random() * h,
        vx: (Math.random() - 0.5) * 0.4, vy: (Math.random() - 0.5) * 0.4,
        r: Math.random() * 2 + 1,
      })
    }

    let animId: number
    const draw = () => {
      ctx.clearRect(0, 0, w, h)
      // Draw connections
      for (let i = 0; i < particles.length; i++) {
        for (let j = i + 1; j < particles.length; j++) {
          const dx = particles[i].x - particles[j].x
          const dy = particles[i].y - particles[j].y
          const dist = Math.sqrt(dx * dx + dy * dy)
          if (dist < 150) {
            ctx.strokeStyle = `rgba(100, 149, 237, ${0.15 * (1 - dist / 150)})`
            ctx.lineWidth = 0.8
            ctx.beginPath()
            ctx.moveTo(particles[i].x, particles[i].y)
            ctx.lineTo(particles[j].x, particles[j].y)
            ctx.stroke()
          }
        }
      }
      // Draw particles
      for (const p of particles) {
        p.x += p.vx; p.y += p.vy
        if (p.x < 0 || p.x > w) p.vx *= -1
        if (p.y < 0 || p.y > h) p.vy *= -1
        ctx.fillStyle = 'rgba(100, 149, 237, 0.6)'
        ctx.beginPath()
        ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2)
        ctx.fill()
      }
      animId = requestAnimationFrame(draw)
    }
    draw()
    const handleResize = () => { w = canvas.width = canvas.offsetWidth; h = canvas.height = canvas.offsetHeight }
    window.addEventListener('resize', handleResize)
    return () => { cancelAnimationFrame(animId); window.removeEventListener('resize', handleResize) }
  }, [])
  return <canvas ref={canvasRef} style={{ position: 'absolute', inset: 0, width: '100%', height: '100%' }} />
}

export default function LoginPage() {
  const { register, handleSubmit } = useForm<{ username: string; password: string }>()
  const setAuth = useAuthStore(s => s.setAuth)
  const navigate = useNavigate()
  const { t } = useTranslation()
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [showPassword, setShowPassword] = useState(false)

  const onSubmit = async (data: { username: string; password: string }) => {
    setLoading(true); setError('')
    try {
      const res = await authApi.login(data.username, data.password) as any
      localStorage.setItem('token', res.access_token)
      const profile = await authApi.profile() as any
      setAuth(profile, res.access_token)
      navigate('/')
    } catch (e: any) {
      localStorage.removeItem('token')
      setError(e?.response?.data?.detail || '登录失败，请检查用户名和密码')
    } finally { setLoading(false) }
  }


  const features = [
    { icon: Brain, text: 'LLM驱动的多轮知识抽取', delay: '0.1s' },
    { icon: Network, text: '交互式知识图谱可视化', delay: '0.2s' },
    { icon: Layers, text: '本体建模与版本管理', delay: '0.3s' },
    { icon: GitBranch, text: '规则推理与动作发射', delay: '0.4s' },
  ]

  return (
    <div className="min-h-screen flex" style={{ fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif" }}>
      {/* Left Panel — Brand */}
      <div className="hidden lg:flex lg:w-1/2 relative overflow-hidden" style={{ background: 'linear-gradient(160deg, #0f172a 0%, #1e293b 40%, #0f172a 100%)' }}>
        <ParticleBackground />
        <div className="relative z-10 flex flex-col justify-between p-12 h-full">
          <div className="anim-fade-in-down">
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 rounded-xl flex items-center justify-center" style={{ background: 'rgba(100, 149, 237, 0.2)' }}>
                <Network size={22} className="text-blue-400" />
              </div>
              <span className="text-xl font-semibold tracking-tight text-white">OpenOntology</span>
            </div>
          </div>

          <div className="space-y-6 anim-fade-in-up" style={{ animationDelay: '0.2s' }}>
            <h2 className="text-4xl font-bold text-white leading-tight" style={{ letterSpacing: '-0.02em' }}>
              智能本体建模<br />
              <span style={{ color: '#6495ed' }}>知识图谱平台</span>
            </h2>
            <p className="text-slate-400 text-base leading-relaxed max-w-md">
              从本体建模到规则推理，从知识抽取到智能问答，构建领域知识的完整认知框架。支持多领域复用，零配置快速上手。
            </p>

            <div className="space-y-3 pt-4">
              {features.map((f, i) => (
                <div key={i} className="flex items-center gap-3 anim-fade-in-right" style={{ animationDelay: f.delay }}>
                  <div className="w-8 h-8 rounded-lg flex items-center justify-center" style={{ background: 'rgba(100, 149, 237, 0.1)', border: '1px solid rgba(100, 149, 237, 0.2)' }}>
                    <f.icon size={15} className="text-blue-400" />
                  </div>
                  <span className="text-sm text-slate-300">{f.text}</span>
                </div>
              ))}
            </div>
          </div>

          <p className="text-xs text-slate-600 anim-fade-in" style={{ animationDelay: '0.6s' }}>
            OpenOntology v2.0 · 企业级知识图谱引擎
          </p>
        </div>
      </div>

      {/* Right Panel — Login Form */}
      <div className="flex-1 flex items-center justify-center bg-[var(--color-bg-base)] p-6">
        <div className="w-full max-w-sm anim-scale-in">
          {/* Mobile Logo */}
          <div className="lg:hidden flex items-center gap-2 mb-8">
            <Network size={20} style={{ color: 'var(--color-primary)' }} />
            <span className="font-semibold text-lg">OpenOntology</span>
          </div>

          <div className="mb-8">
            <h1 className="text-2xl font-semibold text-[var(--color-text-primary)] tracking-tight">欢迎回来</h1>
            <p className="text-sm text-[var(--color-text-tertiary)] mt-1">登录您的知识图谱工作空间</p>
          </div>

          <form onSubmit={handleSubmit(onSubmit)} className="space-y-5">
            <div>
              <label className="block text-sm font-medium text-[var(--color-text-primary)] mb-1.5">用户名</label>
              <input {...register('username', { required: true })} placeholder="输入用户名"
                className="w-full h-10 px-3 rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-elevated)] text-sm text-[var(--color-text-primary)] placeholder:text-[var(--color-text-disabled)] focus:outline-none focus:ring-2 focus:ring-[var(--color-primary)] focus:border-[var(--color-primary)] transition-all" />
            </div>

            <div>
              <label className="block text-sm font-medium text-[var(--color-text-primary)] mb-1.5">密码</label>
              <div className="relative">
                <input {...register('password', { required: true })} type={showPassword ? 'text' : 'password'} placeholder="输入密码"
                  className="w-full h-10 px-3 pr-10 rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-elevated)] text-sm text-[var(--color-text-primary)] placeholder:text-[var(--color-text-disabled)] focus:outline-none focus:ring-2 focus:ring-[var(--color-primary)] focus:border-[var(--color-primary)] transition-all" />
                <button type="button" onClick={() => setShowPassword(!showPassword)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-[var(--color-text-tertiary)] hover:text-[var(--color-text-primary)] transition-colors">
                  {showPassword ? <EyeOff size={16} /> : <Eye size={16} />}
                </button>
              </div>
            </div>

            {error && (
              <div className="p-3 rounded-lg bg-red-50 border border-red-100 text-red-600 text-sm anim-fade-in-down">
                {error}
              </div>
            )}

            <button type="submit" disabled={loading}
              className="w-full h-10 rounded-lg font-medium text-sm text-white transition-all btn-press disabled:opacity-50"
              style={{ background: 'var(--color-primary)' }}>
              {loading ? (
                <span className="flex items-center justify-center gap-2">
                  <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24"><circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none"/><path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/></svg>
                  登录中...
                </span>
              ) : '登录'}
            </button>
          </form>
        </div>
      </div>
    </div>
  )
}
