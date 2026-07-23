import { apiClientV2 } from './client'

export interface PipelineFileRef {
  $type: 'file_ref'
  id: string
  name: string
  size: number
  content_type?: string
  sha256?: string
  /** 平台内使用 Bearer 鉴权的原始下载接口。 */
  download_url: string
  /** 可跨设备打开；未登录时先进入登录页，登录后继续下载。 */
  authenticated_url?: string | null
  /** 长期有效的匿名分享地址。可由已登录用户吊销。 */
  share_url?: string | null
}

export interface FileAssetShare {
  asset_id?: string
  share_url: string
  created_at?: string | null
  revoked_at?: string | null
}

type ShareResponse =
  | FileAssetShare
  | PipelineFileRef
  | { file_ref?: PipelineFileRef; share?: FileAssetShare; share_url?: string }
  | string

function runtimeBase(): string {
  const runtimeWindow = typeof window !== 'undefined'
    ? window as Window & { __API_BASE_URL__?: string }
    : undefined
  return (runtimeWindow?.__API_BASE_URL__ || '').replace(/\/$/, '')
}

/**
 * 将后端返回的相对文件地址变成可跨设备复制的绝对地址。
 * API 地址跟随运行时后端域名；Hash 路由地址始终跟随当前前端域名。
 */
export function absoluteFileUrl(value: string): string {
  const raw = String(value || '').trim()
  if (!raw) throw new Error('附件地址为空')
  if (/^[a-z][a-z\d+.-]*:/i.test(raw)) return raw

  const browserOrigin = typeof window !== 'undefined' ? window.location.origin : ''
  const browserPath = typeof window !== 'undefined' ? window.location.pathname : '/'
  if (raw.startsWith('#')) return `${browserOrigin}${browserPath}${raw}`
  if (raw.startsWith('/#')) return `${browserOrigin}${raw}`

  const apiBase = runtimeBase()
  if (raw.startsWith('/')) return `${apiBase || browserOrigin}${raw}`
  return new URL(raw, `${apiBase || browserOrigin}/`).toString()
}

export function authenticatedFileUrl(ref: Pick<PipelineFileRef, 'id' | 'authenticated_url'>): string {
  if (ref.authenticated_url) return absoluteFileUrl(ref.authenticated_url)
  const origin = typeof window !== 'undefined' ? window.location.origin : ''
  const pathname = typeof window !== 'undefined' ? window.location.pathname : '/'
  return `${origin}${pathname}#/file-assets/${encodeURIComponent(ref.id)}/download`
}

export function isPipelineFileRef(value: unknown): value is PipelineFileRef {
  if (!value || typeof value !== 'object') return false
  const item = value as Partial<PipelineFileRef>
  return item.$type === 'file_ref'
    && typeof item.id === 'string'
    && typeof item.name === 'string'
    && typeof item.download_url === 'string'
}

export function pipelineFileRefsIn(
  value: unknown,
  refs: PipelineFileRef[] = [],
  seen = new Set<string>(),
): PipelineFileRef[] {
  if (isPipelineFileRef(value)) {
    if (!seen.has(value.id)) {
      refs.push(value)
      seen.add(value.id)
    }
  } else if (Array.isArray(value)) {
    value.forEach(item => pipelineFileRefsIn(item, refs, seen))
  } else if (value && typeof value === 'object') {
    Object.values(value as Record<string, unknown>)
      .forEach(item => pipelineFileRefsIn(item, refs, seen))
  }
  return refs
}

function shareUrlFrom(response: ShareResponse): string | null {
  if (typeof response === 'string') return response || null
  if (!response || typeof response !== 'object') return null
  if ('share_url' in response && typeof response.share_url === 'string') {
    return response.share_url
  }
  if ('file_ref' in response && response.file_ref?.share_url) {
    return response.file_ref.share_url
  }
  if ('share' in response && response.share?.share_url) {
    return response.share.share_url
  }
  return null
}

export const fileAssetsApi = {
  get: (assetId: string) =>
    apiClientV2.get<PipelineFileRef>(`/file-assets/${encodeURIComponent(assetId)}`),

  /**
   * 幂等地创建或取得匿名分享。匿名地址长期有效，直到显式吊销。
   */
  ensureShare: async (assetId: string): Promise<string> => {
    const response = await apiClientV2.post<ShareResponse>(
      `/file-assets/${encodeURIComponent(assetId)}/share`,
    )
    const shareUrl = shareUrlFrom(response)
    if (!shareUrl) throw new Error('服务端未返回匿名分享地址')
    return absoluteFileUrl(shareUrl)
  },

  revokeShare: (assetId: string) =>
    apiClientV2.delete<{ status: string }>(
      `/file-assets/${encodeURIComponent(assetId)}/share`,
    ),
}

export async function ensureAnonymousFileUrl(ref: PipelineFileRef): Promise<string> {
  // Always ask the server for the current token. Persisted run previews can
  // still contain an older URL after a share has been revoked or rotated.
  return fileAssetsApi.ensureShare(ref.id)
}

export async function downloadPipelineFile(
  refOrUrl: PipelineFileRef | string,
  filename?: string,
): Promise<void> {
  const downloadUrl = typeof refOrUrl === 'string'
    ? refOrUrl
    : refOrUrl.download_url
  const resolvedFilename = typeof refOrUrl === 'string'
    ? (filename || 'attachment')
    : refOrUrl.name
  const token = localStorage.getItem('token') || ''
  const response = await fetch(absoluteFileUrl(downloadUrl), {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  })
  if (!response.ok) throw new Error(`附件下载失败 (${response.status})`)

  const blob = await response.blob()
  const objectUrl = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = objectUrl
  anchor.download = resolvedFilename
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  URL.revokeObjectURL(objectUrl)
}
