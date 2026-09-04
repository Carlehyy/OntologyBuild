/**
 * 记忆宫殿文档预览的 Markdown 渲染（轻量版）。
 *
 * 排版口径与 explore/Md（聊天回答/需求文档预览共用）保持一致：
 * token 化配色、紧凑行距、表格出边框。不引 mermaid/图片缩放等重能力——
 * 宫殿预览的是文档抽取文本，代码块按普通 code 展示即可。
 */
import { useMemo } from 'react'
import ReactMarkdown, { type Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'

const components: Components = {
  p: p => <p className="mb-2.5 text-sm leading-[1.75] last:mb-0" {...p} />,
  strong: p => <strong className="font-semibold text-[var(--color-text-primary)]" {...p} />,
  h1: p => <h2 className="mb-2 mt-4 text-base font-semibold first:mt-0" {...p} />,
  h2: p => <h3 className="mb-1.5 mt-3.5 text-sm font-semibold first:mt-0" {...p} />,
  h3: p => <h4 className="mb-1 mt-2.5 text-sm font-semibold first:mt-0" {...p} />,
  ul: p => <ul className="mb-2.5 list-disc space-y-1 pl-5" {...p} />,
  ol: p => <ol className="mb-2.5 list-decimal space-y-1 pl-5" {...p} />,
  li: p => <li className="text-sm leading-relaxed" {...p} />,
  hr: () => <hr className="my-3 border-[var(--color-border)]" />,
  code: p => <code className="rounded bg-[var(--color-bg-hover)] px-1 py-0.5 font-mono text-[12px]" {...p} />,
  pre: p => (
    <pre
      className="mb-2.5 overflow-x-auto rounded-lg bg-[var(--color-bg-hover)] p-3 font-mono text-[12px] leading-5"
      {...p}
    />
  ),
  table: p => (
    <div className="mb-2.5 overflow-x-auto rounded-lg border border-[var(--color-border)]">
      <table className="w-full border-collapse text-xs" {...p} />
    </div>
  ),
  thead: p => <thead className="bg-[var(--color-bg-base)]" {...p} />,
  th: p => (
    <th
      className="whitespace-nowrap border-b border-[var(--color-border)] px-3 py-1.5 text-left font-medium text-[var(--color-text-secondary)]"
      {...p}
    />
  ),
  td: p => <td className="border-b border-[var(--color-border)] px-3 py-1.5 align-top" {...p} />,
  a: p => <a className="text-[var(--color-primary)] underline-offset-2 hover:underline" {...p} />,
  blockquote: p => (
    <blockquote
      className="mb-2.5 border-l-2 border-[var(--color-border)] pl-3 text-[var(--color-text-secondary)]"
      {...p}
    />
  ),
}

export default function PalaceMarkdown({ text }: { text: string }) {
  const memo = useMemo(() => (
    <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
      {text}
    </ReactMarkdown>
  ), [text])
  return memo
}
