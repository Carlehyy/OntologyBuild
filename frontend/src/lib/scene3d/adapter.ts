/**
 * 平台场景定义（SceneDefinition）→ 引擎包（EnginePackage）纯函数转换。
 *
 * objects 布局字段平移（layout.x/z/w/d/h → 扁平 x/z/w/d/h）；
 * kind=flow 的 relations 转为 flows 二元组；stage 透传并给白模规范缺省值；
 * dataBindings 原样映射。
 */
import type { SceneDefinition } from '@/types/scene'
import type {
  EngineBindingDef,
  EngineBuildingDef,
  EnginePackage,
  EngineSceneCfg,
} from './engine'

/** 白模视觉规范默认值（与引擎内 DEFAULT_* 一致）。 */
export const WHITE_TWIN_DEFAULTS = {
  background: '#edf0f5',
  camera: { pos: [92, 78, 92], target: [0, 0, -4], fov: 30 },
  floor: { size: 260, gridCell: 8, gridColor: '#dde3ec' },
} as const

function toFlows(definition: SceneDefinition): [string, string][] {
  return (definition.relations ?? [])
    .filter(rel => (rel.kind ?? 'flow') === 'flow')
    .map(rel => [rel.from, rel.to])
}

export function definitionToEnginePackage(definition: SceneDefinition): EnginePackage {
  const buildings: EngineBuildingDef[] = definition.objects.map(obj => ({
    id: obj.id,
    label: obj.label,
    type: obj.type,
    x: obj.layout.x,
    z: obj.layout.z,
    w: obj.layout.w,
    d: obj.layout.d,
    h: obj.layout.h,
    ...(obj.extras?.length ? { extras: [...obj.extras] } : {}),
    ...(obj.beacon === false ? { beacon: false } : {}),
    ...(obj.info ? {
      info: {
        ...(obj.info.desc !== undefined ? { desc: obj.info.desc } : {}),
        ...(obj.info.metrics ? { metrics: obj.info.metrics.map(([k, v]) => [k, v]) } : {}),
      },
    } : {}),
  }))

  const stage = definition.stage ?? {}
  const scene: EngineSceneCfg = {
    background: stage.background ?? WHITE_TWIN_DEFAULTS.background,
    camera: stage.camera
      ? {
          pos: [...stage.camera.pos] as [number, number, number],
          target: [...stage.camera.target] as [number, number, number],
          fov: stage.camera.fov ?? WHITE_TWIN_DEFAULTS.camera.fov,
        }
      : {
          pos: [...WHITE_TWIN_DEFAULTS.camera.pos] as [number, number, number],
          target: [...WHITE_TWIN_DEFAULTS.camera.target] as [number, number, number],
          fov: WHITE_TWIN_DEFAULTS.camera.fov,
        },
    floor: stage.floor
      ? {
          size: stage.floor.size ?? WHITE_TWIN_DEFAULTS.floor.size,
          gridCell: stage.floor.gridCell ?? WHITE_TWIN_DEFAULTS.floor.gridCell,
        }
      : { ...WHITE_TWIN_DEFAULTS.floor },
    ambience: (stage.ambience ?? {}) as EngineSceneCfg['ambience'],
    buildings,
    flows: toFlows(definition),
  }

  const bindings: EngineBindingDef[] = (definition.dataBindings ?? []).map(b => ({
    target: b.target,
    source: b.source,
    ...(b.path !== undefined ? { path: b.path } : {}),
    ...(b.metrics ? { metrics: b.metrics.map(([k, v]) => [k, v]) } : {}),
    rules: b.rules.map(r => ({
      when: r.when,
      status: r.status,
      ...(r.message !== undefined ? { message: r.message } : {}),
    })),
  }))

  return {
    manifest: { ...definition.meta },
    scene,
    bindings,
  }
}
