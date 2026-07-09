"""画布 → 图表的确定性生成（不经 LLM）

四种业务建模视图，全部是画布的纯投影 —— 与转化管线同一哲学：
能确定性生成的绝不交给模型，图与画布严格一致，agent 只负责"何时出图"。

  er        实体关系图：对象 + person/org 主体（它们都将转成 ObjectType）
  flow      业务流程图：某场景的步骤链（含判断分支形状启发）
  sequence  时序图：某场景中 主体→对象 的行为协作顺序
  state     状态图：某对象的枚举状态属性 + 从行为结果中识别的状态迁移

对话中 agent 通过 show_diagram 工具出图（步骤事件携带 mermaid，前端直接
渲染），让用户"看图挑错"—— 图形化误解暴露远快于文字确认。
"""
from __future__ import annotations

import re

from app.exploration.canvas import _ensure_canvas, norm_name
from app.exploration.converter import map_type_hint

_CARDINALITY_MERMAID = {
    "one-to-one": "||--||",
    "one-to-many": "||--o{",
    "many-to-one": "}o--||",
    "many-to-many": "}o--o{",
}

DIAGRAM_KINDS = {
    "er": "ER 图（实体关系）",
    "flow": "业务流程图",
    "sequence": "时序图（主体协作）",
    "state": "状态图（对象生命周期）",
}


class DiagramError(ValueError):
    """画布尚不具备生成该图的条件 —— 信息原样回给 agent/用户，指明先补什么。"""


def _ident(name: str, fallback: str) -> str:
    """mermaid 的实体/节点标识符：字母数字下划线与中文安全。"""
    s = re.sub(r"[^\w一-鿿]+", "_", (name or "").strip()).strip("_")
    return s or fallback


def _text(s: str, cap: int = 60) -> str:
    """节点/消息文本：去掉会破坏 mermaid 语法的字符。"""
    t = re.sub(r'[\[\]{}()<>"`;|]', " ", str(s or "")).strip()
    t = re.sub(r"\s+", " ", t)
    return (t[: cap - 1] + "…") if len(t) > cap else t


# ---------------------------------------------------------------- ER 图


def er_mermaid(canvas) -> str:
    """对象模型（含数据实体型主体，它们同样转成 ObjectType）→ mermaid erDiagram。"""
    c = _ensure_canvas(canvas)
    # person/org 主体是数据实体恒入图；role 主体仅在被关系引用时入图（避免悄悄丢边）；
    # system 主体不建对象不入图；与对象重名时以对象为准 —— 全部与转化管线口径一致
    obj_norms = {norm_name(o.get("name", "")) for o in c["objects"]}
    referenced = {norm_name(r.get("target", ""))
                  for o in c["objects"] for r in (o.get("relations") or [])}

    def actor_in_er(a: dict) -> bool:
        kind = a.get("kind") or "role"
        if kind == "system" or norm_name(a.get("name", "")) in obj_norms:
            return False
        return kind in ("person", "org") \
            or norm_name(a.get("name", "")) in referenced \
            or norm_name(a.get("display_name", "")) in referenced

    entities = list(c["objects"]) + [a for a in c["actors"] if actor_in_er(a)]
    if not entities:
        raise DiagramError("画布还没有对象模型 —— 先在对话中沉淀业务对象")

    lines = ["erDiagram"]
    entity_by_name: dict[str, str] = {}
    for i, o in enumerate(entities):
        ent = _ident(o.get("name", ""), f"Object{i + 1}")
        entity_by_name[norm_name(o.get("name", ""))] = ent
        if o.get("display_name"):   # 关系 target 允许写显示名
            entity_by_name.setdefault(norm_name(o["display_name"]), ent)
        key_attr = norm_name(o.get("key_attribute") or "")
        attr_lines = []
        for j, a in enumerate(o.get("attributes") or []):
            aname = _ident(a.get("name", ""), f"field{j + 1}")
            atype = map_type_hint(a.get("type_hint"))
            pk = " PK" if key_attr and norm_name(a.get("name", "")) == key_attr else ""
            attr_lines.append(f"        {atype} {aname}{pk}")
        lines.append(f"    {ent} {{")
        lines.extend(attr_lines or ["        string id PK"])
        lines.append("    }")

    for o in c["objects"]:
        src = entity_by_name.get(norm_name(o.get("name", "")))
        for r in o.get("relations") or []:
            tgt = entity_by_name.get(norm_name(r.get("target", "")))
            if not src or not tgt:
                continue  # 悬空关系不进图 —— 与质量门同口径
            edge = _CARDINALITY_MERMAID.get((r.get("cardinality") or "").strip().lower(),
                                            _CARDINALITY_MERMAID["one-to-many"])
            label = (r.get("display_name") or r.get("name") or "关联").replace('"', "'")
            lines.append(f'    {src} {edge} {tgt} : "{label}"')

    return "\n".join(lines)


