/**
 * 业务文档弹窗的文档目录模型：把需求文档 Markdown 按标题切分为可跳转的小节。
 *
 * 切分是纯函数且感知代码围栏（fence-aware）：```/~~~ 内部的 `#` 注释行不会
 * 被误认成标题。每节携带自己的 Markdown 原文（含标题行），渲染时整节交给
 * Markdown 组件，节外壳的 DOM id 即目录跳转锚点。
 */

export interface StructureDocSection {
  /** 弹窗内唯一的 DOM 锚点 id。 */
  id: string
  /** 标题级别 1-6；首标题之前的导语小节为 0。 */
  level: number
  /** 标题文本；导语小节为空字符串。 */
  title: string
  /** 本节 Markdown 原文（含标题行）。 */
  markdown: string
}

const FENCE_LINE = /^\s{0,3}(```|~~~)/
const HEADING_LINE = /^(#{1,6})\s+(.+?)\s*#*\s*$/

export function splitMarkdownSections(text: string): StructureDocSection[] {
  const lines = String(text || '').split(/\r?\n/)
  const sections: StructureDocSection[] = []
  let current: { level: number; title: string; lines: string[] } = { level: 0, title: '', lines: [] }
  let fence: string | null = null

  const flush = () => {
    if (current.lines.some(line => line.trim())) {
      sections.push({
        id: `structure-doc-section-${sections.length}`,
        level: current.level,
        title: current.title,
        markdown: current.lines.join('\n').trim(),
      })
    }
  }

  for (const line of lines) {
    const fenceMatch = line.match(FENCE_LINE)
    if (fenceMatch) {
      // 同种围栏符号成对开合，开栏期间的任何行都不视为标题。
      if (!fence) fence = fenceMatch[1]
      else if (fence === fenceMatch[1]) fence = null
      current.lines.push(line)
      continue
    }
    if (!fence) {
      const heading = line.match(HEADING_LINE)
      if (heading) {
        flush()
        current = { level: heading[1].length, title: heading[2].trim(), lines: [line] }
        continue
      }
    }
    current.lines.push(line)
  }
  flush()

  return sections
}

/** 目录只列真实标题小节，导语（level 0）不进目录。 */
export function tocSections(sections: StructureDocSection[]): StructureDocSection[] {
  return sections.filter(section => section.level >= 1)
}
