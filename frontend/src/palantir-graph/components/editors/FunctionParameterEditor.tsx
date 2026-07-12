import { PlusIcon, TrashIcon } from '@heroicons/react/24/outline';
import type { FunctionParameter, PropertyType } from '../../types/ontology';

interface FunctionParameterEditorProps {
  parameters: FunctionParameter[];
  onChange: (parameters: FunctionParameter[]) => void;
}

const typeOptions: PropertyType[] = ['string', 'number', 'boolean', 'date', 'datetime', 'array', 'object'];

export default function FunctionParameterEditor({ parameters, onChange }: FunctionParameterEditorProps) {
  const addParameter = () => {
    const newParam: FunctionParameter = {
      id: `fp-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
      name: '',
      type: 'string',
      required: true,
    };
    onChange([...parameters, newParam]);
  };

  const updateParameter = (id: string, updates: Partial<FunctionParameter>) => {
    onChange(parameters.map((p) => (p.id === id ? { ...p, ...updates } : p)));
  };

  const removeParameter = (id: string) => {
    onChange(parameters.filter((p) => p.id !== id));
  };

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <label className="input-label mb-0">参数列表</label>
        <button
          onClick={addParameter}
          className="flex items-center gap-1 text-xs text-cyan-400 hover:text-cyan-300 transition-colors"
        >
          <PlusIcon className="w-3.5 h-3.5" />
          添加参数
        </button>
      </div>

      {parameters.length === 0 ? (
        <div className="text-center py-4 bg-surface-900/50 rounded-lg border border-dashed border-surface-700">
          <p className="text-sm text-surface-500">无参数（点击上方按钮添加）</p>
        </div>
      ) : (
        <div className="space-y-2">
          {parameters.map((param, index) => (
            <div
              key={param.id}
              className="p-3 bg-surface-900/50 rounded-lg border border-surface-700 space-y-2"
            >
              <div className="flex items-center gap-2">
                <span className="text-xs text-surface-500 w-5">#{index + 1}</span>
                <input
                  type="text"
                  value={param.name}
                  onChange={(e) => updateParameter(param.id, { name: e.target.value.replace(/\s/g, '_').toLowerCase() })}
                  className="input-field flex-1 text-sm py-1.5 font-mono"
                  placeholder="param_name"
                />
                <select
                  value={param.type}
                  onChange={(e) => updateParameter(param.id, { type: e.target.value as PropertyType })}
                  className="select-field text-sm py-1.5 w-28"
                >
                  {typeOptions.map((t) => (
                    <option key={t} value={t}>{t}</option>
                  ))}
                </select>
                <label className="flex items-center gap-1 text-xs text-surface-400 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={param.required}
                    onChange={(e) => updateParameter(param.id, { required: e.target.checked })}
                    className="accent-cyan-500"
                  />
                  必填
                </label>
                <button
                  onClick={() => removeParameter(param.id)}
                  className="p-1 text-surface-500 hover:text-red-400 transition-colors"
                >
                  <TrashIcon className="w-4 h-4" />
                </button>
              </div>
              <input
                type="text"
                value={param.description || ''}
                onChange={(e) => updateParameter(param.id, { description: e.target.value })}
                className="input-field text-xs py-1"
                placeholder="参数描述..."
              />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