# ---------------------------------------------------------------- 场景定位


def _pick_scenario(c: dict, target: str | None) -> dict:
    scenarios = c["scenarios"]
    if not scenarios:
        raise DiagramError("画布还没有场景模型 —— 先与用户确认一个端到端业务场景")
    if target:
        t = norm_name(target)
        for s in scenarios:
            if norm_name(s.get("name", "")) == t or norm_name(s.get("display_name", "")) == t:
                return s
        raise DiagramError(f"场景「{target}」不存在。已有场景: "
                           + ", ".join(str(s.get("display_name") or s.get("name")) for s in scenarios))
    return scenarios[0]


def _slabel(x: dict) -> str:
    return str(x.get("display_name") or x.get("name") or "?")


# ---------------------------------------------------------------- 业务流程图


def flow_mermaid(canvas, target: str | None = None) -> tuple[str, str]:
    """场景步骤 → mermaid flowchart。含「如果/若/是否」的步骤用菱形判断节点。"""
    c = _ensure_canvas(canvas)
    s = _pick_scenario(c, target)
    steps = s.get("steps") or []
    if not steps:
        raise DiagramError(f"场景「{_slabel(s)}」还没有步骤 —— 先与用户把流程步骤问清楚")

    beh_actor = {norm_name(b.get("name", "")): b.get("actor", "")
                 for b in c["behaviors"]}
    beh_display = {norm_name(b.get("name", "")): _slabel(b) for b in c["behaviors"]}

    lines = ["flowchart TD", f'    S0(["开始：{_text(s.get("goal") or _slabel(s), 30)}"])']
    prev = "S0"
    for i, raw in enumerate(steps, 1):
        text = _text(raw)
        nid = f"S{i}"
        # 步骤文本命中已定义行为 → 标注执行主体（图与模型互相印证）
        hit = next((n for n, disp in beh_display.items()
                    if (n and n in norm_name(text))
                    or (disp and disp != "?" and disp in text)), None)
        if hit and beh_actor.get(hit):
            text = f"{text}｜{beh_actor[hit]}"
        if any(k in raw for k in ("如果", "若", "是否")):
            lines.append(f'    {nid}{{"{text}"}}')
        else:
            lines.append(f'    {nid}["{text}"]')
        lines.append(f"    {prev} --> {nid}")
        prev = nid
    outcome = _text(s.get("expected_outcome") or "结束", 30)
    lines.append(f'    SE(["{outcome}"])')
    lines.append(f"    {prev} --> SE")
    return "\n".join(lines), _slabel(s)


# ---------------------------------------------------------------- 时序图


def sequence_mermaid(canvas, target: str | None = None) -> tuple[str, str]:
    """场景 behaviors 顺序 → mermaid sequenceDiagram（主体 →> 对象 : 行为）。"""
    c = _ensure_canvas(canvas)
    s = _pick_scenario(c, target)
    beh_by_name = {norm_name(b.get("name", "")): b for b in c["behaviors"]}
    for b in c["behaviors"]:
        if b.get("display_name"):
            beh_by_name.setdefault(norm_name(b["display_name"]), b)

    names = [norm_name(x) for x in (s.get("behaviors") or [])]
    picked = [beh_by_name[n] for n in names if n in beh_by_name]
    if not picked:
        raise DiagramError(f"场景「{_slabel(s)}」还没有关联行为（behaviors）——"
                           "先把场景涉及的行为补进场景模型，时序图才能反映协作顺序")

    participants: list[tuple[str, str]] = []   # (ident, display)
    seen: set[str] = set()

    def part(name: str, fallback: str) -> str:
        ident = _ident(name, fallback)
        if ident not in seen:
            seen.add(ident)
            participants.append((ident, _text(name or fallback, 20)))
        return ident

    body: list[str] = []
    for b in picked:
        actor = part(b.get("actor") or "未指明主体", "Actor")
        obj = part(b.get("object") or "未指明对象", "Object")
        label = _text(_slabel(b), 40)
        trig = _text(b.get("trigger") or "", 30)
        body.append(f"    {actor}->>+{obj}: {label}" + (f"（{trig}）" if trig else ""))
        outcome = _text(b.get("outcome") or "", 40)
        body.append(f"    {obj}-->>-{actor}: {outcome or '完成'}")

    lines = ["sequenceDiagram", "    autonumber"]
    lines += [f"    participant {i} as {d}" for i, d in participants]
    lines += body
    return "\n".join(lines), _slabel(s)


