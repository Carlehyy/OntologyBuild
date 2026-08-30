"""绑定本体版本漂移简报 — 每回合注入探索系统提示的只读摘要

业务澄清页的探索会话可绑定一个本体版本（session.ontology_version_id），
用户可同时在本体模型视图中人工编辑该版本结构，而引导师此前感知不到。
本模块在每个对话回合现算一次「业务语义层 ⇄ 本体结构」一致性简报
（复用 semantic_gate 的纯函数校验，与试跑闸门同一口径、同一
complete_snapshot 归一方式），让引导师知道人工修改造成了哪些漂移。

Pull 模型：不推送、不落库、不缓存；计算失败由调用方兜底（记日志并跳过
注入），绝不影响对话回合。
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.exploration.models import ExplorationSession
from app.exploration.semantic_gate import semantic_consistency_issues
from app.ontologies.inference.models import AuditLog
from app.ontologies.versions.models import OntologyVersion
from app.ontologies.versions.snapshot_contract import complete_snapshot

# issue code → 中文含义（措辞与 semantic_gate 的校验口径保持一致）
_CODE_MEANINGS = {
    "semantic_structure_missing": "画布有、结构中缺少",
    "semantic_business_missing": "结构有、业务画布中无对应",
    "semantic_signature_mismatch": "同名元素签名不一致",
    "semantic_document_missing": "语义层缺少需求文档",
    "semantic_document_stale": "需求文档或画布指纹已过期",
}

# 注入预算：最多 5 条代表差异 + 3 条近期人工编辑，单条截断，整块控制在十几行内
_MAX_SAMPLES = 5
_MAX_AUDIT_LINES = 3
_MESSAGE_CAP = 120

_SUBTYPE_LABELS = {
    "workspace_saved": "保存结构工作区",
    "workspace_mappings_saved": "保存数据映射",
}
_COLLECTION_LABELS = {
    "objectTypes": "对象类型", "linkTypes": "关系类型", "actions": "动作",
    "functions": "函数", "sentinels": "哨兵",
    "mappings": "属性映射", "linkMappings": "关系映射",
}


def _clip(text: str) -> str:
    message = " ".join(str(text or "").split())
    return message[:_MESSAGE_CAP] + "…" if len(message) > _MESSAGE_CAP else message


def _summarize_diff(diff: dict) -> str:
    """把 _diff_formal 的输出压成一行可读摘要：优先名称，退化为计数。"""
    parts: list[str] = []
    for key, label in _COLLECTION_LABELS.items():
        entry = diff.get(key) or {}
        for verb, names_key, count_key in (
            ("新增", "addedNames", "added"),
            ("修改", "modifiedNames", "modified"),
            ("删除", "deletedNames", "deleted"),
        ):
            names = entry.get(names_key) or []
            count = entry.get(count_key) or 0
            if names:
                seg = f"{verb}{label}「{'、'.join(names[:3])}」"
                if count > len(names[:3]):
                    seg += f"等{count}项"
                parts.append(seg)
            elif count:
                parts.append(f"{verb}{label}{count}项")
    return "；".join(parts)


def _recent_human_edit_lines(db: Session, version: OntologyVersion) -> list[str]:
    """该版本最近的人工保存审计（新→旧），供引导师定位「谁刚改了什么」。"""
    rows = (db.query(AuditLog)
            .filter(AuditLog.ontology_id == version.ontology_id,
                    AuditLog.object_type == "ontology_version",
                    AuditLog.object_id == version.id,
                    AuditLog.event_subtype.in_(list(_SUBTYPE_LABELS)))
            .order_by(AuditLog.created_at.desc())
            .limit(_MAX_AUDIT_LINES)
            .all())
    lines: list[str] = []
    for row in rows:
        ts = row.created_at.strftime("%m-%d %H:%M") if row.created_at else ""
        label = _SUBTYPE_LABELS.get(row.event_subtype, row.event_subtype or "编辑")
        summary = _summarize_diff(((row.meta or {}).get("diff")) or {})
        line = f"- {ts} {row.user_name or '未知用户'} {label}"
        if summary:
            line += f"：{summary}"
        lines.append(_clip(line))
    return lines


def build_bound_version_brief(
    db: Session, session: ExplorationSession,
) -> str | None:
    """现算绑定版本的一致性简报；未绑定或版本已删除时返回 None。"""
    version_id = getattr(session, "ontology_version_id", None)
    if not version_id:
        return None
    version = db.query(OntologyVersion).filter(
        OntologyVersion.id == version_id).first()
    if version is None:
        return None

    snap = complete_snapshot(version.snapshot_formal)
    issues = semantic_consistency_issues(version.snapshot_semantic, snap)
    has_semantic = isinstance(version.snapshot_semantic, dict) \
        and bool(version.snapshot_semantic)

    lines = [
        f"绑定版本 {version.version_number}"
        f"（revision={version.revision or 0}，{version.node_kind}/"
        f"{version.lifecycle_status}，{'含' if has_semantic else '无'}业务语义层）。",
    ]
    if not has_semantic:
        lines.append("该版本尚未沉淀业务语义层，下列「画布无对应」项不能直接视为人工修改。")
    if issues:
        by_code: dict[str, int] = {}
        for issue in issues:
            code = str(issue.get("code") or "")
            by_code[code] = by_code.get(code, 0) + 1
        breakdown = "；".join(
            f"{_CODE_MEANINGS.get(code, code)} {count} 项"
            for code, count in sorted(by_code.items()))
        lines.append(f"业务语义层与本体结构差异共 {len(issues)} 项：{breakdown}。")
        for issue in issues[:_MAX_SAMPLES]:
            lines.append(f"- {_clip(issue.get('message'))}")
        if len(issues) > _MAX_SAMPLES:
            lines.append(f"- …（其余 {len(issues) - _MAX_SAMPLES} 项从略）")
    else:
        lines.append("业务语义与本体结构当前一致（0 项差异）。")
    edit_lines = _recent_human_edit_lines(db, version)
    if edit_lines:
        lines.append("近期人工编辑（审计留痕，新的在前）：")
        lines.extend(edit_lines)
    lines.append(
        "这些差异很可能来自用户在本体模型视图的人工修改；当用户确认时，使用 "
        "upsert_elements/remove_elements 把对应改动回译到业务场景画布，"
        "不要直接声称已修改本体结构。"
    )
    return "\n".join(lines)
