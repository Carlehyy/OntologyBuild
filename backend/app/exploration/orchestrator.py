"""业务探索编排 — LLM ⇄ 画布工具的回合循环（结构仿 agent_runtime.orchestrator）

事件流协议（SSE 每条 data 一个 JSON）：
  {"type": "meta",   "sessionId", "model"}
  {"type": "step",   "tool", "arguments", "summary", "durationMs", "error"?}
  {"type": "canvas", "canvas", "version", "completeness"}      ← 画布被工具修改后推送
  {"type": "answer", "content", "usage"}
  {"type": "error",  "message"}
  {"type": "done"}
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Iterator, Optional

from sqlalchemy.orm import Session

from app.model_configs.selector import select_llm_model_config, llm_call_kwargs
from app.ontologies.agent_runtime import llm_bridge
from app.capabilities.models import CapSkill
from app.exploration import canvas as C
from app.exploration.models import (ExplorationAttachment, ExplorationMessage,
                                    ExplorationSession)
from app.exploration.toolkit import TOOL_DEFS, USE_SKILL_TOOL, ExplorationToolRunner

logger = logging.getLogger(__name__)

_MAX_STEPS = 6
_HISTORY_LIMIT = 12
_TOOL_RESULT_CAP = 6000
_DEFAULT_TITLE = "新的业务探索"
# 附件注入上下文的预算：单文件与总量各自截断，避免撑爆上下文
_ATTACH_PER_FILE_CAP = 12000
_ATTACH_TOTAL_CAP = 28000


def _load_skills(db: Session) -> dict[str, CapSkill]:
    """当前作用域（exploration）已启用的技能，name → CapSkill。"""
    rows = db.query(CapSkill).filter(CapSkill.enabled.is_(True)).all()
    return {s.name: s for s in rows if "exploration" in (s.scopes or [])}


def _skills_block(skills: dict[str, CapSkill]) -> str:
    if not skills:
        return ""
    lines = "\n".join(f"- {s.name}: {s.description or s.display_name}"
                      for s in skills.values())
    return f"""

# 可用技能
用户的请求命中下列技能的适用场景时，先调用 use_skill(name) 获取完整指令，再严格按指令执行；不要凭记忆模仿技能的输出格式。
{lines}"""


def _system_prompt(session: ExplorationSession, skills: dict[str, CapSkill] | None = None) -> str:
    comp = C.completeness(session.canvas)
    gaps = "\n".join(f"- {g}" for g in comp["gaps"]) or "- （暂无明显缺口）"
    return f"""你是「业务探索」引导师，运行在 OntoPrompt 平台。你的使命：通过对话帮助用户澄清业务，并把**已确认**的业务知识实时沉淀为六类结构化模型 —— 本体建模的本质是业务建模与软件需求建模，这些模型将转化为需求文档与本体（对象类型/链接/动作）。

# 六类模型的分工
- 对象模型(object)：业务里的「东西」及其属性、业务主键、对象间关系（带基数）
- 主体模型(actor)：谁在参与 —— 人/组织/系统/角色，各自的职责；person/org 类主体本身也是数据实体，要像对象一样给出识别与档案属性（如编码、名称、联系方式、状态）
- 行为模型(behavior)：主体对对象做什么 —— 触发、输入、结果、约束、是否需审批
- 事件模型(event)：业务中发生了什么值得记录/响应的事，来源与后果
- 规则模型(rule)：约束/校验/派生/审批/告警类规则，作用于哪个对象或行为
- 场景模型(scenario)：端到端业务流程叙事，串起主体/对象/行为，用于最终验收模型完整性

