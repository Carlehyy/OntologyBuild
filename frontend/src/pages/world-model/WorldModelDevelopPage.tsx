import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import CodeMirror from '@uiw/react-codemirror'
import { EditorView } from '@codemirror/view'
import { HighlightStyle, syntaxHighlighting } from '@codemirror/language'
import { tags } from '@lezer/highlight'
import { python } from '@codemirror/lang-python'
import '@fontsource/jetbrains-mono/400.css'
import '@fontsource/jetbrains-mono/600.css'
import {
  ArrowLeft,
  Check,
  CheckCircle2,
  Copy,
  FileCode2,
  HelpCircle,
  History,
  Loader2,
  Play,
  Rocket,
  RotateCcw,
  Save,
  Terminal,
  XCircle,
} from 'lucide-react'
import {
  apiError,
  worldModelApi,
  type ScriptExecutionResult,
  type ScriptVersionItem,
  type TestInput,
  type WorldModelProjectDetail,
  type WorldModelServiceInfo,
} from '@/api/worldModel'
import { engineTypeLabel } from './WorldModelModelsPage'
import PublishServiceDialog from './PublishServiceDialog'
import { useToast } from '@/components/ui/Toast'
import ConfirmDialog from '@/components/ConfirmDialog'
import { writeTextToClipboard } from '@/utils/clipboard'
import {
  Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle,
} from '@/components/ui/sheet'

// 与数据通道 Python 脚本编辑页一致的编辑器观感（GitHub light 高亮）
const editorTheme = EditorView.theme({
  '.cm-content': {
    lineHeight: '1.6',
  },
})

const pythonHighlight = syntaxHighlighting(HighlightStyle.define([
  { tag: tags.keyword, color: '#cf222e', fontWeight: '600' },
  { tag: [tags.string, tags.docComment], color: '#0a3069' },
  { tag: tags.comment, color: '#6e7781', fontStyle: 'italic' },
  { tag: [tags.number, tags.bool, tags.null], color: '#0550ae' },
  { tag: [tags.function(tags.variableName), tags.function(tags.propertyName)], color: '#8250df' },
  { tag: [tags.className, tags.definition(tags.variableName)], color: '#953800' },
  { tag: tags.propertyName, color: '#116329' },
  { tag: tags.operator, color: '#cf222e' },
  { tag: tags.escape, color: '#0550ae' },
]))

const DEFAULT_TEST_INPUT = `{
  "context": { "current_value": 100 },
  "actions": [],
  "horizon": 6
}`

const draftKey = (projectId: string) => `ob:world-model-draft:${projectId}`
const testInputKey = (projectId: string) => `ob:world-model-test-input:${projectId}`

interface Draft {
  script: string
  updatedAt: string
}

function readDraft(projectId: string): Draft | null {
  try {
    const raw = localStorage.getItem(draftKey(projectId))
    if (!raw) return null
    const parsed = JSON.parse(raw) as Draft
    return typeof parsed?.script === 'string' ? parsed : null
  } catch {
    return null
  }
}

function formatDateTime(iso?: string | null): string {
  if (!iso) return '-'
  try {
    return new Date(iso).toLocaleString('zh-CN', {
      month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
    })
  } catch {
    return iso
  }
}

