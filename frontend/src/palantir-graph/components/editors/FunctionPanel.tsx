import { useEffect, useState } from 'react';
import { BeakerIcon, PlayIcon, TrashIcon } from '@heroicons/react/24/outline';
import { useOntologyStore } from '../../store/ontologyStore';
import type { CacheStrategy as FunctionCacheStrategy, FunctionLanguage, FunctionParameter, FunctionType, PropertyType } from '../../types/ontology';
import FunctionParameterEditor from './FunctionParameterEditor';
import { PanelHeader } from './DefinitionPanelShell';

// Function Panel
export default function FunctionPanel({
  mode,
  onClose,
  selectedId,
}: {
  mode: 'create' | 'edit';
  onClose: () => void;
  selectedId: string | null;
}) {
  const { ontology, addFunction, updateFunction, deleteFunction, openFunctionTester } = useOntologyStore();

  const existingFn = mode === 'edit' && selectedId
    ? ontology?.functions.find(f => f.id === selectedId)
    : null;

  const functionTypes: { id: FunctionType; label: string; desc: string; color: string }[] = [
    { id: 'object', label: '对象函数', desc: '绑定单个对象，计算派生属性', color: 'cyan' },
    { id: 'object_set', label: '集合函数', desc: '面向对象集合的聚合计算', color: 'violet' },
    { id: 'action_validation', label: '校验函数', desc: 'Action 执行前业务校验', color: 'red' },
  ];

  const returnTypes: PropertyType[] = ['string', 'number', 'boolean', 'date', 'datetime', 'array', 'object'];

  const [name, setName] = useState(existingFn?.name || '');
  const [displayName, setDisplayName] = useState(existingFn?.displayName || '');
  const [description, setDescription] = useState(existingFn?.description || '');
  const [functionType, setFunctionType] = useState<FunctionType>(existingFn?.functionType || 'object');
  const [targetObjectTypeId, setTargetObjectTypeId] = useState(existingFn?.targetObjectTypeId || '');
  const [targetActionId, setTargetActionId] = useState(existingFn?.targetActionId || '');
  const [parameters, setParameters] = useState<FunctionParameter[]>(existingFn?.parameters || []);
  type FnReturnType = PropertyType | 'object' | 'object_set' | 'void' | 'validation_result';
  const [returnType, setReturnType] = useState<FnReturnType>(existingFn?.returnType || 'string');
  // 新建默认 expression：后端权威引擎（派生重算/动作校验/哨兵联动）只执行 expression；
  // typescript 仅前端模拟。旧函数无 language 字段的视为 typescript（其函数体是 TS 语句）。
  const [language, setLanguage] = useState<FunctionLanguage>(
    existingFn ? (existingFn.language || 'typescript') : 'expression');
  const [body, setBody] = useState(existingFn?.body || defaultBodyFor(functionType, language));
  const [cacheStrategy, setCacheStrategy] = useState<FunctionCacheStrategy>(existingFn?.cacheStrategy || 'none');
  const [cacheTTL, setCacheTTL] = useState<number>(existingFn?.cacheTTL || 60);

  // 代码模板
  function defaultBodyFor(ft: FunctionType, lang: FunctionLanguage = 'expression'): string {
    if (lang === 'expression') {
      switch (ft) {
        case 'object':
          return '(object.unit_price or 0) * (object.quantity or 1)';
        case 'object_set':
          return '[o for o in objects if o.status == "active"]';
        case 'action_validation':
          return '(params.quantity or 0) <= 100';
        default:
          return 'object.value';
      }
    }
    switch (ft) {
      case 'object':
        return '// object: 当前对象实例的属性\n// params: 传入的参数\n// 示例: 计算总价\nreturn (object.unit_price || 0) * (object.quantity || 1);';
      case 'object_set':
        return '// objectSet: 绑定类型的所有实例数组\n// params: 传入的参数\n// 示例: 过滤低库存\nreturn (objectSet || []).filter(item => item.status === "active");';
      case 'action_validation':
        return '// object: 目标对象（可选）\n// params: Action 参数\n// 返回 { valid: boolean, message?: string } 或 boolean\n// 示例: 校验库存\nif (params.quantity > 100) return { valid: false, message: "数量超过上限" };\nreturn { valid: true };';
      default:
        return '// return your value here';
    }
  }

  useEffect(() => {
    if (existingFn) {
      setName(existingFn.name);
      setDisplayName(existingFn.displayName);
      setDescription(existingFn.description || '');
      setFunctionType(existingFn.functionType);
      setTargetObjectTypeId(existingFn.targetObjectTypeId || '');
      setTargetActionId(existingFn.targetActionId || '');
      setParameters(existingFn.parameters);
      setReturnType(existingFn.returnType);
      setBody(existingFn.body);
      setLanguage(existingFn.language || 'typescript');
      setCacheStrategy(existingFn.cacheStrategy || 'none');
      setCacheTTL(existingFn.cacheTTL || 60);
    } else {
      setBody(defaultBodyFor(functionType, language));
    }

  }, [existingFn]);

  // 切换函数类型/语言时重置 body（仅新建时）
  useEffect(() => {
    if (mode === 'create' && !existingFn) {
      setBody(defaultBodyFor(functionType, language));
    }

  }, [functionType, language]);

  const handleSave = () => {
    if (!name || !displayName) return;
    const payload = {
      name, displayName, description, functionType,
      targetObjectTypeId: (functionType === 'object' || functionType === 'object_set') ? targetObjectTypeId : undefined,
      targetActionId: functionType === 'action_validation' ? targetActionId : undefined,
      parameters,
      returnType: returnType,
      body,
      language,
      enabled: true,
      cacheStrategy,
      cacheTTL: cacheStrategy === 'ttl' ? cacheTTL : undefined,
    };
    if (mode === 'create') {
      addFunction(payload);
    } else if (selectedId) {
      updateFunction(selectedId, payload);
    }
    onClose();
  };

  const handleDelete = () => {
    if (selectedId && confirm('确定删除这个函数吗？引用它的派生属性和规则会被禁用。')) {
      deleteFunction(selectedId);
      onClose();
    }
  };

  const handleTest = () => {
    onClose();
    if (selectedId) openFunctionTester(selectedId);
    else {
      // 先应用到画布再测试
      const newId = addFunction({
        name, displayName, description, functionType,
        targetObjectTypeId: (functionType === 'object' || functionType === 'object_set') ? targetObjectTypeId : undefined,
        targetActionId: functionType === 'action_validation' ? targetActionId : undefined,
        parameters,
        returnType: returnType as PropertyType | 'void' | 'object_set',
        body,
        language,
        enabled: true,
        cacheStrategy,
        cacheTTL: cacheStrategy === 'ttl' ? cacheTTL : undefined,
      });
      openFunctionTester(newId);
    }
  };

  const needObjectTarget = functionType === 'object' || functionType === 'object_set';
  const needActionTarget = functionType === 'action_validation';

  return (
    <>
      <PanelHeader title={mode === 'create' ? '创建函数' : '编辑函数'} onClose={onClose} />

      <div className="flex-1 overflow-y-auto p-6 space-y-5">
        {/* 基本信息 */}
        <div className="space-y-4">
          <div>
            <label className="input-label">函数名称 *</label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value.replace(/\s/g, '_').toLowerCase())}
              className="input-field font-mono"
              placeholder="function_name"
            />
          </div>
          <div>
            <label className="input-label">显示名称 *</label>
            <input
              type="text"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              className="input-field"
              placeholder="函数显示名称"
            />
          </div>
          <div>
            <label className="input-label">描述</label>
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              className="input-field resize-none h-16"
              placeholder="描述这个函数的用途..."
            />
          </div>
        </div>

        {/* 函数类型 */}
        <div className="space-y-2">
          <label className="input-label">函数类型 *</label>
          <div className="grid grid-cols-2 gap-2">
            {functionTypes.map(ft => (
              <button
                key={ft.id}
                onClick={() => setFunctionType(ft.id)}
                className={`p-3 rounded-lg border text-left transition-all ${
                  functionType === ft.id
                    ? `border-${ft.color}-500/50 bg-${ft.color}-500/10`
                    : 'border-surface-700 bg-surface-800/50 hover:bg-surface-700/50'
                }`}
                style={functionType === ft.id ? {
                  borderColor: ft.color === 'cyan' ? '#06b6d480' : ft.color === 'violet' ? '#8b5cf680' : ft.color === 'red' ? '#ef444480' : '#10b98180',
                  backgroundColor: ft.color === 'cyan' ? '#06b6d41a' : ft.color === 'violet' ? '#8b5cf61a' : ft.color === 'red' ? '#ef44441a' : '#10b9811a',
                } : {}}
              >
                <div className={`text-sm font-medium ${
                  functionType === ft.id
                    ? ft.color === 'cyan' ? 'text-cyan-400' : ft.color === 'violet' ? 'text-violet-400' : ft.color === 'red' ? 'text-red-400' : 'text-emerald-400'
                    : 'text-surface-200'
                }`}>{ft.label}</div>
                <div className="text-[11px] text-surface-500 mt-0.5 leading-tight">{ft.desc}</div>
              </button>
            ))}
          </div>
        </div>

        {/* 绑定目标 */}
        {needObjectTarget && (
          <div>
            <label className="input-label">绑定对象实体 *</label>
            <select
              value={targetObjectTypeId}
              onChange={(e) => setTargetObjectTypeId(e.target.value)}
              className="select-field"
            >
              <option value="">选择对象实体</option>
              {ontology?.objectTypes.map(ot => (
                <option key={ot.id} value={ot.id}>{ot.displayName}</option>
              ))}
            </select>
          </div>
        )}
        {needActionTarget && (
          <div>
            <label className="input-label">绑定动作 *</label>
            <select
              value={targetActionId}
              onChange={(e) => setTargetActionId(e.target.value)}
              className="select-field"
            >
              <option value="">选择动作</option>
              {ontology?.actions.map(a => (
                <option key={a.id} value={a.id}>{a.displayName}</option>
              ))}
            </select>
          </div>
        )}

        {/* 返回类型 */}
        <div>
          <label className="input-label">返回类型</label>
          <select
            value={returnType}
            onChange={(e) => setReturnType(e.target.value as PropertyType | 'void')}
            className="select-field"
          >
            <option value="void">void (无返回值)</option>
            {returnTypes.map(t => (
              <option key={t} value={t}>{t}</option>
            ))}
          </select>
        </div>

        {/* 参数 */}
        <FunctionParameterEditor parameters={parameters} onChange={setParameters} />

        {/* 执行语言 */}
        <div className="space-y-2">
          <label className="input-label">执行语言</label>
          <div className="flex gap-2">
            <button
              onClick={() => setLanguage('expression')}
              className={`flex-1 px-3 py-2 rounded-lg border text-sm text-left transition-all ${
                language === 'expression'
                  ? 'border-cyan-500/60 bg-cyan-500/10 text-cyan-300'
                  : 'border-surface-700 text-surface-400 hover:border-surface-500'
              }`}
            >
              <div className="font-medium">expression（推荐）</div>
              <div className="text-[11px] opacity-80">后端权威执行：派生属性自动重算、动作校验、哨兵联动都依赖它</div>
            </button>
            <button
              onClick={() => setLanguage('typescript')}
              className={`flex-1 px-3 py-2 rounded-lg border text-sm text-left transition-all ${
                language === 'typescript'
                  ? 'border-amber-500/60 bg-amber-500/10 text-amber-300'
                  : 'border-surface-700 text-surface-400 hover:border-surface-500'
              }`}
            >
              <div className="font-medium">typescript</div>
              <div className="text-[11px] opacity-80">仅前端模拟执行；后端（保存重算/真实动作）不会运行它</div>
            </button>
          </div>
        </div>

        {/* 函数体 */}
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <label className="input-label mb-0">函数体 ({language === 'expression' ? '单个表达式' : 'TypeScript'})</label>
            <div className="text-[11px] text-surface-500">安全沙箱执行</div>
          </div>
          <textarea
            value={body}
            onChange={(e) => setBody(e.target.value)}
            className="w-full h-56 bg-surface-950 border border-surface-700 rounded-lg px-3 py-2 font-mono text-xs text-surface-200 resize-none focus:outline-none focus:border-cyan-500/50 leading-relaxed"
            spellCheck={false}
            placeholder={language === 'expression' ? '单个表达式，如 (object.price or 0) * 1.13' : '在此编写 TypeScript 代码...'}
          />
          <div className="flex items-start gap-2 p-2 bg-surface-800/50 rounded-lg text-[11px] text-surface-500 leading-relaxed">
            <BeakerIcon className="w-4 h-4 flex-shrink-0 mt-0.5 text-cyan-500/70" />
            {language === 'expression' ? (
              <div>
                <div><b>可用变量：</b> <code className="text-cyan-400">object</code>（当前对象属性）、<code className="text-cyan-400">params</code>（参数）、<code className="text-cyan-400">objects</code>（对象集合，集合函数）、<code className="text-cyan-400">utils</code>（sum/avg/count/min/max/round/contains…）</div>
                <div><b>写法：</b> 一个表达式即返回值，如 <code>object.score &gt; 80</code>、<code>utils.sum([o.amount for o in objects])</code>；支持 <code>and/or/not</code> 与三元 <code>x if cond else y</code>。</div>
              </div>
            ) : (
              <div>
                <div><b>可用变量：</b> <code className="text-cyan-400">object</code>（当前对象）、<code className="text-cyan-400">params</code>（参数）、<code className="text-cyan-400">objectSet</code>（对象集合，集合函数）、<code className="text-cyan-400">context</code>（上下文，含 allInstances）</div>
                <div><b>返回：</b> 使用 <code>return value;</code> 返回结果。校验函数返回 <code>{'{ valid: boolean, message?: string }'}</code>。<span className="text-amber-400">注意：TS 函数只在前端模拟运行。</span></div>
              </div>
            )}
          </div>
        </div>

        {/* 缓存策略 */}
        <div className="space-y-2">
          <label className="input-label">缓存策略</label>
          <div className="flex gap-2">
            {(['none', 'ttl'] as FunctionCacheStrategy[]).map(s => (
              <button
                key={s}
                onClick={() => setCacheStrategy(s)}
                className={`flex-1 px-3 py-2 rounded-lg border text-sm transition-all ${
                  cacheStrategy === s
                    ? 'border-cyan-500/50 bg-cyan-500/10 text-cyan-400'
                    : 'border-surface-700 bg-surface-800/50 text-surface-400 hover:bg-surface-700/50'
                }`}
              >
                {s === 'none' ? '不缓存' : `TTL 缓存`}
              </button>
            ))}
          </div>
          {cacheStrategy === 'ttl' && (
            <div className="flex items-center gap-2">
              <input
                type="number"
                value={cacheTTL}
                onChange={(e) => setCacheTTL(parseInt(e.target.value) || 60)}
                className="input-field text-sm"
                min={1}
              />
              <span className="text-xs text-surface-500">秒</span>
            </div>
          )}
        </div>
      </div>

      <div className="flex items-center justify-between px-6 py-4 border-t border-surface-700">
        <div className="flex gap-2">
          {mode === 'edit' ? (
            <button onClick={handleDelete} className="btn-danger">
              <TrashIcon className="w-4 h-4" /> 删除
            </button>
          ) : <div />}
          {mode === 'edit' && (
            <button onClick={handleTest} className="btn-secondary text-emerald-400 hover:text-emerald-300">
              <PlayIcon className="w-4 h-4" /> 测试
            </button>
          )}
        </div>
        <button onClick={handleSave} className="btn-primary" disabled={!name || !displayName}>
          应用到画布
        </button>
      </div>
    </>
  );
}
