import { useEffect, useState } from 'react'
import { Check, Copy, ShieldCheck } from 'lucide-react'
import { apiError, apiHub, type McpInfo } from '@/api/apiHub'
import { Button } from '@/components/ui/Button'
import { Modal } from '@/components/ui/Modal'
import { writeTextToClipboard } from '@/utils/clipboard'

interface Props {
  open: boolean
  onClose: () => void
  onError: (message: string) => void
}

/**
 * 系统管理 MCP：向受信任的 Agent 开放管理能力；Token 只在服务端配置。
 * 原承载于已下线的「授权配置」页面，现作为独立弹窗保留该能力。
 */
export default function SystemMcpModal({ open, onClose, onError }: Props) {
  const [info, setInfo] = useState<McpInfo | null>(null)
  const [copied, setCopied] = useState<'endpoint' | 'config' | null>(null)
  const endpoint = info ? `${window.location.origin}${info.endpoint}` : ''
  const config = info ? JSON.stringify({
    mcpServers: {
      [info.server_name]: {
        type: 'streamable-http',
        url: endpoint,
        headers: {
          Authorization: 'Bearer <API_HUB_SYSTEM_MCP_TOKEN>',
        },
      },
    },
  }, null, 2) : ''

  useEffect(() => {
    if (!open) return
    apiHub.systemMcpInfo()
      .then(setInfo)
      .catch(error => onError(apiError(error)))
  }, [open, onError])

  useEffect(() => {
    if (!copied) return undefined
    const timer = window.setTimeout(() => setCopied(null), 2000)
    return () => window.clearTimeout(timer)
  }, [copied])

  const copy = async (value: string, target: 'endpoint' | 'config', label: string) => {
    if (!value) {
      onError(`${label}尚未加载完成，请稍后重试。`)
      return
    }
    try {
      await writeTextToClipboard(value)
      setCopied(target)
    } catch {
      onError(`${label}复制失败，请检查浏览器剪贴板权限后重试。`)
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="系统管理 MCP"
      description="向受信任的 Agent 开放管理能力；Token 只在服务端配置。"
      size="2xl"
      footer={<><Button variant="outline" onClick={onClose}>关闭</Button></>}
    >
      <div className="space-y-3">
        <div className="flex items-center justify-between text-[11px]">
          <span className="font-medium text-slate-500">服务状态</span>
          {info && (
            <span className={info.token_required
              ? 'rounded-full bg-[#e8f5e9] px-2.5 py-1 text-[10px] font-semibold text-[#2d8a4e]'
              : 'rounded-full bg-[#fff8e1] px-2.5 py-1 text-[10px] font-semibold text-[#c9861a]'}>
              {info.token_required ? 'Token 已启用' : '未配置，端点已禁用'}
            </span>
          )}
        </div>
        <div>
          <div className="mb-1.5 text-[11px] font-semibold text-slate-600">服务地址</div>
          <div className="flex gap-2">
            <input readOnly value={endpoint} className="h-9 w-full rounded-lg border border-slate-200 bg-white px-3 text-xs font-mono text-slate-700 outline-none" />
            <Button
              type="button"
              size="icon"
              variant="outline"
              disabled={!endpoint}
              onClick={() => void copy(endpoint, 'endpoint', '服务地址')}
              aria-label={copied === 'endpoint' ? '服务地址已复制' : '复制服务地址'}
            >
              {copied === 'endpoint' ? <Check size={14} /> : <Copy size={14} />}
            </Button>
          </div>
        </div>
        <div>
          <div className="mb-1.5 flex items-center justify-between text-[11px]">
            <span className="font-medium text-slate-500">Agent 配置 JSON（请替换占位符）</span>
            <button
              type="button"
              disabled={!config}
              onClick={() => void copy(config, 'config', 'Agent 配置')}
              className="inline-flex min-h-8 items-center gap-1.5 rounded-md px-2 text-xs font-medium text-teal-700 transition-colors hover:bg-teal-50 hover:text-teal-900 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {copied === 'config' ? <Check size={13} /> : <Copy size={13} />}
              {copied === 'config' ? '已复制' : '复制配置'}
            </button>
          </div>
          <pre className="min-h-24 overflow-auto whitespace-pre rounded-xl border border-slate-700/80 bg-slate-950 p-3.5 font-mono text-[11px] leading-[1.65] text-slate-100 shadow-inner [tab-size:2]"><code>{config || '正在读取配置…'}</code></pre>
        </div>
        <div className="flex items-center gap-2 rounded-lg bg-slate-50 px-3 py-2 text-[11px] text-slate-500">
          <ShieldCheck size={13} className="shrink-0 text-teal-600" />
          <span>系统管理 MCP 的 Token 仅在服务端环境变量配置，不向调用方暴露。</span>
        </div>
      </div>
    </Modal>
  )
}
