import type { ElementType, Ref } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import {
  Activity,
  AlertTriangle,
  BookOpen,
  Bot,
  ClipboardCheck,
  Download,
  ExternalLink,
  Eye,
  FileText,
  FolderOpen,
  GitBranch,
  Globe,
  Globe2,
  KeyRound,
  Library,
  Loader2,
  Monitor,
  MousePointer2,
  Paperclip,
  Pencil,
  Plus,
  Search,
  ShieldCheck,
  Table2,
  Trash2,
  User,
  Workflow,
  X,
  Zap,
} from 'lucide-react'

import FileRefActions from '@/components/pipelines/FileRefActions'
import { pipelineFileRefsIn } from '@/api/fileAssets'
import type {
  StewardArtifact,
  StewardStep,
  StewardTablePreview,
  StewardTimelineItem,
} from '../stewardModel'
import { formatBytes } from '../stewardModel'


const TOOL_META: Record<string, { label: string; icon: ElementType }> = {
  steward_overview:    { label: '查看全景', icon: Eye },
  list_pipelines:      { label: '列出流水线', icon: GitBranch },
  get_workflow:        { label: '读取工作流', icon: Search },
  create_pipeline:     { label: '新建流水线', icon: Plus },
  update_workflow:     { label: '编排工作流', icon: Workflow },
  check_workflow:      { label: '体检', icon: ClipboardCheck },
  execute_pipeline:    { label: '执行流水线', icon: Zap },
  inspect_runs:        { label: '诊断执行', icon: Activity },
  check_credentials:   { label: '凭据检查', icon: KeyRound },
  list_node_types:     { label: '查节点目录', icon: Zap },
  describe_node:       { label: '查节点详情', icon: BookOpen },
  n8n_reference:       { label: '查编排参考', icon: Library },
  web_search:          { label: '联网检索', icon: Globe2 },
  probe_url:           { label: '探测数据源', icon: Globe },
  list_session_files:  { label: '查看会话文件', icon: FolderOpen },
  read_session_file:   { label: '读取文件', icon: FileText },
  create_session_file: { label: '创建文件', icon: FileText },
  edit_session_file:   { label: '编辑文件', icon: Pencil },
  delete_session_file: { label: '删除文件', icon: Trash2 },
  browser_open:        { label: '打开会话浏览器', icon: Monitor },
  browser_state:       { label: '读取页面', icon: Eye },
  browser_navigate:    { label: '浏览器跳转', icon: Globe },
  browser_click_text:  { label: '点击页面', icon: MousePointer2 },
  browser_click_element: { label: '点击页面元素', icon: MousePointer2 },
  browser_page_resources: { label: '查找页面资源', icon: Search },
  browser_save_resource: { label: '保存页面资源', icon: Download },
  browser_type:        { label: '填写页面', icon: Pencil },
  browser_network_requests: { label: '分析页面接口', icon: Activity },
  download_captured_file: { label: '下载到会话', icon: Download },
  register_proxy_interface: { label: '登记代理接口', icon: Zap },
}

const SUGGESTED = [
  '帮我执行指定n8n流水线并展示结果',
  '新建一条n8n流水线并进行托管',
]

interface ConversationTimelineProps {
  timeline: StewardTimelineItem[]
  busy: boolean
  bottomRef: Ref<HTMLDivElement>
  onSuggested: (prompt: string) => void | Promise<void>
  onDownloadFile: (file: StewardArtifact) => void | Promise<void>
  onRemoveFile: (artifactId: string) => void | Promise<void>
}

