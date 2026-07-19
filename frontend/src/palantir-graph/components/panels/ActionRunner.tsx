import { useState, useMemo } from 'react';
import {
  XMarkIcon,
  PlayIcon,
  BoltIcon,
  CheckCircleIcon,
  XCircleIcon,
  ExclamationTriangleIcon,
  ArrowPathIcon,
  CubeIcon,
  PlusCircleIcon,
  PencilSquareIcon,
  TrashIcon,
  BellIcon,
  DocumentTextIcon,
  ArrowRightIcon,
} from '@heroicons/react/24/outline';
import { useOntologyStore } from '../../store/ontologyStore';
import { runActionRemote } from '../../api/formalApi';
import type { ActionParameter, ActionExecutionLog } from '../../types/ontology';

interface ActionRunnerProps {
  isOpen?: boolean;
  onClose?: () => void;
  initialActionId?: string;
  initialInstanceId?: string;
}

export default function ActionRunner({ isOpen: _externalIsOpen, onClose, initialActionId, initialInstanceId: _initialInstanceId }: ActionRunnerProps) {
  const [_internalIsOpen, setInternalIsOpen] = useState(false);
  const ontology = useOntologyStore((s) => s.ontology);
  const getInstancesForType = useOntologyStore((s) => s.getInstancesForType);
  const openActionExecutor = useOntologyStore((s) => s.openActionExecutor);
  const closeActionExecutor = useOntologyStore((s) => s.closeActionExecutor);
  const executingActionId = useOntologyStore((s) => s.executingActionId);
  const executingTargetInstanceId = useOntologyStore((s) => s.executingTargetInstanceId);
  const lastExecutionLog = useOntologyStore((s) => s.lastExecutionLog);
  const runAction = useOntologyStore((s) => s.runAction);

  const backendId = useOntologyStore((s) => s.backendId);
  const isDirty = useOntologyStore((s) => s.isDirty);
  const workspaceMode = useOntologyStore((s) => s.workspaceMode);
  const executionAllowed = workspaceMode === 'runtime';
  const loadFromBackend = useOntologyStore((s) => s.loadFromBackend);

  const [internalActionId, setInternalActionId] = useState<string | null>(null);
  const [targetInstanceId, setTargetInstanceId] = useState<string>('');
  const [params, setParams] = useState<Record<string, unknown>>({});
  // 后端权威引擎的执行结果与提示
  const [remoteResult, setRemoteResult] = useState<ActionExecutionLog | null>(null);
  const [remoteRunning, setRemoteRunning] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  // 用 || 而非 ??：调用方可能传 initialActionId=''（空串不是 nullish，会把下拉选择永久短路）
  const actionId = initialActionId || internalActionId || executingActionId || null;
  const effectiveTargetInstanceId = targetInstanceId || executingTargetInstanceId || '';

  const action = useMemo(
    () => ontology?.actions.find((a) => a.id === actionId),
    [ontology, actionId]
  );
  const objectType = useMemo(
    () => (action ? ontology?.objectTypes.find((ot) => ot.id === action.objectTypeId) : null),
    [ontology, action]
  );
  const primaryKeyProp = useMemo(
    () => objectType?.properties.find((p) => p.id === objectType.primaryKey || p.name === objectType.primaryKey),
    [objectType]
  );
  const instances = useMemo(
    () => (action && ontology ? getInstancesForType(action.objectTypeId) : []),
    [action, ontology, getInstancesForType]
  );

  if (!ontology) return null;

  const handleClose = () => {
    closeActionExecutor();
    setInternalIsOpen(false);
    onClose?.();
  };

  const handleParamChange = (name: string, value: unknown) => {
    setParams((prev) => ({ ...prev, [name]: value }));
  };

  const handleRun = (dryRun: boolean) => {
    if (!action || !executionAllowed) return;
    setNotice(null);
    // 如果选择了实例，将实例主键写入 source 字段
    const finalParams = { ...params };
    if (effectiveTargetInstanceId) {
      const inst = instances.find((i) => i.id === effectiveTargetInstanceId);
      if (inst && objectType) {
        if (primaryKeyProp) finalParams[primaryKeyProp.name] = inst.properties[primaryKeyProp.name];
      }
    }

    // 真实执行 + 已绑定后端 → 走后端权威引擎（审计落库、哨兵可见）。
    // 模拟执行保留本地引擎（快速、无副作用）。
    if (!dryRun && backendId) {
      if (isDirty) {
        setNotice('画布有未保存的改动。真实执行在后端权威数据上进行，请先保存（Ctrl+S）再执行，或先用模拟执行预览。');
        return;
      }
      setRemoteRunning(true);
      setRemoteResult(null);
      void runActionRemote(backendId, {
        actionId: action.id,
        parameters: finalParams,
        targetInstanceId: effectiveTargetInstanceId || undefined,
        dryRun: false,
      })
        .then(async (log: any) => {
          setRemoteResult({
            ...log,
            errors: [log.errorMessage, ...(log.validationErrors || [])].filter(Boolean) as string[],
          });
          // 后端状态已变化，拉回最新实例与日志保持一致
          await loadFromBackend(backendId);
        })
        .catch((e: any) => {
          setNotice(typeof e?.detail === 'string' ? e.detail : (e?.detail?.message || e?.message || '后端执行失败'));
        })
        .finally(() => setRemoteRunning(false));
      return;
    }

    setRemoteResult(null);
    openActionExecutor(action.id, effectiveTargetInstanceId || undefined);
    runAction(finalParams, dryRun);
  };

  const result = remoteResult ?? lastExecutionLog;
  const resultIsPending = result?.status === 'pending';
  const resultIsSuccess = result?.status === 'success' || result?.status === 'validated';
  const resultIsFailed = result?.status === 'failed';
  const resultLabel = result?.status === 'validated'
    ? '模拟执行成功'
    : result?.status === 'success'
      ? '执行成功'
      : result?.status === 'pending'
        ? '已提交，等待人工审批'
        : result?.status === 'failed'
          ? '校验未通过'
          : '执行失败';
  const resultErrors = result
    ? [
        ...(result.errors || []),
        ...(result.validationErrors || []),
        result.errorMessage,
      ].filter(Boolean)
    : [];

  // 效果图标
  const effectIcon = (type: string) => {
    switch (type) {
      case 'create': return <PlusCircleIcon className="w-4 h-4 text-green-400" />;
      case 'update': return <PencilSquareIcon className="w-4 h-4 text-blue-400" />;
      case 'delete': return <TrashIcon className="w-4 h-4 text-red-400" />;
      case 'link_create':
      case 'link_delete': return <ArrowRightIcon className="w-4 h-4 text-cyan-400" />;
      case 'notification': return <BellIcon className="w-4 h-4 text-yellow-400" />;
      default: return <DocumentTextIcon className="w-4 h-4 text-gray-400" />;
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={handleClose} />
      <div className="relative w-full max-w-2xl bg-gradient-to-b from-slate-900 to-slate-950 shadow-2xl flex flex-col animate-slide-in-right">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 bg-gradient-to-r from-yellow-900/40 to-amber-900/40 border-b border-yellow-700/30">
          <div className="flex items-center gap-3">
            <div className="p-2 rounded-lg bg-yellow-500/20">
              <BoltIcon className="w-6 h-6 text-yellow-400" />
            </div>
            <div>
              <h2 className="text-lg font-bold text-white">动作执行器</h2>
              <p className="text-xs text-yellow-300/70">
                {action ? `${action.displayName} · ${objectType?.displayName || ''}` : '选择要执行的动作'}
                <span className="ml-2 px-1.5 py-0.5 rounded bg-slate-800/80 text-[10px] text-slate-300 border border-slate-600/50">
                  {backendId ? '真实执行：后端权威引擎' : '本地模式'}
                </span>
              </p>
            </div>
          </div>
          <button onClick={handleClose} className="p-2 rounded-lg hover:bg-white/10" aria-label="关闭动作执行器" title="关闭动作执行器">
            <XMarkIcon className="w-5 h-5 text-gray-400" />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-6 space-y-5">
          {!executionAllowed && (
            <div role="status" className="rounded-xl border border-amber-500/30 bg-amber-500/10 px-4 py-3 text-sm text-amber-200">
              当前版本可查看动作定义、参数与规则，但只有当前发布态可以模拟或真实执行动作。
            </div>
          )}
          {/* 选择动作 */}
          {!initialActionId && !executingActionId && (
            <div>
              <label className="input-label">选择动作</label>
              <select
                value={internalActionId || ''}
                onChange={(e) => {
                  setInternalActionId(e.target.value);
                  setParams({});
                  setTargetInstanceId('');
                }}
                className="select-field"
              >
                <option value="">-- 选择要执行的动作 --</option>
                {ontology.actions.map((a) => {
                  const ot = ontology.objectTypes.find((o) => o.id === a.objectTypeId);
                  return (
                    <option key={a.id} value={a.id}>
                      {a.displayName} ({ot?.displayName})
                    </option>
                  );
                })}
              </select>
            </div>
          )}

          {action && (
            <>
              {/* 动作描述 */}
              {action.description && (
                <div className="p-3 bg-slate-800/50 rounded-lg border border-slate-700 text-sm text-gray-300">
                  {action.description}
                </div>
              )}

              {/* 目标实例选择 */}
              {instances.length > 0 && (
                <div>
                  <label className="input-label">
                    操作实例（{objectType?.displayName}）
                  </label>
                  <select
                    value={effectiveTargetInstanceId}
                    onChange={(e) => setTargetInstanceId(e.target.value)}
                    className="select-field"
                  >
                    <option value="">-- 选择要操作的实例（可选，用于 update 类动作） --</option>
                    {instances.map((inst) => {
                      const pk = primaryKeyProp ? String(inst.properties[primaryKeyProp.name] || inst.id) : inst.id;
                      return (
                        <option key={inst.id} value={inst.id}>
                          {pk}
                        </option>
                      );
                    })}
                  </select>
                </div>
              )}

              {/* 参数表单 */}
              {action.parameters.length > 0 && (
                <div className="space-y-3">
                  <h4 className="text-sm font-medium text-gray-300 flex items-center gap-2">
                    <CubeIcon className="w-4 h-4 text-yellow-400" />
                    参数填写
                  </h4>
                  {action.parameters.map((p: ActionParameter) => (
                    <div key={p.id}>
                      <label className="input-label">
                        {p.name}
                        <span className="text-gray-500 font-normal ml-2">({p.type})</span>
                        {p.required && <span className="text-red-400 ml-0.5">*</span>}
                      </label>
                      {p.type === 'boolean' ? (
                        <select
                          value={String(params[p.name] ?? '')}
                          onChange={(e) => handleParamChange(p.name, e.target.value === 'true')}
                          className="select-field"
                        >
                          <option value="">--</option>
                          <option value="true">true</option>
                          <option value="false">false</option>
                        </select>
                      ) : p.type === 'number' ? (
                        <input
                          type="number"
                          value={params[p.name] === undefined ? '' : Number(params[p.name])}
                          onChange={(e) => handleParamChange(p.name, e.target.value === '' ? undefined : Number(e.target.value))}
                          className="input-field"
                          placeholder={`输入 ${p.name}`}
                        />
                      ) : (
                        <input
                          type="text"
                          value={params[p.name] === undefined || params[p.name] === null ? '' : String(params[p.name])}
                          onChange={(e) => handleParamChange(p.name, e.target.value)}
                          className="input-field"
                          placeholder={`输入 ${p.name}`}
                        />
                      )}
                    </div>
                  ))}
                </div>
              )}

              {/* 校验规则提示 */}
              {action.rules && action.rules.length > 0 && (
                <div className="p-3 bg-slate-800/30 rounded-lg border border-slate-700">
                  <h4 className="text-xs text-gray-400 mb-2">执行规则 ({action.rules.filter(r => r.enabled).length} 条启用)</h4>
                  <div className="space-y-1">
                    {action.rules.map((r) => (
                      <div key={r.id} className={`flex items-center gap-2 text-xs ${r.enabled ? 'text-gray-300' : 'text-gray-600 line-through'}`}>
                        {effectIcon(
                          r.type === 'create_object' ? 'create' :
                          r.type === 'update_property' ? 'update' :
                          r.type === 'delete_link' || r.type === 'create_link' ? 'link_create' :
                          r.type === 'notification' ? 'notification' :
                          r.type === 'validation' ? 'validate' : 'other'
                        )}
                        <span>{r.name}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* 提示（未保存 / 远程失败） */}
              {notice && (
                <div className="p-3 rounded-lg border border-amber-500/40 bg-amber-900/20 text-xs text-amber-200 flex items-start gap-2">
                  <ExclamationTriangleIcon className="w-4 h-4 shrink-0 mt-0.5" />
                  <span>{notice}</span>
                </div>
              )}

              {/* 执行按钮 */}
              <div className="flex gap-3">
                <button
                  onClick={() => handleRun(true)}
                  disabled={!executionAllowed || remoteRunning}
                  className="flex-1 btn-secondary justify-center disabled:opacity-40"
                >
                  <ArrowPathIcon className="w-4 h-4" />
                  模拟执行（本地，不修改数据）
                </button>
                <button
                  onClick={() => handleRun(false)}
                  disabled={!executionAllowed || remoteRunning}
                  className="flex-1 btn-primary justify-center disabled:opacity-40"
                >
                  {remoteRunning
                    ? <ArrowPathIcon className="w-4 h-4 animate-spin" />
                    : <PlayIcon className="w-4 h-4" />}
                  {remoteRunning ? '后端执行中…' : executionAllowed ? '执行动作' : '当前状态不可执行'}
                </button>
              </div>

              {/* 执行结果 */}
              {result && result.actionId === action.id && (
                <div className={`rounded-xl border overflow-hidden ${
                  resultIsSuccess ? 'border-green-500/30 bg-green-500/5' :
                  resultIsPending ? 'border-blue-500/30 bg-blue-500/5' :
                  resultIsFailed ? 'border-yellow-500/30 bg-yellow-500/5' :
                  'border-red-500/30 bg-red-500/5'
                }`}>
                  <div className={`px-4 py-3 flex items-center gap-2 ${
                    resultIsSuccess ? 'bg-green-500/10' :
                    resultIsPending ? 'bg-blue-500/10' :
                    resultIsFailed ? 'bg-yellow-500/10' :
                    'bg-red-500/10'
                  }`}>
                    {resultIsSuccess ? (
                      <CheckCircleIcon className="w-5 h-5 text-green-400" />
                    ) : resultIsPending ? (
                      <ExclamationTriangleIcon className="w-5 h-5 text-blue-400" />
                    ) : resultIsFailed ? (
                      <ExclamationTriangleIcon className="w-5 h-5 text-yellow-400" />
                    ) : (
                      <XCircleIcon className="w-5 h-5 text-red-400" />
                    )}
                    <span className={`font-medium ${
                      resultIsSuccess ? 'text-green-400' :
                      resultIsPending ? 'text-blue-400' :
                      resultIsFailed ? 'text-yellow-400' :
                      'text-red-400'
                    }`}>
                      {resultLabel}
                    </span>
                    <span className="ml-auto text-xs text-gray-500">{result.durationMs}ms</span>
                  </div>
                  {resultIsPending && (
                    <div className="px-4 py-2 text-xs text-blue-300/80 bg-blue-500/5 border-t border-blue-500/20">
                      该动作开启了人工审批（requires approval）。请到「执行历史」面板的待审批区批准或拒绝——批准/拒绝都会记录为决策事实。
                    </div>
                  )}
                  <div className="p-4 space-y-3">
                    {/* 校验结果 */}
                    {(result.validations || []).length > 0 && (
                      <div>
                        <h5 className="text-xs text-gray-400 mb-2">校验结果</h5>
                        <div className="space-y-1">
                          {(result.validations || []).map((v, i) => (
                            <div key={i} className={`text-xs flex items-start gap-2 ${
                              v.passed ? 'text-green-400' : 'text-red-400'
                            }`}>
                              {v.passed ? (
                                <CheckCircleIcon className="w-3.5 h-3.5 mt-0.5 flex-shrink-0" />
                              ) : (
                                <XCircleIcon className="w-3.5 h-3.5 mt-0.5 flex-shrink-0" />
                              )}
                              <span>{v.error || v.rule}</span>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}

                    {/* 执行效果 */}
                    {(result.effects || []).length > 0 && (
                      <div>
                        <h5 className="text-xs text-gray-400 mb-2">执行效果（{(result.effects || []).length} 项）</h5>
                        <div className="space-y-1">
                          {(result.effects || []).map((e, i) => (
                            <div key={i} className="text-xs flex items-center gap-2 text-gray-300 bg-slate-800/60 rounded px-2 py-1.5">
                              {effectIcon(e.type)}
                              <span className="font-medium">{e.description}</span>
                              {e.targetObjectTypeId && (
                                <span className="text-gray-500 ml-auto">{e.targetObjectTypeId}</span>
                              )}
                            </div>
                          ))}
                        </div>
                      </div>
                    )}

                    {/* 错误 */}
                    {resultErrors.length > 0 && (
                      <div>
                        <h5 className="text-xs text-red-400 mb-2">错误</h5>
                        {resultErrors.map((err, i) => (
                          <div key={i} className="text-xs text-red-400 bg-red-500/10 rounded px-2 py-1">
                            {err}
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
              )}
            </>
          )}

          {!action && (
            <div className="text-center py-12 text-gray-500">
              <BoltIcon className="w-12 h-12 mx-auto mb-3 opacity-30" />
              <p>请从上方选择一个动作开始执行</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
