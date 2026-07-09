"""探索 agent 的受限工具集 — 沉淀/移除画布元素 + 澄清账本 + 确定性出图

工具即治理：agent 对画布的每一次修改都是一条可审计的 step，前端随
canvas 事件实时看到模型长出来。元素字段的详细约定写在工具描述里，
schema 保持宽松（对象数组），由 canvas.py / questions.py 的 pydantic
校验把关 —— 校验错误原样回填给 LLM，让它按提示修正后重试（对话期修复回路）。

五个常驻工具：
  upsert_elements / remove_elements   六类模型元素的沉淀与修正
  raise_questions / resolve_questions 澄清账本（堵门问题必须定量销账）
  show_diagram                        确定性生成 ER/流程/时序/状态图，直接出现在对话里
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.exploration import canvas as C
from app.exploration import diagram as D
from app.exploration import questions as Q
from app.exploration.models import ExplorationSession

_FIELD_DOC = """元素字段约定（name 用英文 snake_case/PascalCase 标识符，中文放 display_name）：
- object: {name, display_name, description, key_attribute(业务主键属性名), attributes: [{name, display_name, type_hint(如 文本/数字/金额/日期/是否/枚举), required, enum?, notes?}], relations: [{target(对象名), name?, display_name(如 归属于), cardinality(one-to-one|one-to-many|many-to-one|many-to-many)?, description?}]}
- actor: {name, display_name, kind(person|org|system|role), description, responsibilities: [str], attributes: [{name, display_name, type_hint, required, enum?, notes?}], key_attribute(业务主键属性名)?} —— person/org 类主体是数据实体，务必给出识别与档案属性（如编码、名称、联系方式、状态）；system/role 类可省略 attributes
- behavior: {name, display_name, actor(主体名), object(对象名), trigger(触发条件), inputs: [{name, display_name, type_hint, required}], outcome(结果，若引起状态变化写明「从X变为Y」), constraints: [str，定量表述], needs_approval(bool)}
- event: {name, display_name, description, source(行为名|external|time), payload: [str], consequences: [str]}
- rule: {name, display_name, kind(constraint|validation|derivation|approval|alert), applies_to(对象/行为名), statement(定量表述：阈值/枚举/边界要有具体数字), error_message?}
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
    {
        "name": "raise_questions",
        "description": ("把你提给用户的关键问题登记进澄清账本（同题自动去重）。"
                        "kind=blocking：企业特有口径，必须用户拍板（阈值/枚举边界/审批线/级联策略/主键口径/基数），"
                        "不销账就过不了质量门；kind=advisory：行业常识，你先给建议值(suggestion)请用户确认。"
                        "尽量提供 2-4 个候选 options（含具体数值/枚举），用户点选即可作答。"),
        "parameters": {
            "type": "object",
            "properties": {
                "questions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "question": {"type": "string", "description": "完整问题（一句话，带上下文）"},
                            "kind": {"type": "string", "enum": ["blocking", "advisory"]},
                            "target": {"type": "string", "description": "关联的画布元素名（或 元素名.字段）"},
                            "options": {"type": "array", "items": {"type": "string"},
                                        "description": "候选答案（含具体数值/枚举），2-4 个为宜"},
                            "suggestion": {"type": "string", "description": "advisory 的 AI 建议值"},
                        },
                        "required": ["question", "kind"],
                    },
                },
            },
            "required": ["questions"],
        },
    },
    {
        "name": "resolve_questions",
        "description": ("用户给出明确答复后销账。resolution 必须是定量结论"
                        "（数字+单位 / 枚举清单 / 明确边界），blocking 问题含模糊表述且无数值会被拒绝。"
                        "status=dismissed 表示用户明确表示暂不关心（resolution 写明原因）。"
                        "销账后记得把结论 upsert 进画布对应元素 —— 同一回合完成，不要遗留。"),
        "parameters": {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string", "description": "账本 id（或问题原文）"},
                            "resolution": {"type": "string", "description": "定量结论"},
                            "status": {"type": "string", "enum": ["resolved", "dismissed"]},
                        },
                        "required": ["id", "resolution"],
                    },
                },
            },
            "required": ["items"],
        },
    },
    {
        "name": "show_diagram",
        "description": ("从画布确定性生成图表并直接展示在对话里（不经 LLM，图与画布严格一致），"
                        "用于让用户「看图挑错」：er=实体关系图（对象+person/org主体）；"
                        "flow=业务流程图（target=场景名，缺省第一个场景）；"
                        "sequence=时序图（场景的主体→对象协作，需场景已关联 behaviors）；"
                        "state=状态图（target=对象名，需该对象有枚举状态属性）。"
                        "出图后请用户确认图中结构是否与实际相符，并按反馈修正画布。同一张图内容未变化时不要重复展示。"),
        "parameters": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["er", "flow", "sequence", "state"]},
                "target": {"type": "string",
                           "description": "flow/sequence 传场景名；state 传对象名；er 忽略"},
            },
            "required": ["kind"],
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
    last_diagram：show_diagram 的产物，orchestrator 把它挂到 step 事件上推给前端。
    """

    def __init__(self, db: Session, session: ExplorationSession, skills: dict | None = None):
        self.db = db
        self.session = session
        self.skills = skills or {}
        self.canvas_dirty = False
        self.last_diagram: dict | None = None

    def run(self, name: str, args: dict) -> dict:
        self.last_diagram = None
        if name == "upsert_elements":
            return self._upsert(args)
        if name == "remove_elements":
            return self._remove(args)
        if name == "raise_questions":
            return self._raise_questions(args)
        if name == "resolve_questions":
            return self._resolve_questions(args)
        if name == "show_diagram":
            return self._show_diagram(args)
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

    def _commit_canvas(self, new_canvas: dict) -> None:
        self.session.canvas = new_canvas   # JSON 列整体重赋值才会落库
        self.session.canvas_version = (self.session.canvas_version or 0) + 1
        self.db.flush()
        self.canvas_dirty = True

    def _upsert(self, args: dict) -> dict:
        kind = str(args.get("kind") or "")
        new_canvas, applied, errors = C.upsert_elements(
            self.session.canvas, kind, args.get("elements") or [])
        if applied:
            self._commit_canvas(new_canvas)
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
            self._commit_canvas(new_canvas)
        result: dict = {"kind": kind, "removed": removed,
                        "counts": C.completeness(self.session.canvas)["counts"]}
        if missing:
            result["missing"] = missing
        return result

    def _raise_questions(self, args: dict) -> dict:
        new_canvas, ids, errors = Q.raise_questions(
            self.session.canvas, args.get("questions") or [])
        if ids:
            self._commit_canvas(new_canvas)
        opens = Q.open_questions(self.session.canvas)
        result: dict = {"raised": len(ids), "ids": ids,
                        "openBlocking": sum(1 for q in opens
                                            if (q.get("kind") or "blocking") == "blocking"),
                        "openAdvisory": sum(1 for q in opens
                                            if (q.get("kind") or "blocking") == "advisory")}
        if errors:
            result["errors"] = errors
        return result

    def _resolve_questions(self, args: dict) -> dict:
        new_canvas, done, errors = Q.resolve_questions(
            self.session.canvas, args.get("items") or [])
        if done:
            self._commit_canvas(new_canvas)
        opens = Q.open_questions(self.session.canvas)
        result: dict = {"resolved": done,
                        "openBlocking": sum(1 for q in opens
                                            if (q.get("kind") or "blocking") == "blocking"),
                        "openAdvisory": sum(1 for q in opens
                                            if (q.get("kind") or "blocking") == "advisory")}
        if errors:
            result["errors"] = errors
        return result

    def _show_diagram(self, args: dict) -> dict:
        kind = str(args.get("kind") or "")
        target = (str(args.get("target")) if args.get("target") else None)
        try:
            diagram = D.build_diagram(self.session.canvas, kind, target)
        except D.DiagramError as e:
            return {"error": str(e)}
        self.last_diagram = diagram
        # mermaid 源码不回填给 LLM（省上下文且防篡改）—— 图已直接推给前端
        return {"kind": diagram["kind"], "title": diagram["title"],
                "shown": True,
                "note": "图表已在对话中展示给用户。请提醒用户核对结构是否与实际业务一致，并根据反馈修正画布。"}
