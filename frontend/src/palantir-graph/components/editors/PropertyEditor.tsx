import { useState } from 'react';
import { v4 as uuidv4 } from 'uuid';
import { PlusIcon, TrashIcon, KeyIcon } from '@heroicons/react/24/outline';
import type { Property, PropertyType } from '../../types/ontology';
import { useOntologyStore } from '../../store/ontologyStore';
import { sanitizeIdentifier } from '../../utils/identifier';

interface PropertyEditorProps {
  properties: Property[];
  onChange: (properties: Property[]) => void;
  primaryKey?: string;
  onPrimaryKeyChange?: (primaryKey: string) => void;
  showPrimaryKey?: boolean;
  allowComputed?: boolean;
  showDataBinding?: boolean;
  objectTypeId?: string;
}

const propertyTypes: { value: PropertyType; label: string }[] = [
  { value: 'string', label: '字符串' },
  { value: 'number', label: '数字' },
  { value: 'boolean', label: '布尔值' },
  { value: 'date', label: '日期' },
  { value: 'datetime', label: '日期时间' },
  { value: 'array', label: '数组' },
  { value: 'object', label: '对象' },
];

export default function PropertyEditor({
  properties,
  onChange,
  primaryKey,
  onPrimaryKeyChange,
  showPrimaryKey = true,
  allowComputed = true,
  showDataBinding = true,
  objectTypeId,
}: PropertyEditorProps) {
  const { ontology } = useOntologyStore();
  const propertyLabel = (prop: Property) => prop.displayName || prop.name;

  const addProperty = () => {
    const newProperty: Property = {
      id: uuidv4(),
      name: `property_${properties.length + 1}`,
      displayName: `属性 ${properties.length + 1}`,
      type: 'string',
      required: false,
      source: 'stored',
    };
    onChange([...properties, newProperty]);
  };

  const updateProperty = (id: string, updates: Partial<Property>) => {
    onChange(properties.map((p) => (p.id === id ? { ...p, ...updates } : p)));
  };

  const deleteProperty = (id: string) => {
    onChange(properties.filter((p) => p.id !== id));
  };

  const addValidation = (propertyId: string) => {
    updateProperty(propertyId, {
      validation: { min: undefined, max: undefined, pattern: '', enum: [] },
    });
  };

  const removeValidation = (propertyId: string) => {
    updateProperty(propertyId, { validation: undefined });
  };

  // 获取可用于派生属性绑定的 object functions
  const objectFunctions = ontology?.functions.filter(
    (f) => f.functionType === 'object'
  ) || [];

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h4 className="text-sm font-medium text-surface-300">属性定义</h4>
        <button
          onClick={addProperty}
          className="flex items-center gap-1 text-xs text-onto-400 hover:text-onto-300 transition-colors"
        >
          <PlusIcon className="w-4 h-4" />
          添加属性
        </button>
      </div>

      <div className="space-y-3">
        {properties.length === 0 ? (
          <div className="text-center py-8 text-surface-500 text-sm">
            暂无属性，点击上方按钮添加
          </div>
        ) : (
          properties.map((prop, index) => (
            <div
              key={prop.id}
              className={`rounded-lg border p-3 transition-colors ${
                showPrimaryKey && primaryKey === prop.id
                  ? 'border-onto-500/50 bg-onto-500/5'
                  : 'border-surface-700 bg-surface-800/30'
              }`}
            >
              <div className="flex items-start gap-2">
                <div className="flex-1 space-y-2">
                  <div className="grid grid-cols-2 gap-2">
                    <input
                      type="text"
                      value={prop.name}
                      onChange={(e) =>
                        updateProperty(prop.id, {
                          name: sanitizeIdentifier(e.target.value),
                        })
                      }
                      className="input-field text-xs font-mono py-1.5"
                      placeholder="属性标识"
                    />
                    <input
                      type="text"
                      value={propertyLabel(prop)}
                      onChange={(e) =>
                        updateProperty(prop.id, { displayName: e.target.value })
                      }
                      className="input-field text-xs py-1.5"
                      placeholder="显示名称"
                    />
                  </div>

                  <div className="grid grid-cols-2 gap-2">
                    <select
                      value={prop.type}
                      onChange={(e) =>
                        updateProperty(prop.id, {
                          type: e.target.value as PropertyType,
                          referenceType:
                            e.target.value === 'reference'
                              ? prop.referenceType || ''
                              : undefined,
                        })
                      }
                      className="select-field text-xs py-1.5"
                    >
                      {propertyTypes.map((type) => (
                        <option key={type.value} value={type.value}>
                          {type.label}
                        </option>
                      ))}
                    </select>

                    <div className="flex items-center gap-2">
                      <label className="flex items-center gap-1 text-xs text-surface-400">
                        <input
                          type="checkbox"
                          checked={prop.required}
                          onChange={(e) =>
                            updateProperty(prop.id, { required: e.target.checked })
                          }
                          className="rounded border-surface-600 bg-surface-800"
                        />
                        必填
                      </label>

                      {allowComputed && (
                        <label className="flex items-center gap-1 text-xs text-violet-400 cursor-pointer" title="派生属性：由函数计算得出">
                          <input
                            type="checkbox"
                            checked={(prop.source || 'stored') === 'computed'}
                            onChange={(e) =>
                              updateProperty(prop.id, {
                                source: e.target.checked ? 'computed' : 'stored',
                                functionId: e.target.checked ? prop.functionId : undefined,
                              })
                            }
                            className="rounded border-surface-600 bg-surface-800 accent-violet-500"
                          />
                          派生
                        </label>
                      )}

                      {showPrimaryKey && (
                        <button
                          onClick={() => onPrimaryKeyChange?.(prop.id)}
                          className={`flex items-center gap-1 px-2 py-0.5 rounded text-xs transition-colors ${
                            primaryKey === prop.id
                              ? 'bg-onto-500/20 text-onto-400'
                              : 'text-surface-500 hover:text-onto-400'
                          }`}
                          title="设为主键"
                        >
                          <KeyIcon className="w-3 h-3" />
                        </button>
                      )}
                    </div>
                  </div>

                  {/* 数据源字段绑定（只读血缘展示） */}
                  {showDataBinding && (
                    <div className="mt-2 text-[11px] text-surface-500 bg-surface-900/60 rounded px-2 py-1">
                      来源字段：
                      <span className="text-surface-300 font-mono">{prop.dataBinding?.sourceColumn || prop.name}</span>
                      {(prop.dataBinding?.inferred ?? !prop.dataBinding?.sourceColumn) && (
                        <span className="ml-1 text-amber-400">（按属性名推断）</span>
                      )}
                    </div>
                  )}

                  {/* 引用类型选择 */}
                  {prop.type === 'reference' && (
                    <select
                      value={prop.referenceType || ''}
                      onChange={(e) =>
                        updateProperty(prop.id, { referenceType: e.target.value })
                      }
                      className="select-field text-xs py-1.5"
                    >
                      <option value="">选择引用的对象实体</option>
                      {ontology?.objectTypes.map((ot) => (
                        <option key={ot.id} value={ot.id}>
                          {ot.displayName}
                        </option>
                      ))}
                    </select>
                  )}

                  {/* 派生属性：绑定函数 */}
                  {allowComputed && prop.source === 'computed' && (
                    <div className="bg-violet-500/10 border border-violet-500/30 rounded p-2 space-y-2">
                      <div className="text-xs text-violet-300 flex items-center gap-1">
                        <span className="w-1.5 h-1.5 rounded-full bg-violet-400 animate-pulse" />
                        派生属性（由函数计算）
                      </div>
                      <select
                        value={prop.functionId || ''}
                        onChange={(e) => updateProperty(prop.id, { functionId: e.target.value })}
                        className="select-field text-xs py-1.5"
                      >
                        <option value="">选择计算函数...</option>
                        {objectFunctions.map((f) => (
                          <option key={f.id} value={f.id}>
                            {f.displayName} ({f.name})
                          </option>
                        ))}
                      </select>
                      {objectFunctions.length === 0 && (
                        <div className="text-xs text-violet-400/70">
                          提示：需要先创建类型为「对象函数」的 Functions
                        </div>
                      )}
                    </div>
                  )}

                  {/* Validation Section */}
                  {!prop.validation ? (
                    <button
                      onClick={() => addValidation(prop.id)}
                      className="text-xs text-surface-500 hover:text-surface-300 transition-colors"
                    >
                      + 添加验证规则
                    </button>
                  ) : (
                    <div className="bg-surface-800/50 rounded p-2 space-y-2">
                      <div className="flex items-center justify-between">
                        <span className="text-xs text-surface-400">验证规则</span>
                        <button
                          onClick={() => removeValidation(prop.id)}
                          className="text-surface-500 hover:text-red-400"
                        >
                          <TrashIcon className="w-3 h-3" />
                        </button>
                      </div>

                      {(prop.type === 'number' ||
                        prop.type === 'string' ||
                        prop.type === 'array') && (
                        <div className="grid grid-cols-2 gap-2">
                          <input
                            type="number"
                            value={prop.validation.min ?? ''}
                            onChange={(e) =>
                              updateProperty(prop.id, {
                                validation: {
                                  ...prop.validation,
                                  min: e.target.value ? Number(e.target.value) : undefined,
                                },
                              })
                            }
                            className="input-field text-xs py-1"
                            placeholder="最小值"
                          />
                          <input
                            type="number"
                            value={prop.validation.max ?? ''}
                            onChange={(e) =>
                              updateProperty(prop.id, {
                                validation: {
                                  ...prop.validation,
                                  max: e.target.value ? Number(e.target.value) : undefined,
                                },
                              })
                            }
                            className="input-field text-xs py-1"
                            placeholder="最大值"
                          />
                        </div>
                      )}

                      {prop.type === 'string' && (
                        <input
                          type="text"
                          value={prop.validation.pattern ?? ''}
                          onChange={(e) =>
                            updateProperty(prop.id, {
                              validation: {
                                ...prop.validation,
                                pattern: e.target.value || undefined,
                              },
                            })
                          }
                          className="input-field text-xs py-1 font-mono"
                          placeholder="正则表达式 (如 ^[a-z]+$)"
                        />
                      )}
                    </div>
                  )}
                </div>

                <button
                  onClick={() => deleteProperty(prop.id)}
                  className="p-1 text-surface-500 hover:text-red-400 transition-colors flex-shrink-0"
                >
                  <TrashIcon className="w-4 h-4" />
                </button>
              </div>
            </div>
          ))
        )}
      </div>

      {showPrimaryKey && properties.length > 0 && (
        <div className="pt-2 border-t border-surface-700">
          <p className="text-xs text-surface-500">
            主键属性：
            <span className="text-onto-400 ml-1 font-mono">
              {properties.find((p) => p.id === primaryKey)?.name || '未设置'}
            </span>
          </p>
        </div>
      )}
    </div>
  );
}
