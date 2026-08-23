"""三维场景 — 场景建模助手服务。

对话式生成/修改草稿态场景定义：LLM 输出严格 JSON 动作
（set_definition 整体落新版本 / reply 澄清回复），后端负责解析、
DSL 校验、版本冻结与 SSE 事件流。产物与引擎分离的纪律不变——
助手只产出声明式 JSON，不做任何自由代码生成。

LLM 接入沿用平台既有通道：model_configs 选择器 + _call_llm patch
seam（模块属性导入，测试可 monkeypatch）；本域不存储任何密钥。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Generator

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.model_configs.selector import (
    llm_call_kwargs,
    select_llm_model_config,
)
from app.ontologies.extraction.llm_service import (
    _call_llm,
    _parse_response,
)
from app.scenes import models as m
from app.scenes import service, validation
from app.scenes.schemas import (
    SceneChatRequest, SceneConversationCreate, SceneDefinitionSave,
    SceneCreate,
)

MAX_CONTENT_LENGTH = 4000


def _now() -> datetime:
    return datetime.now(timezone.utc)


def sse(event: str, data: dict[str, Any]) -> str:
    return "event: " + event + "\ndata: " + json.dumps(
        data, ensure_ascii=False, default=str) + "\n\n"


def _bad_request(code: str, message: str) -> HTTPException:
    return HTTPException(status_code=400, detail={"code": code, "message": message})


SYSTEM_PROMPT = """你是平台内的三维场景建模助手，通过对话帮助用户以白模风格构建业务场景。

场景定义是纯声明式 JSON（禁函数），词汇如下：
- meta: {"id": kebab-case 全局锚点, "name": 场景名, "version": 语义版本}
- objects: 数组，每项 {"id","label","type","layout":{"x","z","w","d","h"}}；
  type 枚举 office|tower|warehouse|podium|plant；可选 extras:["parking"|"solar"]、
  info:{desc, metrics:[["指标名","{value}%"]] }、beacon:true
- relations: 数组 [{"from","to","kind"}]，kind 枚举 flow|dependency|hierarchy，
  from/to 必须引用存在的对象 id
- dataBindings: 数组 [{"target","source":"client","path":"a.b.c","metrics":[["名","模板"]],
  "rules":[{"when":"> 95","status":"alarm","message":"告警文案"}]}]；
  when 支持 > >= < <= == != 与 between a b，最后一条必须是 {"when":"else",...} 兜底；
  status 枚举 normal|warning|alarm；禁止任何函数或表达式计算
- stage(可选): camera/background/floor/ambience；sources(可选): client 型数据源

输出规则——只输出一个 JSON 对象，不要 markdown 围栏、不要解释性文字：
1) 需要生成或整体更新场景定义时：
   {"action":"set_definition","definition":{...完整定义...},"note":"不超过100字的变更说明"}
2) 需要澄清需求或回答问题时：
   {"action":"reply","message":"你的回复"}

