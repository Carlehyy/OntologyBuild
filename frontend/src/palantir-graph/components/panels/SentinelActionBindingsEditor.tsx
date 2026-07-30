import type {
  Action,
  Property,
} from '../../types/ontology'
import {
  coerceSentinelConstant,
  isSentinelNumericType,
  normalizeSentinelParameterSource,
  sentinelConstantValue,
  sentinelParameterMode,
  sentinelParameterOptions,
} from './sentinelDefinitionCompiler'
import {
  SENTINEL_EVENT_PARAMETER_PROPERTIES,
  type SentinelDraft,
  type SentinelParameterMode,
} from './sentinelDefinitionModel'

interface SentinelActionBindingsEditorProps {
  draft: SentinelDraft
  actions: Action[]
  propertiesOf: (objectTypeId: string) => Property[]
  subjectLabel: (alias: string) => string
  onChange: (draft: SentinelDraft) => void
}

export function SentinelActionBindingsEditor({
  draft,
  actions,
  propertiesOf,
  subjectLabel,
  onChange,
}: SentinelActionBindingsEditorProps) {
  const setActionParameter = (
    actionId: string,
    parameterName: string,
    value: unknown | undefined,
  ) => {
    const actionParameters = { ...draft.actionParameters }
    const params = { ...(actionParameters[actionId] || {}) }
    if (value === undefined) delete params[parameterName]
    else params[parameterName] = value
    if (Object.keys(params).length > 0) actionParameters[actionId] = params
    else delete actionParameters[actionId]
    onChange({ ...draft, actionParameters })
  }

  return (
    <div className="rounded-lg border border-surface-700 p-3 space-y-1">
      <div className="text-surface-200 mb-1">命中后执行的动作<span className="text-[10px] text-surface-500 ml-1">（可多选，依次执行）</span></div>
      {actions.map(action => {
        const checked = draft.actionIds.includes(action.id)
        return (
          <div key={action.id} className="py-1">
            <label className="flex items-center gap-2 cursor-pointer">
              <input type="checkbox" checked={checked}
                onChange={event => {
                  const selected = new Set(draft.actionIds)
                  const actionParameters = { ...draft.actionParameters }
                  if (event.target.checked) selected.add(action.id)
                  else {
                    selected.delete(action.id)
                    delete actionParameters[action.id]
                  }
                  onChange({
                    ...draft,
                    actionIds: [...selected],
                    actionParameters,
                  })
                }} />
              <span className="text-surface-200">{action.displayName}</span>
            </label>
            {checked && (action.parameters || []).length > 0 && (
              <div className="ml-5 mt-2 space-y-2 rounded border border-surface-700 bg-surface-900/35 p-2">
                {(action.parameters || []).map(parameter => {
                  const spec = draft.actionParameters[action.id]?.[parameter.name]
                  const mode = sentinelParameterMode(spec)
                  const binding = (
                    spec && typeof spec === 'object' && !Array.isArray(spec)
                      ? spec as any
                      : {}
                  )
                  const hasDefault = Object.prototype.hasOwnProperty.call(
                    parameter,
                    'defaultValue',
                  )
                  const choices = sentinelParameterOptions(parameter)
                  const propertySelection = JSON.stringify([
                    binding.alias || draft.primaryAlias,
                    binding.property || '',
                  ])
                  const eventProperty = String(
                    binding.property
                    || binding.sourceValue
                    || (
                      normalizeSentinelParameterSource(binding) === 'edge'
                        ? 'edge'
                        : ''
                    ),
                  )
                  const parameterType = String(parameter.type).toLowerCase()
                  const rawConstant = sentinelConstantValue(spec)
                  const invalidNumericConstant = (
                    isSentinelNumericType(parameterType)
                    && typeof rawConstant === 'string'
                    && rawConstant !== ''
                  )
                  return (
                    <div key={parameter.id || parameter.name} className="space-y-1">
                      <div className="flex items-center justify-between gap-2">
                        <span className="text-surface-300">
                          {parameter.displayName || parameter.name}
                          {parameter.required && <span className="ml-0.5 text-red-400">*</span>}
                        </span>
                        <select className="inp-inline max-w-[210px]" value={mode}
                          onChange={event => {
                            const next = event.target.value as SentinelParameterMode
                            if (next === 'default') {
                              setActionParameter(
                                action.id,
                                parameter.name,
                                undefined,
                              )
                            } else if (next === 'property') {
                              const alias = (
                                draft.primaryAlias
                                || draft.bindings[0]?.alias
                              )
                              setActionParameter(action.id, parameter.name, {
                                sourceType: 'property',
                                alias,
                                property: propertiesOf(
                                  draft.bindings.find(
                                    item => item.alias === alias,
                                  )?.objectTypeId || '',
                                )[0]?.name || '',
                              })
                            } else if (next === 'primary_id') {
                              setActionParameter(action.id, parameter.name, {
                                sourceType: 'primary_id',
                              })
                            } else if (next === 'event') {
                              setActionParameter(action.id, parameter.name, {
                                sourceType: 'event',
                                property: 'edge',
                              })
                            } else if (next === 'template') {
                              const alias = (
                                draft.primaryAlias
                                || draft.bindings[0]?.alias
                              )
                              const property = propertiesOf(
                                draft.bindings.find(
                                  item => item.alias === alias,
                                )?.objectTypeId || '',
                              )[0]?.name || 'property'
                              setActionParameter(
                                action.id,
                                parameter.name,
                                `{{${alias}.${property}}}`,
                              )
                            } else if (next === 'advanced') {
                              // 只读保留模式：选择项本身不重写未知的旧配置。
                              return
                            } else {
                              setActionParameter(action.id, parameter.name, {
                                sourceType: 'constant',
                                value: parameter.defaultValue ?? (
                                  String(parameter.type).toLowerCase()
                                    .includes('bool')
                                    ? false
                                    : ''
                                ),
                              })
                            }
                          }}>
                          <option value="default">
                            {hasDefault ? `使用默认值（${String(parameter.defaultValue)}）` : '不传此参数'}
                          </option>
                          <option value="property">取命中对象属性</option>
                          <option value="constant">固定值</option>
                          <option value="primary_id">主对象实例 ID</option>
                          <option value="event">事件上下文</option>
                          <option value="template">字符串模板</option>
                          {mode === 'advanced' && (
                            <option value="advanced">高级配置（原样保留）</option>
                          )}
                        </select>
                      </div>

                      {mode === 'property' && (
                        <select className="inp" value={propertySelection}
                          onChange={event => {
                            const [alias, property] = JSON.parse(
                              event.target.value,
                            )
                            setActionParameter(action.id, parameter.name, {
                              sourceType: 'property',
                              alias,
                              property,
                            })
                          }}>
                          {draft.bindings.flatMap(item =>
                            propertiesOf(item.objectTypeId).map(property => (
                              <option key={`${item.alias}:${property.name}`}
                                value={JSON.stringify([item.alias, property.name])}>
                                {subjectLabel(item.alias)} · {property.displayName}
                              </option>
                            )),
                          )}
                        </select>
                      )}

                      {mode === 'event' && (
                        <select className="inp" value={eventProperty || 'edge'}
                          onChange={event => setActionParameter(
                            action.id,
                            parameter.name,
                            {
                              sourceType: 'event',
                              property: event.target.value,
                            },
                          )}>
                          {SENTINEL_EVENT_PARAMETER_PROPERTIES.map(
                            ([value, label]) => (
                              <option key={value} value={value}>{label}</option>
                            ),
                          )}
                        </select>
                      )}

                      {mode === 'template' && (
                        <input className="inp font-mono"
                          value={typeof spec === 'string' ? spec : ''}
                          onChange={event => setActionParameter(
                            action.id,
                            parameter.name,
                            event.target.value,
                          )}
                          placeholder={`如 {{${draft.primaryAlias}.property}}`} />
                      )}

                      {mode === 'advanced' && (
                        <div className="space-y-1">
                          <pre className="overflow-x-auto rounded bg-surface-950/70 p-2 text-[10px] text-amber-200">
                            {JSON.stringify(spec, null, 2)}
                          </pre>
                          <div className="text-[10px] text-amber-300">
                            该旧配置无法用结构化控件无损编辑；当前会原样保存。选择其他来源才会明确替换。
                          </div>
                        </div>
                      )}

                      {mode === 'constant' && choices.length > 0 && (
                        <select className="inp"
                          value={JSON.stringify(rawConstant) ?? 'null'}
                          onChange={event => setActionParameter(
                            action.id,
                            parameter.name,
                            {
                              sourceType: 'constant',
                              value: JSON.parse(event.target.value),
                            },
                          )}>
                          {choices.map((option, index) => (
                            <option
                              key={index}
                              value={JSON.stringify(option.value) ?? 'null'}
                            >
                              {option.label}
                            </option>
                          ))}
                        </select>
                      )}
                      {mode === 'constant' && choices.length === 0 && (
                        String(parameter.type).toLowerCase().includes('bool')
                          ? (
                              <select className="inp"
                                value={String(sentinelConstantValue(spec) ?? false)}
                                onChange={event => setActionParameter(
                                  action.id,
                                  parameter.name,
                                  {
                                    sourceType: 'constant',
                                    value: event.target.value === 'true',
                                  },
                                )}>
                                <option value="true">是</option>
                                <option value="false">否</option>
                              </select>
                            )
                          : (
                              <input className="inp"
                                value={
                                  typeof sentinelConstantValue(spec) === 'object'
                                    ? JSON.stringify(sentinelConstantValue(spec))
                                    : String(sentinelConstantValue(spec) ?? '')
                                }
                                onChange={event => setActionParameter(
                                  action.id,
                                  parameter.name,
                                  {
                                    sourceType: 'constant',
                                    value: coerceSentinelConstant(
                                      event.target.value,
                                      String(parameter.type),
                                    ),
                                  },
                                )}
                                placeholder={`输入${parameter.displayName || parameter.name}`} />
                            )
                      )}
                      {invalidNumericConstant && (
                        <div className="text-[10px] text-red-300">
                          数值格式无效；当前保留原文且发布/执行会被类型闸门阻断，请输入不带千位分隔符的有限数字。
                        </div>
                      )}
                      {mode === 'default' && parameter.required && !hasDefault && (
                        <div className="text-[10px] text-red-300">
                          必填参数尚未绑定；发布校验和正式执行都会阻断。
                        </div>
                      )}
                    </div>
                  )
                })}
              </div>
            )}
          </div>
        )
      })}
      {actions.length === 0 && <span className="text-surface-500">本体里还没有动作</span>}
    </div>
  )
}
