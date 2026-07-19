import { useCallback, useEffect, useState } from 'react';
import {
  XMarkIcon,
  RocketLaunchIcon,
  ArrowPathIcon,
  ShieldCheckIcon,
  HandRaisedIcon,
  EyeIcon,
  BoltIcon,
  ArrowUpCircleIcon,
  ArrowDownCircleIcon,
} from '@heroicons/react/24/outline';
import { useOntologyStore } from '../../store/ontologyStore';
import { getAutonomyStats, setActionApproval, type AutonomyActionStat } from '../../api/formalApi';

interface Props {
  isOpen: boolean;
  onClose: () => void;
}

const LEVEL_META: Record<string, { label: string; desc: string; icon: React.ElementType; cls: string }> = {
  L0: { label: 'L0 影子', desc: '哨兵全部静默——只观察记录，不产生副作用', icon: EyeIcon, cls: 'bg-slate-500/20 text-slate-300 border-slate-500/40' },
  L1: { label: 'L1 人审', desc: '每次真实执行等人批准，决策写入事实流', icon: HandRaisedIcon, cls: 'bg-blue-500/20 text-blue-300 border-blue-500/40' },
  L2: { label: 'L2 自动', desc: '命中即执行，无需人工确认', icon: BoltIcon, cls: 'bg-emerald-500/20 text-emerald-300 border-emerald-500/40' },
};

/**
 * 自治等级面板 — 自治是挣来的，不是配出来的。
 * 按动作统计 HITL 决策历史（人工批准率），达标才建议晋升 L1→L2；
 * 自动执行失败率高则建议降回 L1。晋升/降级即切换动作的审批闸门。
 */