function Md({ text }: { text: string }) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      components={{
        p: p => <p className="text-sm leading-[1.7] mb-2 last:mb-0" {...p} />,
        strong: p => <strong className="font-semibold text-gray-900" {...p} />,
        h1: p => <h3 className="text-sm font-semibold mt-3 mb-1.5" {...p} />,
        h2: p => <h3 className="text-sm font-semibold mt-3 mb-1.5" {...p} />,
        h3: p => <h4 className="text-sm font-semibold mt-2 mb-1" {...p} />,
        ul: p => <ul className="list-disc pl-5 mb-2 space-y-1" {...p} />,
        ol: p => <ol className="list-decimal pl-5 mb-2 space-y-1" {...p} />,
        li: p => <li className="text-sm leading-relaxed" {...p} />,
        code: p => <code className="px-1 py-0.5 rounded bg-black/[0.05] text-[12px] font-mono" {...p} />,
        pre: p => <pre className="p-3 my-2 rounded-lg bg-black/[0.04] text-[12px] font-mono overflow-x-auto" {...p} />,
        table: p => (
          <div className="overflow-x-auto my-2 rounded-lg border border-gray-200">
            <table className="w-full text-xs border-collapse" {...p} />
          </div>
        ),
        th: p => <th className="px-3 py-1.5 text-left font-medium text-gray-500 border-b bg-gray-50 whitespace-nowrap" {...p} />,
        td: p => <td className="px-3 py-1.5 border-b" {...p} />,
      }}
    >
      {text}
    </ReactMarkdown>
  )
}

function PreviewCell({ value }: { value: unknown }) {
  if (value === null || value === undefined || value === '') {
    return <span className="text-slate-300">空</span>
  }
  if (typeof value === 'boolean') {
    return <span className={value ? 'text-emerald-700' : 'text-slate-500'}>{value ? '是' : '否'}</span>
  }
  const fileRefs = pipelineFileRefsIn(value)
  if (fileRefs.length > 0) {
    return (
      <div className="flex max-w-[320px] flex-col gap-1.5">
        {fileRefs.slice(0, 6).map(ref => <FileRefActions key={ref.id} file={ref} />)}
        {fileRefs.length > 6 && <span className="self-center text-[10px] text-slate-500">另有 {fileRefs.length - 6} 个附件</span>}
      </div>
    )
  }
  const text = typeof value === 'object' ? JSON.stringify(value) : String(value)
  return <span title={text} className="block max-w-[280px] truncate">{text}</span>
}

