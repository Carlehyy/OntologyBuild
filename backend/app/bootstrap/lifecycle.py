"""FastAPI process lifecycle orchestration.

The ordering in this module is a runtime contract: database preparation and
owned background services start in the same sequence they historically used in
``app.main`` and shut down in the reverse ownership-safe sequence.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import settings


_main_logger = logging.getLogger("app.main")


@asynccontextmanager
async def application_lifespan(
    app: FastAPI,
    *,
    seed_database: Callable[[], None],
) -> AsyncIterator[None]:
    seed_database()
    # API-Hub keeps its original isolated SQLite store and refresh scheduler.
    # It is initialized inside the host application's lifecycle so there is
    # still only one backend process to operate.
    from app.api_hub import db as api_hub_db, scheduler as api_hub_scheduler

    api_hub_db.init_db()
    api_hub_scheduler.start()
    # 哨兵引擎：注册 CDC(监听对象改动→变化驱动) + 启动定期扫描 worker
    try:
        from app.services.sentinel import register_cdc, start_scan_worker

        register_cdc(start_worker=True)
        sentinel_started = start_scan_worker()
        if settings.environment == "production" and not sentinel_started:
            raise RuntimeError("Sentinel scan worker is disabled or failed to start")
    except Exception as exc:
        if settings.environment == "production":
            raise RuntimeError("Sentinel engine failed to initialize") from exc
        _main_logger.warning(f"Sentinel 启动失败: {exc}")
    # 初始化 Neo4j 索引；开发环境可降级，生产环境必须完成后才就绪。
    try:
        from app.services.v2.graph.index_setup import setup_indexes

        index_result = setup_indexes()
        if settings.environment == "production" and (
            index_result.get("status") != "done"
            or any(
                item.get("status") != "ok"
                for item in index_result.get("results", [])
            )
        ):
            raise RuntimeError(f"Neo4j index setup incomplete: {index_result}")
    except Exception as exc:
        if settings.environment == "production":
            raise RuntimeError("Neo4j indexes failed to initialize") from exc
    # 启动数据同步任务调度器（后台线程）
    try:
        from app.services.v2.sync_scheduler import get_sync_scheduler

        scheduler = get_sync_scheduler()
        scheduler.start()
        if settings.environment == "production" and not scheduler.healthy:
            raise RuntimeError(
                "Data scheduler is not healthy: "
                f"{scheduler.last_error or 'not running'}"
            )
    except Exception as exc:
        if settings.environment == "production":
            raise RuntimeError("Data scheduler failed to initialize") from exc
        _main_logger.warning(f"SyncScheduler 启动失败: {exc}")
    from app.api_hub import mcp_server as api_hub_mcp
    from app.settings.object_storage import mcp_server as minio_mcp

    # session manager 每实例只能 run 一次；重复进入 lifespan（如测试）需重建
    api_hub_public, api_hub_system = api_hub_mcp.reset_session_managers()
    from app.data_channel.file_assets.service import file_asset_cleanup_loop

    file_cleanup_task = asyncio.create_task(file_asset_cleanup_loop())
    try:
        async with (
            minio_mcp.reset_session_manager().run(),
            api_hub_public.run(),
            api_hub_system.run(),
        ):
            yield
    finally:
        file_cleanup_task.cancel()
        try:
            await file_cleanup_task
        except asyncio.CancelledError:
            pass
        from app.data_channel.steward.browser_runtime import browser_manager

        browser_manager.close_all()
        try:
            from app.services.v2.sync_scheduler import get_sync_scheduler

            get_sync_scheduler().shutdown()
        except Exception:  # noqa: BLE001
            pass
        try:
            from app.services.sentinel import (
                stop_cdc_worker,
                stop_scan_worker,
            )

            # Stop the producer first, then the durable outbox consumer.
            stop_scan_worker()
            stop_cdc_worker()
        except Exception:  # noqa: BLE001
            pass
        api_hub_scheduler.shutdown()
