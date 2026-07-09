import { useState, useMemo, useEffect } from 'react';
import {
  XMarkIcon,
  TableCellsIcon,
  PlusIcon,
  TrashIcon,
  PencilSquareIcon,
  ChevronDownIcon,
  ChevronRightIcon,
  KeyIcon,
  CpuChipIcon,
  ClockIcon,
  ChartBarIcon,
  ArrowPathIcon,
} from '@heroicons/react/24/outline';
import { useOntologyStore } from '../../store/ontologyStore';
import { fetchObjectSetAggregates, type ObjectSetAggregate } from '../../api/formalApi';
import { executeFunction } from '../../engine/functionEngine';
import InstanceFactsDrawer from './InstanceFactsDrawer';
import type { ObjectInstance, Property } from '../../types/ontology';

interface InstanceBrowserProps {
  isOpen?: boolean;
  onClose?: () => void;
  initialObjectTypeId?: string;
  onRunAction?: (actionId?: string, instanceId?: string) => void;
}

export default function InstanceBrowser({ isOpen: externalIsOpen, onClose, initialObjectTypeId, onRunAction }: InstanceBrowserProps) {
  const [internalIsOpen, setInternalIsOpen] = useState(false);
  const isOpen = externalIsOpen !== undefined ? externalIsOpen : internalIsOpen;
  const handleClose = () => (onClose ? onClose() : setInternalIsOpen(false));

  const ontology = useOntologyStore((s) => s.ontology);
  const addInstance = useOntologyStore((s) => s.addInstance);
  const updateInstance = useOntologyStore((s) => s.updateInstance);
  const deleteInstance = useOntologyStore((s) => s.deleteInstance);
  const getInstancesForType = useOntologyStore((s) => s.getInstancesForType);
  const saveToBackend = useOntologyStore((s) => s.saveToBackend);
  const isDirty = useOntologyStore((s) => s.isDirty);
  const syncStatus = useOntologyStore((s) => s.syncStatus);
  const syncError = useOntologyStore((s) => s.syncError);

  const [selectedTypeId, setSelectedTypeId] = useState<string>(initialObjectTypeId || '');
  const [editingInstance, setEditingInstance] = useState<ObjectInstance | null>(null);
  const [isCreating, setIsCreating] = useState(false);
  const [showComputed, setShowComputed] = useState(true);
  const [factsTarget, setFactsTarget] = useState<{ id: string; label: string } | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<ObjectInstance | null>(null);
  const backendId = useOntologyStore((s) => s.backendId);

  // 集合指标（object_set 函数聚合）：expression 走后端权威，typescript 前端引擎兜底
  const [aggregates, setAggregates] = useState<ObjectSetAggregate[]>([]);
  const [aggLoading, setAggLoading] = useState(false);
  const [aggRefresh, setAggRefresh] = useState(0);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const st = useOntologyStore.getState();
      const type = st.ontology?.objectTypes.find((o) => o.id === selectedTypeId);
      if (!type || !st.ontology) { setAggregates([]); return; }
      const objectSetFns = st.ontology.functions.filter(
        (f) => f.functionType === 'object_set' && f.targetObjectTypeId === type.id && f.enabled,
      );
      if (objectSetFns.length === 0) { setAggregates([]); return; }
      setAggLoading(true);
      const byId = new Map<string, ObjectSetAggregate>();
      if (st.backendId) {
        try {
          (await fetchObjectSetAggregates(st.backendId, type.id)).forEach((r) => byId.set(r.functionId, r));
        } catch { /* 离线/未保存：全部走前端引擎兜底 */ }
      }
      // TypeScript 函数（clientSide）或无后端连接时：前端引擎用已加载实例计算
      const objects = st.getInstancesForType(type.id).map((i) => i.properties);
      for (const fn of objectSetFns) {
        const remote = byId.get(fn.id);
        if (remote && !remote.clientSide && remote.success) continue;
        if (fn.language === 'typescript') {
          const r = executeFunction(fn, st.ontology, { objects });
          byId.set(fn.id, {
            functionId: fn.id, name: fn.name, displayName: fn.displayName,
            returnType: String(fn.returnType), language: fn.language,
            success: r.success, result: r.data ?? r.result, error: r.error,
            clientSide: true, durationMs: r.durationMs ?? 0,
          });
        }
      }
      if (!cancelled) {
        setAggregates(objectSetFns.map((f) => byId.get(f.id)).filter(Boolean) as ObjectSetAggregate[]);
        setAggLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [selectedTypeId, backendId, aggRefresh]);

  const objectTypes = ontology?.objectTypes || [];
  const selectedType = objectTypes.find((ot) => ot.id === selectedTypeId);
  const instances = selectedTypeId ? getInstancesForType(selectedTypeId) : [];

  // Derived (computed) properties definition
  const computedProps = useMemo(() => {
    if (!selectedType) return [] as { prop: Property; fnId: string }[];
    return selectedType.properties
      .filter((p) => p.source === 'computed' && p.functionId)
      .map((p) => ({ prop: p, fnId: p.functionId! }));
  }, [selectedType]);

  const confirmDelete = () => {
    if (!deleteTarget) return;
    deleteInstance(deleteTarget.id);
    setDeleteTarget(null);
  };

  const formatValue = (v: unknown): string => {
    if (v === null || v === undefined) return '-';
    if (typeof v === 'object') return JSON.stringify(v);
    return String(v);
  };

  const propertyLabel = (p: Property): string => p.displayName || p.name;

  const openCreate = () => {
    if (!selectedType) return;
    setIsCreating(true);
    const initialProps: Record<string, unknown> = {};
    selectedType.properties.forEach((p) => {
      if (p.source === 'computed') return;
      initialProps[p.name] = p.defaultValue ?? '';
    });
    setEditingInstance({
      id: '',
      objectTypeId: selectedType.id,
      properties: initialProps,
      createdAt: '',
      updatedAt: '',
    });
  };

  const saveInstance = () => {
    if (!editingInstance || !selectedTypeId) return;
    if (isCreating) {
      addInstance({
        id: `inst_${Date.now()}`,
        objectTypeId: selectedTypeId,
        properties: editingInstance.properties,
      });
    } else {
      updateInstance(editingInstance.id, { properties: editingInstance.properties });
    }
    setEditingInstance(null);
    setIsCreating(false);
  };

  const renderPropertyInput = (p: Property) => {
    const value = editingInstance?.properties[p.name];
    const setValue = (next: unknown) => {
      if (!editingInstance) return;
      setEditingInstance({
        ...editingInstance,
        properties: { ...editingInstance.properties, [p.name]: next },
      });
    };

    if (p.type === 'boolean') {
      return (
        <label className="mt-2 inline-flex items-center gap-2 text-sm text-gray-300">
          <input
            type="checkbox"
            checked={Boolean(value)}
            onChange={(e) => setValue(e.target.checked)}
            className="h-4 w-4 rounded border-slate-600 bg-slate-900 text-cyan-500 focus:ring-cyan-500/40"
          />
          启用
        </label>
      );
    }

    return (
      <input
        type={p.type === 'number' ? 'number' : 'text'}
        value={value === null || value === undefined ? '' : String(value)}
        onChange={(e) => setValue(castValue(e.target.value, p.type))}
        className="input-field"
        placeholder={`输入 ${propertyLabel(p)}`}
      />
    );
  };

  if (!isOpen) return null;

  const displayProps = selectedType?.properties.filter((p) => showComputed || p.source !== 'computed') || [];
  const primaryKeyProp = selectedType
    ? selectedType.properties.find((p) => p.id === selectedType.primaryKey || p.name === selectedType.primaryKey)
    : undefined;

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={handleClose} />
      <div className="relative w-full max-w-6xl bg-gradient-to-b from-slate-900 to-slate-950 shadow-2xl flex flex-col animate-slide-in-right">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 bg-gradient-to-r from-cyan-900/50 to-teal-900/50 border-b border-cyan-800/30">
          <div className="flex items-center gap-3">
            <div className="p-2 rounded-lg bg-cyan-600/20">
              <TableCellsIcon className="w-6 h-6 text-cyan-400" />
            </div>
            <div>
              <h2 className="text-xl font-bold text-white">对象实例浏览器</h2>
              <p className="text-sm text-cyan-300/70">查看、创建、编辑对象实例数据</p>
            </div>
          </div>
          <button
            onClick={handleClose}
            className="p-2 rounded-lg hover:bg-white/10 transition-colors"
            aria-label="关闭对象实例浏览器"
            title="关闭对象实例浏览器"
          >
            <XMarkIcon className="w-6 h-6 text-gray-400 hover:text-white" />
          </button>
        </div>

        {/* Toolbar */}
        <div className="px-6 py-3 border-b border-slate-700/50 flex items-center gap-3 bg-slate-900/50">
          <select
            value={selectedTypeId}
            onChange={(e) => setSelectedTypeId(e.target.value)}
            className="bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-white"
          >
            <option value="">选择对象实体...</option>
            {objectTypes.map((ot) => (
              <option key={ot.id} value={ot.id}>
                {ot.icon} {ot.displayName} ({(ontology?.instances || []).filter((i: ObjectInstance) => i.objectTypeId === ot.id).length})
              </option>
            ))}
          </select>
          {selectedType && (
            <>
              <button onClick={openCreate} className="px-4 py-2 bg-cyan-600 hover:bg-cyan-500 text-white rounded-lg text-sm flex items-center gap-1.5">
                <PlusIcon className="w-4 h-4" /> 新建实例
              </button>
              <label className="flex items-center gap-2 text-sm text-gray-400 ml-2">
                <input type="checkbox" checked={showComputed} onChange={(e) => setShowComputed(e.target.checked)} />
                显示派生属性
              </label>
            </>
          )}
          <div className="ml-auto flex items-center gap-3">
            {syncStatus === 'error' && syncError && (
              <span className="max-w-xs truncate text-xs text-red-300" title={syncError}>
                {syncError}
              </span>
            )}
            {isDirty && (
              <button
                onClick={() => void saveToBackend()}
                disabled={syncStatus === 'saving'}
                className="px-4 py-2 bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 text-white rounded-lg text-sm"
              >
                {syncStatus === 'saving' ? '保存中...' : '保存全部更改'}
              </button>
            )}
          </div>
        </div>

        {/* 集合指标条（object_set 聚合） */}
        {selectedType && aggregates.length > 0 && (
          <div className="px-6 py-3 border-b border-slate-700/50 bg-slate-900/30">
            <div className="mb-2 flex items-center gap-2">
              <ChartBarIcon className="w-4 h-4 text-violet-400" />
              <span className="text-xs font-medium uppercase tracking-wider text-violet-300">集合指标</span>
              <span className="text-xs text-gray-600">· 基于 {instances.length} 条实例</span>
              <button
                onClick={() => setAggRefresh((n) => n + 1)}
                className="ml-auto rounded p-1 text-gray-500 hover:text-violet-400 hover:bg-violet-500/10"
                title="刷新集合指标"
                aria-label="刷新集合指标"
              >
                <ArrowPathIcon className={`w-3.5 h-3.5 ${aggLoading ? 'animate-spin' : ''}`} />
              </button>
            </div>
            <div className="flex flex-wrap gap-2">
              {aggregates.map((agg) => (
                <div
                  key={agg.functionId}
                  className="min-w-[120px] rounded-lg border border-slate-700/60 bg-slate-800/50 px-3 py-2"
                  title={agg.clientSide ? `${agg.name}（前端计算）` : agg.name}
                >
                  <div className="truncate text-[11px] text-gray-400">{agg.displayName}</div>
                  {agg.success ? (
                    <div className="mt-0.5 font-mono text-lg font-semibold text-violet-300">{formatAggValue(agg.result)}</div>
                  ) : (
                    <div className="mt-0.5 truncate text-xs text-red-400/80" title={agg.error}>计算失败</div>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Content */}
        <div className="flex-1 overflow-auto p-6">
          {!selectedType ? (
            <div className="flex flex-col items-center justify-center h-full text-gray-500">
              <TableCellsIcon className="w-16 h-16 mb-4 opacity-30" />
              <p>请选择一个对象实体查看实例数据</p>
            </div>
          ) : instances.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-full text-gray-500">
              <TableCellsIcon className="w-16 h-16 mb-4 opacity-30" />
              <p>暂无实例数据</p>
              <button onClick={openCreate} className="mt-4 px-4 py-2 bg-cyan-600 hover:bg-cyan-500 text-white rounded-lg text-sm">
                创建第一个实例
              </button>
            </div>
          ) : (
            <div className="overflow-x-auto rounded-xl border border-slate-700/50">
              <table className="w-full text-sm">
                <thead>
                  <tr className="bg-slate-800/70 border-b border-slate-700">
                    {displayProps.map((p) => (
                      <th key={p.id} className="px-4 py-3 text-left font-medium text-gray-300 whitespace-nowrap">
                        <div className="flex items-center gap-1.5">
                          {(p.id === selectedType.primaryKey || p.name === selectedType.primaryKey) && <KeyIcon className="w-3.5 h-3.5 text-yellow-400" />}
                          {p.source === 'computed' && <CpuChipIcon className="w-3.5 h-3.5 text-purple-400" title="派生属性" />}
                          {propertyLabel(p)}
                          <span className="text-gray-600 font-mono text-xs">({p.name})</span>
                        </div>
                      </th>
                    ))}
                    <th className="px-4 py-3 text-right text-gray-500 w-24">操作</th>
                  </tr>
                </thead>
                <tbody>
                  {instances.map((inst) => (
                    <tr key={inst.id} className="border-b border-slate-800 hover:bg-slate-800/40">
                      {displayProps.map((p) => {
                        let val: unknown = inst.properties[p.name];
                        if (p.source === 'computed') {
                          val = inst._computed?.[p.name];
                          if (val === undefined) val = '(点击刷新计算)';
                        }
                        const isKey = p.id === selectedType.primaryKey || p.name === selectedType.primaryKey;
                        return (
                          <td key={p.id} className={`px-4 py-3 ${isKey ? 'font-mono text-yellow-400' : 'text-gray-300'}`}>
                            {p.source === 'computed' && val === '(点击刷新计算)' ? (
                              <span className="text-gray-600 italic text-xs">{val}</span>
                            ) : (
                              formatValue(val)
                            )}
                          </td>
                        );
                      })}
                      <td className="px-4 py-3 text-right">
                        <div className="flex items-center justify-end gap-1">
                          {backendId && (
                            <button
                              onClick={() => {
                                const pk = primaryKeyProp ? String(inst.properties[primaryKeyProp.name] ?? inst.id) : inst.id;
                                setFactsTarget({ id: inst.id, label: `${selectedType.displayName} · ${pk}` });
                              }}
                              className="p-1.5 text-gray-500 hover:text-violet-400 hover:bg-violet-500/10 rounded"
                              title="属性溯源（变更历史）"
                            >
                              <ClockIcon className="w-4 h-4" />
                            </button>
                          )}
                          <button onClick={() => setEditingInstance(inst)} className="p-1.5 text-gray-500 hover:text-cyan-400 hover:bg-cyan-500/10 rounded" title="编辑">
                            <PencilSquareIcon className="w-4 h-4" />
                          </button>
                          <button onClick={() => setDeleteTarget(inst)} className="p-1.5 text-gray-500 hover:text-red-400 hover:bg-red-500/10 rounded" title="删除">
                            <TrashIcon className="w-4 h-4" />
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {/* 属性溯源抽屉 */}
        {factsTarget && (
          <InstanceFactsDrawer
            instanceId={factsTarget.id}
            instanceLabel={factsTarget.label}
            onClose={() => setFactsTarget(null)}
          />
        )}

        {/* Edit Modal */}
        {editingInstance && (
          <div className="absolute inset-0 bg-black/70 z-50 flex items-center justify-center">
            <div className="bg-slate-800 rounded-xl border border-slate-700 w-full max-w-2xl max-h-[80vh] overflow-hidden flex flex-col">
              <div className="px-6 py-4 border-b border-slate-700 flex items-center justify-between">
                <h3 className="text-lg font-semibold text-white">
                  {isCreating ? '新建实例' : '编辑实例'} - {selectedType?.displayName}
                </h3>
                <button onClick={() => { setEditingInstance(null); setIsCreating(false); }} className="text-gray-400 hover:text-white">
                  <XMarkIcon className="w-5 h-5" />
                </button>
              </div>
              <div className="p-6 space-y-4 overflow-y-auto">
                {selectedType?.properties.filter(p => p.source !== 'computed').map((p) => (
                  <div key={p.id}>
                    <label className="input-label flex items-center gap-1.5">
                      {(p.id === selectedType.primaryKey || p.name === selectedType.primaryKey) && <KeyIcon className="w-3.5 h-3.5 text-yellow-400" />}
                      {propertyLabel(p)}
                      <span className="text-gray-600 font-mono text-xs">({p.name})</span>
                      {p.required && <span className="text-red-400">*</span>}
                    </label>
                    {renderPropertyInput(p)}
                  </div>
                ))}
              </div>
              <div className="px-6 py-4 border-t border-slate-700 flex justify-end gap-3">
                <button onClick={() => { setEditingInstance(null); setIsCreating(false); }} className="btn-secondary">取消</button>
                <button onClick={saveInstance} className="btn-primary">保存</button>
              </div>
            </div>
          </div>
        )}

        {deleteTarget && (
          <div className="absolute inset-0 z-[60] flex items-center justify-center bg-black/70">
            <div className="w-full max-w-md rounded-xl border border-slate-700 bg-slate-800 p-5 shadow-2xl">
              <div className="flex items-start gap-3">
                <div className="rounded-lg bg-red-500/15 p-2 text-red-300">
                  <TrashIcon className="h-5 w-5" />
                </div>
                <div className="min-w-0">
                  <h3 className="text-base font-semibold text-white">删除实例</h3>
                  <p className="mt-1 text-sm text-gray-400">
                    删除后需要保存全部更改才会写入数据库。该实例的属性事实会保留为历史记录。
                  </p>
                  <p className="mt-3 truncate rounded-lg bg-slate-900/70 px-3 py-2 font-mono text-xs text-gray-300">
                    {formatValue(deleteTarget.properties[primaryKeyProp?.name || ''] ?? deleteTarget.id)}
                  </p>
                </div>
              </div>
              <div className="mt-5 flex justify-end gap-3">
                <button onClick={() => setDeleteTarget(null)} className="btn-secondary">取消</button>
                <button onClick={confirmDelete} className="bg-red-600 hover:bg-red-500 text-white px-4 py-2 rounded-lg text-sm">
                  删除实例
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};

function castValue(v: string, type: string): unknown {
  if (type === 'number') return v === '' ? null : Number(v);
  if (type === 'boolean') return v === 'true';
  return v;
}

/** 集合指标值格式化：数字直显、数组显示项数、对象紧凑 JSON */
function formatAggValue(v: unknown): string {
  if (v === null || v === undefined) return '-';
  if (typeof v === 'number') return Number.isInteger(v) ? String(v) : v.toFixed(2);
  if (typeof v === 'boolean') return v ? '是' : '否';
  if (Array.isArray(v)) return `${v.length} 项`;
  if (typeof v === 'object') {
    const s = JSON.stringify(v);
    return s.length > 40 ? s.slice(0, 40) + '…' : s;
  }
  return String(v);
}