function OutputPreviewTable({ preview }: { preview: StewardTablePreview }) {
  const hasRows = preview.rows.length > 0 && preview.columns.length > 0
  return (
    <div className="ml-7 overflow-hidden rounded-xl border border-teal-200 bg-white shadow-[0_8px_24px_-20px_rgba(15,118,110,0.5)]">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-slate-100 bg-teal-50/60 px-3 py-2">
        <div className="flex min-w-0 items-center gap-1.5 text-xs font-semibold text-slate-800">
          <Table2 size={13} className="shrink-0 text-teal-700" />
          <span className="truncate">{preview.title || '输出样例'}</span>
        </div>
        <span className="text-[10px] text-slate-500">
          {preview.node ? `${preview.node} · ` : ''}{preview.shownRows}/{preview.totalRows} 行
          {preview.totalColumns > 0 ? ` · ${preview.columns.length}/${preview.totalColumns} 列` : ''}
        </span>
        {preview.redactedColumns.length > 0 && (
          <span className="ml-auto inline-flex items-center gap-1 rounded-full border border-emerald-200 bg-white px-2 py-0.5 text-[10px] text-emerald-700">
            <ShieldCheck size={10} /> 敏感字段已隐藏
          </span>
        )}
      </div>
      {hasRows ? (
        <div className="max-h-[320px] overflow-auto">
          <table className="min-w-full border-separate border-spacing-0 text-left text-xs">
            <thead className="sticky top-0 z-10 bg-slate-50/95 backdrop-blur">
              <tr>
                <th className="w-10 border-b border-r border-slate-200 px-2.5 py-2 text-center font-medium text-slate-400">#</th>
                {preview.columns.map(column => (
                  <th key={column} className="whitespace-nowrap border-b border-r border-slate-200 px-3 py-2 font-semibold text-slate-600 last:border-r-0">
                    {column}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {preview.rows.map((row, rowIndex) => (
                <tr key={rowIndex} className="odd:bg-white even:bg-slate-50/45 hover:bg-teal-50/40">
                  <td className="border-b border-r border-slate-100 px-2.5 py-2 text-center font-mono text-[10px] text-slate-400">{rowIndex + 1}</td>
                  {preview.columns.map(column => (
                    <td key={column} className="whitespace-nowrap border-b border-r border-slate-100 px-3 py-2 text-slate-700 last:border-r-0">
                      <PreviewCell value={row[column]} />
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="px-3 py-5 text-center text-xs text-slate-400">最近一次执行没有产生可展示的行数据</div>
      )}
      {(preview.truncated || preview.missingColumns.length > 0) && (
        <div className="border-t border-slate-100 px-3 py-2 text-[10px] leading-4 text-slate-500">
          {preview.truncated && `当前仅展示部分结果${preview.omittedColumns > 0 ? `，另有 ${preview.omittedColumns} 个字段未展开` : ''}。`}
          {preview.missingColumns.length > 0 && ` 本次输出中未找到：${preview.missingColumns.join('、')}。`}
        </div>
      )}
    </div>
  )
}

function StepTrace({ steps, running }: { steps: StewardStep[]; running?: boolean }) {
  if (steps.length === 0 && !running) return null
  return (
    <div className="mb-3 space-y-2 rounded-xl border border-slate-200 bg-slate-50 px-3.5 py-3">
      {steps.map((step, index) => {
        const meta = TOOL_META[step.tool] || { label: step.tool, icon: Zap }
        const Icon = meta.icon
        return (
          <div key={index} className="space-y-2">
            <div className="flex items-start gap-2.5">
              <div className={`mt-px w-5 h-5 rounded-md flex items-center justify-center shrink-0 ${
                step.error ? 'bg-red-50 text-red-500' : 'bg-teal-100 text-teal-700'}`}>
                {step.tool === 'web_search' ? <Globe2 size={11} /> : <Icon size={11} />}
              </div>
              <div className="min-w-0 text-xs leading-5">
                <span className={`font-medium ${step.error ? 'text-red-600' : 'text-gray-800'}`}>{meta.label}</span>
                <span className="text-slate-400"> · {step.summary}</span>
              </div>
            </div>
            {step.searchResults && step.searchResults.length > 0 && (
              <div className="ml-7 space-y-1">
                {step.searchResults.map((result, resultIndex) => (
                  <a
                    key={`${result.url}-${resultIndex}`}
                    href={result.url}
                    target="_blank"
                    rel="noreferrer"
                    title={result.snippet || result.title}
                    className="group/source flex min-w-0 items-center gap-1.5 rounded-md px-2 py-1 text-[11px] text-slate-600 transition-colors hover:bg-teal-50 hover:text-teal-700"
                  >
                    <span className="shrink-0 font-mono text-[10px] text-slate-400">[{resultIndex + 1}]</span>
                    <span className="min-w-0 flex-1 truncate">{result.title}</span>
                    <ExternalLink size={10} className="shrink-0 opacity-0 transition-opacity group-hover/source:opacity-100" />
                  </a>
                ))}
              </div>
            )}
            {step.preview && <OutputPreviewTable preview={step.preview} />}
          </div>
        )
      })}
      {running && (
        <div className="flex items-center gap-2.5">
          <div className="w-5 h-5 rounded-md bg-teal-50 flex items-center justify-center shrink-0">
            <Loader2 size={11} className="animate-spin text-teal-600" />
          </div>
          <span className="text-xs text-gray-400">
            {steps.length === 0 ? '正在识别你的意图并选择最合适的处理路径…' : '正在综合工具结果继续…'}
          </span>
        </div>
      )}
    </div>
  )
}

export default function ConversationTimeline({
  timeline,
  busy,
  bottomRef,
  onSuggested,
  onDownloadFile,
  onRemoveFile,
}: ConversationTimelineProps) {
  if (timeline.length === 0) {
    return (
      <div className="flex h-full flex-col items-center justify-center text-center px-6">
        <div className="mb-4 flex h-14 w-14 items-center justify-center rounded-2xl border border-teal-200 bg-teal-50 text-teal-700 shadow-sm">
          <Bot size={26} />
        </div>
        <h2 className="text-base font-semibold text-gray-900">让数据管家替你编排流水线</h2>
        <p className="mt-1.5 max-w-md text-sm leading-relaxed text-gray-500">
          数据管家Agent可以帮助您托管和编排流水线
        </p>
        <div className="mt-5 flex flex-wrap justify-center gap-2">
          {SUGGESTED.map(prompt => (
            <button key={prompt} onClick={() => onSuggested(prompt)} disabled={busy}
              className="rounded-full border bg-white px-3 py-1.5 text-xs text-gray-600 transition-all hover:border-teal-300 hover:bg-teal-50 hover:text-teal-700 disabled:opacity-50">
              {prompt}
            </button>
          ))}
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-5">
      {timeline.map(item => {
        if (item.kind === 'file' || item.kind === 'upload') {
          const uploading = item.kind === 'upload'
          const name = uploading ? item.upload.name : item.file.filename
          return (
            <div key={item.key} className="flex flex-row-reverse gap-3">
              <div className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-xl border border-teal-200 bg-teal-50 text-teal-700">
                <Paperclip size={14} />
              </div>
              <div className={`group flex max-w-[82%] items-center gap-2.5 rounded-xl border bg-white px-3 py-2 ${uploading
                ? 'border-dashed border-slate-300'
                : 'border-slate-200'}`}
              >
                <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-teal-50 text-teal-600">
                  {uploading ? <Loader2 size={15} className="animate-spin" /> : <FileText size={16} />}
                </span>
                <div className="min-w-0 text-left">
                  <div className="truncate text-sm font-medium text-slate-800" title={name}>{name}</div>
                  <div className="mt-0.5 text-[11px] text-slate-400">
                    {uploading ? '上传中…' : `会话附件 · ${formatBytes(item.file.size)} · 仅本会话可见`}
                  </div>
                </div>
                {!uploading && (
                  <div className="flex shrink-0 items-center gap-0.5 opacity-0 transition-opacity group-hover:opacity-100">
                    <button
                      type="button"
                      onClick={() => void onDownloadFile(item.file)}
                      title="下载文件"
                      className="rounded p-1 text-slate-400 hover:bg-slate-50 hover:text-teal-600"
                    >
                      <Download size={13} />
                    </button>
                    <button
                      type="button"
                      onClick={() => void onRemoveFile(item.file.id)}
                      title="移除附件"
                      className="rounded p-1 text-slate-400 hover:bg-red-50 hover:text-red-600"
                    >
                      <X size={13} />
                    </button>
                  </div>
                )}
              </div>
            </div>
          )
        }
        const message = item.message
        return message.role === 'user' ? (
          <div key={message.id} id={`steward-msg-${message.id}`} className="flex scroll-mt-4 justify-end gap-3">
            <div className="max-w-[82%] rounded-2xl rounded-br-md bg-teal-700 px-4 py-3 text-white shadow-sm">
              {message.targetName && (
                <p className="mb-1.5 flex items-center justify-end gap-1 text-[10px] font-medium text-teal-100">
                  <Workflow size={10} /> 操作目标 · {message.targetName}
                </p>
              )}
              <p className="whitespace-pre-wrap break-words text-sm leading-relaxed">{message.content}</p>
            </div>
            <div className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-xl border border-teal-200 bg-teal-50 text-teal-700">
              <User size={14} />
            </div>
          </div>
        ) : (
          <div key={message.id} className="flex gap-3">
            <div className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-xl bg-slate-900 text-white">
              <Bot size={14} />
            </div>
            <div className="min-w-0 flex-1">
              <StepTrace steps={message.steps} running={message.loading} />
              {message.error ? (
                <div className="rounded-lg border border-red-200 bg-red-50/70 px-4 py-3">
                  <p className="flex items-start gap-2 text-sm text-red-600">
                    <AlertTriangle size={14} className="mt-0.5 shrink-0" />{message.error}
                  </p>
                </div>
              ) : message.content ? (
                <div className="text-gray-800"><Md text={message.content} /></div>
              ) : null}
            </div>
          </div>
        )
      })}
      <div ref={bottomRef} />
    </div>
  )
}
