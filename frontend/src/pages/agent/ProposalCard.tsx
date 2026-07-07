/**
 * 动作提案卡片 — agent 只提案，人签字。
 *
 * 展示 dry-run 预演结果（参数 + 模拟效果 + 校验错误），用户点「确认执行」
 * 才经动作引擎真实执行；requires_approval 的动作执行后进入 HITL 审批队列。
 */
import { useState } from 'react'
import {
  Play, ShieldCheck, CheckCircle2, XCircle, Clock3, Loader2, FlaskConical,
} from 'lucide-react'
import { agentApi, type AgentProposal, type ExecuteProposalResult } from '@/api/agent'

export function ProposalCard({ oid, proposal }: { oid: string; proposal: AgentProposal }) {
  const [executing, setExecuting] = useState(false)
  const [result, setResult] = useState<ExecuteProposalResult | null>(null)
  const [error, setError] = useState('')

  const dryRunOk = proposal.status === 'success'
  const params = Object.entries(proposal.parameters || {})

  const execute = async () => {
    setExecuting(true)
    setError('')
    try {
      const r = await agentApi.executeProposal(oid, {
        actionId: proposal.actionId,
        parameters: proposal.parameters,
        targetInstanceId: proposal.targetInstanceId,
      })
      setResult(r)
    } catch (e: any) {
      setError(e?.detail || e?.message || '执行失败')
    } finally {
      setExecuting(false)
    }
  }

  const resultBanner = () => {
    if (!result) return null
    if (result.pendingApproval || result.status === 'pending') {
      return (
        <div className="flex items-center gap-1.5 px-3 py-2 rounded-lg bg-amber-50 text-xs font-medium text-amber-700">
          <Clock3 size={13} /> 已提交，等待人工审批 — 批准后才会真正落库
        </div>
      )
    }
    if (result.status === 'success') {
      return (
        <div className="flex items-center gap-1.5 px-3 py-2 rounded-lg bg-emerald-50 text-xs font-medium text-emerald-700">
          <CheckCircle2 size={13} /> 已执行成功，变更已写入事实流（可在实例历史中溯源）
        </div>
      )
    }
    return (
      <div className="flex items-center gap-1.5 px-3 py-2 rounded-lg bg-red-50 text-xs font-medium text-red-600">
        <XCircle size={13} /> 执行失败：{result.errorMessage || (result.validationErrors || []).join('；')}
      </div>
    )
  }

  return (
    <div className="mt-3 rounded-xl border border-indigo-200/70 bg-gradient-to-b from-indigo-50/40 to-transparent overflow-hidden">
      {/* 头部 */}
      <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-2 px-4 py-2.5 border-b border-indigo-100/80">
        <div className="flex flex-wrap items-center gap-2 min-w-0">
          <div className="w-6 h-6 rounded-lg bg-indigo-100 flex items-center justify-center shrink-0">
            <FlaskConical size={12} className="text-indigo-600" />
          </div>
          <span className="text-sm font-semibold text-[var(--color-text-primary)] truncate">
            {proposal.actionName}
          </span>
          <span className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded-md text-[10px] font-medium shrink-0 ${dryRunOk
            ? 'bg-emerald-50 text-emerald-600' : 'bg-red-50 text-red-500'}`}>
            预演{dryRunOk ? '通过' : '未通过'}
          </span>
          {proposal.requiresApproval && (
            <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-md text-[10px] font-medium bg-amber-50 text-amber-600 shrink-0">
              <ShieldCheck size={9} />需审批
            </span>
          )}
        </div>
        {!result && (
          <button onClick={execute} disabled={!dryRunOk || executing}
            className="inline-flex items-center gap-1.5 h-7 px-3 rounded-lg bg-[var(--color-primary)] text-white text-xs font-medium hover:opacity-90 disabled:opacity-30 disabled:cursor-not-allowed transition-all shrink-0">
            {executing ? <Loader2 size={11} className="animate-spin" /> : <Play size={11} />}
            确认执行
          </button>
        )}
      </div>

      <div className="px-4 py-3 space-y-2.5">
        {params.length > 0 && (
          <div className="space-y-1 text-xs">
            {params.map(([k, v]) => (
              <div key={k} className="flex gap-3">
                <span className="text-[var(--color-text-tertiary)] shrink-0 min-w-[64px]">{k}</span>
                <span className="text-[var(--color-text-secondary)] break-all">
                  {typeof v === 'object' ? JSON.stringify(v) : String(v)}
                </span>
              </div>
            ))}
          </div>
        )}

        {proposal.effects.length > 0 && (
          <div>
            <p className="text-[10px] font-medium text-[var(--color-text-tertiary)] uppercase tracking-wide mb-1.5">
              预演效果 · 尚未落库
            </p>
            <div className="space-y-1">
              {proposal.effects.map((e, i) => (
                <div key={i} className="flex items-start gap-2 text-xs text-[var(--color-text-secondary)]">
                  <span className="mt-1.5 w-1 h-1 rounded-full bg-indigo-400 shrink-0" />
                  {e.description || e.type}
                </div>
              ))}
            </div>
          </div>
        )}

        {proposal.validationErrors.length > 0 && (
          <div className="text-xs text-red-500 space-y-0.5">
            {proposal.validationErrors.map((e, i) => <div key={i}>· {e}</div>)}
          </div>
        )}

        {resultBanner()}
        {error && (
          <div className="flex items-center gap-1.5 px-3 py-2 rounded-lg bg-red-50 text-xs font-medium text-red-600">
            <XCircle size={13} />{error}
          </div>
        )}
      </div>
    </div>
  )
}
