"""画布 → 图表的确定性生成（不经 LLM）

ER 图是对象模型的纯投影：对象→实体（属性行 + PK 标记），
relations→关系边（基数映射 mermaid 记号）。与转化管线同一哲学：
能确定性生成的绝不交给模型。
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


def _ident(name: str, fallback: str) -> str:
    """mermaid erDiagram 的实体/属性标识符：字母数字下划线与中文安全。"""
    s = re.sub(r"[^\w一-鿿]+", "_", (name or "").strip()).strip("_")
    return s or fallback


def er_mermaid(canvas) -> str:
    """对象模型 → mermaid erDiagram 文本。没有对象时返回空串。"""
    c = _ensure_canvas(canvas)
    objects = c["objects"]
    if not objects:
        return ""

    lines = ["erDiagram"]
    entity_by_name: dict[str, str] = {}
    for i, o in enumerate(objects):
        ent = _ident(o.get("name", ""), f"Object{i + 1}")
        entity_by_name[norm_name(o.get("name", ""))] = ent
        key_attr = norm_name(o.get("key_attribute") or "")
        attr_lines = []
        for j, a in enumerate(o.get("attributes") or []):
            aname = _ident(a.get("name", ""), f"field{j + 1}")
            atype = map_type_hint(a.get("type_hint"))
            pk = " PK" if key_attr and norm_name(a.get("name", "")) == key_attr else ""
            attr_lines.append(f"        {atype} {aname}{pk}")
        if attr_lines:
            lines.append(f"    {ent} {{")
            lines.extend(attr_lines)
            lines.append("    }")
        else:
            lines.append(f"    {ent} {{")
            lines.append("        string id PK")
            lines.append("    }")

    for o in objects:
        src = entity_by_name.get(norm_name(o.get("name", "")))
        for r in o.get("relations") or []:
            tgt = entity_by_name.get(norm_name(r.get("target", "")))
            if not src or not tgt:
                continue  # 悬空关系不进图 —— 与转化管线的 lint 同口径
            edge = _CARDINALITY_MERMAID.get((r.get("cardinality") or "").strip().lower(),
                                            _CARDINALITY_MERMAID["one-to-many"])
            label = (r.get("display_name") or r.get("name") or "关联").replace('"', "'")
            lines.append(f'    {src} {edge} {tgt} : "{label}"')

    return "\n".join(lines)
