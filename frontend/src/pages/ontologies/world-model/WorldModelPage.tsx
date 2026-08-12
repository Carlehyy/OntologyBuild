import { Link, Navigate, useParams } from 'react-router-dom'
import { Boxes, ScrollText } from 'lucide-react'
import WorldModelListTab from './WorldModelListTab'
import WorldModelCallsTab from './WorldModelCallsTab'

const TABS = [
  { key: 'models', label: '推演模型', icon: Boxes, description: '演化模型的开发、调试与版本管理' },
  { key: 'calls', label: '调用记录', icon: ScrollText, description: '推演服务的调用审计（随发布能力开放后产生数据）' },
] as const

type TabKey = (typeof TABS)[number]['key']

export default function WorldModelPage() {
  const { tab = 'models' } = useParams<{ tab: string }>()
  if (!TABS.some(item => item.key === tab)) {
    return <Navigate to="/ontologies/world-model/models" replace />
  }
  const active = tab as TabKey

  return (
    <div className="flex min-h-full flex-col">
      <header className="mb-4">
        <h1 className="text-lg font-semibold text-[var(--color-text-primary)]">世界模型</h1>
        <p className="mt-0.5 text-xs text-[var(--color-text-tertiary)]">
          演化层能力：开发推演模型，回答「接下来可能发生什么，以及采取行动后会怎样」
        </p>
        <nav className="mt-3 flex gap-1 border-b border-slate-200" aria-label="世界模型子功能">
          {TABS.map(item => {
            const Icon = item.icon
            const isActive = item.key === active
            return (
              <Link
                key={item.key}
                to={`/ontologies/world-model/${item.key}`}
                title={item.description}
                className={`-mb-px inline-flex items-center gap-1.5 border-b-2 px-3 pb-2 pt-1 text-sm transition-colors ${
                  isActive
                    ? 'border-teal-600 font-medium text-teal-700'
                    : 'border-transparent text-[var(--color-text-tertiary)] hover:border-slate-300 hover:text-[var(--color-text-primary)]'
                }`}
                aria-current={isActive ? 'page' : undefined}
              >
                <Icon size={15} />
                {item.label}
              </Link>
            )
          })}
        </nav>
      </header>

      <div className="min-h-0 flex-1">
        {active === 'models' ? <WorldModelListTab /> : <WorldModelCallsTab />}
      </div>
    </div>
  )
}
