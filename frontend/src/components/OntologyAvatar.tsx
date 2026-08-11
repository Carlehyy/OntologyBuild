import type { LucideIcon } from 'lucide-react'
import {
  Boxes,
  Building2,
  Cpu,
  Database,
  Factory,
  GraduationCap,
  HeartPulse,
  Landmark,
  Network,
  Scale,
  ShieldCheck,
  ShoppingCart,
  Users,
  Zap,
} from 'lucide-react'

export interface IconOption {
  key: string
  label: string
  icon: LucideIcon
  tone: string
}

export const ICON_OPTIONS: IconOption[] = [
  { key: 'network', label: '通用本体', icon: Network, tone: 'bg-teal-50 text-teal-600' },
  { key: 'boxes', label: '产品知识', icon: Boxes, tone: 'bg-blue-50 text-blue-600' },
  { key: 'shopping-cart', label: '采购电商', icon: ShoppingCart, tone: 'bg-emerald-50 text-emerald-600' },
  { key: 'factory', label: '生产制造', icon: Factory, tone: 'bg-orange-50 text-orange-600' },
  { key: 'heart-pulse', label: '医疗健康', icon: HeartPulse, tone: 'bg-rose-50 text-rose-600' },
  { key: 'landmark', label: '金融财务', icon: Landmark, tone: 'bg-amber-50 text-amber-600' },
  { key: 'scale', label: '法律合规', icon: Scale, tone: 'bg-violet-50 text-violet-600' },
  { key: 'graduation-cap', label: '教育培训', icon: GraduationCap, tone: 'bg-indigo-50 text-indigo-600' },
  { key: 'cpu', label: '科技研发', icon: Cpu, tone: 'bg-cyan-50 text-cyan-600' },
  { key: 'zap', label: '能源动力', icon: Zap, tone: 'bg-yellow-50 text-yellow-600' },
  { key: 'shield-check', label: '风控安全', icon: ShieldCheck, tone: 'bg-red-50 text-red-600' },
  { key: 'users', label: '组织客户', icon: Users, tone: 'bg-sky-50 text-sky-600' },
  { key: 'database', label: '数据资产', icon: Database, tone: 'bg-purple-50 text-purple-600' },
  { key: 'building-2', label: '企业架构', icon: Building2, tone: 'bg-lime-50 text-lime-700' },
]

const DEFAULT_ICON = ICON_OPTIONS[0]

export function iconOption(key?: string) {
  return ICON_OPTIONS.find(option => option.key === key) ?? DEFAULT_ICON
}

export function OntologyAvatar({ icon, size = 'md' }: { icon?: string; size?: 'md' | 'lg' }) {
  const option = iconOption(icon)
  const Icon = option.icon
  return (
    <div
      className={`${size === 'lg' ? 'h-14 w-14 rounded-2xl' : 'h-11 w-11 rounded-xl'} ${option.tone} flex shrink-0 items-center justify-center`}
      aria-hidden="true"
    >
      <Icon size={size === 'lg' ? 26 : 21} strokeWidth={1.8} />
    </div>
  )
}
