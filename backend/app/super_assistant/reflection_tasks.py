"""超级助手反思任务的 NATS 消息处理器（executor 进程内运行）。

与流水线 executor 的既有 handler 同一模式：解析 payload → 开独立
SessionLocal → ``asyncio.to_thread`` 调同步 reflection_service。业务异常
只在 handler 内记日志（run_* 自身也会把 LLM/解析异常记入 run.error），
不向外抛——逃到 executor 的异常会被 nak 重投，而反思失败重投无意义。
"""
from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)


async def run_micro_reflection_message(payload: dict) -> None:
    """super_assistant.reflect.micro：每轮对话后的轻量反思。"""
    owner_id = str(payload["owner_id"])
    conversation_id = str(payload["conversation_id"])
    message_id = str(payload["message_id"])

    from app.database import SessionLocal
    from app.super_assistant import reflection_service

    db = SessionLocal()
    try:
        await asyncio.to_thread(
            reflection_service.run_micro_reflection,
            db,
            owner_id,
            conversation_id,
            message_id,
        )
        logger.info(
            "超级助手 micro 反思完成（conversation=%s message=%s）",
            conversation_id,
            message_id,
        )
    except Exception:
        logger.exception(
            "超级助手 micro 反思执行失败（conversation=%s message=%s）",
            conversation_id,
            message_id,
        )
    finally:
        db.close()


async def run_full_reflection_message(payload: dict) -> None:
    """super_assistant.reflect.full：整会话手动反思。"""
    owner_id = str(payload["owner_id"])
    conversation_id = str(payload["conversation_id"])

    from app.database import SessionLocal
    from app.super_assistant import reflection_service

    db = SessionLocal()
    try:
        await asyncio.to_thread(
            reflection_service.run_full_reflection,
            db,
            owner_id,
            conversation_id,
        )
        logger.info(
            "超级助手 full 反思完成（conversation=%s）",
            conversation_id,
        )
    except Exception:
        logger.exception(
            "超级助手 full 反思执行失败（conversation=%s）",
            conversation_id,
        )
    finally:
        db.close()


async def run_focused_reflection_message(payload: dict) -> None:
    """super_assistant.reflect.focused：带人工提示的定向技能反思。"""
    owner_id = str(payload["owner_id"])
    conversation_id = str(payload["conversation_id"])
    message_id = str(payload["message_id"])
    hint = str(payload.get("hint") or "")

    from app.database import SessionLocal
    from app.super_assistant import reflection_service

    db = SessionLocal()
    try:
        await asyncio.to_thread(
            reflection_service.run_focused_reflection,
            db,
            owner_id,
            conversation_id,
            message_id,
            hint,
        )
        logger.info(
            "超级助手 focused 反思完成（conversation=%s message=%s）",
            conversation_id,
            message_id,
        )
    except Exception:
        logger.exception(
            "超级助手 focused 反思执行失败（conversation=%s message=%s）",
            conversation_id,
            message_id,
        )
    finally:
        db.close()
