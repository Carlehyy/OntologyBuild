import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import {
  buildPalaceTree,
  normalizePalacePath,
  PALACE_TREE_ROOT,
  palaceDirId,
  palaceFileId,
  palaceTreeDirIds,
} from '../../pages/super-assistant/components/palaceTreeModel.ts'
import type { PalaceFile } from '../../api/superAssistant'

let seq = 0

function makeFile(partial: Partial<PalaceFile> & { id?: string; filename: string }): PalaceFile {
  seq += 1
  return {
    id: partial.id ?? `f-${seq}`,
    filename: partial.filename,
    path: partial.path ?? '',
    mimeType: partial.mimeType ?? 'text/markdown',
    size: partial.size ?? 10,
    sha256: 'x',
    extractedChars: 0,
    status: partial.status ?? 'built',
    error: null,
    entityCount: 0,
    relationCount: 0,
    editable: partial.editable ?? true,
    isImage: partial.isImage ?? false,
    createdAt: '2026-09-04T00:00:00Z',
    updatedAt: '2026-09-04T00:00:00Z',
  }
}

describe('normalizePalacePath', () => {
  it('容忍反斜杠、空白段与 null/undefined', () => {
    assert.equal(normalizePalacePath('a/b/c'), 'a/b/c')
    assert.equal(normalizePalacePath('a\\b\\c'), 'a/b/c')
    assert.equal(normalizePalacePath(' /a/ /b/ '), 'a/b')
    assert.equal(normalizePalacePath(null), '')
    assert.equal(normalizePalacePath(undefined), '')
  })
})

describe('buildPalaceTree', () => {
  it('空库只有根哨兵', () => {
    const model = buildPalaceTree([])
    assert.deepEqual(Object.keys(model.items), [PALACE_TREE_ROOT])
    assert.deepEqual(model.children[PALACE_TREE_ROOT], [])
  })

  it('根目录文件直接挂在根下；目录排前、文件排后，同组按名称排序', () => {
    const model = buildPalaceTree([
      makeFile({ filename: 'b.md' }),
      makeFile({ filename: 'a.md', path: '' }),
      makeFile({ filename: 'doc.md', path: '资料包' }),
      makeFile({ filename: 'nested.txt', path: '资料包/sub' }),
    ])
    // 目录在前，文件按名称排序：a.md(f-2)、b.md(f-1)
    assert.deepEqual(model.children[PALACE_TREE_ROOT], [
      palaceDirId('资料包'),
      palaceFileId('f-2'),
      palaceFileId('f-1'),
    ])
    assert.deepEqual(model.children[palaceDirId('资料包')], [
      palaceDirId('资料包/sub'),
      palaceFileId('f-3'),
    ])
    assert.equal(model.items[palaceFileId('f-4')].file?.filename, 'nested.txt')
    assert.equal(model.items[palaceDirId('资料包/sub')].name, 'sub')
  })

  it('同名目录跨文件合并；不同目录下同名文件互不冲突', () => {
    const model = buildPalaceTree([
      makeFile({ id: 'fa', filename: '共享.md', path: 'a' }),
      makeFile({ id: 'fb', filename: '共享.md', path: 'b' }),
      makeFile({ id: 'fd', filename: '深.txt', path: 'a/deep' }),
    ])
    assert.deepEqual(model.children[palaceDirId('a')], [
      palaceDirId('a/deep'),
      palaceFileId('fa'),
    ])
    assert.deepEqual(model.children[palaceDirId('b')], [palaceFileId('fb')])
    assert.deepEqual(model.children[palaceDirId('a/deep')], [palaceFileId('fd')])
    // 目录节点只创建一次（a 与 a/deep 各一）
    assert.deepEqual(palaceTreeDirIds(model).sort(), ['dir:a', 'dir:a/deep', 'dir:b'])
  })

  it('path 反斜杠与空白段被规整后再建树', () => {
    const model = buildPalaceTree([
      makeFile({ id: 'fx', filename: 'x.md', path: '\\导入\\ 子目录\\' }),
    ])
    assert.deepEqual(palaceTreeDirIds(model).sort(), ['dir:导入', 'dir:导入/子目录'])
    assert.deepEqual(model.children[palaceDirId('导入/子目录')], [palaceFileId('fx')])
  })
})