增量对话时基于下方「当前定义」整体返回新的 definition（保持已有对象的 id 稳定，
除非用户要求改名）。用户描述模糊时优先用 reply 澄清关键对象清单，而不是臆测。
"""

def build_messages(db: Session, conversation: m.SceneConversation, content: str) -> list[dict]:
    history = (
        db.query(m.SceneMessage)
        .filter(m.SceneMessage.conversation_id == conversation.id)
        .order_by(m.SceneMessage.created_at.desc())
        .limit(m.CHAT_HISTORY_KEEP)
        .all()
    )
    messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
    if conversation.scene_id:
        scene = db.query(m.Scene).filter(m.Scene.id == conversation.scene_id).one_or_none()
        current = None
        if scene and scene.current_version_no >= 1:
            latest = (
                db.query(m.SceneVersion)
                .filter(
                    m.SceneVersion.scene_id == scene.id,
                    m.SceneVersion.version_no == scene.current_version_no,
                )
                .one_or_none()
            )
            current = latest.definition if latest else None
        messages.append({
            "role": "system",
            "content": "当前定义：\n" + json.dumps(
                current, ensure_ascii=False, default=str) if current else "当前定义：尚无（从零新建）",
        })
    for msg in reversed(history):
        messages.append({"role": msg.role, "content": msg.content})
    messages.append({"role": "user", "content": content})
    return messages


def _persist_assistant_message(
    db: Session, conversation: m.SceneConversation, *,
    content: str, status: str = "complete", version_no: int | None = None,
) -> None:
    db.add(m.SceneMessage(
        conversation_id=conversation.id,
        role="assistant",
        content=content[:6000],
        status=status,
        version_no=version_no,
    ))
    db.commit()


def require_conversation(db: Session, conversation_id: str) -> m.SceneConversation:
    conversation = (
        db.query(m.SceneConversation)
        .filter(m.SceneConversation.id == conversation_id)
        .one_or_none()
    )
    if conversation is None:
        raise HTTPException(
            status_code=404, detail={"code": "scene_conversation_not_found"})
    return conversation


def create_conversation(
    db: Session, body: SceneConversationCreate, user,
) -> m.SceneConversation:
    if body.scene_id:
        from app.scenes.query_service import require_scene
        require_scene(db, body.scene_id)
    conversation = m.SceneConversation(
        scene_id=body.scene_id,
        title=(body.title or "").strip()[:200],
        model_config_id=body.model_config_id,
        created_by=getattr(user, "id", None),
    )
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    return conversation


def chat_stream(
    db: Session, conversation: m.SceneConversation,
    body: SceneChatRequest, user,
) -> Generator[str, None, None]:
    """处理一轮对话并产出 SSE 事件流。

    事件契约（新增即契约，发布后不可改名）：
      meta          {conversation_id, scene_id}
      text          {content}                        —— reply 动作的澄清回复
      scene_updated {scene_id, name, version_no, status, note}
      error         {code, message, issues?}
      done          {}
    """
    db.add(m.SceneMessage(
        conversation_id=conversation.id, role="user", content=body.content))
    if not conversation.title:
        conversation.title = body.content[:50]
    if body.model_config_id:
        conversation.model_config_id = body.model_config_id
    db.commit()

    yield sse("meta", {
        "conversation_id": conversation.id,
        "scene_id": conversation.scene_id,
    })

    try:
        config = select_llm_model_config(
            db, body.model_config_id or conversation.model_config_id)
        call_kwargs = llm_call_kwargs(config) if config else None
        if not call_kwargs:
            message = "尚未配置可用的对话模型，请先到「模型配置」启用一个 LLM"
            _persist_assistant_message(db, conversation, content=message, status="error")
            yield sse("error", {"code": "model_unavailable", "message": message})
            yield sse("done", {})
            return

        # llm_call_kwargs 会附带 model_config_id / max_*_tokens 等键，
        # _call_llm 签名只收 provider/api_key/api_base/model/messages/json_mode，
        # 必须显式取字段转发（**展开会 TypeError）。
        raw = _call_llm(
            provider=call_kwargs["provider"],
            api_key=call_kwargs["api_key"],
            api_base=call_kwargs.get("api_base"),
            model=call_kwargs["model"],
            messages=build_messages(db, conversation, body.content),
        )
        parsed = _parse_response(raw) if isinstance(raw, str) else raw
        action = parsed.get("action") if isinstance(parsed, dict) else None

        if action == "reply":
            text = str(parsed.get("message") or "")
            _persist_assistant_message(db, conversation, content=text)
            yield sse("text", {"content": text})
            yield sse("done", {})
            return

        if action == "set_definition":
            definition = parsed.get("definition")
            issues = validation.validate_definition(definition)
            if issues:
                _persist_assistant_message(
                    db, conversation,
                    content="生成的定义未通过校验：" + "; ".join(
                        issue["path"] + " " + issue["message"] for issue in issues[:5]),
                    status="error",
                )
                yield sse("error", {
                    "code": "invalid_definition",
                    "message": "生成的场景定义未通过校验",
                    "issues": issues,
                })
                yield sse("done", {})
                return

            note = str(parsed.get("note") or "场景助手更新")
            if conversation.scene_id:
                scene = (
                    db.query(m.Scene)
                    .filter(m.Scene.id == conversation.scene_id)
                    .one_or_none()
                )
                if scene is None:
                    raise _bad_request("scene_not_found", "会话绑定的场景已不存在")
            else:
                scene = service.create_scene(db, SceneCreate(
                    name=str((definition or {}).get("meta", {}).get("name", "未命名场景"))[:120],
                ), user)
                conversation.scene_id = scene.id
                db.commit()

            version = service.save_definition(
                db, scene,
                SceneDefinitionSave(
                    definition=validation.normalize_definition(definition),
                    note=note,
                ),
                source=m.VERSION_SOURCE_ASSISTANT, user=user,
            )
            _persist_assistant_message(
                db, conversation, content=note, version_no=version.version_no)
            yield sse("scene_updated", {
                "scene_id": scene.id,
                "name": scene.name,
                "version_no": version.version_no,
                "status": scene.status,
                "note": note,
            })
            yield sse("done", {})
            return

        _persist_assistant_message(
            db, conversation, content="助手返回了未知动作：" + str(action), status="error")
        yield sse("error", {"code": "unknown_action", "message": "助手返回了无法理解的动作"})
        yield sse("done", {})
    except Exception as exc:  # noqa: BLE001 —— 兜底转为 error 事件，保证流总有 done
        message = str(exc) or exc.__class__.__name__
        try:
            _persist_assistant_message(db, conversation, content=message, status="error")
        except Exception:  # noqa: BLE001
            pass
        yield sse("error", {"code": "assistant_failed", "message": message})
        yield sse("done", {})

def _iso(value):
    return value.isoformat() if value else None

def conversation_out(conversation: m.SceneConversation) -> dict:
    return {
        'id': conversation.id,
        'scene_id': conversation.scene_id,
        'title': conversation.title,
        'model_config_id': conversation.model_config_id,
        'created_at': _iso(conversation.created_at),
        'updated_at': _iso(conversation.updated_at),
    }

def message_out(message: m.SceneMessage) -> dict:
    return {
        'id': message.id,
        'conversation_id': message.conversation_id,
        'role': message.role,
        'content': message.content,
        'status': message.status,
        'version_no': message.version_no,
        'created_at': _iso(message.created_at),
    }

def list_conversations(db: Session, *, scene_id: str | None = None, page: int = 1, page_size: int = 50) -> dict:
    query = db.query(m.SceneConversation)
    if scene_id:
        query = query.filter(m.SceneConversation.scene_id == scene_id)
    total = query.count()
    rows = query.order_by(m.SceneConversation.updated_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return {'items': [conversation_out(row) for row in rows], 'total': total}

def list_messages(db: Session, conversation: m.SceneConversation) -> dict:
    rows = db.query(m.SceneMessage).filter(m.SceneMessage.conversation_id == conversation.id).order_by(m.SceneMessage.created_at.asc()).all()
    return {'items': [message_out(row) for row in rows], 'total': len(rows)}