export default function AutonomyPanel({ isOpen, onClose }: Props) {
  const backendId = useOntologyStore((s) => s.backendId);
  const workspaceMode = useOntologyStore((s) => s.workspaceMode);
  const actions = useOntologyStore((s) => s.ontology?.actions || []);
  const runtimeAccessible = workspaceMode === 'runtime';
  const [stats, setStats] = useState<AutonomyActionStat[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (!backendId || !runtimeAccessible) { setStats([]); return; }
    setLoading(true);
    setError(null);
    try {
      setStats(await getAutonomyStats(backendId));
    } catch (e: any) {
      setError(typeof e?.detail === 'string' ? e.detail : (e?.message || '加载失败'));
    } finally {
      setLoading(false);
    }
  }, [backendId, runtimeAccessible]);

  useEffect(() => {
    if (isOpen) void refresh();
  }, [isOpen, refresh]);

  if (!isOpen) return null;

  const toggleGate = async (s: AutonomyActionStat, requiresApproval: boolean) => {
    if (!backendId || !runtimeAccessible) return;
    const verb = requiresApproval ? '降级到 L1（加回人工审批闸门）' : '晋升到 L2（关闭人工审批，命中即自动执行）';
    if (!window.confirm(`确定把「${s.actionName}」${verb}？\n变更立即生效于后端权威配置。`)) return;
    setBusy(s.actionId);
    setError(null);
    try {
      await setActionApproval(backendId, s.actionId, requiresApproval);
      // 同步编辑器本地副本（不标脏）：否则画布下次整体保存会用旧值把闸门改回去
      useOntologyStore.setState((st) => st.ontology ? {
        ontology: {
          ...st.ontology,
          actions: st.ontology.actions.map((a) =>
            a.id === s.actionId ? { ...a, requiresApproval } : a),
        },
      } : st);
      await refresh();
    } catch (e: any) {
      setError(typeof e?.detail === 'string' ? e.detail : (e?.detail?.message || e?.message || '切换失败'));
    } finally {
      setBusy(null);
    }
  };

  const pct = (v: number | null) => (v === null ? '—' : `${Math.round(v * 100)}%`);

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={onClose} />
      <div className="relative w-full max-w-2xl bg-gradient-to-b from-slate-900 to-slate-950 shadow-2xl flex flex-col animate-slide-in-right">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 bg-gradient-to-r from-amber-900/40 to-orange-900/40 border-b border-amber-700/30">
          <div className="flex items-center gap-3">
            <div className="p-2 rounded-lg bg-amber-500/20">
              <RocketLaunchIcon className="w-6 h-6 text-amber-400" />
            </div>
            <div>
              <h2 className="text-lg font-bold text-white">自治等级</h2>
              <p className="text-xs text-amber-300/70">影子 → 人审 → 自动：按人工批准率逐级放权</p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <button onClick={() => void refresh()} disabled={loading || !runtimeAccessible}
              className="p-2 rounded-lg hover:bg-white/10 disabled:opacity-40" title="刷新">
              <ArrowPathIcon className={`w-5 h-5 text-gray-400 ${loading ? 'animate-spin' : ''}`} />
            </button>
            <button onClick={onClose} className="p-2 rounded-lg hover:bg-white/10" aria-label="关闭自治等级" title="关闭自治等级">
              <XMarkIcon className="w-5 h-5 text-gray-400" />
            </button>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto p-6 space-y-3">
          {!runtimeAccessible && (
            <div role="status" className="rounded-xl border border-amber-500/30 bg-amber-500/10 px-4 py-3 text-xs text-amber-200">
              自治等级功能区保持可见；当前版本可查看动作的审批配置，但运行统计与晋升、降级只对当前发布态开放。
            </div>
          )}
          {!runtimeAccessible && actions.map((action) => (
            <div key={action.id} className="rounded-xl border border-slate-700/70 bg-slate-800/40 px-4 py-3.5">
              <div className="flex items-center gap-2.5">
                <span className={`rounded-full border px-2 py-0.5 text-[11px] ${action.requiresApproval ? LEVEL_META.L1.cls : LEVEL_META.L2.cls}`}>
                  {action.requiresApproval ? LEVEL_META.L1.label : LEVEL_META.L2.label}
                </span>
                <span className="text-sm font-medium text-gray-100">{action.displayName}</span>
                <span className="ml-auto text-[11px] text-gray-500">只读定义</span>
              </div>
              {action.description && <p className="mt-2 text-xs text-gray-500">{action.description}</p>}
            </div>
          ))}
          {!backendId && runtimeAccessible && (
            <p className="text-center text-sm text-gray-500 py-10">本地模式没有后端执行统计</p>
          )}
          {error && <p className="text-center text-xs text-red-400 py-2">{error}</p>}

          {stats.map((s) => {
            const meta = LEVEL_META[s.level];
            const rate = s.decisions.recentApprovalRate;
            const need = s.thresholds.promoteMinDecisions;
            return (
              <div key={s.actionId} className={`rounded-xl border px-4 py-3.5 bg-slate-800/40 ${
                s.recommendation === 'promote' ? 'border-emerald-500/50' :
                s.recommendation === 'demote' ? 'border-red-500/50' : 'border-slate-700/70'
              }`}>
                <div className="flex items-center gap-2.5">
                  <span className={`flex items-center gap-1.5 px-2 py-0.5 rounded-full border text-[11px] font-medium shrink-0 ${meta.cls}`}
                    title={meta.desc}>
                    <meta.icon className="w-3.5 h-3.5" />
                    {meta.label}
                  </span>
                  <span className="text-sm text-gray-100 font-medium truncate">{s.actionName}</span>
                  {s.pending > 0 && (
                    <span className="px-1.5 rounded bg-blue-500/25 text-blue-200 text-[10px]">{s.pending} 待审批</span>
                  )}
                  <div className="ml-auto flex gap-1.5 shrink-0">
                    {s.level === 'L1' && (
                      <button
                        onClick={() => void toggleGate(s, false)}
                        disabled={busy === s.actionId || s.recommendation !== 'promote'}
                        title={s.recommendation === 'promote'
                          ? '批准率达标，晋升为自动执行'
                          : `晋升条件：近 ${need} 次决策批准率 ≥ ${Math.round(s.thresholds.promoteRate * 100)}%（当前 ${s.decisions.recentCount} 次 / ${pct(rate)}）`}
                        className={`flex items-center gap-1 px-2.5 py-1 rounded-lg text-[11px] border ${
                          s.recommendation === 'promote'
                            ? 'bg-emerald-500/15 border-emerald-500/50 text-emerald-300 hover:bg-emerald-500/25'
                            : 'border-slate-700 text-gray-600 cursor-not-allowed'
                        }`}>
                        <ArrowUpCircleIcon className="w-3.5 h-3.5" /> 晋升 L2
                      </button>
                    )}
                    {s.level === 'L2' && (
                      <button
                        onClick={() => void toggleGate(s, true)}
                        disabled={busy === s.actionId}
                        className="flex items-center gap-1 px-2.5 py-1 rounded-lg text-[11px] border bg-blue-500/10 border-blue-500/40 text-blue-300 hover:bg-blue-500/20"
                        title="加回人工审批闸门（每次真实执行等人拍板）">
                        <ArrowDownCircleIcon className="w-3.5 h-3.5" /> 降级 L1
                      </button>
                    )}
                  </div>
                </div>

                {/* 批准率进度条（晋升的度量衡） */}
                <div className="mt-2.5 flex items-center gap-2 text-[11px] text-gray-400">
                  <span className="shrink-0 w-20">近期批准率</span>
                  <div className="flex-1 h-1.5 rounded-full bg-slate-700/70 overflow-hidden">
                    <div className={`h-full rounded-full transition-all ${
                      rate !== null && rate >= s.thresholds.promoteRate ? 'bg-emerald-400' :
                      rate !== null && rate >= 0.5 ? 'bg-amber-400' : 'bg-red-400/80'
                    }`} style={{ width: `${Math.round((rate ?? 0) * 100)}%` }} />
                  </div>
                  <span className="shrink-0 font-mono">{pct(rate)}</span>
                  <span className="shrink-0 text-gray-600">({s.decisions.recentCount}/{need} 次)</span>
                </div>

                <div className="mt-1.5 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-gray-500">
                  <span>累计决策 {s.decisions.total}（👍{s.decisions.approved} / 👎{s.decisions.rejected}）</span>
                  <span>自动执行 {s.autoRuns.total} 次{s.autoRuns.failed ? `（失败 ${s.autoRuns.failed}）` : ''}</span>
                  {s.sentinels.length > 0 && (
                    <span className="flex items-center gap-1 flex-wrap">
                      哨兵：
                      {s.sentinels.map((sn) => (
                        <span key={sn.id} className={`px-1.5 rounded text-[10px] ${
                          sn.muted ? 'bg-slate-700/80 text-gray-500' : 'bg-rose-500/15 text-rose-300'
                        }`} title={sn.muted ? '静默（影子观察）' : '在线'}>
                          {sn.name}{sn.muted ? ' ·影子' : ''}
                        </span>
                      ))}
                    </span>
                  )}
                </div>

                {s.recommendationReason && (
                  <div className={`mt-2 flex items-start gap-1.5 text-[11px] px-2.5 py-1.5 rounded-lg ${
                    s.recommendation === 'promote' ? 'bg-emerald-500/10 text-emerald-300' :
                    s.recommendation === 'demote' ? 'bg-red-500/10 text-red-300' :
                    'bg-slate-700/40 text-gray-400'
                  }`}>
                    <ShieldCheckIcon className="w-3.5 h-3.5 shrink-0 mt-0.5" />
                    {s.recommendationReason}
                  </div>
                )}
              </div>
            );
          })}

          {!loading && stats.length === 0 && backendId && runtimeAccessible && (
            <div className="text-center py-12 text-gray-500">
              <RocketLaunchIcon className="w-10 h-10 mx-auto mb-2 opacity-25" />
              <p className="text-xs">还没有动作。在画布上创建动作并绑定哨兵后，这里会展示每个动作的自治等级。</p>
            </div>
          )}
        </div>

        <div className="px-6 py-3 border-t border-slate-800 text-[10px] text-gray-600 leading-relaxed">
          自治是挣来的：L1 阶段人工决策会记为决策事实；近期批准率达标后才可晋升 L2。
          影子（L0）请到哨兵面板用「静默」开关控制。所有晋升/降级立即写入后端权威配置。
        </div>
      </div>
    </div>
  );
}
