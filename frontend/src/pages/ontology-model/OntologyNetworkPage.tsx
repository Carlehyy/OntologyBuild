import { Share2, Sparkles } from 'lucide-react'


export default function OntologyNetworkPage() {
  return (
    <div className="relative flex min-h-[calc(100vh-7rem)] items-center justify-center overflow-hidden rounded-3xl border border-white/80 bg-white/55 p-6 shadow-[0_18px_60px_rgba(15,23,42,0.06)] backdrop-blur-xl">
      <div aria-hidden="true" className="pointer-events-none absolute -left-24 top-10 h-72 w-72 rounded-full bg-violet-200/25 blur-3xl" />
      <div aria-hidden="true" className="pointer-events-none absolute -right-24 bottom-0 h-80 w-80 rounded-full bg-indigo-200/30 blur-3xl" />
      <section className="relative w-full max-w-xl text-center" aria-labelledby="ontology-network-title">
        <div className="mx-auto flex h-16 w-16 items-center justify-center rounded-2xl border border-indigo-100 bg-white/90 text-indigo-600 shadow-[0_12px_34px_rgba(79,70,229,0.12)]">
          <Share2 size={26} strokeWidth={1.8} />
        </div>
        <div className="mt-5 inline-flex items-center gap-1.5 rounded-full border border-indigo-100 bg-indigo-50/80 px-3 py-1 text-xs font-medium text-indigo-700">
          <Sparkles size={12} /> 本体网络
        </div>
        <h1 id="ontology-network-title" className="mt-4 text-2xl font-semibold tracking-tight text-slate-900 sm:text-3xl">
          此功能正在建设中，敬请期待~
        </h1>
        <p className="mx-auto mt-3 max-w-md text-sm leading-6 text-slate-500">
          我们将在这里呈现跨本体的关联网络与全局本体图景，建设完成后会在这里与你见面。
        </p>
      </section>
    </div>
  )
}
