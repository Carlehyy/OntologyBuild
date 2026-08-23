/**
 * WhiteTwinEngine —— 浅色科技风三维白模数字孪生引擎（平台内部模块）。
 *
 * 移植自技能包 app/white-twin.js（Three.js r160），差异：
 *  - 输入改为进程内 EnginePackage（不再 fetch 场景目录 / postMessage / WebSocket）；
 *  - 事件经 onEvent(cb) 回调（select / statusChange），不再派发 window CustomEvent；
 *  - 所有动态文本用 textContent 渲染（原版面板指标用 innerHTML 拼插）。
 * 视觉规范照 skill-inner/threejs-white-twin/references/style-guide.md 默认值。
 */
import * as THREE from 'three'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'
import { CSS2DRenderer, CSS2DObject } from 'three/examples/jsm/renderers/CSS2DRenderer.js'

// ---------- 引擎包形态（与技能包 app/scenes/park 的 JSON 一致） ----------

export interface EngineBuildingDef {
  id?: string
  label: string
  type: string
  x: number
  z: number
  w: number
  d: number
  h: number
  extras?: string[]
  beacon?: boolean
  info?: {
    desc?: string
    metrics?: [string, string][]
  }
}

export interface EngineBindingRule {
  when: string
  status: 'normal' | 'warning' | 'alarm'
  message?: string
}

export interface EngineBindingDef {
  target: string
  source?: string
  path?: string
  metrics?: [string, string][]
  desc?: string
  rules: EngineBindingRule[]
}

export interface EngineSceneCfg {
  title?: string
  background?: string
  camera?: { pos: [number, number, number]; target: [number, number, number]; fov?: number }
  floor?: { size?: number; gridCell?: number; gridColor?: string }
  /** 其余氛围配置（sitePad / roads 等），原样透传给引擎装饰系统 */
  ambience?: {
    sitePad?: { w: number; h: number; radius?: number; x?: number; z?: number }
    roads?: { x: number; z: number; w: number; l: number; rot?: number }[]
    [key: string]: unknown
  }
  buildings: EngineBuildingDef[]
  flows?: [string, string][]
}

export interface EnginePackage {
  manifest: { id: string; name: string; version: string }
  scene: EngineSceneCfg
  bindings?: EngineBindingDef[]
}

export type EngineEvent =
  | { type: 'select'; id: string | null }
  | {
      type: 'statusChange'
      objectId: string
      level: 'normal' | 'warning' | 'alarm'
      message: string
      path?: string
      value?: number | null
    }

// ---------- 白模视觉规范常量 ----------

/** 状态色规范：normal 蓝 / warning 黄 / alarm 红 */
const STATUS = {
  normal: { hex: 0x3d7bfd, css: '#3d7bfd', emissive: 0 },
  warning: { hex: 0xffb020, css: '#ffb020', emissive: 0.18 },
  alarm: { hex: 0xff4d4f, css: '#ff4d4f', emissive: 0.32 },
} as const

type StatusLevel = keyof typeof STATUS

const DEFAULT_BACKGROUND = '#edf0f5'
const DEFAULT_CAMERA: {
  pos: [number, number, number]
  target: [number, number, number]
  fov: number
} = { pos: [92, 78, 92], target: [0, 0, -4], fov: 30 }
const DEFAULT_FLOOR = { size: 260, gridCell: 8, gridColor: '#dde3ec' } as const
const DEFAULT_SITE_PAD = { w: 120, h: 78, radius: 6, x: 0, z: -4 } as const
const BEACON_H = 9

const CSS = `
.wt-root { position: absolute; inset: 0; overflow: hidden;
  font-family: "PingFang SC", "Microsoft YaHei", "Helvetica Neue", Arial, sans-serif; }
.wt-root canvas { display: block; }
.wt-zoom { position: absolute; right: 18px; bottom: 90px; z-index: 10; display: flex;
  flex-direction: column; border-radius: 8px; overflow: hidden;
  box-shadow: 0 2px 10px rgba(90,110,140,.16); }
.wt-zoom button { width: 34px; height: 34px; border: none; background: rgba(255,255,255,.95);
  color: #5b6b83; font-size: 18px; cursor: pointer; }
.wt-zoom button + button { border-top: 1px solid #edf0f5; }
.wt-zoom button:hover { color: #3d7bfd; background: #fff; }
.b-label { pointer-events: none; padding: 5px 14px; border-radius: 6px;
  background: rgba(255,255,255,.95); color: #4a5a75; font-size: 13px; font-weight: 600;
  letter-spacing: 1px; white-space: nowrap; box-shadow: 0 3px 12px rgba(90,110,140,.18);
  transition: all .25s; }
.b-label.st-warning { color: #d89500; background: #fffcf2;
  box-shadow: 0 3px 14px rgba(255,176,32,.4); }
.b-label.st-alarm { color: #ff4d4f; background: #fff5f5;
  box-shadow: 0 3px 16px rgba(255,77,79,.45); }
.wt-panel { position: absolute; top: 64px; right: 18px; z-index: 10; width: 264px;
  border-radius: 12px; background: rgba(255,255,255,.95); backdrop-filter: blur(8px);
  box-shadow: 0 6px 24px rgba(90,110,140,.2); padding: 16px 18px;
  transform: translateX(300px); opacity: 0;
  transition: transform .28s cubic-bezier(.2,.9,.3,1.2), opacity .22s; }
.wt-panel.show { transform: translateX(0); opacity: 1; }
.wt-panel .ip-head { display: flex; justify-content: space-between; align-items: center;
  padding-bottom: 10px; border-bottom: 1px solid #eef1f6; }
.wt-panel .ip-title { font-size: 15px; font-weight: 700; color: #33415c; letter-spacing: 1px; }
.wt-panel .ip-title::before { content: ""; display: inline-block; width: 8px; height: 8px;
  border-radius: 2px; background: var(--st, #3d7bfd); margin-right: 8px; vertical-align: 1px; }
.wt-panel .ip-close { border: none; background: none; font-size: 18px; color: #9aa8bd;
  cursor: pointer; line-height: 1; padding: 2px 4px; }
.wt-panel .ip-close:hover { color: #3d7bfd; }
.wt-panel .ip-desc { margin: 10px 0 12px; font-size: 12px; color: #7d8ba1; line-height: 1.7; }
.wt-panel .ip-status { display: none; margin: 0 0 10px; padding: 6px 10px; border-radius: 8px;
  font-size: 12px; font-weight: 600; }
.wt-panel .ip-status.show { display: block; }
.wt-panel .ip-status.warning { color: #d89500; background: #fff8e6; }
.wt-panel .ip-status.alarm { color: #ff4d4f; background: #ffecec; }
.wt-panel .ip-metric { display: flex; justify-content: space-between; align-items: center;
  padding: 8px 10px; border-radius: 8px; background: #f5f7fb; margin-bottom: 6px; }
.wt-panel .m-name { font-size: 12px; color: #7d8ba1; }
.wt-panel .m-value { font-size: 15px; font-weight: 700; color: #3d7bfd;
  font-variant-numeric: tabular-nums; }
`