# ---------------------------------------------------------------- 状态图

_TRANSITION_RE = re.compile(
    r"(?:从\s*([^\s，。；;、]{1,20})\s*)?(?:变更为|变为|改为|置为|转为|流转到|->|→)\s*([^\s，。；;、]{1,20})")


def state_mermaid(canvas, target: str | None = None) -> tuple[str, str]:
    """对象的枚举状态属性 → mermaid stateDiagram；从行为结果文本识别显式迁移。"""
    c = _ensure_canvas(canvas)
    objects = c["objects"]
    if not objects:
        raise DiagramError("画布还没有对象模型")

    def status_attr(o: dict) -> dict | None:
        cands = [a for a in (o.get("attributes") or []) if a.get("enum")]
        for a in cands:
            label = f"{a.get('name', '')}{a.get('display_name', '')}".lower()
            if any(k in label for k in ("状态", "status", "state", "阶段", "stage")):
                return a
        return cands[0] if cands else None

    obj = None
    if target:
        t = norm_name(target)
        obj = next((o for o in objects
                    if norm_name(o.get("name", "")) == t
                    or norm_name(o.get("display_name", "")) == t), None)
        if obj is None:
            raise DiagramError(f"对象「{target}」不存在")
        if status_attr(obj) is None:
            raise DiagramError(f"对象「{_slabel(obj)}」没有枚举型状态属性 ——"
                               "先与用户确认它的状态字段与全部状态值（枚举）")
    else:
        obj = next((o for o in objects if status_attr(o)), None)
        if obj is None:
            raise DiagramError("没有任何对象拥有枚举型状态属性 —— 先确认哪个对象有生命周期状态")

    attr = status_attr(obj)
    states = [str(v) for v in (attr.get("enum") or [])]
    by_norm = {norm_name(v): v for v in states}
    ids = {v: f"st{i}" for i, v in enumerate(states)}

    lines = ["stateDiagram-v2"]
    for v in states:
        lines.append(f'    {ids[v]} : {_text(v, 20)}')
    if states:
        lines.append(f"    [*] --> {ids[states[0]]}")

    # 行为 outcome/描述/触发 里的显式迁移（…变为X / 从X变为Y / X→Y）
    edges: set[tuple[str, str, str]] = set()
    for b in c["behaviors"]:
        if norm_name(b.get("object", "")) not in (norm_name(obj.get("name", "")),
                                                  norm_name(obj.get("display_name", ""))):
            continue
        blob = "；".join(str(b.get(k) or "") for k in ("outcome", "description", "trigger"))
        for m in _TRANSITION_RE.finditer(blob):
            src, dst = m.group(1), m.group(2)
            dst_v = by_norm.get(norm_name(dst or ""))
            if not dst_v:
                continue
            src_v = by_norm.get(norm_name(src or ""))
            if src_v and src_v != dst_v:
                edges.add((ids[src_v], ids[dst_v], _slabel(b)))
    for src, dst, label in sorted(edges):
        lines.append(f"    {src} --> {dst} : {_text(label, 20)}")
    if not edges:
        lines.append("    %% 未能从行为结果中识别状态迁移，仅列出状态；")
        lines.append("    %% 在行为的 outcome 里写明「从X变为Y」即可自动连线")
    return "\n".join(lines), f"{_slabel(obj)} · {attr.get('display_name') or attr.get('name')}"


# ---------------------------------------------------------------- 统一入口


def build_diagram(canvas, kind: str, target: str | None = None) -> dict:
    """生成指定图表：{kind, title, target?, mermaid}。条件不足抛 DiagramError。"""
    k = (kind or "").strip().lower()
    if k not in DIAGRAM_KINDS:
        raise DiagramError(f"不支持的图表类型「{kind}」，可选: {', '.join(DIAGRAM_KINDS)}")
    if k == "er":
        return {"kind": "er", "title": DIAGRAM_KINDS["er"], "mermaid": er_mermaid(canvas)}
    if k == "flow":
        mermaid, scen = flow_mermaid(canvas, target)
        return {"kind": "flow", "title": f"{DIAGRAM_KINDS['flow']} · {scen}",
                "target": scen, "mermaid": mermaid}
    if k == "sequence":
        mermaid, scen = sequence_mermaid(canvas, target)
        return {"kind": "sequence", "title": f"{DIAGRAM_KINDS['sequence']} · {scen}",
                "target": scen, "mermaid": mermaid}
    mermaid, label = state_mermaid(canvas, target)
    return {"kind": "state", "title": f"{DIAGRAM_KINDS['state']} · {label}",
            "target": label, "mermaid": mermaid}
