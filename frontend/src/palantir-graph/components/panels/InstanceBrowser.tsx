import { useState, useEffect } from 'react';
import {
  XMarkIcon,
  TableCellsIcon,
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
import { objectTypeIconGlyph } from '../../utils/objectTypeIcon';

interface InstanceBrowserProps {
  isOpen?: boolean;
  onClose?: () => void;
  initialObjectTypeId?: string;
  onRunAction?: (actionId?: string, instanceId?: string) => void;
}

export default function InstanceBrowser({ isOpen: externalIsOpen, onClose, initialObjectTypeId, onRunAction: _onRunAction }: InstanceBrowserProps) {
  const [internalIsOpen, setInternalIsOpen] = useState(false);
  const isOpen = externalIsOpen !== undefined ? externalIsOpen : internalIsOpen;
  const handleClose = () => (onClose ? onClose() : setInternalIsOpen(false));

  const ontology = useOntologyStore((s) => s.ontology);
  const getInstancesForType = useOntologyStore((s) => s.getInstancesForType);
  const workspaceMode = useOntologyStore((s) => s.workspaceMode);

  const [selectedTypeId, setSelectedTypeId] = useState<string>(initialObjectTypeId || '');
  const [showComputed, setShowComputed] = useState(true);
  const [factsTarget, setFactsTarget] = useState<{ id: string; label: string } | null>(null);
  const backendId = useOntologyStore((s) => s.backendId);
  const canTraceFacts = workspaceMode === 'runtime' && Boolean(backendId);

  // 集合指标（object_set 函数聚合）：expression 走后端权威，typescript 前端引擎兜底
  const [aggregates, setAggregates] = useState<ObjectSetAggregate[]>([]);
  const [aggLoading, setAggLoading] = useState(false);
  const [aggRefresh, setAggRefresh] = useState(0);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const st = useOntologyStore.getState();
      if (st.workspaceMode !== 'runtime') { setAggregates([]); return; }
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
  }, [selectedTypeId, backendId, aggRefresh, workspaceMode]);

  const objectTypes = ontology?.objectTypes || [];
  const selectedType = objectTypes.find((ot) => ot.id === selectedTypeId);
  const instances = selectedTypeId ? getInstancesForType(selectedTypeId) : [];

  const formatValue = (v: unknown): string => {
    if (v === null || v === undefined) return '-';
    if (typeof v === 'object') return JSON.stringify(v);
    return String(v);
  };

  const propertyLabel = (p: Property): string => p.displayName || p.name;

  if (!isOpen) return null;

  const displayProps = selectedType?.properties.filter((p) => showComputed || p.source !== 'computed') || [];
  const primaryKeyProp = selectedType
    ? selectedType.properties.find((p) => p.id === selectedType.primaryKey || p.name === selectedType.primaryKey)
    : undefined;

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={handleClose} />
      <div
        data-testid="instance-browser"
        className="relative w-full max-w-6xl bg-gradient-to-b from-slate-900 to-slate-950 shadow-2xl flex flex-col animate-slide-in-right"
      >
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 bg-gradient-to-r from-cyan-900/50 to-teal-900/50 border-b border-cyan-800/30">
          <div className="flex items-center gap-3">
            <div className="p-2 rounded-lg bg-cyan-600/20">
              <TableCellsIcon className="w-6 h-6 text-cyan-400" />
            </div>
            <div>
              <h2 className="text-xl font-bold text-white">对象实例浏览器</h2>
              <p className="text-sm text-cyan-300/70">
                {workspaceMode === 'runtime'
                  ? '只读查看正式实例与事实溯源'
                  : '只读查看当前版本的实例数据'}
              </p>
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

        <div
          role="status"
          className={`border-b px-6 py-2.5 text-xs ${
            workspaceMode === 'runtime'
              ? 'border-cyan-500/20 bg-cyan-500/10 text-cyan-100'
              : 'border-amber-500/20 bg-amber-500/10 text-amber-200'
          }`}
        >
          {workspaceMode === 'runtime'
            ? '当前发布运行实例是只读投影。新增或变更只能通过已审批的数据湖 Mapping 或 Action 写入；这里仍可查看正式事实溯源。'
            : workspaceMode === 'trial'
              ? '正在查看试跑隔离空间的数据。内容只读，且不读取当前发布版的正式事实溯源。'
              : workspaceMode === 'draft'
                ? '草稿态只维护模型定义，不直接写入实例；实例变更需在发布后通过已审批的数据湖 Mapping 或 Action 完成。'
                : '历史或归档版本只展示该版本快照，不承载实例写入，也不读取当前发布版的正式事实溯源。'}
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
                {objectTypeIconGlyph(ot.icon)} {ot.displayName} ({(ontology?.instances || []).filter((i: ObjectInstance) => i.objectTypeId === ot.id).length})
              </option>
            ))}
          </select>
          {selectedType && (
            <label className="flex items-center gap-2 text-sm text-gray-400 ml-2">
              <input type="checkbox" checked={showComputed} onChange={(e) => setShowComputed(e.target.checked)} />
              显示派生属性
            </label>
          )}
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
                    {canTraceFacts && (
                      <th className="px-4 py-3 text-right text-gray-500 w-20">溯源</th>
                    )}
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
                      {canTraceFacts && (
                        <td className="px-4 py-3 text-right">
                          <div className="flex items-center justify-end">
                            <button
                              onClick={() => {
                                const pk = primaryKeyProp ? String(inst.properties[primaryKeyProp.name] ?? inst.id) : inst.id;
                                setFactsTarget({ id: inst.id, label: `${selectedType.displayName} · ${pk}` });
                              }}
                              className="p-1.5 text-gray-500 hover:text-violet-400 hover:bg-violet-500/10 rounded"
                              title="属性溯源（变更历史）"
                              aria-label={`查看 ${selectedType.displayName} ${primaryKeyProp ? String(inst.properties[primaryKeyProp.name] ?? inst.id) : inst.id} 的属性溯源`}
                            >
                              <ClockIcon className="w-4 h-4" />
                            </button>
                          </div>
                        </td>
                      )}
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

      </div>
    </div>
  );
};

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
