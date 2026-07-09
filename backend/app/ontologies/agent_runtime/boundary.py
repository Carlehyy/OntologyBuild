"""
授权边界 (AgentScope) — agent 世界观的唯一入口

把「本体 × AgentProfile」解析成 agent 可见的对象类型 / 链接类型 / 动作集合，
并从这个受限视图确定性地生成一份「本体技能卡」(skill card)：
对象、属性、关系、动作的紧凑说明，注入 system prompt。

规则：
  - 白名单语义：None=全部允许，[]=全部拒绝，[id...]=仅列表内。
  - 链接类型仅当"两端对象类型都可见"时才可见（防止经由链接泄露隐藏类型）。
  - 动作若绑定了不可见的对象类型则不可见。
所有工具都必须经 require_* 取类型/动作 —— 越界即 ToolError，不存在旁路。
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.ontology import OntologyProject
from app.models.ontology_formal import (
    ObjectType, LinkType, ActionType, ObjectInstance,
)
from app.ontologies.agent_runtime.models import AgentProfile


class ToolError(Exception):
    """工具层可恢复错误 — 返回给 LLM 让其自我修正，不打断对话。"""


def get_or_create_profile(db: Session, ontology_id: str) -> AgentProfile:
    profile = db.query(AgentProfile).filter(
        AgentProfile.ontology_id == ontology_id).first()
    if not profile:
        profile = AgentProfile(ontology_id=ontology_id, allowed_action_ids=[])
        db.add(profile)
        db.commit()
        db.refresh(profile)
    return profile


def _allowed(ids: Optional[list], item_id: str) -> bool:
    if ids is None:
        return True
    return item_id in ids


class AgentScope:
    """profile 过滤后的本体视图。工具层只认识这个对象，不认识原始表。"""

    def __init__(self, db: Session, ontology: OntologyProject, profile: AgentProfile):
        self.db = db
        self.ontology = ontology
        self.profile = profile

        oid = ontology.id
        all_types = db.query(ObjectType).filter(ObjectType.ontology_id == oid).all()
        self.object_types: dict[str, ObjectType] = {
            t.id: t for t in all_types
            if _allowed(profile.allowed_object_type_ids, t.id)
        }

        all_links = db.query(LinkType).filter(LinkType.ontology_id == oid).all()
        self.link_types: dict[str, LinkType] = {
            lt.id: lt for lt in all_links
            if _allowed(profile.allowed_link_type_ids, lt.id)
            and lt.source_object_type_id in self.object_types
            and lt.target_object_type_id in self.object_types
        }

        all_actions = db.query(ActionType).filter(ActionType.ontology_id == oid).all()
        self.actions: dict[str, ActionType] = {
            a.id: a for a in all_actions
            if _allowed(profile.allowed_action_ids, a.id)
            and (a.object_type_id is None or a.object_type_id in self.object_types)
        }

    # ---------- 引用解析（id / name / displayName 均可） ----------

    def _resolve(self, pool: dict, ref: str, kind: str):
        ref = (ref or "").strip()
        if ref in pool:
            return pool[ref]
        for item in pool.values():
            if item.name == ref or item.display_name == ref:
                return item
        names = "、".join(f"{i.display_name}({i.name})" for i in pool.values()) or "（无）"
        raise ToolError(f"{kind}「{ref}」不存在或不在授权范围内。可用{kind}：{names}")

    def require_object_type(self, ref: str) -> ObjectType:
        return self._resolve(self.object_types, ref, "对象类型")

    def require_link_type(self, ref: str) -> LinkType:
        return self._resolve(self.link_types, ref, "链接类型")

    def require_action(self, ref: str) -> ActionType:
        return self._resolve(self.actions, ref, "动作")

    def resolve_property(self, ot: ObjectType, ref: str) -> str:
        """把属性引用（name 或 displayName）解析为规范 name；越界即 ToolError（含可用属性）。

        合法集直接取自 ot.properties —— 派生/计算属性也声明在其中（source='computed'，
        computed 的键名即 prop['name']），故一并覆盖，不会误伤合法的派生属性过滤。
        这是「越界硬失败」：LLM 拼错或臆造属性名时，返回可用清单让它自我修正，
        而不是让一个查不到的条件静默匹配为空、给出看似正确实则错误的答案。
        """
        ref = (ref or "").strip()
        for p in (ot.properties or []):
            if isinstance(p, dict) and ref in (p.get("name"), p.get("displayName"), p.get("display_name")):
                return p.get("name")
        avail = "、".join(
            f"{p.get('displayName') or p.get('name')}({p.get('name')})"
            for p in (ot.properties or []) if isinstance(p, dict) and p.get("name")
        ) or "（无）"
        raise ToolError(f"属性「{ref}」不在对象类型「{ot.display_name}」上，或拼写有误。可用属性：{avail}")

    def visible_instance(self, inst: Optional[ObjectInstance]) -> ObjectInstance:
        """实例必须属于本体 + 其类型在授权范围内。"""
        if not inst or inst.ontology_id != self.ontology.id:
            raise ToolError("对象实例不存在")
        if inst.object_type_id not in self.object_types:
            raise ToolError("该实例所属的对象类型不在授权范围内")
        return inst

    # ---------- 本体技能卡 ----------

    def instance_counts(self) -> dict[str, int]:
        rows = (self.db.query(ObjectInstance.object_type_id, func.count(ObjectInstance.id))
                .filter(ObjectInstance.ontology_id == self.ontology.id)
                .group_by(ObjectInstance.object_type_id).all())
        return {tid: n for tid, n in rows if tid in self.object_types}

    def skill_card(self) -> str:
        """从受限视图生成紧凑的 schema 说明 — agent 对世界的全部认知。"""
        counts = self.instance_counts()
        lines: list[str] = []
        lines.append(f"# 本体「{self.ontology.name}」")
        if self.ontology.description:
            lines.append(str(self.ontology.description).strip())

        lines.append("\n## 对象类型")
        if not self.object_types:
            lines.append("（无授权的对象类型）")
        for t in self.object_types.values():
            n = counts.get(t.id, 0)
            desc = f" — {t.description}" if t.description else ""
            lines.append(f"### {t.display_name} (name={t.name}, 实例数={n}){desc}")
            for p in (t.properties or []):
                if not isinstance(p, dict):
                    continue
                req = "必填" if p.get("required") else "可选"
                pdesc = f" — {p['description']}" if p.get("description") else ""
                pk = " [主键]" if t.primary_key and (p.get("id") == t.primary_key or p.get("name") == t.primary_key) else ""
                lines.append(f"- {p.get('displayName') or p.get('name')} (name={p.get('name')}, {p.get('type', 'string')}, {req}){pk}{pdesc}")

        lines.append("\n## 链接类型（对象间关系）")
        if not self.link_types:
            lines.append("（无）")
        for lt in self.link_types.values():
            src = self.object_types.get(lt.source_object_type_id)
            tgt = self.object_types.get(lt.target_object_type_id)
            desc = f" — {lt.description}" if lt.description else ""
            lines.append(
                f"- {lt.display_name} (name={lt.name}): "
                f"{src.display_name if src else '?'} → {tgt.display_name if tgt else '?'}"
                f" [{lt.cardinality}]{desc}")

        lines.append("\n## 可用动作（业务操作，先 propose_action 预演，永不直接执行）")
        if not self.actions:
            lines.append("（当前授权范围内没有可用动作 — 只能做查询和分析）")
        for a in self.actions.values():
            ot = self.object_types.get(a.object_type_id) if a.object_type_id else None
            approval = "，需人工审批" if a.requires_approval else ""
            desc = f" — {a.description}" if a.description else ""
            lines.append(f"### {a.display_name} (name={a.name}"
                         f"{f', 作用于 {ot.display_name}' if ot else ''}{approval}){desc}")
            for p in (a.parameters or []):
                if not isinstance(p, dict):
                    continue
                req = "必填" if p.get("required") else "可选"
                pdesc = f" — {p['description']}" if p.get("description") else ""
                lines.append(f"- 参数 {p.get('displayName') or p.get('name')} "
                             f"(name={p.get('name')}, {p.get('type', 'string')}, {req}){pdesc}")
        return "\n".join(lines)

    def summary(self) -> dict:
        """给前端的能力概览（边界可视化）。"""
        counts = self.instance_counts()
        return {
            "enabled": self.profile.enabled,
            "objectTypes": [
                {"id": t.id, "name": t.name, "displayName": t.display_name,
                 "instanceCount": counts.get(t.id, 0)}
                for t in self.object_types.values()
            ],
            "linkTypes": [
                {"id": lt.id, "name": lt.name, "displayName": lt.display_name}
                for lt in self.link_types.values()
            ],
            "actions": [
                {"id": a.id, "name": a.name, "displayName": a.display_name,
                 "requiresApproval": bool(a.requires_approval)}
                for a in self.actions.values()
            ],
            "allowActionProposals": self.profile.allow_action_proposals,
            "maxRowsPerQuery": self.profile.max_rows_per_query,
            "maxSteps": self.profile.max_steps,
        }


def build_scope(db: Session, ontology_id: str) -> tuple[OntologyProject, AgentProfile, AgentScope]:
    ontology = db.query(OntologyProject).filter(OntologyProject.id == ontology_id).first()
    if not ontology:
        raise ToolError("本体不存在")
    profile = get_or_create_profile(db, ontology_id)
    return ontology, profile, AgentScope(db, ontology, profile)
