import { X, Star, TestTube2, Loader2, CheckCircle2, XCircle } from 'lucide-react'
import type { ModelConfig } from '@/types/ontology'
import ModelStatsPanel from './ModelStatsPanel'
import type { DailyStats } from '../hooks/useMockModels'

interface ModelDetailDrawerProps {
  model: ModelConfig | null
  isOpen: boolean
  onClose: () => void
  isDefault: boolean
  onSetDefault: () => void
  testStatus: 'idle' | 'testing' | 'success' | 'error'
  testResult: string
  onTest: () => void
  dailyStats: DailyStats[]
}

export default function ModelDetailDrawer({
  model,
  isOpen,
  onClose,
  isDefault,
  onSetDefault,
  testStatus,
  testResult,
  onTest,
  dailyStats,
}: ModelDetailDrawerProps) {
  if (!isOpen || !model) return null

  const statusConfig = {
    idle: { label: '测试连通性', icon: TestTube2, className: 'bg-slate-100 text-slate-600 hover:bg-slate-200' },
    testing: { label: '测试中...', icon: Loader2, className: 'bg-blue-50 text-blue-600' },
    success: { label: '已连通', icon: CheckCircle2, className: 'bg-emerald-50 text-emerald-600' },
    error: { label: '连接失败', icon: XCircle, className: 'bg-red-50 text-red-600' },
  }
  const config = statusConfig[testStatus]
  const StatusIcon = config.icon

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-black/30 backdrop-blur-sm transition-opacity"
        onClick={onClose}
      />
      {/* Drawer */}
      <div className="relative w-full max-w-2xl h-full bg-slate-50 shadow-2xl overflow-y-auto animate-slide-in-right">
        {/* Header */}
        <div className="sticky top-0 z-10 bg-white border-b border-slate-200 px-6 py-4">
          <div className="flex items-start justify-between">
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-3 mb-1">
                <h2 className="text-lg font-bold text-slate-800 truncate">{model.name}</h2>
                {isDefault && (
                  <span className="shrink-0 inline-flex items-center gap-1 text-[10px] font-semibold px-2 py-0.5 rounded-full bg-slate-100 text-slate-700 border border-slate-300">
                    <Star size={10} className="fill-slate-700" /> 默认
                  </span>
                )}
              </div>
              <p className="text-xs text-slate-500">
                {model.provider} {model.api_base ? `· ${model.api_base}` : ''}
              </p>
            </div>
            <button
              onClick={onClose}
              className="shrink-0 ml-4 p-2 rounded-lg hover:bg-slate-100 transition-colors text-slate-400 hover:text-slate-600"
            >
              <X size={18} />
            </button>
          </div>

          {/* Action buttons */}
          <div className="flex items-center gap-2 mt-4">
            <button
              onClick={onTest}
              disabled={testStatus === 'testing'}
              className={`inline-flex items-center gap-1.5 px-4 py-2 rounded-lg text-xs font-medium transition-all disabled:opacity-60 ${config.className}`}
            >
              <StatusIcon size={14} className={testStatus === 'testing' ? 'animate-spin' : ''} />
              {config.label}
            </button>
            {!isDefault && (
              <button
                onClick={onSetDefault}
                className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg text-xs font-medium bg-slate-100 text-slate-700 hover:bg-slate-200 transition-colors border border-slate-300"
              >
                <Star size={14} /> 设为默认
              </button>
            )}
          </div>

          {testResult && testStatus === 'error' && (
            <p className="mt-2 text-xs text-red-600 bg-red-50 px-3 py-2 rounded-lg">{testResult}</p>
          )}
          {testResult && testStatus === 'success' && (
            <p className="mt-2 text-xs text-emerald-600 bg-emerald-50 px-3 py-2 rounded-lg">{testResult}</p>
          )}
        </div>

        {/* Content */}
        <div className="p-6 space-y-6">
          {/* 基本信息 */}
          <div className="bg-white rounded-xl border border-slate-200 p-5">
            <h3 className="text-sm font-semibold text-slate-700 mb-4">基本信息</h3>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <p className="text-[11px] text-slate-400 mb-0.5">配置类型</p>
                <p className="text-sm text-slate-700 font-medium">
                  {model.config_type === 'llm' ? 'LLM配置' : model.config_type === 'ocr' ? 'OCR配置' : '其他配置'}
                </p>
              </div>
              <div>
                <p className="text-[11px] text-slate-400 mb-0.5">Provider</p>
                <p className="text-sm text-slate-700 font-medium capitalize">{model.provider}</p>
              </div>
              <div>
                <p className="text-[11px] text-slate-400 mb-0.5">API Base</p>
                <p className="text-sm text-slate-700 font-medium truncate">{model.api_base || '-'}</p>
              </div>
              <div>
                <p className="text-[11px] text-slate-400 mb-0.5">API Key</p>
                <div className="text-sm text-slate-700 font-medium">
                  {model.has_api_key ? (
                    <span className="inline-flex items-center gap-1 text-emerald-600">
                      <CheckCircle2 size={12} /> 已配置
                    </span>
                  ) : (
                    <span className="inline-flex items-center gap-1 text-slate-400">
                      <XCircle size={12} /> 未配置
                    </span>
                  )}
                </div>
              </div>
              <div>
                <p className="text-[11px] text-slate-400 mb-0.5">创建时间</p>
                <p className="text-sm text-slate-700 font-medium">
                  {new Date(model.created_at).toLocaleString('zh-CN')}
                </p>
              </div>
              <div>
                <p className="text-[11px] text-slate-400 mb-0.5">更新时间</p>
                <p className="text-sm text-slate-700 font-medium">
                  {new Date(model.updated_at).toLocaleString('zh-CN')}
                </p>
              </div>
            </div>

            {/* 模型列表 */}
            {Boolean(model.models) && (model.models as string[]).length > 0 && (
              <div className="mt-4 pt-4 border-t border-slate-100">
                <p className="text-[11px] text-slate-400 mb-2">可用模型</p>
                <div className="flex flex-wrap gap-2">
                  {(model.models as string[]).map((m) => (
                    <span
                      key={m}
                      className="text-xs bg-blue-50 text-blue-600 px-2.5 py-1 rounded-lg font-medium border border-blue-100"
                    >
                      {m}
                    </span>
                  ))}
                </div>
              </div>
            )}

            {/* 用途标签 */}
            {Boolean(model.options?.usage_tags) && ((model.options?.usage_tags) as string[]).length > 0 && (
              <div className="mt-4 pt-4 border-t border-slate-100">
                <p className="text-[11px] text-slate-400 mb-2">用途标签</p>
                <div className="flex flex-wrap gap-2">
                  {((model.options?.usage_tags) as string[]).map((tag) => (
                    <span
                      key={tag}
                      className="text-xs bg-slate-50 text-slate-600 px-2.5 py-1 rounded-full font-medium border border-slate-200"
                    >
                      {tag}
                    </span>
                  ))}
                </div>
              </div>
            )}

            {/* 高级参数 */}
            {model.options && Object.keys(model.options).filter((k) => k !== 'usage_tags').length > 0 && (
              <div className="mt-4 pt-4 border-t border-slate-100">
                <p className="text-[11px] text-slate-400 mb-2">高级参数</p>
                <div className="bg-slate-50 rounded-lg p-3 font-mono text-xs text-slate-600 overflow-x-auto">
                  <pre>{JSON.stringify(
                    Object.fromEntries(Object.entries(model.options).filter(([k]) => k !== 'usage_tags')),
                    null,
                    2
                  )}</pre>
                </div>
              </div>
            )}
          </div>

          {/* 统计数据 */}
          {dailyStats.length > 0 && (
            <div>
              <h3 className="text-sm font-semibold text-slate-700 mb-4">调用统计</h3>
              <ModelStatsPanel dailyStats={dailyStats} />
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
