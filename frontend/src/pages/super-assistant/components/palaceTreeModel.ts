/**
 * 记忆宫殿文件树构建（纯函数，单测见 test/unit/palaceFileTree.test.ts）。
 *
 * 目录是后端一等公民（palace_folders 行，支持空目录），文件行只带归属路径
 * folder_path：先按目录行建树（携带目录 id，供拖拽/重命名），再由文件路径
 * 兜底补齐缺失层级（迁移前的存量目录、并发窗口）。目录排前、文件排后，
 * 各自按名称 zh locale 排序。
 * 节点 id：目录 dir:<path>、文件 file:<id>，供 headless-tree dataLoader 使用。
 */
import type { PalaceFile, PalaceFolder } from '../../../api/superAssistant'

export const PALACE_TREE_ROOT = 'palace-root'

export interface PalaceTreeItemData {
  kind: 'dir' | 'file'
  name: string
  /** 目录归一路径（根哨兵为空串） */
  path?: string
  /** 目录行 id（palace_folders）；仅由文件路径派生的中间目录无行时缺省 */
  dirId?: string
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

/** 目录路径 + 新子目录名 → 子目录归一路径 */
export const joinPalacePath = (dirPath: string, name: string): string =>
  [normalizePalacePath(dirPath), name.trim()].filter(Boolean).join('/')

export function buildPalaceTree(files: PalaceFile[], folders: PalaceFolder[] = []): PalaceTreeModel {
  const items: Record<string, PalaceTreeItemData> = {
    [PALACE_TREE_ROOT]: { kind: 'dir', name: '', path: '' },
  }
  const children: Record<string, string[]> = { [PALACE_TREE_ROOT]: [] }

  const ensureDir = (path: string, dirId?: string): string => {
    if (!path) return PALACE_TREE_ROOT
    const id = palaceDirId(path)
    if (!items[id]) {
      items[id] = { kind: 'dir', name: path.slice(path.lastIndexOf('/') + 1), path, dirId }
      children[id] = []
      const parentPath = path.includes('/') ? path.slice(0, path.lastIndexOf('/')) : ''
      children[ensureDir(parentPath)].push(id)
    } else if (dirId) {
      // 文件路径与目录行都指向该目录：补上行 id
      items[id] = { ...items[id], dirId }
    }
    return id
  }

  // 目录行先行（一等公民，空目录也成立），文件路径随后兜底补缺失层级
  for (const folder of folders) {
    const path = normalizePalacePath(folder.path)
    if (path) ensureDir(path, folder.id)
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
