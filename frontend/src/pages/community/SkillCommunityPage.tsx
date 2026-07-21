import { Hammer, Sparkles } from 'lucide-react'


export default function SkillCommunityPage() {
  return (
    <div className="relative flex min-h-[calc(100vh-7rem)] items-center justify-center overflow-hidden rounded-3xl border border-white/80 bg-white/55 p-6 shadow-[0_18px_60px_rgba(15,23,42,0.06)] backdrop-blur-xl">
      <div aria-hidden="true" className="pointer-events-none absolute -left-24 top-10 h-72 w-72 rounded-full bg-teal-200/25 blur-3xl" />
      <div aria-hidden="true" className="pointer-events-none absolute -right-24 bottom-0 h-80 w-80 rounded-full bg-sky-200/30 blur-3xl" />
      <section className="relative w-full max-w-xl text-center" aria-labelledby="skill-community-title">
        <div className="mx-auto flex h-16 w-16 items-center justify-center rounded-2xl border border-teal-100 bg-white/90 text-teal-700 shadow-[0_12px_34px_rgba(13,148,136,0.12)]">
          <Hammer size={26} strokeWidth={1.8} />
        </div>
        <div className="mt-5 inline-flex items-center gap-1.5 rounded-full border border-teal-100 bg-teal-50/80 px-3 py-1 text-xs font-medium text-teal-700">
          <Sparkles size={12} /> 技能社区
        </div>
        <h1 id="skill-community-title" className="mt-4 text-2xl font-semibold tracking-tight text-slate-900 sm:text-3xl">
          此功能正在修缮中，稍等片刻~
        </h1>
        <p className="mx-auto mt-3 max-w-md text-sm leading-6 text-slate-500">
          我们正在打磨技能的发现、安装与管理体验，完成后会在这里与你见面。
        </p>
      </section>
    </div>
  )
}
