import { useEffect, useId, useRef, useState } from 'react'

/**
 * Mermaid 渲染块 — 动态 import（mermaid 体积大，按需分包），
 * 渲染失败降级为原始代码 + 错误信息，绝不让整个页面崩。
 */
let mermaidPromise: Promise<typeof import('mermaid')['default']> | null = null

function loadMermaid() {
  if (!mermaidPromise) {
    mermaidPromise = import('mermaid').then(m => {
      m.default.initialize({
        startOnLoad: false,
        securityLevel: 'strict',
        theme: 'neutral',
        fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
      })
      return m.default
    })
  }
  return mermaidPromise
}

export default function MermaidBlock({ chart }: { chart: string }) {
  const reactId = useId().replace(/[^a-zA-Z0-9]/g, '')
  const [svg, setSvg] = useState('')
  const [error, setError] = useState('')
  const seq = useRef(0)
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const mySeq = ++seq.current
    setError('')
    if (!chart.trim()) { setSvg(''); return }

    const container = containerRef.current
    if (!container) return

    // 清理上一次渲染残留（避免 mermaid render 在 id 匹配时复用旧节点）
    container.innerHTML = ''

    loadMermaid()
      .then(mermaid => mermaid.render(`mmd-${reactId}-${mySeq}`, chart, container))
      .then(({ svg: out }) => { if (seq.current === mySeq) setSvg(out) })
      .catch(e => {
        const message = String(e?.message || e)
        if (/Failed to fetch dynamically imported module|Importing a module script failed|error loading dynamically imported module/i.test(message)) {
          const reloading = (window as any).__openOntologyReloadForAssets?.()
          if (reloading) return
        }
        if (seq.current === mySeq) setError(message)
      })

    return () => {
      if (container) container.innerHTML = ''
    }
  }, [chart, reactId])

  return (
    <>
      <div ref={containerRef} style={{ display: 'none' }} aria-hidden />
      {error ? (
        <div className="my-2 rounded-lg border border-amber-200 bg-amber-50/60 p-3">
          <div className="text-[11px] text-amber-700 mb-1.5">图表渲染失败（显示源码）：{error.slice(0, 160)}</div>
          <pre className="text-[12px] font-mono overflow-x-auto whitespace-pre-wrap">{chart}</pre>
        </div>
      ) : !svg ? (
        <div className="my-2 text-[11px] text-[var(--color-text-tertiary)]">图表渲染中…</div>
      ) : (
        <div
          className="my-2 rounded-lg border border-[var(--color-border)] bg-white p-3 overflow-x-auto [&_svg]:max-w-full [&_svg]:h-auto"
          dangerouslySetInnerHTML={{ __html: svg }}
        />
      )}
    </>
  )
}
