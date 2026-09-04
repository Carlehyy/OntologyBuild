import { Children, isValidElement, useMemo } from 'react'
import ReactMarkdown, { type Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import MermaidBlock from '@/components/MermaidBlock'
import ZoomableImage from '@/components/ZoomableImage'

const isMermaidEl = (child: unknown) =>
  isValidElement(child) && String((child.props as any)?.className || '').includes('language-mermaid')

/** Markdown 渲染（与智能助手一致的排版；聊天回答与需求文档预览共用，```mermaid 出图） */
export default function Md({ text }: { text: string }) {
  const components = useMemo<Components>(() => ({
    p: p => <p className="text-sm leading-[1.7] mb-2 last:mb-0" {...p} />,
    strong: p => <strong className="font-semibold text-[var(--color-text-primary)]" {...p} />,
    h1: p => <h2 className="text-base font-semibold mt-4 mb-2" {...p} />,
    h2: p => <h3 className="text-sm font-semibold mt-3 mb-1.5" {...p} />,
    h3: p => <h4 className="text-sm font-semibold mt-2 mb-1" {...p} />,
    ul: p => <ul className="list-disc pl-5 mb-2 space-y-1" {...p} />,
    ol: p => <ol className="list-decimal pl-5 mb-2 space-y-1" {...p} />,
    li: p => <li className="text-sm leading-relaxed" {...p} />,
    code: ({ className, children, ...p }) => {
      if (String(className || '').includes('language-mermaid')) {
        return <MermaidBlock chart={String(children).trim()} />
      }
      return <code className={`px-1 py-0.5 rounded bg-[var(--color-bg-overlay)] text-[12px] font-mono ${className || ''}`} {...p}>{children}</code>
    },
    pre: ({ children, ...p }) => {
      // mermaid 代码块由 MermaidBlock 接管，去掉 pre 包裹
      if (Children.toArray(children).some(isMermaidEl)) return <>{children}</>
      return <pre className="p-3 my-2 rounded-lg bg-[var(--color-bg-overlay)] text-[12px] font-mono overflow-x-auto" {...p}>{children}</pre>
    },
    table: p => (
      <div className="overflow-x-auto my-2 rounded-lg border border-[var(--color-border)]">
        <table className="w-full text-xs border-collapse" {...p} />
      </div>
    ),
    thead: p => <thead className="bg-[var(--color-bg-base)]" {...p} />,
    th: p => <th className="px-3 py-1.5 text-left font-medium text-[var(--color-text-secondary)] border-b border-[var(--color-border)] whitespace-nowrap" {...p} />,
    td: p => <td className="px-3 py-1.5 border-b border-[var(--color-border)]" {...p} />,
    a: p => <a className="text-[var(--color-primary)] underline-offset-2 hover:underline" {...p} />,
    blockquote: p => <blockquote className="border-l-2 border-[var(--color-border)] pl-3 my-2 text-[var(--color-text-secondary)]" {...p} />,
    img: ({ src, alt }) => <ZoomableImage src={src} alt={alt} />,
  }), [])

  return (
    <div className="explore-md">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={components}
      >
        {text}
      </ReactMarkdown>
    </div>
  )
}
