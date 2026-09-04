/**
 * 记忆宫殿文件树构建（纯函数，单测见 test/unit/palaceFileTree.test.ts）。
 *
 * 由 PalaceFile.path（"/" 分隔，根目录空串）推导目录层级：目录排前、文件排后，
 * 各自按名称 zh locale 排序；不同来源的同路径目录自然合并（如两次同名 ZIP）。
 * 节点 id：目录 dir:<path>、文件 file:<id>，供 headless-tree dataLoader 使用。
 */
import type { PalaceFile } from '../../../api/superAssistant'

export const PALACE_TREE_ROOT = 'palace-root'

export interface PalaceTreeItemData {
  kind: 'dir' | 'file'
  name: string
  file?: PalaceFile
}

export interface PalaceTreeModel {
  /** 节点 id → 数据（含 PALACE_TREE_ROOT 根哨兵） */
  items: Record<string, PalaceTreeItemData>
  /** 节点 id → 有序子节点 id */
  children: Record<string, string[]>
}

export const palaceDirId = (path: string): string => `dir:${path}`
export const palaceFileId = (fileId: string): string => `file:${fileId}`

const collator = new Intl.Collator('zh', { numeric: true, sensitivity: 'base' })

/** 规整 path：斜杠统一、去空段与首尾空白，容忍后端缺省/异常值 */
export const normalizePalacePath = (path: string | null | undefined): string =>
  String(path ?? '')
    .replace(/\\/g, '/')
    .split('/')
    .map(segment => segment.trim())
    .filter(Boolean)
    .join('/')

export function buildPalaceTree(files: PalaceFile[]): PalaceTreeModel {
  const items: Record<string, PalaceTreeItemData> = {
    [PALACE_TREE_ROOT]: { kind: 'dir', name: '' },
  }
  const children: Record<string, string[]> = { [PALACE_TREE_ROOT]: [] }

  const ensureDir = (path: string): string => {
    if (!path) return PALACE_TREE_ROOT
    const id = palaceDirId(path)
    if (!items[id]) {
      items[id] = { kind: 'dir', name: path.slice(path.lastIndexOf('/') + 1) }
      children[id] = []
      const parentPath = path.includes('/') ? path.slice(0, path.lastIndexOf('/')) : ''
      children[ensureDir(parentPath)].push(id)
    }
    return id
  }

  for (const file of files) {
    const dirId = ensureDir(normalizePalacePath(file.path))
    const id = palaceFileId(file.id)
    items[id] = { kind: 'file', name: file.filename, file }
    children[dirId].push(id)
  }

  // 深度优先排序：目录在前、文件在后，同组按名称排序
  const byName = (a: string, b: string) => collator.compare(items[a].name, items[b].name)
  const walk = (id: string) => {
    const list = children[id] ?? []
    const dirs = list.filter(child => items[child]?.kind === 'dir').sort(byName)
    const files = list.filter(child => items[child]?.kind === 'file').sort(byName)
    children[id] = [...dirs, ...files]
    for (const dir of dirs) walk(dir)
  }
  walk(PALACE_TREE_ROOT)

  return { items, children }
}

/** 全部目录节点 id（不含根哨兵），用于初始展开与新增目录检测 */
export function palaceTreeDirIds(model: PalaceTreeModel): string[] {
  return Object.keys(model.items).filter(
    id => id !== PALACE_TREE_ROOT && model.items[id].kind === 'dir',
  )
}