let cssInjected = false
function injectCSS() {
  if (cssInjected) return
  cssInjected = true
  const el = document.createElement('style')
  el.textContent = CSS
  document.head.appendChild(el)
}

// ---------- 声明式绑定 DSL 求值（纯函数，无副作用） ----------

/** 按 'a.b.c' 路径取值 */
function pathValue(data: unknown, path: string | undefined): unknown {
  if (!path) return undefined
  return String(path)
    .split('.')
    .reduce<unknown>((o, k) =>
      o !== null && typeof o === 'object' ? (o as Record<string, unknown>)[k] : undefined, data)
}

/**
 * 安全求值规则表达式：'> 95' / '>= 85' / '< 10' / '== text' / '!= a' / 'between 80 90'。
 * 非法表达式恒为 false，兜底交给 else 规则。
 */
export function evalCompare(expr: string, val: unknown): boolean {
  const m = String(expr).trim().match(/^(>=|<=|==|!=|>|<|between)\s+(.+)$/)
  if (!m) return false
  const [, op, rest] = m
  if (op === 'between') {
    const [a, b] = rest.split(/[\s,~]+/).map(Number)
    if (Number.isNaN(a) || Number.isNaN(b)) return false
    return Number(val) >= a && Number(val) <= b
  }
  const num = Number(rest)
  const rhs = Number.isNaN(num) ? rest.replace(/^['"]|['"]$/g, '') : num
  const lhs = Number.isNaN(num) ? String(val ?? '') : Number(val)
  switch (op) {
    case '>': return lhs > rhs
    case '>=': return lhs >= rhs
    case '<': return lhs < rhs
    case '<=': return lhs <= rhs
    case '==': return String(lhs) === String(rhs)
    case '!=': return String(lhs) !== String(rhs)
  }
  return false
}

/** 数值显示：整数原样，小数保留一位 */
function fmt(v: unknown): string {
  if (v === null || v === undefined) return '-'
  if (typeof v === 'number' && !Number.isInteger(v)) return String(+v.toFixed(1))
  return String(v)
}

interface BuildingEntry {
  cfg: EngineBuildingDef
  group: THREE.Group
  status: StatusLevel
  statusMsg: string
  beacon: {
    group: THREE.Group
    coneMat: THREE.ShaderMaterial
    ring: THREE.Mesh
    glow: THREE.Sprite
  } | null
  labelEl: HTMLDivElement | null
}

interface FlowDot {
  curve: THREE.QuadraticBezierCurve3
  dot: THREE.Sprite
  off: number
  speed: number
}

export class WhiteTwinEngine {
  private container: HTMLElement
  private eventCbs = new Set<(e: EngineEvent) => void>()

  private renderer!: THREE.WebGLRenderer
  private labelRenderer!: CSS2DRenderer
  private scene!: THREE.Scene
  private camera!: THREE.PerspectiveCamera
  private controls!: OrbitControls
  private clock = new THREE.Clock()
  private raf = 0
  private elapsed = 0
  private disposed = false

  private MAT!: {
    white: THREE.MeshStandardMaterial
    pad: THREE.MeshStandardMaterial
    road: THREE.MeshStandardMaterial
    gray: THREE.MeshStandardMaterial
    solar: THREE.MeshStandardMaterial
  }
  private glowCache = new Map<string, THREE.CanvasTexture>()
  private buildingMap = new Map<string, BuildingEntry>()
  private beacons: BuildingEntry['beacon'][] = []
  private flowDots: FlowDot[] = []
  private hover: BuildingEntry | null = null
  private sel: BuildingEntry | null = null
  private pickRoots: THREE.Object3D[] = []
  private raycaster = new THREE.Raycaster()
  private pointer = new THREE.Vector2()

  private pkg: EnginePackage | null = null
  private bindingDefs: EngineBindingDef[] = []
  /** 每条绑定上次命中的状态，用于只在变化时发 statusChange */
  private lastHits = new Map<number, { status: StatusLevel; message: string }>()

  constructor(container: HTMLElement) {
    injectCSS()
    this.container = container
    this.container.classList.add('wt-root')
  }

  /** 订阅引擎事件；返回取消订阅函数。 */
  onEvent(cb: (e: EngineEvent) => void): () => void {
    this.eventCbs.add(cb)
    return () => this.eventCbs.delete(cb)
  }

  private emit(e: EngineEvent) {
    this.eventCbs.forEach(cb => cb(e))
  }

  /** 加载标准产物包（进程内对象，非 URL）。 */
  loadPackage(pkg: EnginePackage): this {
    this.teardown()
    this.pkg = pkg
    this.build()
    this.applyBindings(pkg.bindings ?? [])
    this.animate()
    return this
  }

  /** 宿主向 client 型数据源推送数据（mock / 真实接口 / WebSocket 桥）。 */
  push(values: Record<string, unknown>): this {
    for (let i = 0; i < this.bindingDefs.length; i++) {
      const b = this.bindingDefs[i]
      if ((b.source ?? 'client') !== 'client') continue
      this.evalBinding(i, b, values)
    }
    return this
  }

  /** 程序化选中对象（不触发 select 事件）；传 null 取消选中。 */
  select(id: string | null): this {
    const entry = id ? this.buildingMap.get(id) : null
    if (id && !entry) return this
    this.selectInternal(entry ?? null, true)
    return this
  }

  get selected(): string | null {
    return this.sel?.cfg.id ?? null
  }

  list(): { id: string; label: string; type: string; status: StatusLevel }[] {
    return [...this.buildingMap.values()].map(e => ({
      id: e.cfg.id ?? e.cfg.label,
      label: e.cfg.label,
      type: e.cfg.type,
      status: e.status,
    }))
  }

  /** 容器尺寸变化时由宿主调用（ResizeObserver）。 */
  resize() {
    if (!this.renderer || this.disposed) return
    const w = this.container.clientWidth
    const h = this.container.clientHeight
    this.camera.aspect = w / h
    this.camera.updateProjectionMatrix()
    this.renderer.setSize(w, h)
    this.labelRenderer.setSize(w, h)
  }

  /** 销毁实例，释放 WebGL / DOM 资源。 */
  destroy() {
    this.disposed = true
    cancelAnimationFrame(this.raf)
    this.teardown()
    this.controls?.dispose()
    this.renderer?.dispose()
    this.glowCache.forEach(t => t.dispose())
    this.glowCache.clear()
    this.eventCbs.clear()
    this.container.classList.remove('wt-root')
  }

  private teardown() {
    cancelAnimationFrame(this.raf)
    if (this.scene) {
      this.scene.traverse((obj: THREE.Object3D) => {
        const mesh = obj as THREE.Mesh & { material?: THREE.Material | THREE.Material[] }
        mesh.geometry?.dispose()
        const mats = Array.isArray(mesh.material) ? mesh.material : [mesh.material]
        mats.forEach((m?: THREE.Material) => m?.dispose())
      })
    }
    this.container.innerHTML = ''
    this.buildingMap = new Map()
    this.beacons = []
    this.flowDots = []
    this.hover = null
    this.sel = null
    this.pickRoots = []
    this.bindingDefs = []
    this.lastHits.clear()
  }

  // ================= 场景构建 =================

  private build() {
    const cfg = this.pkg!.scene
    const C = this.container
    C.innerHTML = ''

    const bg = new THREE.Color(cfg.background || DEFAULT_BACKGROUND)
    this.scene = new THREE.Scene()
    this.scene.background = bg
    // 背景=雾同色，远景「消失于空气」
    this.scene.fog = new THREE.Fog(bg, 140, 320)

    const cam = cfg.camera ?? DEFAULT_CAMERA
    this.camera = new THREE.PerspectiveCamera(
      cam.fov ?? DEFAULT_CAMERA.fov, C.clientWidth / C.clientHeight, 0.1, 1000)
    this.camera.position.set(...cam.pos)

    this.renderer = new THREE.WebGLRenderer({ antialias: true })
    this.renderer.setSize(C.clientWidth, C.clientHeight)
    this.renderer.setPixelRatio(Math.min(devicePixelRatio, 2))
    this.renderer.shadowMap.enabled = true
    this.renderer.shadowMap.type = THREE.PCFSoftShadowMap
    this.renderer.outputColorSpace = THREE.SRGBColorSpace
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping
    this.renderer.toneMappingExposure = 1.15
    C.appendChild(this.renderer.domElement)

    this.labelRenderer = new CSS2DRenderer()
    this.labelRenderer.setSize(C.clientWidth, C.clientHeight)
    Object.assign(this.labelRenderer.domElement.style,
      { position: 'absolute', inset: '0', pointerEvents: 'none', zIndex: '5' })
    C.appendChild(this.labelRenderer.domElement)

    this.controls = new OrbitControls(this.camera, this.renderer.domElement)
    this.controls.target.set(...cam.target)
    Object.assign(this.controls, {
      enableDamping: true,
      dampingFactor: 0.06,
      maxPolarAngle: Math.PI * 0.46,
      minDistance: 30,
      maxDistance: 220,
    })
    this.controls.update()

    // 灯光：高亮度 + 柔和阴影（体积感来自柔影 + AO 式漫反射）
    this.scene.add(new THREE.HemisphereLight(0xffffff, 0xdde3ec, 1.0))
    const sun = new THREE.DirectionalLight(0xffffff, 2.2)
    sun.position.set(60, 90, 40)
    sun.castShadow = true
    sun.shadow.mapSize.set(2048, 2048)
    sun.shadow.radius = 8
    sun.shadow.bias = -0.0004
    Object.assign(sun.shadow.camera, { left: -110, right: 110, top: 110, bottom: -110, far: 300 })
    this.scene.add(sun, new THREE.AmbientLight(0xffffff, 0.35))

    // 高粗糙度是「黏土/3D 打印」质感的关键
    this.MAT = {
      white: new THREE.MeshStandardMaterial({ color: 0xfafbfc, roughness: .85, metalness: .02 }),
      pad: new THREE.MeshStandardMaterial({ color: 0xf4f6f9, roughness: .95 }),
      road: new THREE.MeshStandardMaterial({ color: 0xdde2ea, roughness: 1 }),
      gray: new THREE.MeshStandardMaterial({ color: 0xc9d2de, roughness: .9 }),
      solar: new THREE.MeshStandardMaterial({ color: 0x7ba2f8, roughness: .35, metalness: .25 }),
    }

    this.buildGround()
    ;(cfg.buildings ?? []).forEach(b => this.addBuilding(b))
    ;(cfg.ambience?.roads ?? []).forEach(r => {
      const road = new THREE.Mesh(new THREE.BoxGeometry(r.l, .12, r.w), this.MAT.road)
      road.position.set(r.x, .78, r.z)
      road.rotation.y = r.rot ?? 0
      road.receiveShadow = true
      this.scene.add(road)
    })
    this.buildFlows()
    this.buildInteraction()
    this.buildUI()
    this.clock = new THREE.Clock()
    this.elapsed = 0
  }

  private buildGround() {
    const cfg = this.pkg!.scene
    const floor = { ...DEFAULT_FLOOR, ...(cfg.floor ?? {}) }
    const amb = cfg.ambience ?? {}
    const cv = document.createElement('canvas')
    cv.width = cv.height = 256
    const g = cv.getContext('2d')!
    g.fillStyle = '#e9edf3'
    g.fillRect(0, 0, 256, 256)
    g.strokeStyle = floor.gridColor
    g.lineWidth = 1.4
    g.strokeRect(0, 0, 256, 256)
    g.strokeStyle = 'rgba(215,222,233,0.55)'
    g.lineWidth = 0.7
    for (let i = 64; i < 256; i += 64) {
      g.beginPath(); g.moveTo(i, 0); g.lineTo(i, 256); g.stroke()
      g.beginPath(); g.moveTo(0, i); g.lineTo(256, i); g.stroke()
    }
    const tex = new THREE.CanvasTexture(cv)
    tex.wrapS = tex.wrapT = THREE.RepeatWrapping
    tex.repeat.set(floor.size / floor.gridCell, floor.size / floor.gridCell)
    tex.colorSpace = THREE.SRGBColorSpace
    tex.anisotropy = 8
    const ground = new THREE.Mesh(
      new THREE.PlaneGeometry(floor.size, floor.size),
      new THREE.MeshStandardMaterial({ map: tex, roughness: 1 }))
    ground.rotation.x = -Math.PI / 2
    ground.receiveShadow = true
    this.scene.add(ground)

    const pad = amb.sitePad ?? DEFAULT_SITE_PAD
    const s = new THREE.Shape()
    const hw = pad.w / 2, hh = pad.h / 2, r = pad.radius ?? DEFAULT_SITE_PAD.radius
    s.moveTo(-hw + r, -hh)
    s.lineTo(hw - r, -hh); s.absarc(hw - r, -hh + r, r, -Math.PI / 2, 0)
    s.lineTo(hw, hh - r); s.absarc(hw - r, hh - r, r, 0, Math.PI / 2)
    s.lineTo(-hw + r, hh); s.absarc(-hw + r, hh - r, r, Math.PI / 2, Math.PI)
    s.lineTo(-hw, -hh + r); s.absarc(-hw + r, -hh + r, r, Math.PI, Math.PI * 1.5)
    const p = new THREE.Mesh(
      new THREE.ExtrudeGeometry(s, { depth: .7, bevelEnabled: false }), this.MAT.pad)
    p.rotation.x = -Math.PI / 2
    p.position.set(pad.x ?? 0, 0, pad.z ?? -4)
    p.receiveShadow = p.castShadow = true
    this.scene.add(p)
  }

  // ================= 程序化立面贴图 =================

  private facade(type: string, w: number, h: number): THREE.CanvasTexture {
    const cv = document.createElement('canvas')
    cv.width = Math.max(2, Math.round(w * 16))
    cv.height = Math.max(2, Math.round(h * 16))
    const g = cv.getContext('2d')!
    g.fillStyle = '#fafbfc'
    g.fillRect(0, 0, cv.width, cv.height)
    const u = cv.width / w
    if (type === 'office') {
      g.fillStyle = 'rgba(140,160,190,0.55)'
      const cw = 1.6 * u, ch = 1.9 * u, gx = 1.1 * u, gy = 1.0 * u
      for (let y = gy; y + ch < cv.height - gy * .5; y += ch + gy)
        for (let x = gx; x + cw < cv.width - gx * .5; x += cw + gx)
          g.fillRect(x, y, cw, ch)
    } else if (type === 'tower') {
      for (let x = 0; x < cv.width; x += 2.2 * u) {
        g.fillStyle = 'rgba(150,170,200,0.35)'
        g.fillRect(x, 0, 0.9 * u, cv.height)
      }
      g.fillStyle = 'rgba(140,160,190,0.5)'
      g.fillRect(0, 0, cv.width, 1.2 * u)
    } else if (type === 'warehouse') {
      g.fillStyle = 'rgba(150,168,195,0.3)'
      for (let y = 3 * u; y < cv.height; y += 4 * u) g.fillRect(0, y, cv.width, 0.5 * u)
      g.fillStyle = 'rgba(135,155,185,0.35)'
      g.fillRect(cv.width * .3, cv.height - 2.8 * u, 3.2 * u, 2.8 * u)
      g.fillRect(cv.width * .62, cv.height - 2.8 * u, 3.2 * u, 2.8 * u)
    } else if (type === 'podium') {
      g.fillStyle = 'rgba(140,160,190,0.5)'
      for (let y = 1.6 * u; y < cv.height - u; y += 3 * u)
        g.fillRect(1.2 * u, y, cv.width - 2.4 * u, 1.1 * u)
    } else if (type === 'plant') {
      for (let x = 0; x < cv.width; x += 1.2 * u) {
        g.fillStyle = 'rgba(150,170,198,0.28)'
        g.fillRect(x, 0, 0.4 * u, cv.height)
      }
    }
    const tex = new THREE.CanvasTexture(cv)
    tex.colorSpace = THREE.SRGBColorSpace
    tex.anisotropy = 4
    return tex
  }

  // ================= 建筑 =================

  private addBuilding(cfg: EngineBuildingDef) {
    if (!cfg.id) cfg.id = cfg.label
    const g = new THREE.Group()
    const mats: THREE.MeshStandardMaterial[] = []
    const { w, d, h, type } = cfg
    const topMat = this.MAT.white.clone()
    const grayMat = this.MAT.gray.clone()
    const mW = new THREE.MeshStandardMaterial({ map: this.facade(type, w, h), roughness: .85 })
    const mD = new THREE.MeshStandardMaterial({ map: this.facade(type, d, h), roughness: .85 })
    const body = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), [mD, mD, topMat, topMat, mW, mW])
    body.position.y = h / 2
    body.castShadow = body.receiveShadow = true
    g.add(body)
    // 细节增信三件套之一：女儿墙
    const parapet = new THREE.Mesh(new THREE.BoxGeometry(w + .35, .55, d + .35), topMat)
    parapet.position.y = h + .2
    parapet.castShadow = true
    g.add(parapet)
    // 屋顶设备箱
    if (type !== 'podium') {
      const n = 1 + Math.floor(Math.random() * 2)
      for (let i = 0; i < n; i++) {
        const bw = w * .22, bd = d * .26
        const box = new THREE.Mesh(new THREE.BoxGeometry(bw, .9, bd), grayMat)
        box.position.set((Math.random() - .5) * (w - bw) * .7, h + .55,
          (Math.random() - .5) * (d - bd) * .7)
        box.castShadow = true
        g.add(box)
      }
    }
    // tower 顶部造型框
    if (type === 'tower') {
      const frame = new THREE.Mesh(new THREE.BoxGeometry(w * .55, h * .22, d * .55), topMat)
      frame.position.y = h + h * .11
      frame.castShadow = true
      g.add(frame)
      const innerMat = new THREE.MeshStandardMaterial({ color: 0xdde4ee, roughness: .9 })
      const inner = new THREE.Mesh(new THREE.BoxGeometry(w * .38, h * .22 + .2, d * .38), innerMat)
      inner.position.y = h + h * .11
      g.add(inner)
      mats.push(innerMat)
    }
    mats.push(mW, mD, topMat, grayMat)
    g.userData.mats = mats
    g.userData.cfgId = cfg.id
    g.position.set(cfg.x, 0.7, cfg.z)
    this.scene.add(g)

    const entry: BuildingEntry = {
      cfg, group: g, status: 'normal', statusMsg: '', beacon: null, labelEl: null,
    }
    this.buildingMap.set(cfg.id, entry)
    this.pickRoots.push(g)

    if (cfg.beacon !== false) {
      const bc = this.createBeacon(cfg.label)
      bc.group.position.set(cfg.x, 0.7 + cfg.h + .5, cfg.z)
      bc.group.userData.cfgId = cfg.id
      this.scene.add(bc.group)
      entry.beacon = bc
      entry.labelEl = bc.labelEl
      this.beacons.push(bc)
      this.pickRoots.push(bc.group)
    }
    // 装饰：extras 枚举两种 parking / solar，由引擎消费
    if (cfg.extras?.includes('parking')) {
      ;[[-6, 3.4], [-3.6, 3.4], [-1.2, 3.4]].forEach(([ox, oz], i) => {
        const car = this.createCar(i ? 0xf2f5f9 : 0xffffff)
        car.position.set(cfg.x + ox, 0.7 + cfg.h, cfg.z + oz - cfg.d / 2 + 1)
        this.scene.add(car)
      })
    }
    if (cfg.extras?.includes('solar')) {
      for (let i = 0; i < 3; i++) {
        const panel = new THREE.Mesh(new THREE.BoxGeometry(3.2, .12, 2.2), this.MAT.solar)
        panel.rotation.x = -0.28
        panel.castShadow = true
        panel.position.set(cfg.x - cfg.w / 2 + 3 + i * 4, 0.7 + cfg.h + .5, cfg.z + cfg.d / 2 - 3)
        this.scene.add(panel)
      }
    }
  }

  // ================= 光柱 + 标签 =================

  private glowTex(cssColor: string): THREE.CanvasTexture {
    const cached = this.glowCache.get(cssColor)
    if (cached) return cached
    const cv = document.createElement('canvas')
    cv.width = cv.height = 128
    const g = cv.getContext('2d')!
    const grad = g.createRadialGradient(64, 64, 0, 64, 64, 64)
    grad.addColorStop(0, cssColor)
    grad.addColorStop(.35, cssColor + 'aa')
    grad.addColorStop(1, 'rgba(0,0,0,0)')
    g.fillStyle = grad
    g.fillRect(0, 0, 128, 128)
    const tex = new THREE.CanvasTexture(cv)
    this.glowCache.set(cssColor, tex)
    return tex
  }

  private createBeacon(label = '') {
    const group = new THREE.Group()
    const st = STATUS.normal
    const coneMat = new THREE.ShaderMaterial({
      transparent: true,
      depthWrite: false,
      side: THREE.DoubleSide,
      blending: THREE.AdditiveBlending,
      uniforms: { uColor: { value: new THREE.Color(st.hex) }, uTime: { value: 0 } },
      vertexShader: `varying float vY; void main(){ vY = uv.y;
        gl_Position = projectionMatrix * modelViewMatrix * vec4(position,1.); }`,
      fragmentShader: `uniform vec3 uColor; uniform float uTime; varying float vY;
        void main(){
          float a = pow(vY, 1.6) * .85;
          a *= .82 + .18 * sin(uTime * 2.2 + vY * 6.0);
          gl_FragColor = vec4(uColor, a);
        }`,
    })
    const cone = new THREE.Mesh(
      new THREE.CylinderGeometry(0.1, 1.1, BEACON_H, 24, 1, true), coneMat)
    cone.position.y = BEACON_H / 2
    group.add(cone)
    const glow = new THREE.Sprite(new THREE.SpriteMaterial({
      map: this.glowTex(st.css),
      transparent: true,
      depthWrite: false,
      blending: THREE.AdditiveBlending,
      opacity: .75,
    }))
    glow.scale.set(2.6, 2.6, 1)
    glow.position.y = .3
    group.add(glow)
    const ring = new THREE.Mesh(
      new THREE.RingGeometry(1.1, 1.45, 40),
      new THREE.MeshBasicMaterial({
        color: st.hex, transparent: true, opacity: .8,
        side: THREE.DoubleSide, depthWrite: false,
      }))
    ring.rotation.x = -Math.PI / 2
    ring.position.y = .12
    group.add(ring)
    let labelEl: HTMLDivElement | null = null
    if (label) {
      labelEl = document.createElement('div')
      labelEl.className = 'b-label'
      labelEl.textContent = label // 数据不进 HTML，杜绝注入
      const tag = new CSS2DObject(labelEl)
      tag.position.set(0, BEACON_H + 1.6, 0)
      group.add(tag)
    }
    return { group, coneMat, ring, glow, labelEl }
  }

  // ================= 小物件 =================

  private createCar(color = 0xffffff) {
    const car = new THREE.Group()
    const body = new THREE.Mesh(
      new THREE.BoxGeometry(1.9, .5, .9),
      new THREE.MeshStandardMaterial({ color, roughness: .5 }))
    body.position.y = .42
    body.castShadow = true
    const cabin = new THREE.Mesh(
      new THREE.BoxGeometry(1, .42, .8),
      new THREE.MeshStandardMaterial({ color: 0x9fb2cc, roughness: .3 }))
    cabin.position.set(-.1, .82, 0)
    cabin.castShadow = true
    car.add(body, cabin)
    return car
  }

  // ================= 能量流 =================

  private buildFlows() {
    const flows = this.pkg!.scene.flows
    if (!flows) return
    flows.forEach(([ia, ib], k) => {
      const A = this.buildingMap.get(ia)?.cfg
      const B = this.buildingMap.get(ib)?.cfg
      if (!A || !B) return
      const a = new THREE.Vector3(A.x, A.h + 2.5, A.z)
      const b = new THREE.Vector3(B.x, B.h + 2.5, B.z)
      const mid = a.clone().add(b).multiplyScalar(.5)
      mid.y = Math.max(a.y, b.y) + 7
      const curve = new THREE.QuadraticBezierCurve3(a, mid, b)
      const line = new THREE.Line(
        new THREE.BufferGeometry().setFromPoints(curve.getPoints(60)),
        new THREE.LineBasicMaterial({ color: 0x7ba2f8, transparent: true, opacity: .14 }))
      this.scene.add(line)
      for (let j = 0; j < 2; j++) {
        const dot = new THREE.Sprite(new THREE.SpriteMaterial({
          map: this.glowTex('#3d7bfd'),
          transparent: true,
          depthWrite: false,
          blending: THREE.AdditiveBlending,
        }))
        dot.scale.set(1.9, 1.9, 1)
        this.scene.add(dot)
        this.flowDots.push({ curve, dot, off: j * .5 + k * .2, speed: .12 })
      }
    })
  }

  // ================= 交互：拾取 + 高亮 + 面板 =================

  private applyEmissive(entry: BuildingEntry) {
    const st = STATUS[entry.status] ?? STATUS.normal
    const hex: number = st.hex
    let inten: number = st.emissive
    if (entry.status === 'normal') {
      if (this.sel === entry) inten = .16
      else if (this.hover === entry) inten = .07
    }
    const mats = entry.group.userData.mats as THREE.MeshStandardMaterial[]
    mats.forEach(m => {
      if (m.emissive) {
        m.emissive.setHex(hex)
        m.emissiveIntensity = inten
      }
    })
  }

  private buildInteraction() {
    const dom = this.renderer.domElement
    dom.addEventListener('pointermove', (e: PointerEvent) => {
      if (this.disposed) return
      const en = this.pickAt(e.clientX, e.clientY)
      if (en !== this.hover) {
        const prev = this.hover
        this.hover = en
        if (prev) this.applyEmissive(prev)
        if (en) this.applyEmissive(en)
        dom.style.cursor = en ? 'pointer' : ''
      }
    })
    let downXY: [number, number] | null = null
    dom.addEventListener('pointerdown', (e: PointerEvent) => { downXY = [e.clientX, e.clientY] })
    dom.addEventListener('pointerup', (e: PointerEvent) => {
      if (!downXY) return
      const moved = Math.hypot(e.clientX - downXY[0], e.clientY - downXY[1])
      downXY = null
      if (moved > 5) return
      this.selectInternal(this.pickAt(e.clientX, e.clientY))
    })
  }

  private pickAt(x: number, y: number): BuildingEntry | null {
    this.pointer.set(
      (x / this.container.clientWidth) * 2 - 1,
      -(y / this.container.clientHeight) * 2 + 1)
    this.raycaster.setFromCamera(this.pointer, this.camera)
    const hits = this.raycaster.intersectObjects(this.pickRoots, true)
    let o: THREE.Object3D | null = hits[0]?.object ?? null
    while (o) {
      if (o.userData?.cfgId) return this.buildingMap.get(o.userData.cfgId as string) ?? null
      o = o.parent
    }
    return null
  }

  /** 面板文本全部走 textContent，数据永不进 HTML */
  private showPanel(entry: BuildingEntry) {
    const p = this.container.querySelector('.wt-panel')
    if (!p) return
    const cfg = entry.cfg
    const set = (sel: string, text: string) => {
      const el = p.querySelector(sel)
      if (el) el.textContent = text
    }
    set('.ip-title', cfg.label || cfg.id || '')
    ;(p as HTMLElement).style.setProperty('--st', STATUS[entry.status].css)
    const stEl = p.querySelector('.ip-status') as HTMLElement | null
    if (stEl) {
      if (entry.status !== 'normal' && entry.statusMsg) {
        stEl.textContent = entry.statusMsg
        stEl.className = `ip-status show ${entry.status}`
      } else {
        stEl.className = 'ip-status'
      }
    }
    set('.ip-desc', cfg.info?.desc ?? '')
    const wrap = p.querySelector('.ip-metrics')
    if (wrap) {
      wrap.textContent = ''
      for (const [name, rawValue] of cfg.info?.metrics ?? []) {
        const row = document.createElement('div')
        row.className = 'ip-metric'
        const nameEl = document.createElement('span')
        nameEl.className = 'm-name'
        nameEl.textContent = name
        const valueEl = document.createElement('span')
        valueEl.className = 'm-value'
        valueEl.textContent = fmt(rawValue)
        valueEl.style.color = STATUS[entry.status].css
        row.append(nameEl, valueEl)
        wrap.appendChild(row)
      }
    }
    p.classList.add('show')
  }

  private selectInternal(entry: BuildingEntry | null, silent = false) {
    const prev = this.sel
    this.sel = entry
    if (prev) this.applyEmissive(prev)
    if (entry) {
      this.applyEmissive(entry)
      this.showPanel(entry)
      if (!silent) this.emit({ type: 'select', id: entry.cfg.id ?? entry.cfg.label })
    } else {
      this.container.querySelector('.wt-panel')?.classList.remove('show')
      if (!silent) this.emit({ type: 'select', id: null })
    }
  }

  // ================= UI =================

  private buildUI() {
    const C = this.container
    const title = this.pkg!.scene.title
    if (title) {
      const el = document.createElement('div')
      el.className = 'wt-title'
      el.style.cssText = 'position:absolute;top:18px;left:50%;transform:translateX(-50%);' +
        'z-index:10;color:#8b99b0;font-size:14px;letter-spacing:4px;user-select:none;'
      el.textContent = title
      C.appendChild(el)
    }
    const panel = document.createElement('div')
    panel.className = 'wt-panel'
    const head = document.createElement('div')
    head.className = 'ip-head'
    const titleEl = document.createElement('span')
    titleEl.className = 'ip-title'
    const closeBtn = document.createElement('button')
    closeBtn.className = 'ip-close'
    closeBtn.type = 'button'
    closeBtn.textContent = '×'
    closeBtn.setAttribute('aria-label', '关闭详情')
    closeBtn.onclick = () => this.selectInternal(null)
    head.append(titleEl, closeBtn)
    const statusEl = document.createElement('div')
    statusEl.className = 'ip-status'
    const descEl = document.createElement('div')
    descEl.className = 'ip-desc'
    const metricsEl = document.createElement('div')
    metricsEl.className = 'ip-metrics'
    panel.append(head, statusEl, descEl, metricsEl)
    C.appendChild(panel)

    const cam = this.pkg!.scene.camera ?? DEFAULT_CAMERA
    const zoom = document.createElement('div')
    zoom.className = 'wt-zoom'
    const dolly = (f: number) => {
      const v = this.camera.position.clone().sub(this.controls.target).multiplyScalar(f)
      this.camera.position.copy(this.controls.target).add(v)
    }
    const mkBtn = (text: string, fontSize: number, onClick: () => void) => {
      const btn = document.createElement('button')
      btn.type = 'button'
      btn.textContent = text
      btn.style.fontSize = `${fontSize}px`
      btn.onclick = onClick
      zoom.appendChild(btn)
    }
    mkBtn('+', 18, () => dolly(.85))
    mkBtn('−', 18, () => dolly(1.18))
    mkBtn('⌂', 14, () => {
      this.camera.position.set(...cam.pos)
      this.controls.target.set(...cam.target)
    })
    C.appendChild(zoom)
  }

  // ================= 状态与声明式绑定执行器 =================

  /** 设置对象状态：驱动光柱颜色、建筑泛色、标签样式、面板状态条 */
  setStatus(id: string, status: StatusLevel = 'normal', message = ''): this {
    const e = this.buildingMap.get(id)
    if (!e || !STATUS[status]) return this
    e.status = status
    e.statusMsg = message
    const st = STATUS[status]
    if (e.beacon) {
      e.beacon.coneMat.uniforms.uColor.value.setHex(st.hex)
      ;(e.beacon.ring.material as THREE.MeshBasicMaterial).color.setHex(st.hex)
      e.beacon.glow.material.map = this.glowTex(st.css)
      e.beacon.glow.material.needsUpdate = true
    }
    e.labelEl?.classList.toggle('st-warning', status === 'warning')
    e.labelEl?.classList.toggle('st-alarm', status === 'alarm')
    this.applyEmissive(e)
    if (this.sel === e) this.showPanel(e)
    return this
  }

  /** 更新对象的展示数据（desc / metrics），选中时实时刷新面板 */
  setData(id: string, info: { desc?: string; metrics?: [string, string][] }): this {
    const e = this.buildingMap.get(id)
    if (!e) return this
    e.cfg.info = { ...(e.cfg.info ?? {}), ...info }
    if (this.sel === e) this.showPanel(e)
    return this
  }

  private applyBindings(bindings: EngineBindingDef[]) {
    this.bindingDefs = bindings
    this.lastHits.clear()
  }

  private evalBinding(index: number, b: EngineBindingDef, data: unknown) {
    const val = pathValue(data, b.path)
    let status: StatusLevel = 'normal'
    let message = ''
    // 自上而下首中生效；else 为兜底
    for (const r of b.rules ?? []) {
      if (r.when === 'else' || evalCompare(r.when, val)) {
        status = r.status
        message = r.message ?? ''
        break
      }
    }
    const render = (s: string) => typeof s === 'string'
      ? s.replaceAll('{value}', fmt(val))
         .replace(/\{path:([^}]+)\}/g, (_, p: string) => fmt(pathValue(data, p)))
      : s
    this.setData(b.target, {
      metrics: b.metrics?.map(([n, t]) => [n, render(t)] as [string, string]),
      desc: b.desc !== undefined ? render(b.desc) : undefined,
    })
    this.setStatus(b.target, status, message)
    const prev = this.lastHits.get(index)
    if (!prev || prev.status !== status || prev.message !== message) {
      this.lastHits.set(index, { status, message })
      const numeric = typeof val === 'number' ? val : Number(val)
      this.emit({
        type: 'statusChange',
        objectId: b.target,
        level: status,
        message,
        path: b.path,
        value: val === undefined ? null : Number.isNaN(numeric) ? null : numeric,
      })
    }
  }

  // ================= 主循环 =================

  private animate() {
    const step = () => {
      if (this.disposed) return
      this.raf = requestAnimationFrame(step)
      const dt = Math.min(this.clock.getDelta(), .05)
      this.elapsed += dt
      const t = this.elapsed
      this.beacons.forEach((b, i) => {
        if (!b) return
        b.coneMat.uniforms.uTime.value = t + i
        const s = 1 + 0.25 * ((t * .8 + i * .3) % 1)
        b.ring.scale.setScalar(s)
        ;(b.ring.material as THREE.MeshBasicMaterial).opacity = .8 * (1 - ((t * .8 + i * .3) % 1))
      })
      this.flowDots.forEach(f => {
        const u = (t * f.speed + f.off) % 1
        f.dot.position.copy(f.curve.getPoint(u))
        f.dot.material.opacity = Math.sin(u * Math.PI) * .9
      })
      this.controls.update()
      this.renderer.render(this.scene, this.camera)
      this.labelRenderer.render(this.scene, this.camera)
    }
    step()
  }
}
