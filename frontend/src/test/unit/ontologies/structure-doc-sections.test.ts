import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import {
  splitMarkdownSections,
  tocSections,
} from '../../../pages/ontologies/detail/tabs/structureDocSections.ts'

describe('splitMarkdownSections', () => {
  it('按标题切分小节并保留标题行原文', () => {
    const md = [
      '# 订单需求',
      '总览段落。',
      '',
      '## 业务对象',
      '订单是核心对象。',
      '',
      '### 属性约束',
      '- 订单号必填',
    ].join('\n')

    const sections = splitMarkdownSections(md)
    assert.deepEqual(sections.map(s => [s.level, s.title]), [
      [1, '订单需求'],
      [2, '业务对象'],
      [3, '属性约束'],
    ])
    assert.ok(sections[0].markdown.startsWith('# 订单需求'))
    assert.ok(sections[2].markdown.includes('- 订单号必填'))
    assert.ok(sections.every(s => s.id.startsWith('structure-doc-section-')))
  })

  it('首标题之前的导语单独成节，且不进目录', () => {
    const md = ['导语一段。', '', '## 正文标题', '正文内容。'].join('\n')
    const sections = splitMarkdownSections(md)
    assert.equal(sections.length, 2)
    assert.equal(sections[0].level, 0)
    assert.equal(sections[0].title, '')
    assert.deepEqual(tocSections(sections).map(s => s.title), ['正文标题'])
  })

  it('代码围栏内的 # 注释行不会被视为标题', () => {
    const md = [
      '## 函数说明',
      '```python',
      '# 这是注释，不是标题',
      'value = 1',
      '```',
      '## 结尾',
      '完成。',
    ].join('\n')

    const sections = splitMarkdownSections(md)
    assert.deepEqual(sections.map(s => s.title), ['函数说明', '结尾'])
    assert.ok(sections[0].markdown.includes('# 这是注释，不是标题'))
  })

  it('围栏开合符号不同种时不互相闭合（~~~ 不关闭 ```）', () => {
    const md = ['## A', '```text', '~~~', '# 仍在围栏内', '```', '## B'].join('\n')
    const sections = splitMarkdownSections(md)
    assert.deepEqual(sections.map(s => s.title), ['A', 'B'])
    assert.ok(sections[0].markdown.includes('# 仍在围栏内'))
  })

  it('无标题文档整体作为单个导语节，目录为空', () => {
    const sections = splitMarkdownSections('只有一段文字。')
    assert.equal(sections.length, 1)
    assert.equal(sections[0].level, 0)
    assert.equal(tocSections(sections).length, 0)
  })

  it('空文档与空输入返回空列表', () => {
    assert.deepEqual(splitMarkdownSections(''), [])
    assert.deepEqual(splitMarkdownSections('   \n  '), [])
  })

  it('标题行尾的 # 修饰与多余空白被去除', () => {
    const sections = splitMarkdownSections('## 闭环标题   ##')
    assert.equal(sections[0].title, '闭环标题')
  })

  it('超过 6 级的 # 不是 Markdown 标题', () => {
    const sections = splitMarkdownSections('####### 不是标题\n## 真标题')
    // 7 个 # 的行回退为导语正文，标题切分只认 真标题。
    assert.equal(sections.length, 2)
    assert.equal(sections[0].level, 0)
    assert.ok(sections[0].markdown.includes('####### 不是标题'))
    assert.equal(sections[1].title, '真标题')
  })
})
