/**
 * 场景模型内容面板 — 构成三维场景的元素清单（对象 / 关系 / 事件 / 数据绑定）。
 * 与渲染引擎 DSL 一一对应；自详情页版式重排起拆为四个平铺区块，
 * 由 SceneDetailPage 的操作栏标签分别挂载，不再套圆角子卡片。
 */
import type { SceneDefinition } from '@/types/scene'
import { EmptyState } from '@/components/ui/LoadingState'

const KIND_LABELS: Record<string, string> = {
  flow: '能量流', dependency: '依赖', hierarchy: '层级',
}
const STATUS_CLASSES: Record<string, string> = {
  alarm: 'bg-red-50 text-red-700 dark:bg-red-950 dark:text-red-300',
  warning: 'bg-[var(--color-warning-bg)] text-[var(--color-warning)]',
  normal: 'bg-[var(--color-success-bg)] text-[var(--color-success)]',
  info: 'bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300',
}

/** 平铺区块头：细字重标题 + 计数，不带边框与圆角容器。 */
function BlockHead({ title, count }: { title: string; count?: number }) {
  return (
    <div className="mb-2 flex items-baseline gap-1.5 border-b border-[var(--color-border)] pb-1.5">
      <h3 className="text-sm font-semibold text-[var(--color-text-primary)]">{title}</h3>
      {count != null && <span className="text-xs tabular-nums text-slate-400">{count}</span>}
    </div>
  )
}

function None({ text }: { text: string }) {
  return <p className="text-xs text-[var(--color-text-tertiary)]">{text}</p>
}

export function ObjectsPanel({ objects }: { objects: NonNullable<SceneDefinition['objects']> }) {
  return (
    <div>
      <BlockHead title="对象" count={objects.length} />
      {objects.length === 0 ? (
        <None text="暂无对象" />
      ) : (
        <table className="w-full text-left text-xs">
          <thead className="text-[var(--color-text-tertiary)]">
            <tr>
              <th className="pb-2 pr-4 font-medium">ID</th>
              <th className="pb-2 pr-4 font-medium">名称</th>
              <th className="pb-2 pr-4 font-medium">类型</th>
              <th className="pb-2 pr-4 font-medium">尺寸 W×D×H</th>
              <th className="pb-2 pr-4 font-medium">装饰</th>
              <th className="pb-2 font-medium">概念</th>
            </tr>
          </thead>
          <tbody>
            {objects.map(obj => (
              <tr key={obj.id} className="border-t border-[var(--color-border)]">
                <td className="py-2 pr-4 font-mono text-[11px]">{obj.id}</td>
                <td className="py-2 pr-4">{obj.label}</td>
                <td className="py-2 pr-4">{obj.type}</td>
                <td className="py-2 pr-4 font-mono text-[11px]">
                  {obj.layout.w}×{obj.layout.d}×{obj.layout.h}
                </td>
                <td className="py-2 pr-4">{(obj.extras ?? []).join('、') || '—'}</td>
                <td className="py-2 font-mono text-[11px] text-teal-700 dark:text-teal-300">{obj.ontology_concept_id ?? '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}

export function RelationsPanel({ relations }: { relations: NonNullable<SceneDefinition['relations']> }) {
  return (
    <div>
      <BlockHead title="关系" count={relations.length} />
      {relations.length === 0 ? (
        <None text="暂无关系" />
      ) : (
        <ul className="text-xs">
          {relations.map((rel, index) => (
            <li key={index} className={'flex items-center gap-2 py-1.5 ' + (index > 0 ? 'border-t border-[var(--color-border)]' : '')}>
              <span className="font-mono text-[11px]">{rel.from}</span>
              <span className="text-[var(--color-text-tertiary)]">→</span>
              <span className="font-mono text-[11px]">{rel.to}</span>
              <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-600 dark:bg-slate-800 dark:text-slate-300">
                {KIND_LABELS[rel.kind ?? 'flow'] ?? rel.kind}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

export function EventsPanel({ events }: { events: NonNullable<SceneDefinition['events']> }) {
  return (
    <div>
      <BlockHead title="事件" count={events.length} />
      {events.length === 0 ? (
        <None text="暂无事件" />
      ) : (
        <table className="w-full text-left text-xs">
          <thead className="text-[var(--color-text-tertiary)]">
            <tr>
              <th className="pb-2 pr-4 font-medium">Key</th>
              <th className="pb-2 pr-4 font-medium">名称</th>
              <th className="pb-2 pr-4 font-medium">关联对象</th>
              <th className="pb-2 font-medium">描述</th>
            </tr>
          </thead>
          <tbody>
            {events.map(ev => (
              <tr key={ev.key} className="border-t border-[var(--color-border)]">
                <td className="py-2 pr-4 font-mono text-[11px]">{ev.key}</td>
                <td className="py-2 pr-4">{ev.label}</td>
                <td className="py-2 pr-4 font-mono text-[11px]">{ev.objectId || '—'}</td>
                <td className="py-2">{ev.description || '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}

export function BindingsPanel({ bindings }: { bindings: NonNullable<SceneDefinition['dataBindings']> }) {
  return (
    <div>
      <BlockHead title="数据绑定" count={bindings.length} />
      {bindings.length === 0 ? (
        <None text="暂无数据绑定" />
      ) : (
        <div className="space-y-3">
          {bindings.map((binding, index) => (
            <div key={index} className={index > 0 ? 'border-t border-[var(--color-border)] pt-2' : ''}>
              <div className="mb-1.5 flex flex-wrap items-center gap-2 text-xs">
                <span className="font-medium">目标</span>
                <span className="font-mono text-[11px]">{binding.target}</span>
                {binding.path && (
                  <>
                    <span className="font-medium">路径</span>
                    <span className="font-mono text-[11px]">{binding.path}</span>
                  </>
                )}
              </div>
              <ul className="space-y-1">
                {(binding.rules ?? []).map((rule, ruleIndex) => (
                  <li key={ruleIndex} className="flex items-center gap-2 text-xs">
                    <span className={'rounded px-1.5 py-0.5 text-[10px] font-medium ' + (STATUS_CLASSES[rule.status] ?? STATUS_CLASSES.info)}>
                      {rule.status === 'alarm' ? '告警' : rule.status === 'warning' ? '预警' : '正常'}
                    </span>
                    <span className="font-mono text-[11px] text-[var(--color-text-secondary)]">when {rule.when}</span>
                    {rule.message && <span className="text-[var(--color-text-secondary)]">{rule.message}</span>}
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

/** 兼容导出：定义缺失时的统一空态。 */
export function ModelsEmpty() {
  return (
    <EmptyState
      title="暂无场景定义"
      description="保存或生成场景定义后，这里将展示构成场景的全部元素"
    />
  )
}
