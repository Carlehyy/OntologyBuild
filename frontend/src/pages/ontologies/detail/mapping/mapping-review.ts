/**
 * 数据映射总览页的审核状态与已灌入版本派生逻辑。
 * 零依赖纯函数层：不 import API/client，保持 node:test 可直接运行。
 */

/** curated 数据集走审核流；manual（人工数据集）无审核约束，返回 'na'。 */
export type DatasetReviewState = 'approved' | 'pending_review' | 'rejected' | 'na'

export interface ReviewStatusSource {
  source: 'curated' | 'manual'
  reviewStatus: string | null
}

export function datasetReviewState(dataset: ReviewStatusSource): DatasetReviewState {
  if (dataset.source !== 'curated' || !dataset.reviewStatus) return 'na'
  if (dataset.reviewStatus === 'approved') return 'approved'
  if (dataset.reviewStatus === 'rejected') return 'rejected'
  // 其余非终态（pending_review 等）一律按待审核处理：同样不可预览/灌入。
  return 'pending_review'
}

/** 有审核异常（不可预览/不可重新灌入）的态。 */
export function isReviewIssue(state: DatasetReviewState): state is 'rejected' | 'pending_review' {
  return state === 'rejected' || state === 'pending_review'
}

type MappingFieldBag = Record<string, string | boolean | unknown> | undefined

function stringField(bag: MappingFieldBag, key: string): string | null {
  const value = bag?.[key]
  return typeof value === 'string' && value ? value : null
}

/** 对象映射快照内记录的"最近一次灌入所用数据集版本"。 */
export function appliedDatasetVersionId(fieldMapping: MappingFieldBag): string | null {
  return stringField(fieldMapping, '__applied_dataset_version_id__')
}

export interface LinkDatasetRoles {
  srcDatasetId?: string | null
  tgtDatasetId?: string | null
  edgeDatasetId?: string | null
}

/**
 * 关系映射按数据集角色取最近一次灌入版本。
 * 胖关系（fat）的审核生命周期挂在边数据集上，因此 edge 优先于 src/tgt。
 */
export function appliedLinkVersionId(
  fieldMapping: MappingFieldBag,
  roles: LinkDatasetRoles,
  datasetId: string,
): string | null {
  if (roles.edgeDatasetId && datasetId === roles.edgeDatasetId) {
    return stringField(fieldMapping, '__applied_edge_version_id__')
  }
  if (roles.srcDatasetId && datasetId === roles.srcDatasetId) {
    return stringField(fieldMapping, '__applied_source_version_id__')
  }
  if (roles.tgtDatasetId && datasetId === roles.tgtDatasetId) {
    return stringField(fieldMapping, '__applied_target_version_id__')
  }
  return null
}

export interface DatasetVersionSummary {
  id: string
  version_no: number
  processed_at?: string | null
}

export interface AppliedVersionInfo {
  versionNo: number
  processedAt: string | null
}

/** 在数据集版本列表中定位"已灌入版本"；找不到（历史数据缺失）返回 null，调用方静默降级。 */
export function matchAppliedVersion(
  versions: readonly DatasetVersionSummary[],
  appliedId: string | null,
): AppliedVersionInfo | null {
  if (!appliedId) return null
  const hit = versions.find(version => version.id === appliedId)
  if (!hit || typeof hit.version_no !== 'number') return null
  return { versionNo: hit.version_no, processedAt: hit.processed_at ?? null }
}

const pad2 = (value: number) => String(value).padStart(2, '0')

/** 短日期时间 `MM-DD HH:mm`；非法输入返回空串，调用方自行省略该段。 */
export function formatShortDateTime(iso: string | null | undefined): string {
  if (!iso) return ''
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return ''
  return `${pad2(date.getMonth() + 1)}-${pad2(date.getDate())} ${pad2(date.getHours())}:${pad2(date.getMinutes())}`
}

/** 完整日期时间 `YYYY-MM-DD HH:mm`（用于 title 悬浮提示）；非法输入返回空串。 */
export function formatFullDateTime(iso: string | null | undefined): string {
  if (!iso) return ''
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return ''
  return `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())} ${pad2(date.getHours())}:${pad2(date.getMinutes())}`
}
