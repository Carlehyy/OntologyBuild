/**
 * 场景模型标签 — 构成三维场景的元素清单：对象 / 关系 / 数据绑定。
 * 与渲染引擎 DSL 一一对应（白模三件套），本标签只读。
 */
import type { SceneDefinition } from '@/types/scene'
import { EmptyState } from '@/components/ui/LoadingState'

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="rounded-xl border border-[var(--color-border)] bg-card p-4">
      <h3 className="mb-3 text-sm font-semibold text-[var(--color-text-primary)]">{title}</h3>
      {children}
    </section>
  )
}

const KIND_LABELS: Record<string, string> = {
  flow: '能量流', dependency: '依赖', hierarchy: '层级',
}
const STATUS_CLASSES: Record<string, string> = {
  alarm: 'bg-red-50 text-red-700 dark:bg-red-950 dark:text-red-300',
  warning: 'bg-[var(--color-warning-bg)] text-[var(--color-warning)]',
  normal: 'bg-[var(--color-success-bg)] text-[var(--color-success)]',
  info: 'bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300',
}

export function ModelsTab({ definition }: { definition: SceneDefinition | null | undefined }) {
  if (definition == null) {
    return (
      <div className="rounded-xl border border-[var(--color-border)] bg-card p-10">
        <EmptyState title="暂无场景定义" description="保存或生成场景定义后，这里将展示构成场景的全部元素" />
      </div>
    )
  }
  const objects = definition.objects ?? []
  const relations = definition.relations ?? []
  const bindings = definition.dataBindings ?? []
  return (
    <div className="space-y-4">
      <Section title={'对象（' + objects.length + '）'}>
        {objects.length === 0 ? (
          <p className="text-xs text-[var(--color-text-tertiary)]">暂无对象</p>
        ) : (
          <table className="w-full text-left text-xs">
            <thead className="text-[var(--color-text-tertiary)]">
              <tr>
                <th className="pb-2 pr-4 font-medium">ID</th>
                <th className="pb-2 pr-4 font-medium">名称</th>
                <th className="pb-2 pr-4 font-medium">类型</th>
                <th className="pb-2 pr-4 font-medium">尺寸 W×D×H</th>
                <th className="pb-2 font-medium">装饰</th>
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
                  <td className="py-2">{(obj.extras ?? []).join('、') || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Section>

      <Section title={'关系（' + relations.length + '）'}>
        {relations.length === 0 ? (
          <p className="text-xs text-[var(--color-text-tertiary)]">暂无关系</p>
        ) : (
          <ul className="space-y-1.5 text-xs">
            {relations.map((rel, index) => (
              <li key={index} className="flex items-center gap-2">
                <span className="font-mono text-[11px]">{rel.from}</span>
                <span className="text-[var(--color-text-tertiary)">→</span>
                <span className="font-mono text-[11px]">{rel.to}</span>
                <span className="rounded-full bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-600 dark:bg-slate-800 dark:text-slate-300">
                  {KIND_LABELS[rel.kind ?? 'flow'] ?? rel.kind}
                </span>
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section title={'数据绑定（' + bindings.length + '）'}>
        {bindings.length === 0 ? (
          <p className="text-xs text-[var(--color-text-tertiary)]">暂无数据绑定</p>
        ) : (
          <div className="space-y-3">
            {bindings.map((binding, index) => (
              <div key={index} className="rounded-lg border border-[var(--color-border)] p-3">
                <div className="mb-2 flex items-center gap-2 text-xs">
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
      </Section>
    </div>
  )
}
