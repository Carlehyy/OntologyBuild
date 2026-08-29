"""助手适配层 — 把各助手的落库轨迹归一化为评估输入。

平台现有 5 个有落库对话的智能体面，各自的消息/步骤结构不同：
- ontology_agent   本体助手     /agent           AgentMessage.steps: {tool, arguments, summary, durationMs, result, error?}
- super_assistant  超级助手     /super-assistant  SuperAssistantMessage.steps: {toolName, status, arguments, preview}
- exploration      建模对话     /explore         ExplorationMessage.steps: {tool, arguments, summary, durationMs, error?}
- steward          数据管家     数据集成          StewardMessage.steps: {tool, arguments, resultSummary, durationMs, error?}
- scene_assistant  场景建模助手  三维场景          SceneMessage：role/content（无工具步骤）

每个适配器负责两件事：会话列表（供选择）与轨迹装载（归一化为 Trace）。
轨迹按 OpenAI messages 格式重建：助手消息携带 tool_calls，紧随 tool 消息
（结果取自各助手 steps 的结果字段并截断），轨迹类评分器可直接消费。
新助手接入 = 在 build_adapters() 里加一个条目。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Callable

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.data_channel.steward.models import StewardConversation, StewardMessage
from app.exploration.models import ExplorationMessage, ExplorationSession
from app.ontologies.agent_runtime.models import AgentConversation, AgentMessage
from app.scenes.models import SceneConversation, SceneMessage
from app.super_assistant.models import SuperAssistantConversation, SuperAssistantMessage


@dataclass
class Trace:
    """一条会话的归一化评估输入。"""

    query: str                                            # 最后一轮用户输入
    response: str                                         # 助手最终答复
    # OpenAI messages 格式全轨迹：assistant 消息携带 tool_calls，紧随 tool 消息
    # （工具结果已截断），轨迹类评分器可直接消费。
    openai_messages: list = field(default_factory=list)
    actions: list = field(default_factory=list)           # 归一化工具动作序列
    tool_error_count: int = 0                             # 失败工具调用数


@dataclass
class ConversationRef:
    id: str
    title: str
    created_at: object = None
    updated_at: object = None
    message_count: int = 0


@dataclass
class AssistantAdapter:
    key: str
    label: str
    description: str
    conv_model: type                                       # 会话 ORM 模型（取标题/时间）
    list_conversations: Callable  # (db, limit, offset, since=None) -> (total, list[ConversationRef])
    load_trace: Callable          # (db, conversation_id) -> Trace | None

    def get_title(self, db: Session, conversation_id: str) -> str:
        row = db.query(self.conv_model).filter(self.conv_model.id == conversation_id).first()
        return (getattr(row, "title", "") or "") if row is not None else ""

    def get_created_at(self, db: Session, conversation_id: str):
        row = db.query(self.conv_model).filter(self.conv_model.id == conversation_id).first()
        return getattr(row, "created_at", None) if row is not None else None


_TOOL_CONTENT_LIMIT = 2000


def _truncate(value, limit: int) -> str:
    """截断长文本，避免巨型工具结果撑爆 judge 上下文。"""
    text = str(value or "")
    return text if len(text) <= limit else text[:limit] + "…（已截断）"


def _extract_steps(rows, name_key: str, status_key: str | None,
                    result_keys: tuple) -> tuple[list, list, int]:
    """从消息行提取归一化动作序列与 OpenAI 格式轨迹。

    助手消息的每个工具步骤还原为 assistant.tool_calls + tool 消息对
    （OpenAI messages 格式），轨迹类评分器因此能拿到完整工具证据；
    工具结果取 result_keys 中第一个非空字段，长度截断防爆上下文。
    """
    actions: list = []
    openai_messages: list = []
    tool_error_count = 0
    for row_index, row in enumerate(rows):
        if row.role != "assistant":
            openai_messages.append({"role": row.role, "content": row.content or ""})
            continue
        tool_calls: list = []
        tool_messages: list = []
        for step_index, step in enumerate(row.steps or []):
            if not isinstance(step, dict):
                continue
            name = str(step.get(name_key) or "")
            if not name or name == "agent":
                # "agent" 是运行时自标记步骤（如 GOAL_FAILED），不是真实工具调用；
                # 但其失败状态计入错误数（自主模式失败标记）。
                if status_key and str(step.get(status_key) or "") in {"failed", "error"}:
                    tool_error_count += 1
                continue
            status = str(step.get(status_key) or "") if status_key else ""
            error = step.get("error")
            failed = bool(error) or status in {"error", "failed"}
            if failed:
                tool_error_count += 1
            result_text = ""
            for candidate in result_keys:
                value = step.get(candidate)
                if value:
                    result_text = _truncate(value, _TOOL_CONTENT_LIMIT)
                    break
            if not result_text:
                result_text = f"调用失败：{error or status}" if failed else "（无结果）"
            call_id = f"call_{row_index}_{step_index}"
            tool_calls.append({
                "id": call_id,
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": json.dumps(step.get("arguments") or {},
                                            ensure_ascii=False),
                },
            })
            tool_messages.append({"role": "tool", "tool_call_id": call_id,
                                  "name": name, "content": result_text})
            actions.append({"name": name, "arguments": step.get("arguments") or {},
                            "failed": failed, "status": status,
                            "preview": result_text[:600]})
        assistant_msg: dict = {"role": "assistant", "content": row.content or ""}
        if tool_calls:
            assistant_msg["tool_calls"] = tool_calls
        openai_messages.append(assistant_msg)
        openai_messages.extend(tool_messages)
    return actions, openai_messages, tool_error_count


def _finalize_trace(openai_messages: list, actions: list,
                    tool_error_count: int) -> Trace | None:
    """取最后一轮 user 提问与最后一条 assistant 答复组装 Trace。"""
    query = next((m["content"] for m in reversed(openai_messages)
                  if m["role"] == "user" and m.get("content")), "")
    response = next((m["content"] for m in reversed(openai_messages)
                      if m["role"] == "assistant" and m.get("content")), "")
    if not query or not response:
        return None
    return Trace(query=query, response=response, openai_messages=openai_messages,
                 actions=actions, tool_error_count=tool_error_count)


def _make_list(conv_model, msg_model, conv_field: str, title_col: str,
               order_col_name: str = "updated_at"):
    """通用会话分页列表工厂：按时间倒序，附带消息计数；since 为可选窗口过滤。"""

    def _list(db: Session, limit: int, offset: int,
              since=None) -> tuple[int, list[ConversationRef]]:
        total_query = db.query(func.count(conv_model.id))
        rows_query = db.query(conv_model)
        if since is not None:
            total_query = total_query.filter(conv_model.created_at >= since)
            rows_query = rows_query.filter(conv_model.created_at >= since)
        total = total_query.scalar() or 0
        order_col = getattr(conv_model, order_col_name)
        rows = (
            rows_query
            .order_by(order_col.desc().nullslast(), conv_model.id.desc())
            .limit(limit).offset(offset).all()
        )
        refs = [
            ConversationRef(
                id=r.id,
                title=(getattr(r, title_col, None) or "未命名会话"),
                created_at=getattr(r, "created_at", None),
                updated_at=getattr(r, "updated_at", None) or getattr(r, "created_at", None),
            )
            for r in rows
        ]
        ids = [r.id for r in rows]
        if ids:
            counts = dict(
                db.query(getattr(msg_model, conv_field), func.count(msg_model.id))
                .filter(getattr(msg_model, conv_field).in_(ids))
                .group_by(getattr(msg_model, conv_field))
                .all()
            )
            for ref in refs:
                ref.message_count = int(counts.get(ref.id, 0))
        return total, refs

    return _list


def _load_agent_runtime_trace(db: Session, conversation_id: str) -> Trace | None:
    rows = (
        db.query(AgentMessage)
        .filter(AgentMessage.conversation_id == conversation_id)
        .order_by(AgentMessage.created_at.asc(), AgentMessage.id.asc())
        .all()
    )
    actions, msgs, errors = _extract_steps(rows, "tool", None,
                                            ("result", "summary"))
    return _finalize_trace(msgs, actions, errors)


def _load_super_assistant_trace(db: Session, conversation_id: str) -> Trace | None:
    rows = (
        db.query(SuperAssistantMessage)
        .filter(SuperAssistantMessage.conversation_id == conversation_id,
                SuperAssistantMessage.status == "complete",
                SuperAssistantMessage.role.in_(("user", "assistant")))
        .order_by(SuperAssistantMessage.created_at.asc(), SuperAssistantMessage.id.asc())
        .all()
    )
    actions, msgs, errors = _extract_steps(rows, "toolName", "status", ("preview",))
    return _finalize_trace(msgs, actions, errors)


def _load_exploration_trace(db: Session, session_id: str) -> Trace | None:
    rows = (
        db.query(ExplorationMessage)
        .filter(ExplorationMessage.session_id == session_id)
        .order_by(ExplorationMessage.created_at.asc(), ExplorationMessage.id.asc())
        .all()
    )
    actions, msgs, errors = _extract_steps(rows, "tool", None, ("summary",))
    return _finalize_trace(msgs, actions, errors)


def _load_steward_trace(db: Session, conversation_id: str) -> Trace | None:
    rows = (
        db.query(StewardMessage)
        .filter(StewardMessage.conversation_id == conversation_id)
        .order_by(StewardMessage.created_at.asc(), StewardMessage.id.asc())
        .all()
    )
    actions, msgs, errors = _extract_steps(rows, "tool", None, ("resultSummary", "summary"))
    return _finalize_trace(msgs, actions, errors)


def _load_scene_trace(db: Session, conversation_id: str) -> Trace | None:
    """场景助手为纯问答（SceneMessage 无 steps 字段，MYW-79 生产实测崩溃点）：
    直接由 role/content 组装轨迹，不经过 _extract_steps。"""
    rows = (
        db.query(SceneMessage)
        .filter(SceneMessage.conversation_id == conversation_id)
        .order_by(SceneMessage.created_at.asc(), SceneMessage.id.asc())
        .all()
    )
    msgs = [{"role": r.role, "content": r.content or ""} for r in rows]
    return _finalize_trace(msgs, [], 0)


def build_adapters() -> dict[str, AssistantAdapter]:
    return {
        "ontology_agent": AssistantAdapter(
            key="ontology_agent", label="本体助手",
            description="/agent 本体智能体问答与分析报告（含完整工具轨迹）",
            conv_model=AgentConversation,
            list_conversations=_make_list(AgentConversation, AgentMessage,
                                          "conversation_id", "title"),
            load_trace=_load_agent_runtime_trace,
        ),
        "super_assistant": AssistantAdapter(
            key="super_assistant", label="超级助手",
            description="/super-assistant 通用智能协作入口（技能 / MCP 工具 / 记忆）",
            conv_model=SuperAssistantConversation,
            list_conversations=_make_list(SuperAssistantConversation, SuperAssistantMessage,
                                          "conversation_id", "title"),
            load_trace=_load_super_assistant_trace,
        ),
        "exploration": AssistantAdapter(
            key="exploration", label="建模对话",
            description="/explore 对话式业务建模与需求探索",
            conv_model=ExplorationSession,
            list_conversations=_make_list(ExplorationSession, ExplorationMessage,
                                          "session_id", "title"),
            load_trace=_load_exploration_trace,
        ),
        "steward": AssistantAdapter(
            key="steward", label="数据管家",
            description="数据集成内的 n8n 流水线管家对话",
            conv_model=StewardConversation,
            list_conversations=_make_list(StewardConversation, StewardMessage,
                                          "conversation_id", "title"),
            load_trace=_load_steward_trace,
        ),
        "scene_assistant": AssistantAdapter(
            key="scene_assistant", label="场景建模助手",
            description="三维场景的白模生成与修改对话",
            conv_model=SceneConversation,
            list_conversations=_make_list(SceneConversation, SceneMessage,
                                          "conversation_id", "title",
                                          order_col_name="created_at"),
            load_trace=_load_scene_trace,
        ),
    }


_ADAPTERTS: dict[str, AssistantAdapter] | None = None


def get_adapters() -> dict[str, AssistantAdapter]:
    global _ADAPTERTS
    if _ADAPTERTS is None:
        _ADAPTERTS = build_adapters()
    return _ADAPTERTS
