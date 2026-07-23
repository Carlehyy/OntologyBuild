import { useCallback, useEffect, useRef, useState } from 'react'
import { CheckCircle2, Download, FileDown, Loader2, RefreshCw, Workflow, XCircle } from 'lucide-react'
import { useNavigate, useParams } from 'react-router-dom'
import { downloadPipelineFile, fileAssetsApi } from '@/api/fileAssets'

type DownloadPhase = 'downloading' | 'complete' | 'error'

export default function FileAssetDownloadPage() {
  const { assetId = '' } = useParams()
  const navigate = useNavigate()
  const started = useRef(false)
  const [phase, setPhase] = useState<DownloadPhase>('downloading')
  const [filename, setFilename] = useState('流水线附件')
  const [error, setError] = useState('')

  const startDownload = useCallback(async () => {
    if (!assetId) {
      setError('下载地址缺少附件编号。')
      setPhase('error')
      return
    }
    setPhase('downloading')
    setError('')
    try {
      const file = await fileAssetsApi.get(assetId)
      setFilename(file.name)
      await downloadPipelineFile(file)
      setPhase('complete')
    } catch (cause) {
      const detail = cause as { detail?: string; message?: string }
      setError(detail?.detail || detail?.message || '附件不存在、已过期，或当前账号无权访问。')
      setPhase('error')
    }
  }, [assetId])

  useEffect(() => {
    // React StrictMode 会重复执行 effect；避免浏览器连续触发两次下载。
    if (started.current) return
    started.current = true
    void startDownload()
  }, [startDownload])

  return (
    <main className="flex min-h-dvh items-center justify-center bg-slate-50 px-4 py-10">
      <section
        aria-labelledby="file-download-title"
        className="w-full max-w-md overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-[0_24px_70px_-28px_rgba(15,23,42,0.32)]"
      >
        <header className="flex items-center gap-2 border-b border-slate-100 px-5 py-4">
          <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-teal-700 text-white">
            <FileDown size={17} aria-hidden="true" />
          </span>
          <div>
            <h1 id="file-download-title" className="text-sm font-semibold text-slate-950">平台附件下载</h1>
            <p className="mt-0.5 text-xs text-slate-500">当前下载受 OpenOntology 登录权限保护</p>
          </div>
        </header>

        <div className="px-6 py-10 text-center" aria-live="polite">
          {phase === 'downloading' && (
            <>
              <span className="mx-auto flex h-12 w-12 items-center justify-center rounded-2xl bg-teal-50 text-teal-700">
                <Loader2 size={23} className="animate-spin" aria-hidden="true" />
              </span>
              <h2 className="mt-4 text-base font-semibold text-slate-900">正在准备下载</h2>
              <p className="mt-1 break-all text-xs leading-5 text-slate-500">{filename}</p>
            </>
          )}

          {phase === 'complete' && (
            <>
              <span className="mx-auto flex h-12 w-12 items-center justify-center rounded-2xl bg-emerald-50 text-emerald-700">
                <CheckCircle2 size={23} aria-hidden="true" />
              </span>
              <h2 className="mt-4 text-base font-semibold text-slate-900">下载已开始</h2>
              <p className="mt-1 break-all text-xs leading-5 text-slate-500">
                {filename}；若浏览器没有响应，可以再次下载。
              </p>
              <button
                type="button"
                onClick={() => void startDownload()}
                className="mt-5 inline-flex min-h-10 items-center gap-1.5 rounded-xl bg-teal-700 px-4 text-sm font-medium text-white transition hover:bg-teal-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-500 focus-visible:ring-offset-2"
              >
                <Download size={14} aria-hidden="true" /> 再次下载
              </button>
            </>
          )}

          {phase === 'error' && (
            <>
              <span className="mx-auto flex h-12 w-12 items-center justify-center rounded-2xl bg-red-50 text-red-600">
                <XCircle size={23} aria-hidden="true" />
              </span>
              <h2 className="mt-4 text-base font-semibold text-slate-900">附件下载失败</h2>
              <p role="alert" className="mt-1 break-all text-xs leading-5 text-slate-500">{error}</p>
              <button
                type="button"
                onClick={() => void startDownload()}
                className="mt-5 inline-flex min-h-10 items-center gap-1.5 rounded-xl border border-slate-200 px-4 text-sm font-medium text-slate-700 transition hover:border-teal-200 hover:bg-teal-50 hover:text-teal-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-500"
              >
                <RefreshCw size={14} aria-hidden="true" /> 重试
              </button>
            </>
          )}
        </div>

        <footer className="flex items-center justify-between border-t border-slate-100 bg-slate-50/70 px-5 py-3">
          <p className="text-[11px] text-slate-500">登录地址不会公开附件内容</p>
          <button
            type="button"
            onClick={() => navigate('/data/pipelines/steward')}
            className="inline-flex min-h-9 items-center gap-1.5 rounded-lg px-2.5 text-xs font-medium text-teal-700 transition hover:bg-teal-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-500"
          >
            <Workflow size={13} aria-hidden="true" /> 返回数据管家
          </button>
        </footer>
      </section>
    </main>
  )
}
