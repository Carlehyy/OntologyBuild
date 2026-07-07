import { useState, useMemo, useEffect } from 'react';
import {
  XMarkIcon,
  BeakerIcon,
  PlayIcon,
  CubeIcon,
  ClockIcon,
  CheckCircleIcon,
  XCircleIcon,
} from '@heroicons/react/24/outline';
import { useOntologyStore } from '../../store/ontologyStore';
import { testFunctionRemote } from '../../api/formalApi';
import type { FunctionParameter, ObjectInstance } from '../../types/ontology';

interface FunctionTesterProps {
  isOpen?: boolean;
  initialFunctionId?: string;
  onClose?: () => void;
}

export default function FunctionTester({ isOpen, initialFunctionId, onClose }: FunctionTesterProps) {
  const functions = useOntologyStore((s) => s.ontology?.functions || []);
  const instances = useOntologyStore((s) => s.ontology?.instances || []);
  const objectTypes = useOntologyStore((s) => s.ontology?.objectTypes || []);
  const testFunction = useOntologyStore((s) => s.testFunction);
  const backendId = useOntologyStore((s) => s.backendId);
  const isDirty = useOntologyStore((s) => s.isDirty);

  const [functionId, setFunctionId] = useState<string>(initialFunctionId || (functions[0]?.id ?? ''));
  const [params, setParams] = useState<Record<string, unknown>>({});
  const [selectedInstanceId, setSelectedInstanceId] = useState<string>('');
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<{ success: boolean; result?: unknown; error?: string; durationMs?: number; logs?: string[] } | null>(null);
  const [log, setLog] = useState<string[]>([]);
  // 后端权威引擎执行开关：后端仅支持 expression；TypeScript 函数默认使用前端沙箱预览。
  const [useRemote, setUseRemote] = useState(false);

  useEffect(() => {
    if (initialFunctionId) setFunctionId(initialFunctionId);
  }, [initialFunctionId]);

  const fn = functions.find((f) => f.id === functionId);
  const targetObjectType = fn?.targetObjectTypeId ? objectTypes.find((o) => o.id === fn.targetObjectTypeId) : undefined;
  const primaryKeyProp = targetObjectType?.properties.find((p) => p.id === targetObjectType.primaryKey || p.name === targetObjectType.primaryKey);

  useEffect(() => {
    setUseRemote(!!backendId && !isDirty && fn?.language === 'expression');
  }, [backendId, isDirty, fn?.language]);

  const eligibleInstances = useMemo<ObjectInstance[]>(() => {
    if (!fn?.targetObjectTypeId) return [];
    return instances.filter((i) => i.objectTypeId === fn.targetObjectTypeId);
  }, [fn, instances]);

  useEffect(() => {
    if ((fn?.functionType === 'object' || fn?.functionType === 'action_validation') && eligibleInstances.length > 0 && !selectedInstanceId) {
      setSelectedInstanceId(eligibleInstances[0].id);
    }
  }, [fn, eligibleInstances, selectedInstanceId]);

  const targetInstance = eligibleInstances.find((i) => i.id === selectedInstanceId);

  const handleRun = () => {
    if (!fn) return;
    setLoading(true);
    setResult(null);
    setLog([]);
    const start = performance.now();
    const collectedLog: string[] = [];

    // 后端权威引擎：结果与生产运行时（哨兵/派生属性）语义一致
    if (useRemote && backendId) {
      void testFunctionRemote(backendId, {
        functionId: fn.id,
        params,
        objectInstanceId: selectedInstanceId || undefined,
      })
        .then((r: any) => {
          const duration = Math.round(performance.now() - start);
          setResult({
            success: !!r.success,
            result: r.result ?? r.data,
            error: r.error,
            durationMs: r.durationMs ?? duration,
            logs: r.logs || [],
          });
          setLog(r.logs || []);
        })
        .catch((e: any) => {
          const duration = Math.round(performance.now() - start);
          setResult({
            success: false,
            error: typeof e?.detail === 'string' ? e.detail : (e?.detail?.message || e?.message || '后端执行失败'),
            durationMs: duration,
          });
        })
        .finally(() => setLoading(false));
      return;
    }

    try {
      let instanceProps: Record<string, unknown> | undefined;
      if ((fn.functionType === 'object' || fn.functionType === 'action_validation') && targetInstance) {
        instanceProps = targetInstance.properties;
      }

      const execResult = testFunction(fn.id, params, instanceProps || selectedInstanceId || undefined);
      const duration = Math.round(performance.now() - start);
      if (execResult.success) {
        setResult({ success: true, result: execResult.result, durationMs: duration, logs: collectedLog });
      } else {
        setResult({ success: false, error: execResult.error, durationMs: duration, logs: collectedLog });
      }
      setLog(collectedLog);
    } catch (err) {
      const duration = Math.round(performance.now() - start);
      setResult({ success: false, error: err instanceof Error ? err.message : String(err), durationMs: duration, logs: collectedLog });
      setLog(collectedLog);
    } finally {
      setLoading(false);
    }
  };

  const renderParamInput = (p: FunctionParameter) => {
    const value = params[p.name] ?? '';
    const handleChange = (v: unknown) => setParams((prev) => ({ ...prev, [p.name]: v }));

    switch (p.type) {
      case 'number':
        return (
          <input
            type="number"
            value={value as number | string}
            onChange={(e) => handleChange(e.target.value ? Number(e.target.value) : undefined)}
            className="input-field text-sm"
            placeholder={p.description || `输入${p.name}`}
          />
        );
      case 'boolean':
        return (
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={!!value}
              onChange={(e) => handleChange(e.target.checked)}
              className="w-4 h-4 rounded bg-surface-700 border-surface-600"
            />
            <span className="text-sm text-surface-300">{value ? 'true' : 'false'}</span>
          </label>
        );
      default:
        return (
          <input
            type="text"
            value={value as string}
            onChange={(e) => handleChange(e.target.value)}
            className="input-field text-sm font-mono"
            placeholder={p.description || `输入${p.name}`}
          />
        );
    }
  };

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-[80] flex justify-end">
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={onClose} />
      <div className="relative w-full max-w-2xl bg-gradient-to-b from-slate-900 to-slate-950 shadow-2xl flex flex-col animate-slide-in-right">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 bg-gradient-to-r from-cyan-900/50 to-blue-900/50 border-b border-cyan-700/30">
          <div className="flex items-center gap-3">
            <div className="p-2 rounded-lg bg-cyan-500/20">
              <BeakerIcon className="w-6 h-6 text-cyan-400" />
            </div>
            <div>
              <h2 className="text-xl font-bold text-white">函数测试器</h2>
              <p className="text-sm text-cyan-300/70">Function Tester - 输入参数，模拟执行，查看结果</p>
            </div>
          </div>
          <button onClick={onClose} className="p-2 rounded-lg hover:bg-white/10 transition-colors">
            <XMarkIcon className="w-6 h-6 text-gray-400" />
          </button>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-6 space-y-6">
          {!fn ? (
            <div className="text-center py-12">
              <BeakerIcon className="w-16 h-16 text-surface-600 mx-auto mb-4" />
              <p className="text-surface-400 text-lg mb-2">暂无函数</p>
              <p className="text-surface-500 text-sm">先在工具栏创建一个函数</p>
            </div>
          ) : (
            <>
              {/* 函数选择 */}
              <div className="bg-slate-800/50 rounded-xl p-4 border border-slate-700">
                <label className="input-label">选择函数</label>
                <select
                  value={functionId}
                  onChange={(e) => {
                    setFunctionId(e.target.value);
                    setParams({});
                    setResult(null);
                    setSelectedInstanceId('');
                  }}
                  className="select-field"
                >
                  {functions.map((f) => (
                    <option key={f.id} value={f.id}>
                      {f.displayName} ({f.functionType})
                    </option>
                  ))}
                </select>
                <div className="mt-3 flex items-center gap-2 text-sm">
                  <span className="px-2 py-0.5 rounded bg-cyan-500/20 text-cyan-400 text-xs">{fn.functionType}</span>
                  {fn.language === 'typescript' && (
                    <span className="px-2 py-0.5 rounded bg-blue-500/20 text-blue-400 text-xs">TypeScript</span>
                  )}
                  {fn.language === 'expression' && (
                    <span className="px-2 py-0.5 rounded bg-purple-500/20 text-purple-400 text-xs">Expression</span>
                  )}
                  {targetObjectType && (
                    <span className="px-2 py-0.5 rounded bg-indigo-500/20 text-indigo-400 text-xs flex items-center gap-1">
                      <CubeIcon className="w-3 h-3" /> {targetObjectType.displayName}
                    </span>
                  )}
                </div>
                {fn.description && <p className="mt-2 text-sm text-surface-400">{fn.description}</p>}
              </div>

              {/* 目标对象选择 */}
              {(fn.functionType === 'object' || fn.functionType === 'action_validation') && (
                <div className="bg-slate-800/50 rounded-xl p-4 border border-slate-700">
                  <label className="input-label">目标对象实例</label>
                  {eligibleInstances.length === 0 ? (
                    <p className="text-sm text-amber-400 bg-amber-500/10 border border-amber-500/20 rounded-lg p-3">
                      暂无 {targetObjectType?.displayName} 实例。请去「实例数据」面板创建示例数据。
                    </p>
                  ) : (
                    <select
                      value={selectedInstanceId}
                      onChange={(e) => setSelectedInstanceId(e.target.value)}
                      className="select-field"
                    >
                      {eligibleInstances.map((inst) => (
                        <option key={inst.id} value={inst.id}>
                          {String(inst.properties[primaryKeyProp?.name || 'id'] || inst.id)}
                        </option>
                      ))}
                    </select>
                  )}
                  {targetInstance && (
                    <div className="mt-3 p-2 bg-slate-900/50 rounded text-xs font-mono text-surface-400 max-h-28 overflow-y-auto">
                      {Object.entries(targetInstance.properties)
                        .slice(0, 8)
                        .map(([k, v]) => (
                          <div key={k}>
                            <span className="text-cyan-400">{k}</span>: {JSON.stringify(v)}
                          </div>
                        ))}
                    </div>
                  )}
                </div>
              )}

              {/* Object Set info */}
              {fn.functionType === 'object_set' && (
                <div className="bg-slate-800/50 rounded-xl p-4 border border-slate-700">
                  <div className="flex items-center justify-between">
                    <span className="text-sm text-surface-300">对象集大小</span>
                    <span className="px-2 py-0.5 rounded bg-indigo-500/20 text-indigo-400 text-sm font-mono">
                      {eligibleInstances.length} 个对象
                    </span>
                  </div>
                </div>
              )}

              {/* 参数 */}
              {fn.parameters.length > 0 && (
                <div className="bg-slate-800/50 rounded-xl p-4 border border-slate-700 space-y-3">
                  <h3 className="text-sm font-medium text-surface-200">参数</h3>
                  {fn.parameters.map((p) => (
                    <div key={p.id}>
                      <label className="input-label text-xs">
                        {p.name}
                        <span className={`ml-1 type-badge type-${p.type}`}>{p.type}</span>
                        {p.required && <span className="text-red-400 ml-1">*</span>}
                      </label>
                      {renderParamInput(p)}
                    </div>
                  ))}
                </div>
              )}

              {/* 执行引擎选择：后端权威 / 本地预览 */}
              {backendId && (
                <label className="flex items-center gap-2 text-xs text-gray-400 select-none cursor-pointer">
                  <input
                    type="checkbox"
                    checked={useRemote}
                    onChange={(e) => setUseRemote(e.target.checked)}
                    disabled={isDirty || fn.language !== 'expression'}
                    className="accent-cyan-500"
                  />
                  使用后端权威引擎执行（与生产运行时语义一致）
                  {fn.language !== 'expression' && <span className="text-cyan-400">— TypeScript 函数使用前端沙箱预览</span>}
                  {isDirty && <span className="text-amber-400">— 有未保存改动，已回退为本地预览（基于当前画布）</span>}
                </label>
              )}

              {/* 执行按钮 */}
              <button
                onClick={handleRun}
                disabled={loading || ((fn.functionType === 'object' || fn.functionType === 'action_validation') && !targetInstance)}
                className="w-full py-3 bg-gradient-to-r from-cyan-600 to-blue-600 hover:from-cyan-500 hover:to-blue-500 disabled:opacity-50 disabled:cursor-not-allowed text-white rounded-xl font-medium transition-all flex items-center justify-center gap-2"
              >
                {loading ? (
                  <div className="w-5 h-5 border-2 border-white border-t-transparent rounded-full animate-spin" />
                ) : (
                  <PlayIcon className="w-5 h-5" />
                )}
                {loading ? '执行中...' : '执行函数'}
              </button>

              {/* 结果 */}
              {result && (
                <div
                  className={`rounded-xl border p-4 ${
                    result.success
                      ? 'bg-emerald-500/10 border-emerald-500/30'
                      : 'bg-red-500/10 border-red-500/30'
                  }`}
                >
                  <div className="flex items-center justify-between mb-3">
                    <div className="flex items-center gap-2">
                      {result.success ? (
                        <CheckCircleIcon className="w-5 h-5 text-emerald-400" />
                      ) : (
                        <XCircleIcon className="w-5 h-5 text-red-400" />
                      )}
                      <span className={`font-medium ${result.success ? 'text-emerald-400' : 'text-red-400'}`}>
                        {result.success ? '执行成功' : '执行失败'}
                      </span>
                    </div>
                    {result.durationMs !== undefined && (
                      <span className="flex items-center gap-1 text-xs text-surface-400">
                        <ClockIcon className="w-3 h-3" /> {result.durationMs}ms
                      </span>
                    )}
                  </div>
                  {result.error && (
                    <div className="bg-red-900/30 text-red-300 p-3 rounded-lg text-sm font-mono mb-3 whitespace-pre-wrap">
                      {result.error}
                    </div>
                  )}
                  {result.result !== undefined && (
                    <div>
                      <p className="text-xs text-surface-400 mb-1">返回值：</p>
                      <pre className="bg-slate-900/70 text-emerald-300 p-3 rounded-lg text-xs font-mono overflow-x-auto max-h-60 overflow-y-auto">
                        {typeof result.result === 'string' ? result.result : JSON.stringify(result.result, null, 2)}
                      </pre>
                    </div>
                  )}
                  {log.length > 0 && (
                    <div className="mt-3">
                      <p className="text-xs text-surface-400 mb-1">执行日志：</p>
                      <div className="bg-slate-900/70 p-3 rounded-lg text-xs font-mono text-surface-300 max-h-40 overflow-y-auto space-y-0.5">
                        {log.map((l, i) => (
                          <div key={i}>{l}</div>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