export default function WorldModelDevelopPage() {
  const { modelId } = useParams<{ modelId: string }>()
  const navigate = useNavigate()
  const { toast } = useToast()

  const [project, setProject] = useState<WorldModelProjectDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState('')
  const [script, setScript] = useState('')
  const [savedScript, setSavedScript] = useState('')
  const [testInputText, setTestInputText] = useState(DEFAULT_TEST_INPUT)
  const [executing, setExecuting] = useState(false)
  const [saving, setSaving] = useState(false)
  const [result, setResult] = useState<ScriptExecutionResult | null>(null)
  // 客户端门槛：只有「当前编辑器内容 + 当前测试入参」执行通过，保存才可点；
  // 服务端保存时仍会重跑复验（双重保障）。
  const [validatedKey, setValidatedKey] = useState<string | null>(null)
  const [draftRestoredAt, setDraftRestoredAt] = useState<string | null>(null)
  const draftTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const abortRef = useRef<AbortController | null>(null)
  const [showVersions, setShowVersions] = useState(false)
  const [versions, setVersions] = useState<ScriptVersionItem[] | null>(null)
  const [versionsLoading, setVersionsLoading] = useState(false)
  const [showHelp, setShowHelp] = useState(false)
  const [confirmRevert, setConfirmRevert] = useState(false)
  const [confirmRestoreVersionId, setConfirmRestoreVersionId] = useState<string | null>(null)
  const [service, setService] = useState<WorldModelServiceInfo | null>(null)
  const [publishOpen, setPublishOpen] = useState(false)
  const [publishVersions, setPublishVersions] = useState<ScriptVersionItem[]>([])
  const [endpointCopied, setEndpointCopied] = useState(false)

  const dirty = script !== savedScript
  const contentKey = `${script}\n${testInputText}`
  const canSave = dirty && !executing && !saving && validatedKey === contentKey

  const parseTestInput = (): TestInput | null => {
    try {
      const parsed = JSON.parse(testInputText || '{}')
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
        toast({ tone: 'error', title: '测试入参格式不正确', description: '测试入参必须是 JSON 对象。' })
        return null
      }
      return parsed as TestInput
    } catch {
      toast({ tone: 'error', title: '测试入参不是有效 JSON', description: '请检查测试入参的语法。' })
      return null
    }
  }

  useEffect(() => {
    if (!modelId) return
    setLoading(true)
    worldModelApi.getProject(modelId)
      .then(detail => {
        setProject(detail)
        const draft = readDraft(modelId)
        if (draft && draft.script !== detail.script) {
          setScript(draft.script)
          setDraftRestoredAt(draft.updatedAt)
        } else {
          setScript(detail.script)
        }
        setSavedScript(detail.script)
        const cachedInput = localStorage.getItem(testInputKey(modelId))
        if (cachedInput) setTestInputText(cachedInput)
      })
      .catch(error => setLoadError(apiError(error)))
      .finally(() => setLoading(false))
    worldModelApi.getService(modelId)
      .then(info => setService(info))
      .catch(() => setService(null))
  }, [modelId])

  // 草稿自动保存（防抖 800ms）
  useEffect(() => {
    if (!modelId || loading) return
    if (draftTimer.current) clearTimeout(draftTimer.current)
    draftTimer.current = setTimeout(() => {
      if (script !== savedScript) {
        localStorage.setItem(draftKey(modelId), JSON.stringify({ script, updatedAt: new Date().toISOString() }))
      } else {
        localStorage.removeItem(draftKey(modelId))
      }
    }, 800)
    return () => { if (draftTimer.current) clearTimeout(draftTimer.current) }
  }, [script, savedScript, modelId, loading])

  useEffect(() => {
    if (!modelId || loading) return
    const timer = setTimeout(() => localStorage.setItem(testInputKey(modelId), testInputText), 800)
    return () => clearTimeout(timer)
  }, [testInputText, modelId, loading])

  const execute = useCallback(async () => {
    if (!modelId || executing) return
    const testInput = parseTestInput()
    if (!testInput) return
    setExecuting(true)
    setResult(null)
    const controller = new AbortController()
    abortRef.current = controller
    try {
      const execution = await worldModelApi.executeScript(modelId, script, testInput, controller.signal)
      setResult(execution)
      if (execution.ok) {
        setValidatedKey(`${script}\n${testInputText}`)
      } else {
        setValidatedKey(null)
      }
    } catch (error) {
      if ((error as { name?: string })?.name !== 'AbortError') {
        setValidatedKey(null)
        setResult(null)
        toast({ tone: 'error', title: '执行失败', description: apiError(error) })
      }
    } finally {
      setExecuting(false)
      abortRef.current = null
    }
  }, [modelId, executing, script, testInputText, toast])

  const save = useCallback(async () => {
    if (!modelId || !canSave) return
    const testInput = parseTestInput()
    if (!testInput) return
    setSaving(true)
    try {
      const saveResult = await worldModelApi.saveScript(modelId, script, testInput)
      if (!saveResult.ok) {
        setResult(saveResult.execution)
        setValidatedKey(null)
        toast({ tone: 'error', title: '保存前复核未通过', description: saveResult.execution.error ?? '脚本执行失败，未保存。' })
        return
      }
      setSavedScript(script)
      setValidatedKey(null)
      if (modelId) localStorage.removeItem(draftKey(modelId))
      setDraftRestoredAt(null)
      toast({ tone: 'success', title: `已保存为版本 v${saveResult.version_no}` })
    } catch (error) {
      toast({ tone: 'error', title: '保存失败', description: apiError(error) })
    } finally {
      setSaving(false)
    }
  }, [modelId, canSave, script, toast])

  const openVersions = useCallback(async () => {
    if (!modelId) return
    setShowVersions(true)
    setVersionsLoading(true)
    try {
      setVersions(await worldModelApi.listVersions(modelId))
    } catch (error) {
      toast({ tone: 'error', title: '版本列表加载失败', description: apiError(error) })
    } finally {
      setVersionsLoading(false)
    }
  }, [modelId, toast])

  const restoreVersion = useCallback(async (versionId: string) => {
    if (!modelId) return
    try {
      const detail = await worldModelApi.getVersion(modelId, versionId)
      setScript(detail.script)
      setValidatedKey(null)
      setShowVersions(false)
      toast({ tone: 'success', title: `已恢复 v${detail.version_no} 的脚本内容`, description: '恢复后请重新执行并保存。' })
    } catch (error) {
      toast({ tone: 'error', title: '版本恢复失败', description: apiError(error) })
    } finally {
      setConfirmRestoreVersionId(null)
    }
  }, [modelId, toast])

  // 取消执行：中断客户端等待（内核随服务端收尾销毁），编辑内容不受影响
  const cancelExecution = useCallback(() => {
    abortRef.current?.abort()
  }, [])

  const revertToSaved = () => {
    setScript(savedScript)
    setValidatedKey(null)
    setConfirmRevert(false)
  }

  // 打开发布对话框：先拉版本列表供选择（默认最新）
  const openPublish = useCallback(async () => {
    if (!modelId) return
    try {
      setPublishVersions(await worldModelApi.listVersions(modelId))
      setPublishOpen(true)
    } catch (error) {
      toast({ tone: 'error', title: '版本列表加载失败', description: apiError(error) })
    }
  }, [modelId, toast])

  const toggleServiceStatus = useCallback(async () => {
    if (!modelId || !service) return
    const next = service.status === 'online' ? 'offline' : 'online'
    try {
      setService(await worldModelApi.setServiceStatus(modelId, next))
      toast({ tone: 'success', title: next === 'online' ? '服务已上线' : '服务已下线' })
    } catch (error) {
      toast({ tone: 'error', title: '状态切换失败', description: apiError(error) })
    }
  }, [modelId, service, toast])

  const copyEndpoint = useCallback(() => {
    if (!service?.endpoint_path) return
    writeTextToClipboard(service.endpoint_path).then(() => {
      setEndpointCopied(true)
      window.setTimeout(() => setEndpointCopied(false), 1400)
    }).catch(() => undefined)
  }, [service])

  if (loading) {
    return <div className="flex h-full items-center justify-center text-sm text-[var(--color-text-tertiary)]">正在加载推演模型…</div>
  }
  if (loadError || !project) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3">
        <p className="text-sm text-[var(--color-danger)]">{loadError || '推演模型不存在'}</p>
        <button
          type="button"
          onClick={() => navigate('/world-model/models')}
          className="rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs text-slate-600 hover:bg-slate-50"
        >
          返回推演模型列表
        </button>
      </div>
    )
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      {/* 页头 */}
      <header className="mb-3 flex flex-wrap items-center gap-3">
        <button
          type="button"
          onClick={() => navigate('/world-model/models')}
          className="inline-flex h-8 w-8 items-center justify-center rounded-lg border border-slate-200 bg-white text-slate-500 transition-colors hover:bg-slate-50 hover:text-slate-700"
          aria-label="返回推演模型列表"
        >
          <ArrowLeft size={15} />
        </button>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h1 className="truncate text-base font-semibold text-[var(--color-text-primary)]">{project.name}</h1>
            <span className="inline-flex shrink-0 items-center rounded-md bg-slate-100 px-1.5 py-0.5 text-[11px] text-slate-600">
              {engineTypeLabel(project.engine_type)}
            </span>
            {dirty
              ? <span className="shrink-0 text-[11px] text-amber-600">未保存</span>
              : <span className="shrink-0 text-[11px] text-slate-400">已保存</span>}
            {draftRestoredAt && (
              <span className="shrink-0 text-[11px] text-teal-600">
                已恢复 {formatDateTime(draftRestoredAt)} 的草稿
              </span>
            )}
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => setShowHelp(true)}
            className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 text-xs text-slate-600 transition-colors hover:bg-slate-50"
          >
            <HelpCircle size={14} /> 契约说明
          </button>
          <button
            type="button"
            onClick={openVersions}
            className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 text-xs text-slate-600 transition-colors hover:bg-slate-50"
          >
            <History size={14} /> 历史版本
          </button>
          <button
            type="button"
            onClick={() => void openPublish()}
            disabled={project.version_count === 0}
            title={project.version_count === 0 ? '先执行通过并保存一个版本，才能发布' : service ? '重新发布（覆盖更新当前服务）' : '发布为推演服务'}
            className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-teal-200 bg-teal-50 px-3 text-xs font-medium text-teal-700 transition-colors hover:bg-teal-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-400 disabled:cursor-not-allowed disabled:opacity-40"
          >
            <Rocket size={14} /> {service ? '重新发布' : '发布'}
          </button>
          {dirty && (
            <button
              type="button"
              onClick={() => setConfirmRevert(true)}
              className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 text-xs text-slate-600 transition-colors hover:bg-slate-50"
            >
              <RotateCcw size={14} /> 恢复到已保存
            </button>
          )}
          {executing ? (
            <button
              type="button"
              onClick={cancelExecution}
              className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3.5 text-xs font-medium text-slate-600 transition-colors hover:bg-slate-50"
            >
              <Loader2 size={14} className="animate-spin" /> 取消执行
            </button>
          ) : (
            <button
              type="button"
              onClick={execute}
              disabled={saving}
              className="inline-flex h-8 items-center gap-1.5 rounded-lg bg-[var(--color-nav-bg)] px-3.5 text-xs font-medium text-white transition-opacity hover:opacity-90 disabled:opacity-50"
            >
              <Play size={14} /> 执行
            </button>
          )}
          <button
            type="button"
            onClick={save}
            disabled={!canSave}
            title={canSave ? '保存脚本' : '先执行通过，才能保存当前内容'}
            className="inline-flex h-8 items-center gap-1.5 rounded-lg bg-teal-600 px-3.5 text-xs font-medium text-white transition-colors hover:bg-teal-700 disabled:bg-slate-200 disabled:text-slate-400"
          >
            {saving ? <Loader2 size={14} className="animate-spin" /> : <Save size={14} />}
            保存
          </button>
        </div>
      </header>

      {/* 推演服务面板：发布后展示端点 / 状态 / 上下线 */}
      {service && (
        <section
          data-testid="world-model-service-panel"
          className={`mb-3 flex flex-wrap items-center gap-x-3 gap-y-2 rounded-xl border px-4 py-2.5 ${service.status === 'online'
            ? 'border-teal-200 bg-teal-50/60'
            : 'border-slate-200 bg-slate-50'}`}
        >
          <span className={`inline-flex items-center rounded-md px-1.5 py-0.5 text-[11px] font-medium ${service.status === 'online' ? 'bg-teal-100 text-teal-700' : 'bg-slate-200 text-slate-500'}`}>
            {service.status === 'online' ? '在线' : '已下线'}
          </span>
          <span className="text-sm font-medium text-[var(--color-text-primary)]">{service.name}</span>
          {service.version_no !== null && (
            <span className="text-[11px] text-[var(--color-text-tertiary)]">v{service.version_no}</span>
          )}
          {service.endpoint_path && (
            <span className="flex min-w-0 items-center gap-1 rounded-md border border-[var(--color-border)] bg-white px-2 py-1">
              <code className="truncate font-mono text-[11px] text-slate-600">POST {service.endpoint_path}</code>
              <button
                type="button"
                onClick={copyEndpoint}
                aria-label={endpointCopied ? '端点已复制' : '复制调用端点'}
                className="inline-flex shrink-0 items-center gap-1 rounded px-1 text-[10px] text-slate-400 transition-colors hover:text-teal-700"
              >
                {endpointCopied ? <Check size={11} /> : <Copy size={11} />}
                {endpointCopied ? '已复制' : '复制'}
              </button>
            </span>
          )}
          <span className="ml-auto flex items-center gap-2">
            <button
              type="button"
              onClick={() => void toggleServiceStatus()}
              className="inline-flex h-7 items-center rounded-md border border-[var(--color-border)] bg-white px-2.5 text-[11px] text-[var(--color-text-secondary)] transition-colors hover:bg-slate-50"
            >
              {service.status === 'online' ? '下线' : '上线'}
            </button>
          </span>
        </section>
      )}

      {/* 主区域：左编辑器，右入参+结果 */}
      <div className="grid min-h-0 flex-1 grid-cols-1 gap-3 lg:grid-cols-2">
        <section className="flex min-h-[420px] flex-col overflow-hidden rounded-xl border border-slate-200 bg-white">
          <div className="flex items-center gap-2 border-b border-slate-100 px-3 py-2 text-xs text-slate-500">
            <FileCode2 size={14} />
            <span>推演脚本（Python）</span>
          </div>
          <div className="min-h-0 flex-1">
            <CodeMirror
              value={script}
              onChange={value => { setScript(value); setValidatedKey(null) }}
              extensions={[python(), editorTheme, pythonHighlight]}
              height="100%"
              style={{ height: '100%' }}
              basicSetup={{ lineNumbers: true, foldGutter: true }}
            />
          </div>
        </section>

        <div className="flex min-h-0 flex-col gap-3">
          <section className="flex flex-col overflow-hidden rounded-xl border border-slate-200 bg-white" style={{ maxHeight: '38%' }}>
            <div className="flex items-center gap-2 border-b border-slate-100 px-3 py-2 text-xs text-slate-500">
              <Terminal size={14} />
              <span>测试入参（context / actions / horizon）</span>
            </div>
            <textarea
              value={testInputText}
              onChange={event => { setTestInputText(event.target.value); setValidatedKey(null) }}
              spellCheck={false}
              className="min-h-[110px] flex-1 resize-none px-3 py-2 font-mono text-xs leading-5 text-slate-700 focus:outline-none"
              aria-label="测试入参 JSON"
            />
          </section>

          <section className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-xl border border-slate-200 bg-white">
            <div className="flex items-center gap-2 border-b border-slate-100 px-3 py-2 text-xs text-slate-500">
              {result
                ? result.ok
                  ? <CheckCircle2 size={14} className="text-teal-600" />
                  : <XCircle size={14} className="text-red-500" />
                : <Terminal size={14} />}
              <span>执行结果</span>
              {result && (
                <span className="ml-auto text-[11px] tabular-nums text-slate-400">
                  {result.duration_ms} ms
                </span>
              )}
            </div>
            <div className="min-h-0 flex-1 overflow-auto px-3 py-2">
              {executing ? (
                <div className="flex h-full items-center justify-center gap-2 text-xs text-slate-400">
                  <Loader2 size={14} className="animate-spin" /> 内核执行中…
                </div>
              ) : !result ? (
                <p className="text-xs text-slate-400">
                  点击「执行」在内核中试运行 simulate(context, actions, horizon)，此处展示返回值与标准输出。
                </p>
              ) : (
                <div className="space-y-3">
                  {result.ok ? (
                    <div>
                      <p className="mb-1 text-[11px] font-medium text-slate-500">simulate 返回值</p>
                      <pre className="overflow-auto rounded-lg bg-slate-50 p-2.5 text-xs leading-5 text-slate-700">
                        {JSON.stringify(result.payload, null, 2)}
                      </pre>
                    </div>
                  ) : (
                    <div>
                      <p className="mb-1 text-[11px] font-medium text-red-600">执行失败</p>
                      <p className="text-xs text-red-600">{result.error}</p>
                      {result.traceback && (
                        <pre className="mt-2 overflow-auto rounded-lg bg-red-50 p-2.5 text-[11px] leading-5 text-red-700">
                          {result.traceback}
                        </pre>
                      )}
                    </div>
                  )}
                  {result.stdout && (
                    <div>
                      <p className="mb-1 text-[11px] font-medium text-slate-500">标准输出</p>
                      <pre className="max-h-48 overflow-auto rounded-lg bg-slate-50 p-2.5 text-[11px] leading-5 text-slate-600">
                        {result.stdout}
                      </pre>
                    </div>
                  )}
                </div>
              )}
            </div>
          </section>
        </div>
      </div>

      {/* 历史版本抽屉 */}
      <Sheet open={showVersions} onOpenChange={setShowVersions}>
        <SheetContent className="w-[420px] overflow-y-auto sm:max-w-[420px]">
          <SheetHeader>
            <SheetTitle>历史版本</SheetTitle>
            <SheetDescription>每次保存冻结一版，最多保留 20 版；恢复后需重新执行并保存。</SheetDescription>
          </SheetHeader>
          <div className="mt-4 space-y-2">
            {versionsLoading ? (
              <p className="py-8 text-center text-xs text-slate-400">加载版本列表…</p>
            ) : !versions?.length ? (
              <p className="py-8 text-center text-xs text-slate-400">暂无历史版本，保存一次后此处会出现记录。</p>
            ) : (
              versions.map(version => (
                <div key={version.id} className="flex items-center justify-between rounded-lg border border-slate-200 px-3 py-2.5">
                  <div>
                    <p className="text-sm font-medium text-slate-700">v{version.version_no}</p>
                    <p className="mt-0.5 text-[11px] text-slate-400">
                      {formatDateTime(version.created_at)} · {version.duration_ms} ms
                    </p>
                  </div>
                  <button
                    type="button"
                    onClick={() => { setShowVersions(false); setConfirmRestoreVersionId(version.id) }}
                    className="inline-flex h-7 items-center gap-1 rounded-md border border-slate-200 px-2.5 text-xs text-slate-600 transition-colors hover:bg-slate-50"
                  >
                    <RotateCcw size={12} /> 恢复
                  </button>
                </div>
              ))
            )}
          </div>
        </SheetContent>
      </Sheet>

      {/* 契约说明抽屉 */}
      <Sheet open={showHelp} onOpenChange={setShowHelp}>
        <SheetContent className="w-[460px] overflow-y-auto sm:max-w-[460px]">
          <SheetHeader>
            <SheetTitle>推演脚本契约</SheetTitle>
            <SheetDescription>平台与推演模型之间的统一接口约定</SheetDescription>
          </SheetHeader>
          <div className="mt-4 space-y-4 text-xs leading-6 text-slate-600">
            <section>
              <h3 className="text-sm font-semibold text-slate-800">入口函数</h3>
              <pre className="mt-2 overflow-auto rounded-lg bg-slate-50 p-3 font-mono text-[11px] leading-5">
{`def simulate(context, actions, horizon):
    ...
    return {...}  # JSON 可序列化`}
              </pre>
            </section>
            <section>
              <h3 className="text-sm font-semibold text-slate-800">参数</h3>
              <ul className="mt-1 list-disc space-y-1 pl-4">
                <li><code>context: dict</code> — 当前状态快照（来自数字孪生/知识图谱的业务对象状态）</li>
                <li><code>actions: list</code> — 候选行动列表；无干预推演（纯预测）时为空列表</li>
                <li><code>horizon: int</code> — 推演时域（步数/期数，语义由模型自行定义）</li>
              </ul>
            </section>
            <section>
              <h3 className="text-sm font-semibold text-slate-800">返回值</h3>
              <p className="mt-1">JSON 可序列化的 dict，建议包含 <code>trajectory</code>（各时点轨迹）、<code>confidence</code>（置信度）、<code>boundary</code>（适用边界说明）。</p>
            </section>
            <section>
              <h3 className="text-sm font-semibold text-slate-800">保存规则</h3>
              <ul className="mt-1 list-disc space-y-1 pl-4">
                <li>只有当前内容与测试入参执行通过，保存按钮才可用</li>
                <li>保存时服务端会重新执行复核（双重保障），通过才落库</li>
                <li>每次保存冻结一个历史版本，可随时恢复</li>
              </ul>
            </section>
          </div>
        </SheetContent>
      </Sheet>

      <ConfirmDialog
        open={confirmRevert}
        title="恢复到已保存内容？"
        message="当前未保存的修改将被丢弃，此操作无法撤销。"
        confirmLabel="恢复"
        tone="primary"
        onConfirm={revertToSaved}
        onCancel={() => setConfirmRevert(false)}
      />
      <ConfirmDialog
        open={confirmRestoreVersionId !== null}
        title="恢复到该历史版本？"
        message="当前编辑器中的未保存修改将被覆盖丢弃，恢复后需重新执行并保存。"
        confirmLabel="恢复"
        tone="primary"
        onConfirm={() => { if (confirmRestoreVersionId) void restoreVersion(confirmRestoreVersionId) }}
        onCancel={() => setConfirmRestoreVersionId(null)}
      />
      {project && (
        <PublishServiceDialog
          open={publishOpen}
          onClose={() => setPublishOpen(false)}
          project={project}
          versions={publishVersions}
          service={service}
          onPublished={published => {
            setService(published)
            setProject(current => current ? {
              ...current,
              status: 'published',
              service_status: published.status === 'offline' ? 'offline' : 'online',
            } : current)
          }}
        />
      )}
    </div>
  )
}