# 工作方式
1. 每回合聚焦 1-2 个关键问题，循序渐进；不要一次抛出问题清单轰炸用户。
2. 用户每确认一条信息，立即用 upsert_elements 沉淀进画布 —— 不要攒到最后。宁可先落粗粒度元素再逐步补全字段。
3. 同名元素 upsert 是整体覆盖：更新某元素时带上它已有的全部字段。
4. 概念含糊或互相冲突时先澄清再落库；用户否定的概念用 remove_elements 移除。
5. 建议探索顺序：先场景与主体（业务边界）→ 对象及属性/关系 → 行为 → 规则与事件；但跟随用户的表达，不要机械执行。
6. name 一律用英文标识符（snake_case 或 PascalCase），中文名放 display_name。
7. 回答用中文，简洁；每回合结尾用一句话告诉用户画布进展（如「已记录 2 个对象，还差主键确认」），再提出下一个问题。

# 当前画布
{C.canvas_summary(session.canvas)}

# 当前缺口（追问方向参考）
{gaps}{_skills_block(skills or {})}"""


def _attachments_block(db: Session, session_id: str) -> str:
    """已就绪的会话附件 → 注入引导师上下文的参考资料块（分文件截断 + 总量封顶）。"""
    rows = (db.query(ExplorationAttachment)
            .filter(ExplorationAttachment.session_id == session_id,
                    ExplorationAttachment.status == "ready")
            .order_by(ExplorationAttachment.created_at.asc()).all())
    parts: list[str] = []
    budget = _ATTACH_TOTAL_CAP
    for r in rows:
        text = (r.extracted_text or "").strip()
        if not text:
            continue
        if budget <= 0:
            parts.append("（其余附件因篇幅限制未展开，可在对话中点名让我聚焦某个文件）")
            break
        cap = min(_ATTACH_PER_FILE_CAP, budget)
        clipped = text[:cap]
        budget -= len(clipped)
        suffix = "\n…（该文件内容较长，已截断）" if len(text) > len(clipped) else ""
        parts.append(f"## 附件：{r.filename}\n{clipped}{suffix}")
    if not parts:
        return ""
    return ("# 用户上传的参考资料（仅本会话可见）\n"
            "以下是用户为这次业务探索上传的附件，作为澄清业务、沉淀画布的背景依据。"
            "请主动从中提炼对象/主体/行为/事件/规则/场景，并在对话里与用户确认；"
            "不要臆造资料中没有的信息，引用要点时注明来源文件名。\n\n"
            + "\n\n".join(parts))


def _summarize(name: str, result: dict) -> str:
    if "error" in result:
        return str(result["error"])[:120]
    label = C.KIND_LABELS.get(result.get("kind", ""), result.get("kind", ""))
    if name == "upsert_elements":
        s = f"沉淀 {result.get('applied', 0)} 个{label}模型元素"
        if result.get("errors"):
            s += f"（{len(result['errors'])} 个被拒）"
        return s
    if name == "remove_elements":
        return f"移除 {result.get('removed', 0)} 个{label}模型元素"
    if name == "use_skill":
        return f"激活技能「{result.get('displayName', result.get('skill', ''))}」"
    return "完成"


def _canvas_event(session: ExplorationSession) -> dict:
    return {"type": "canvas", "canvas": session.canvas,
            "version": session.canvas_version,
            "completeness": C.completeness(session.canvas)}


def run_exploration_turn(db: Session, session_id: str, user, message: str,
                         model_id: Optional[str] = None) -> Iterator[dict]:
    """执行一个探索回合。所有异常转 error 事件，绝不让 SSE 裸断。"""
    try:
        yield from _run(db, session_id, user, message, model_id)
    except Exception as e:  # noqa: BLE001
        logger.exception("业务探索回合失败")
        yield {"type": "error", "message": f"探索回合执行失败: {e}"}
    finally:
        yield {"type": "done"}


def _run(db: Session, session_id: str, user, message: str,
         model_id: Optional[str]) -> Iterator[dict]:
    session = db.query(ExplorationSession).filter(ExplorationSession.id == session_id).first()
    if not session:
        yield {"type": "error", "message": "会话不存在"}
        return

    cfg = select_llm_model_config(db, model_id=model_id)
    try:
        call_kwargs = llm_call_kwargs(cfg)
    except ValueError as e:
        yield {"type": "error", "message": str(e)}
        return
    if not call_kwargs:
        yield {"type": "error",
               "message": "尚未配置可用的 LLM。请先到「模型配置」添加一个对话模型（OpenAI 兼容或 Anthropic）。"}
        return

    history = (db.query(ExplorationMessage)
               .filter(ExplorationMessage.session_id == session.id)
               .order_by(ExplorationMessage.created_at.desc())
               .limit(_HISTORY_LIMIT).all())[::-1]

    if session.title == _DEFAULT_TITLE and not history:
        session.title = message.strip()[:60] or _DEFAULT_TITLE
    db.add(ExplorationMessage(session_id=session.id, role="user", content=message))
    db.commit()

    yield {"type": "meta", "sessionId": session.id, "model": call_kwargs.get("model")}

    skills = _load_skills(db)
    tools = TOOL_DEFS + ([USE_SKILL_TOOL] if skills else [])

    sys_content = _system_prompt(session, skills)
    attach_block = _attachments_block(db, session.id)
    if attach_block:
        sys_content += "\n\n" + attach_block
    messages: list[dict] = [{"role": "system", "content": sys_content}]
    for m in history:
        if m.role in ("user", "assistant") and (m.content or "").strip():
            messages.append({"role": m.role, "content": m.content})
    messages.append({"role": "user", "content": message})

    runner = ExplorationToolRunner(db, session, skills=skills)
    steps: list[dict] = []
    usage_total = {"inputTokens": 0, "outputTokens": 0}
    answer: Optional[str] = None

    for _ in range(_MAX_STEPS):
        try:
            resp = llm_bridge.chat(call_kwargs, messages, tools)
        except llm_bridge.LLMError as e:
            yield {"type": "error", "message": str(e)}
            _persist_assistant(db, session, f"[执行中断] {e}", steps, call_kwargs, usage_total)
            return

        for k in usage_total:
            if resp.get("usage") and resp["usage"].get(k):
                usage_total[k] += resp["usage"][k]

        if not resp["tool_calls"]:
            answer = resp.get("content") or "（模型未给出回答）"
            break

        messages.append({"role": "assistant", "content": resp.get("content"),
                         "tool_calls": resp["tool_calls"]})
        for tc in resp["tool_calls"]:
            started = time.time()
            runner.canvas_dirty = False
            try:
                result = runner.run(tc["name"], tc.get("arguments") or {})
            except Exception as e:  # noqa: BLE001 — 工具内部意外不摧毁回合
                logger.exception("探索工具 %s 执行异常", tc["name"])
                result = {"error": f"工具内部错误: {e}"}
            duration = int((time.time() - started) * 1000)

            step = {"tool": tc["name"], "arguments": tc.get("arguments") or {},
                    "summary": _summarize(tc["name"], result), "durationMs": duration}
            if "error" in result:
                step["error"] = result["error"]
            steps.append(step)
            yield {"type": "step", **step}
            if runner.canvas_dirty:
                yield _canvas_event(session)

            payload = json.dumps(result, ensure_ascii=False, default=str)
            if len(payload) > _TOOL_RESULT_CAP:
                payload = payload[:_TOOL_RESULT_CAP] + "…（已截断）"
            messages.append({"role": "tool", "tool_call_id": tc["id"],
                             "name": tc["name"], "content": payload})
    else:
        answer = f"本回合已沉淀 {len(steps)} 步画布修改。信息量较大，请继续对话逐项确认。"

    _persist_assistant(db, session, answer or "", steps, call_kwargs, usage_total)
    yield {"type": "answer", "content": answer, "usage": usage_total}


def _persist_assistant(db: Session, session: ExplorationSession, content: str,
                       steps: list, call_kwargs: dict, usage: dict) -> None:
    db.add(ExplorationMessage(
        session_id=session.id, role="assistant", content=content,
        steps=steps, model=call_kwargs.get("model"), token_usage=usage,
    ))
    session.updated_at = datetime.now(timezone.utc)
    db.commit()
