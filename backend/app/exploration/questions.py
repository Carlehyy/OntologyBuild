"""澄清账本（question ledger）— 把「不确定」变成可销账的显性负债

设计动机：探索对话的质量瓶颈不在"沉淀了多少"，而在"还有多少事悬而未决、
被模棱两可地带过"。账本把 agent 提出的每个关键问题登记为一条负债：

  - blocking（B 类）：企业特有口径，必须用户拍板 —— 阈值/枚举边界/审批线/
    级联策略/主键口径/基数。有任何一条未销账，质量门不放行草稿。
  - advisory（A 类）：行业常识，AI 先给建议值（suggestion），用户可改可确认。

销账（resolve）时强制「定量」：blocking 问题的结论若仍含模糊词且不含任何
数值/枚举选项，直接拒绝并把原因回填给 LLM —— 让它继续追问，而不是记下一句
"金额较大时需要审批"这类无法形式化的话。

账本存储在会话画布 JSON 的 questions 键下（随 canvas 事件推送、随文档/草稿
快照冻结），与六类模型元素同生命周期，天然可追溯。
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.exploration.canvas import norm_name

QUESTIONS_KEY = "questions"

KIND_BLOCKING = "blocking"
KIND_ADVISORY = "advisory"

# 模糊表述词表：出现任一且结论中无任何数量信息 → 视为未定量
_VAGUE_TERMS = (
    "大额", "小额", "大量", "少量", "较大", "较小", "较多", "较少", "很多", "很少",
    "及时", "尽快", "定期", "不定期", "长期", "短期", "频繁", "偶尔", "一段时间",
    "若干", "一些", "多次", "数次", "适当", "合理", "必要时", "视情况", "酌情",
    "大概", "大约", "差不多", "可能", "或许", "一般来说", "通常", "正常范围",
    "过高", "过低", "太久", "太多", "太少", "超时",
)
# 数量信号：阿拉伯/全角数字、中文数词（含"两"）、比较/枚举记号
_QUANT_RE = re.compile(r"[0-9０-９]|[一二两三四五六七八九十百千万亿]|[≥≤><=%％]")


def vague_terms_in(text: str) -> list[str]:
    """文本中命中的模糊词（无数量信号时才算未定量，见 is_quantified）。"""
    t = text or ""
    return [w for w in _VAGUE_TERMS if w in t]


def is_quantified(text: str) -> bool:
    """结论是否「定量」：没有模糊词，或虽有但同句含数值/比较/枚举信号。

    例：「大额指 ≥ 50000 元」→ 定量；「金额较大时需审批」→ 未定量。
    """
    t = (text or "").strip()
    if not t:
        return False
    if not vague_terms_in(t):
        return True
    return bool(_QUANT_RE.search(t))


class Question(BaseModel):
    """账本条目。宽容解析（同画布元素约定），LLM 给错字段名可自愈。"""
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: Optional[str] = None
    question: str
    kind: str = KIND_BLOCKING                 # blocking | advisory
    target: Optional[str] = None              # 关联元素名（或 元素名.字段）
    options: list[str] = Field(default_factory=list)   # 结构化候选，用户点选即答
    suggestion: Optional[str] = None          # advisory 的 AI 建议值
    status: str = "open"                      # open | resolved | dismissed
    resolution: Optional[str] = None          # 定量结论（销账时必填）
    created_at: Optional[str] = None
    resolved_at: Optional[str] = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get_questions(canvas: Any) -> list[dict]:
    items = (canvas or {}).get(QUESTIONS_KEY)
    return [dict(x) for x in items] if isinstance(items, list) else []


def open_questions(canvas: Any, kind: Optional[str] = None) -> list[dict]:
    out = [q for q in get_questions(canvas) if q.get("status") == "open"]
    if kind:
        out = [q for q in out if (q.get("kind") or KIND_BLOCKING) == kind]
    return out


def _with_questions(canvas: Any, items: list[dict]) -> dict:
    """返回带新账本的全新画布 dict（JSON 列必须整体重赋值才会写库）。"""
    out = dict(canvas) if isinstance(canvas, dict) else {}
    out[QUESTIONS_KEY] = items
    return out


def raise_questions(canvas: Any, raw_items: list[dict]) -> tuple[dict, list[str], list[str]]:
    """登记问题（按归一化问题文本去重，重复登记返回既有 id）。

    返回 (新画布, 生效/命中的 id 列表, 错误列表)。
    """
    items = get_questions(canvas)
    by_text = {norm_name(q.get("question", "")): q for q in items}
    ids: list[str] = []
    errors: list[str] = []
    for raw in raw_items or []:
        if not isinstance(raw, dict):
            errors.append(f"问题必须是对象，收到: {type(raw).__name__}")
            continue
        try:
            q = Question.model_validate(raw)
        except ValidationError as e:
            bad = "; ".join(f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}"
                            for err in e.errors()[:3])
            errors.append(f"问题「{str(raw.get('question', '?'))[:40]}」不合法: {bad}")
            continue
        if q.kind not in (KIND_BLOCKING, KIND_ADVISORY):
            errors.append(f"问题「{q.question[:40]}」kind 必须是 blocking 或 advisory")
            continue
        existing = by_text.get(norm_name(q.question))
        if existing is not None:
            # 已有同题：open 的直接复用；已销账的不复活（避免循环追问）
            ids.append(existing.get("id") or "")
            continue
        data = q.model_dump(exclude_none=True)
        data["id"] = q.id or f"q-{uuid.uuid4().hex[:8]}"
        data["status"] = "open"
        data.pop("resolution", None)
        data["created_at"] = _now_iso()
        items.append(data)
        by_text[norm_name(q.question)] = data
        ids.append(data["id"])
    return _with_questions(canvas, items), ids, errors


def resolve_questions(canvas: Any, raw_items: list[dict]) -> tuple[dict, list[dict], list[str]]:
    """销账。blocking 问题的 resolution 必须定量，否则拒绝该条并回填原因。

    raw_items: [{id(账本 id 或问题原文), resolution, status?: resolved|dismissed}]
    返回 (新画布, 生效条目列表, 错误列表)。
    """
    items = get_questions(canvas)
    by_id = {q.get("id"): q for q in items if q.get("id")}
    by_text = {norm_name(q.get("question", "")): q for q in items}
    done: list[dict] = []
    errors: list[str] = []
    for raw in raw_items or []:
        if not isinstance(raw, dict):
            errors.append(f"销账条目必须是对象，收到: {type(raw).__name__}")
            continue
        key = str(raw.get("id") or raw.get("question") or "").strip()
        q = by_id.get(key) or by_text.get(norm_name(key))
        if q is None:
            errors.append(f"账本中找不到问题「{key[:40]}」")
            continue
        status = str(raw.get("status") or "resolved")
        if status not in ("resolved", "dismissed"):
            errors.append(f"问题「{q.get('question', '')[:40]}」status 只能是 resolved 或 dismissed")
            continue
        resolution = str(raw.get("resolution") or "").strip()
        if not resolution:
            errors.append(f"问题「{q.get('question', '')[:40]}」缺少 resolution"
                          f"（{'搁置也要写明原因' if status == 'dismissed' else '请写入定量结论'}）")
            continue
        if status == "resolved" and (q.get("kind") or KIND_BLOCKING) == KIND_BLOCKING \
                and not is_quantified(resolution):
            hits = "、".join(vague_terms_in(resolution)[:3])
            errors.append(f"问题「{q.get('question', '')[:40]}」的结论仍含模糊表述（{hits}）"
                          f"且没有任何数值/枚举 —— 请追问用户拿到明确数字+单位或枚举值后再销账")
            continue
        q["status"] = status
        q["resolution"] = resolution
        q["resolved_at"] = _now_iso()
        done.append({"id": q.get("id"), "question": q.get("question"),
                     "status": status, "resolution": resolution})
    return _with_questions(canvas, items), done, errors


def ledger_summary(canvas: Any, max_items: int = 12) -> str:
    """开放问题的紧凑摘要，注入探索 agent 系统提示。"""
    opens = open_questions(canvas)
    if not opens:
        return "（无开放问题 —— 账本已清）"
    opens.sort(key=lambda q: 0 if (q.get("kind") or KIND_BLOCKING) == KIND_BLOCKING else 1)
    lines = []
    for q in opens[:max_items]:
        tag = "堵门" if (q.get("kind") or KIND_BLOCKING) == KIND_BLOCKING else "建议待确认"
        opt = f"（候选: {' / '.join(str(o) for o in q.get('options') or [])[:80]}）" \
            if q.get("options") else ""
        tgt = f"[{q.get('target')}] " if q.get("target") else ""
        lines.append(f"- ({tag}) {tgt}{q.get('question')}{opt} (id={q.get('id')})")
    if len(opens) > max_items:
        lines.append(f"- …共 {len(opens)} 个开放问题")
    return "\n".join(lines)
