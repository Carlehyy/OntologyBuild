"""
流水线任务 NATS executor 进程

独立进程消费 JetStream 工作队列并执行流水线调度任务，把执行负载（GIL、
内存、数据库连接）移出 Web 进程。NATS 只负责送达；并发正确性由执行引擎
的数据库原子租约（``engine._claim_task``）兜底，重复消息在 claim 失败
返回「任务正在执行中」后直接 ack 丢弃。

启动方式::

    python -m app.data_channel.pipeline_tasks.nats_executor

已知语义：进程被打断时，in-flight 任务表现为「执行中断」，其数据库租约
最长 6 小时（``engine._TASK_LEASE``）后过期，再由 Web 进程内的对账器
（默认 5 分钟周期，``reconciler.reconcile_pipeline_executions``）收口为
failed——与此前部署打断内联执行的语义一致。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sys
import time

logger = logging.getLogger(__name__)

HEARTBEAT_PATH = "/tmp/nats_executor.heartbeat"
# compose stop 默认宽限 30 秒，这里留出 5 秒余量
_SHUTDOWN_TIMEOUT_SECONDS = 25
# ack_wait=30s，执行期间每 20 秒续约一次防止重投
_IN_PROGRESS_INTERVAL_SECONDS = 20
_FETCH_TIMEOUT_SECONDS = 5
_HEARTBEAT_INTERVAL_SECONDS = 5
_CONSUMER_DURABLE = "pipeline-executor"


def _touch_heartbeat() -> None:
    """刷新心跳文件 mtime，供 compose 健康检查判断主循环存活。"""
    now = time.time()
    fd = os.open(HEARTBEAT_PATH, os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o644)
    os.close(fd)
    os.utime(HEARTBEAT_PATH, (now, now))


class PipelineExecutor:
    """流水线执行消费者：拉取消息、限并发执行、按结果 ack/nak。"""

    def __init__(self) -> None:
        from app.config import settings

        self._concurrency = max(
            1, int(settings.pipeline_executor_concurrency or 2)
        )
        self._semaphore = asyncio.Semaphore(self._concurrency)
        self._shutdown = asyncio.Event()
        self._in_flight: set[asyncio.Task] = set()

    def request_shutdown(self) -> None:
        """信号回调：停止拉取与续约，在途执行最多再宽限一段后退出。"""
        if not self._shutdown.is_set():
            logger.info("流水线 executor 收到关闭信号，停止拉取新消息")
        self._shutdown.set()

    async def _process_message(self, msg) -> None:
        """执行一条消息并按结果 ack/nak；执行期间周期续约。"""
        try:
            payload = json.loads(msg.data.decode())
            task_id = str(payload["task_id"])
            trigger_type = str(payload.get("trigger_type") or "manual")
        except Exception:
            logger.error("流水线执行消息无法解析，nak 丢弃: %r", msg.data)
            await msg.nak()
            return

        from app.data_channel.pipeline_tasks.engine import execute_pipeline_task

        execute = asyncio.ensure_future(
            asyncio.to_thread(execute_pipeline_task, task_id, trigger_type)
        )
        while True:
            done, _ = await asyncio.wait(
                {execute}, timeout=_IN_PROGRESS_INTERVAL_SECONDS
            )
            if done:
                break
            if self._shutdown.is_set():
                # 关闭流程中不再续约，但仍等执行线程自然结束；消息若因
                # ack_wait 到期被重投，由数据库租约拦下重复执行
                continue
            try:
                await msg.in_progress()
            except Exception:
                logger.exception("PipelineTask %s 执行消息续约失败", task_id)
        try:
            result = execute.result()
        except Exception:
            # execute_pipeline_task 内部消化所有业务异常；能逃到这里的是
            # 线程级意外，交给 nak 重投
            logger.exception("PipelineTask %s 执行线程异常逃逸", task_id)
            await msg.nak()
            return
        if isinstance(result, dict) and result.get("status") == "ok":
            logger.info("PipelineTask %s 执行完成", task_id)
        else:
            # claim 冲突（"任务正在执行中"）与业务失败都算送达成功：
            # 正确性由数据库租约兜底，消息直接 ack 丢弃
            logger.warning(
                "PipelineTask %s 执行未成功: %s",
                task_id,
                (result or {}).get("error") if isinstance(result, dict) else result,
            )
        await msg.ack()

    async def _run_message(self, msg) -> None:
        try:
            await self._process_message(msg)
        except Exception:
            logger.exception("流水线执行消息处理异常")
            try:
                await msg.nak()
            except Exception:
                logger.exception("流水线执行消息 nak 失败")
        finally:
            self._semaphore.release()

    async def _fetch_loop(self, subscription) -> None:
        """批量拉取消息并限并发派发执行；关闭信号到来时退出循环。"""
        while not self._shutdown.is_set():
            try:
                messages = await subscription.fetch(
                    batch=self._concurrency, timeout=_FETCH_TIMEOUT_SECONDS
                )
            except asyncio.TimeoutError:
                # nats.py 拉取超时即为空批次，继续等下一批/关闭信号
                continue
            except Exception:
                if self._shutdown.is_set():
                    break
                logger.exception("流水线执行消息拉取失败，5 秒后重试")
                await asyncio.sleep(5)
                continue
            for msg in messages:
                if self._shutdown.is_set():
                    # 已拉取但未开始的消息立即 nak，让其他 executor 尽快接管
                    try:
                        await msg.nak()
                    except Exception:
                        logger.exception("关闭时退回未执行消息失败")
                    continue
                await self._semaphore.acquire()
                if self._shutdown.is_set():
                    # 等待信号量期间收到关闭信号：同样不再启动新执行
                    self._semaphore.release()
                    try:
                        await msg.nak()
                    except Exception:
                        logger.exception("关闭时退回未执行消息失败")
                    continue
                task = asyncio.ensure_future(self._run_message(msg))
                self._in_flight.add(task)
                task.add_done_callback(self._in_flight.discard)

    async def _heartbeat_loop(self) -> None:
        while True:
            _touch_heartbeat()
            await asyncio.sleep(_HEARTBEAT_INTERVAL_SECONDS)

    async def run(self) -> None:
        import nats
        from nats.js.api import ConsumerConfig

        from app.config import settings
        from app.data_channel.pipeline_tasks.dispatch import (
            PIPELINE_EXECUTE_SUBJECT,
            PIPELINE_STREAM,
            ensure_pipeline_stream,
        )

        nc = await nats.connect(settings.nats_url.strip(), connect_timeout=3)
        heartbeat = asyncio.ensure_future(self._heartbeat_loop())
        try:
            js = nc.jetstream()
            await ensure_pipeline_stream(js)
            subscription = await js.pull_subscribe(
                PIPELINE_EXECUTE_SUBJECT,
                durable=_CONSUMER_DURABLE,
                stream=PIPELINE_STREAM,
                config=ConsumerConfig(ack_wait=30, max_deliver=5),
            )
            logger.info(
                "流水线 executor 已启动（并发上限 %d，durable=%s）",
                self._concurrency,
                _CONSUMER_DURABLE,
            )
            await self._fetch_loop(subscription)
            if self._in_flight:
                logger.info(
                    "等待 %d 个在途执行结束（至多 %d 秒）",
                    len(self._in_flight),
                    _SHUTDOWN_TIMEOUT_SECONDS,
                )
                _, pending = await asyncio.wait(
                    self._in_flight, timeout=_SHUTDOWN_TIMEOUT_SECONDS
                )
                for task in pending:
                    task.cancel()
            logger.info("流水线 executor 已退出")
        finally:
            heartbeat.cancel()
            try:
                await nc.drain()
            except Exception:
                logger.exception("流水线 executor 关闭 NATS 连接失败")


async def _async_main() -> int:
    from app.config import settings

    nats_url = (settings.nats_url or "").strip()
    if not nats_url:
        print(
            "流水线 executor 启动失败：未配置 NATS_URL（JetStream 消息通道），"
            "生产环境必须显式配置后再启动",
            file=sys.stderr,
        )
        return 1

    executor = PipelineExecutor()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, executor.request_shutdown)
        except (NotImplementedError, RuntimeError):  # 非 Unix 事件循环
            pass
    await executor.run()
    return 0


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    # executor 进程不经过 app.main 的导入链，必须自行注册全部表映射——
    # 否则首次 INSERT 的依赖排序解析不到 FK 目标表（如 v2_dataset_versions），
    # 运行记录初始化会以 NoReferencedTableError 失败。
    from app.model_registry import import_all_models

    import_all_models()
    raise SystemExit(asyncio.run(_async_main()))


if __name__ == "__main__":
    main()
