import type { HeatCell } from '../hooks/useMockModels'

interface ModelHeatStripProps {
  cells: HeatCell[]
  /** 单格高度（px），默认 16 */
  height?: number
}

/**
 * 历史调用热力条 —— 每一格代表一次调用。
 * 绿色代表成功（越深延迟越低），琥珀 = 超时，红 = 失败，浅灰 = 无调用/已停用。
 */
export default function ModelHeatStrip({ cells, height = 16 }: ModelHeatStripProps) {
  return (
    // flex-1 让 60 格按容器宽度等分铺满；不设 minWidth，避免格子总宽超过卡片导致右侧溢出被裁切
    <div className="flex gap-px">
      {cells.map((c, i) => (
        <span
          key={i}
          title={c.title}
          className="flex-1 min-w-0 rounded-[1px]"
          style={{ height, background: c.color }}
        />
      ))}
    </div>
  )
}
