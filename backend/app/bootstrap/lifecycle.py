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
    api_hub_scheduler = None
    api_hub_scheduler_attempted = False
    sentinel_attempted = False
    data_scheduler = None
    data_scheduler_attempted = False
    file_cleanup_task = None
    runtime_resources_started = False
    try:
        seed_database()

        if settings.environment != "test":
            # Probe every configured integration before starting any owned
            # background thread. Chromium CDP is handled as advisory by this
            # probe; PostgreSQL, Redis, Neo4j, MinIO and n8n fail closed.
            from app.shared.dependency_probe import (
                probe_startup_dependencies,
            )

            probe_startup_dependencies()

        # 初始化 Neo4j 索引。除显式单元测试环境外，索引初始化是启动契约，
        # 不能让应用在缺少图约束/索引时继续对外服务。
        try:
            from app.services.v2.graph.index_setup import setup_indexes

            index_result = setup_indexes()
            if settings.environment != "test" and (
                index_result.get("status") != "done"
                or any(
                    item.get("status") != "ok"
                    for item in index_result.get("results", [])
                )
            ):
                raise RuntimeError(
                    f"Neo4j index setup incomplete: {index_result}"
                )
        except Exception as exc:
            if settings.environment != "test":
                raise RuntimeError(
                    "Neo4j indexes failed to initialize"
                ) from exc

        # Migrations deliberately fence every pre-existing ontology because
        # its older Neo4j shape cannot be assumed to satisfy the stable-ID
        # contract. Reconcile before any worker or request can observe it.
        if settings.environment != "test":
            try:
                from app.ontologies.projection_state import (
                    repair_unready_projections,
                )

                repaired = repair_unready_projections()
                if repaired:
                    _main_logger.info(
                        "Validated %s ontology Neo4j projection(s) at startup",
                        repaired,
                    )
            except Exception as exc:
                raise RuntimeError(
                    "Ontology Neo4j projections failed to initialize"
                ) from exc

        # External state is now validated. Only after that barrier may the
        # process create background workers which would otherwise leak when a
        # later startup check fails.
        from app.api_hub import (
            db as api_hub_db,
            scheduler as current_api_hub_scheduler,
        )

        api_hub_scheduler = current_api_hub_scheduler
        api_hub_db.init_db()
        api_hub_scheduler_attempted = True
        api_hub_scheduler.start()

        # 哨兵引擎：注册 CDC(监听对象改动→变化驱动) + 启动定期扫描 worker
        try:
            from app.services.sentinel import register_cdc, start_scan_worker

            sentinel_attempted = True
            register_cdc(start_worker=True)
            sentinel_started = start_scan_worker()
            if settings.environment == "production" and not sentinel_started:
                raise RuntimeError(
                    "Sentinel scan worker is disabled or failed to start"
                )
        except Exception as exc:
            if settings.environment == "production":
                raise RuntimeError(
                    "Sentinel engine failed to initialize"
                ) from exc
            _main_logger.warning("Sentinel 启动失败: %s", exc)

        # 启动数据同步任务调度器（后台线程）
        try:
            from app.services.v2.sync_scheduler import get_sync_scheduler

            data_scheduler = get_sync_scheduler()
            data_scheduler_attempted = True
            data_scheduler.start()
            if settings.environment == "production" and not data_scheduler.healthy:
                raise RuntimeError(
                    "Data scheduler is not healthy: "
                    f"{data_scheduler.last_error or 'not running'}"
                )
        except Exception as exc:
            if settings.environment == "production":
                raise RuntimeError(
                    "Data scheduler failed to initialize"
                ) from exc
            _main_logger.warning("SyncScheduler 启动失败: %s", exc)

        from app.api_hub import mcp_server as api_hub_mcp
        from app.settings.object_storage import mcp_server as minio_mcp

        # session manager 每实例只能 run 一次；重复进入 lifespan（如测试）需重建
        api_hub_public, api_hub_system = api_hub_mcp.reset_session_managers()
        from app.data_channel.file_assets.service import (
            file_asset_cleanup_loop,
        )

        file_cleanup_task = asyncio.create_task(file_asset_cleanup_loop())
        runtime_resources_started = True
        async with (
            minio_mcp.reset_session_manager().run(),
            api_hub_public.run(),
            api_hub_system.run(),
        ):
            yield
    finally:
        if file_cleanup_task is not None:
            file_cleanup_task.cancel()
            try:
                await file_cleanup_task
            except asyncio.CancelledError:
                pass
            except Exception:  # noqa: BLE001
                _main_logger.exception("File cleanup task shutdown failed")
        if runtime_resources_started:
            try:
                from app.data_channel.steward.browser_runtime import (
                    browser_manager,
                )

                browser_manager.close_all()
            except Exception:  # noqa: BLE001
                _main_logger.exception("Browser runtime cleanup failed")
        if data_scheduler_attempted and data_scheduler is not None:
            try:
                data_scheduler.shutdown()
            except Exception:  # noqa: BLE001
                _main_logger.exception("Data scheduler cleanup failed")
        if sentinel_attempted:
            try:
                from app.services.sentinel import (
                    stop_cdc_worker,
                    stop_scan_worker,
                )

                # Stop the producer first, then the durable outbox consumer.
                stop_scan_worker()
                stop_cdc_worker()
            except Exception:  # noqa: BLE001
                _main_logger.exception("Sentinel cleanup failed")
        if api_hub_scheduler_attempted and api_hub_scheduler is not None:
            try:
                api_hub_scheduler.shutdown()
            except Exception:  # noqa: BLE001
                _main_logger.exception("API Hub scheduler cleanup failed")
