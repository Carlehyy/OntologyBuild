"""探索 agent 的受限工具集 — 只有两个工具：沉淀 / 移除画布元素

工具即治理：agent 对画布的每一次修改都是一条可审计的 step，前端随
canvas 事件实时看到模型长出来。元素字段的详细约定写在工具描述里，
schema 保持宽松（对象数组），由 canvas.py 的 pydantic 校验把关 ——
校验错误原样回填给 LLM，让它按提示修正后重试（对话期修复回路）。
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.exploration import canvas as C
from app.exploration.models import ExplorationSession

_FIELD_DOC = """元素字段约定（name 用英文 snake_case/PascalCase 标识符，中文放 display_name）：
- object: {name, display_name, description, key_attribute(业务主键属性名), attributes: [{name, display_name, type_hint(如 文本/数字/金额/日期/是否), required, enum?, notes?}], relations: [{target(对象名), name?, display_name(如 归属于), cardinality(one-to-one|one-to-many|many-to-one|many-to-many)?, description?}]}
- actor: {name, display_name, kind(person|org|system|role), description, responsibilities: [str]}
- behavior: {name, display_name, actor(主体名), object(对象名), trigger(触发条件), inputs: [{name, display_name, type_hint, required}], outcome(结果), constraints: [str], needs_approval(bool)}
- event: {name, display_name, description, source(行为名|external|time), payload: [str], consequences: [str]}
- rule: {name, display_name, kind(constraint|validation|derivation|approval|alert), applies_to(对象/行为名), statement(规则表述), error_message?}
- scenario: {name, display_name, goal, actors: [主体名], steps: [str], objects: [涉及对象名], behaviors: [涉及行为名], expected_outcome}"""

TOOL_DEFS = [
    {
        "name": "upsert_elements",
        "description": "把对话中已确认的业务知识沉淀/更新到业务画布。同名或同 id 元素会被整体覆盖（请带全字段）。\n" + _FIELD_DOC,
        "parameters": {
            "type": "object",
            "properties": {
                "kind": {"type": "string",
                         "enum": ["object", "actor", "behavior", "event", "rule", "scenario"],
                         "description": "模型类别"},
                "elements": {"type": "array", "items": {"type": "object"},
                             "description": "元素数组，字段见工具描述"},
            },
            "required": ["kind", "elements"],
        },
    },
    {
        "name": "remove_elements",
        "description": "从业务画布移除元素（用户否定了某个概念、或概念被合并时使用）。",
        "parameters": {
            "type": "object",
            "properties": {
                "kind": {"type": "string",
                         "enum": ["object", "actor", "behavior", "event", "rule", "scenario"]},
                "ids": {"type": "array", "items": {"type": "string"},
                        "description": "元素 id 或名称列表"},
            },
            "required": ["kind", "ids"],
        },
    },
]

# 技能激活工具 —— 仅当当前作用域有已启用技能时才挂载（见 orchestrator）
USE_SKILL_TOOL = {
    "name": "use_skill",
    "description": "激活一个平台技能，获取它的完整操作指令。当用户的请求命中「可用技能」目录中某项的适用场景时，先调用本工具，然后严格按返回的指令执行。",
    "parameters": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "技能 name（见系统提示的可用技能目录）"},
        },
        "required": ["name"],
    },
}


class ExplorationToolRunner:
    """工具执行器：改画布、递增版本。canvas_dirty 供 orchestrator 决定是否推 canvas 事件。

    skills：本回合可用的技能（name → CapSkill），use_skill 按需取全文（渐进披露）。
    """

    def __init__(self, db: Session, session: ExplorationSession, skills: dict | None = None):
        self.db = db
        self.session = session
        self.skills = skills or {}
        self.canvas_dirty = False

    def run(self, name: str, args: dict) -> dict:
        if name == "upsert_elements":
            return self._upsert(args)
        if name == "remove_elements":
            return self._remove(args)
        if name == "use_skill":
            return self._use_skill(args)
        return {"error": f"未知工具: {name}"}

    def _use_skill(self, args: dict) -> dict:
        name = str(args.get("name") or "").strip()
        skill = self.skills.get(name)
        if not skill:
            return {"error": f"技能「{name}」不存在或未启用。可用技能: "
                             f"{', '.join(sorted(self.skills)) or '（无）'}"}
        return {"skill": skill.name, "displayName": skill.display_name,
                "instructions": skill.instructions}

    def _upsert(self, args: dict) -> dict:
        kind = str(args.get("kind") or "")
        new_canvas, applied, errors = C.upsert_elements(
            self.session.canvas, kind, args.get("elements") or [])
        if applied:
            self.session.canvas = new_canvas   # JSON 列整体重赋值才会落库
            self.session.canvas_version = (self.session.canvas_version or 0) + 1
            self.db.flush()
            self.canvas_dirty = True
        result: dict = {"kind": kind, "applied": len(applied), "ids": applied,
                        "counts": C.completeness(self.session.canvas)["counts"]}
        if errors:
            result["errors"] = errors
        return result

    def _remove(self, args: dict) -> dict:
        kind = str(args.get("kind") or "")
        new_canvas, removed, missing = C.remove_elements(
            self.session.canvas, kind, args.get("ids") or [])
        if removed:
            self.session.canvas = new_canvas
            self.session.canvas_version = (self.session.canvas_version or 0) + 1
            self.db.flush()
            self.canvas_dirty = True
        result: dict = {"kind": kind, "removed": removed,
                        "counts": C.completeness(self.session.canvas)["counts"]}
        if missing:
            result["missing"] = missing
        return result
