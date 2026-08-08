"""
流水线任务 NATS JetStream 派发

Web 进程只负责「送达」：把触发请求发布到 JetStream 工作队列流，由独立
executor 进程消费执行。重复消息无害——执行引擎的数据库原子租约
（``engine._claim_task``）保证同一任务最多一个执行者领取成功，重复
投递在 claim 失败返回「任务正在执行中」后直接 ack 丢弃。
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime

logger = logging.getLogger(__name__)

PIPELINE_STREAM = "PIPELINE_TASKS"
PIPELINE_EXECUTE_SUBJECT = "pipeline.task.execute"

# 进程内缓存：每个进程只在首次派发时确保一次 Stream
_stream_ensured = False


async def ensure_pipeline_stream(js) -> None:
    """确保流水线执行流存在；已存在则跳过。"""
    from nats.js.api import RetentionPolicy, StreamConfig

    try:
        await js.add_stream(
            StreamConfig(
                name=PIPELINE_STREAM,
                subjects=[PIPELINE_EXECUTE_SUBJECT],
                # 工作队列语义：每条消息只被一个 executor 消费
                retention=RetentionPolicy.WORK_QUEUE,
                max_age=7 * 24 * 3600,       # 7 天兜底保留
                duplicate_window=10 * 60,    # 10 分钟 Msg-Id 去重窗
            )
        )
    except Exception as exc:
        # 多进程同时首次派发时只有一个能建流成功；“名字已占用”即已存在
        if "already in use" not in str(exc):
            raise


async def _dispatch(task_id: str, trigger_type: str, nats_url: str) -> None:
    global _stream_ensured
    import nats

    nc = await nats.connect(nats_url, connect_timeout=3)
    try:
        js = nc.jetstream()
        if not _stream_ensured:
            await ensure_pipeline_stream(js)
            _stream_ensured = True
        payload = json.dumps(
            {
                "task_id": task_id,
                "trigger_type": trigger_type,
                "dispatched_at": datetime.utcnow().isoformat(),
            }
        ).encode()
        # 纳秒时间戳让每次派发都有独立 Msg-Id，去重窗内不误伤正常连发
        await js.publish(
            PIPELINE_EXECUTE_SUBJECT,
            payload,
            headers={"Nats-Msg-Id": f"{task_id}:{trigger_type}:{time.time_ns()}"},
        )
    finally:
        await nc.drain()


def dispatch_pipeline_task(task_id: str, trigger_type: str) -> None:
    """同步派发入口：供 APScheduler 调度线程与 FastAPI 请求线程调用。

    nats.py 是 asyncio 客户端；每次派发建立独立短连接，避免跨线程共享
    事件循环。调度频率低（小时级），连接开销可忽略。
    """
    from app.config import settings

    nats_url = (settings.nats_url or "").strip()
    if not nats_url:
        raise RuntimeError(
            "流水线任务派发失败：未配置 NATS_URL（JetStream 消息通道），"
            "请在环境配置中显式设置后重试"
        )
    asyncio.run(_dispatch(task_id, trigger_type, nats_url))
