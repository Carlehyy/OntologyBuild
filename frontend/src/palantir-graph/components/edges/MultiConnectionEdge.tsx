import { BaseEdge, type EdgeProps } from '@xyflow/react';

/**
 * 支持"平行边"的自定义边。
 *
 * 两个对象实体之间可以存在多条实体关系，若都走同一对端口会完全重合。
 * 这里按每条边在其平行组中的偏移量 `data.__offset`（有符号，已做方向归一），
 * 沿两端连线的法线方向弯出不同弧度的二次贝塞尔曲线，使若干关系分列展开、互不遮挡。
 *
 * - offset === 0            → 退化为直线（组内只有一条边时）
 * - offset 对称展开(±)      → 多条边以中线为轴向两侧均匀散开
 * - 标签落在各自曲线顶点     → 多条关系的名称也不会相互压盖
 * - source === target(自环) → 特殊绘制为节点上方的环，避免直线穿过节点体
 */
export default function MultiConnectionEdge({
  sourceX,
  sourceY,
  targetX,
  targetY,
  source,
  target,
  markerEnd,
  label,
  selected,
  data,
  style,
}: EdgeProps) {
  const offset = (data?.__offset as number | undefined) ?? 0;

  const stroke = selected ? '#22d3ee' : '#06b6d4';
  const edgeStyle = {
    ...style,
    stroke,
    strokeWidth: selected ? 2.6 : 2,
  };
  const labelBgStyle = {
    fill: '#0f172a',
    fillOpacity: 0.92,
    stroke: selected ? '#22d3ee' : '#334155',
    strokeWidth: 1,
  };
  const labelStyle = { fill: '#e2e8f0', fontSize: 11, fontWeight: 500 };

  // 自环：从右端口向上绕回左端口。多条自环用有符号 offset 做水平错位
  // （组内各不相同，保证不重叠），高度随 |offset| 略增，外圈更高。
  if (source === target) {
    const h = 66 + Math.abs(offset) * 0.6;
    const path = `M ${sourceX},${sourceY} C ${sourceX + 30 + offset},${sourceY - h} ${targetX - 30 + offset},${targetY - h} ${targetX},${targetY}`;
    const labelX = (sourceX + targetX) / 2 + offset;
    const labelY = Math.min(sourceY, targetY) - h * 0.82;
    return (
      <BaseEdge
        path={path}
        markerEnd={markerEnd}
        style={edgeStyle}
        interactionWidth={24}
        label={label}
        labelX={labelX}
        labelY={labelY}
        labelShowBg
        labelBgPadding={[8, 4]}
        labelBgBorderRadius={7}
        labelBgStyle={labelBgStyle}
        labelStyle={labelStyle}
      />
    );
  }

  const dx = targetX - sourceX;
  const dy = targetY - sourceY;
  const dist = Math.hypot(dx, dy) || 1;
  // 连线的单位法线向量
  const nx = -dy / dist;
  const ny = dx / dist;

  const mx = (sourceX + targetX) / 2;
  const my = (sourceY + targetY) / 2;

  // 二次贝塞尔在 t=0.5 处仅到达控制点到中线距离的一半，
  // 因此控制点取 2*offset，曲线顶点恰好落在 offset 处。
  const cx = mx + nx * offset * 2;
  const cy = my + ny * offset * 2;
  const path = `M ${sourceX},${sourceY} Q ${cx},${cy} ${targetX},${targetY}`;

  // 标签放在曲线顶点，随弧度一起散开
  const labelX = mx + nx * offset;
  const labelY = my + ny * offset;

  return (
    <BaseEdge
      path={path}
      markerEnd={markerEnd}
      style={edgeStyle}
      interactionWidth={24}
      label={label}
      labelX={labelX}
      labelY={labelY}
      labelShowBg
      labelBgPadding={[8, 4]}
      labelBgBorderRadius={7}
      labelBgStyle={labelBgStyle}
      labelStyle={labelStyle}
    />
  );
}
