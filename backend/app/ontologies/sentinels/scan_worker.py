"""定期扫描 worker — 后台线程，每 tick 调 run_scheduled 评估到期哨兵。"""
from __future__ import annotations

import logging
import os
import threading
import time

logger = logging.getLogger(__name__)

# 扫描 tick(秒)。哨兵各自的 scan_interval_seconds 决定它是否在本 tick 到期。
TICK_SECONDS = int(os.getenv("SENTINEL_SCAN_TICK", "15"))

_started = False


def _loop():
    from app.services.sentinel.engine import run_scheduled
    from app.database import SessionLocal
    while True:
        time.sleep(TICK_SECONDS)
        db = SessionLocal()
        try:
            run_scheduled(db)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Sentinel scan tick 失败: {e}")
            db.rollback()
        finally:
            db.close()


def start_scan_worker() -> None:
    global _started
    if _started:
        return
    if os.getenv("SENTINEL_SCAN_ENABLED", "1") in ("0", "false", "False"):
        return
    threading.Thread(target=_loop, daemon=True).start()
    _started = True
