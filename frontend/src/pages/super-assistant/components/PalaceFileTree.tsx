/**
 * 记忆宫殿左侧文件树（ReUI Tree + @headless-tree）。
 *
 * 数据由父组件的 PalaceFile[] 全量传入，本地经 buildPalaceTree 折叠成目录树；
 * 文件刷新（上传/轮询）时 rebuildTree 并自动展开“新出现”的目录（如刚导入的
 * ZIP 顶层目录），既有目录的展开状态保持用户上次选择。单击/回车选中文件上抛。
 */
import { useEffect, useLayoutEffect, useMemo, useRef } from 'react'
import { hotkeysCoreFeature, selectionFeature, syncDataLoaderFeature } from '@headless-tree/core'
import type { ItemInstance } from '@headless-tree/core'
import { useTree } from '@headless-tree/react'
import { AlertCircle, FileText, Folder, FolderOpen, Image as ImageIcon, Loader2 } from 'lucide-react'

import type { PalaceFile } from '@/api/superAssistant'
import { Tree, TreeItem, TreeItemLabel } from '@/components/ui/tree'
import {
  buildPalaceTree,
  PALACE_TREE_ROOT,
  palaceFileId,
  palaceTreeDirIds,
  type PalaceTreeItemData,
} from './palaceTreeModel'

interface PalaceFileTreeProps {
  files: PalaceFile[]
  selectedFileId: string | null
  onSelectFile: (file: PalaceFile) => void
}

const FILE_ICON_SIZE = 14

export default function PalaceFileTree({ files, selectedFileId, onSelectFile }: PalaceFileTreeProps) {
  const model = useMemo(() => buildPalaceTree(files), [files])
  const modelRef = useRef(model)
  modelRef.current = model
  const onSelectFileRef = useRef(onSelectFile)
  onSelectFileRef.current = onSelectFile
  // 刷新与 rebuildTree 之间有一帧“结构缓存仍含已删 id”的窗口：sync
  // dataLoader 的 getItem 返回 undefined/null 会整树抛错（生产白屏），
  // 这里用“见过的节点”兜底，占位行在绘制前的 layout rebuild 中消失。
  const seenItemsRef = useRef<Record<string, PalaceTreeItemData>>({})
  const placeholderRef = useRef<PalaceTreeItemData>({ kind: 'dir', name: '…' })

  const tree = useTree<PalaceTreeItemData>({
    rootItemId: PALACE_TREE_ROOT,
    getItemName: item => item.getItemData()?.name ?? '',
    isItemFolder: item => item.getItemData()?.kind === 'dir',
    dataLoader: {
      getItem: id => modelRef.current.items[id] ?? seenItemsRef.current[id] ?? placeholderRef.current,
      getChildren: id => modelRef.current.children[id] ?? [],
    },
    features: [syncDataLoaderFeature, hotkeysCoreFeature, selectionFeature],
    initialState: { expandedItems: palaceTreeDirIds(model), selectedItems: [] },
    onPrimaryAction: item => {
      const data = item.getItemData()
      if (data?.kind === 'file' && data.file) onSelectFileRef.current(data.file)
    },
  })
  const treeRef = useRef(tree)
  treeRef.current = tree

  // 已知目录集合：刷新后自动展开新增目录，既有目录维持用户展开状态
  const knownDirIdsRef = useRef<Set<string>>(new Set(palaceTreeDirIds(model)))

  useLayoutEffect(() => {
    const dirIds = palaceTreeDirIds(model)
    const known = knownDirIdsRef.current
    const fresh = dirIds.filter(id => !known.has(id))
    knownDirIdsRef.current = new Set(dirIds)
    for (const id of fresh) treeRef.current.getItemInstance(id).expand()
    seenItemsRef.current = { ...seenItemsRef.current, ...model.items }
    // 布局阶段重建结构缓存：绘制前收敛到最新 files，避免渲染已删条目
    treeRef.current.rebuildTree()
  }, [tree, model])

  // 选中态同步进树（data-selected 样式）；文件被删除时清掉悬挂选中
  useEffect(() => {
    const instance = treeRef.current
    const id = selectedFileId ? palaceFileId(selectedFileId) : ''
    const current = instance.getSelectedItems().map(item => item.getId())
    const needsUpdate = id ? !current.includes(id) : current.length > 0
    if (needsUpdate) {
      instance.setSelectedItems(id ? [id] : [])
    }
  }, [tree, selectedFileId])

  const handleSelect = (item: ItemInstance<PalaceTreeItemData>) => {
    const data = item.getItemData()
    if (data?.kind === 'file' && data.file) onSelectFileRef.current(data.file)
  }

  return (
    <Tree
      tree={tree}
      label="记忆宫殿文件树"
      indent={16}
      data-testid="palace-file-tree"
      className="min-h-0 flex-1 overflow-y-auto pe-1"
    >
      {tree.getItems().map(item => {
        const data = item.getItemData()
        const file = data?.kind === 'file' ? data.file : undefined
        const extracting = file?.status === 'pending' || file?.status === 'building'
        return (
          <TreeItem
            key={item.getId()}
            item={item}
            onItemSelect={handleSelect}
            data-palace-file={file ? file.id : undefined}
            className="py-0.5"
          >
            <TreeItemLabel item={item}>
              {data?.kind === 'dir' ? (
                <>
                  {item.isExpanded() ? (
                    <FolderOpen size={FILE_ICON_SIZE} className="shrink-0 text-[var(--color-primary)]" />
                  ) : (
                    <Folder size={FILE_ICON_SIZE} className="shrink-0 text-[var(--color-primary)]" />
                  )}
                  <span className="truncate text-[13px]">{data.name}</span>
                </>
              ) : (
                <>
                  {file?.isImage ? (
                    <ImageIcon size={FILE_ICON_SIZE} className="shrink-0 text-[var(--color-text-tertiary)]" />
                  ) : (
                    <FileText size={FILE_ICON_SIZE} className="shrink-0 text-[var(--color-text-tertiary)]" />
                  )}
                  <span className="truncate text-[13px]" title={file?.error || file?.filename}>
                    {file?.filename}
                  </span>
                  {file?.status === 'failed' && (
                    <AlertCircle size={11} className="ml-auto shrink-0 text-red-500" aria-label="抽取失败" />
                  )}
                  {extracting && (
                    <Loader2 size={11} className="ml-auto shrink-0 animate-spin text-amber-500" aria-label="抽取中" />
                  )}
                </>
              )}
            </TreeItemLabel>
          </TreeItem>
        )
      })}
    </Tree>
  )
}
