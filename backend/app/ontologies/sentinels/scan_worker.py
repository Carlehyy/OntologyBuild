"""定期扫描 worker — 后台线程，每 tick 调 run_scheduled 评估到期哨兵。"""
from __future__ import annotations

import logging
import os
import threading

logger = logging.getLogger(__name__)

# 扫描 tick(秒)。哨兵各自的 scan_interval_seconds 决定它是否在本 tick 到期。
TICK_SECONDS = int(os.getenv("SENTINEL_SCAN_TICK", "15"))

_started = False
_thread: threading.Thread | None = None
_stop_event = threading.Event()
_last_error: str | None = None


def _loop():
    global _last_error
    from app.services.sentinel.engine import run_scheduled
    from app.database import SessionLocal
    while not _stop_event.wait(TICK_SECONDS):
        db = SessionLocal()
        try:
            run_scheduled(db)
            _last_error = None
        except Exception as e:  # noqa: BLE001
            _last_error = str(e)
            logger.warning(f"Sentinel scan tick 失败: {e}")
            db.rollback()
        finally:
            db.close()


def start_scan_worker() -> bool:
    global _started, _thread
    if _started and _thread is not None and _thread.is_alive():
        return True
    if os.getenv("SENTINEL_SCAN_ENABLED", "1") in ("0", "false", "False"):
        return False
    _stop_event.clear()
    _thread = threading.Thread(
        target=_loop, daemon=True, name="sentinel-scan-worker")
    _thread.start()
    _started = _thread.is_alive()
    return _started


def stop_scan_worker(*, timeout: float = 5.0) -> bool:
    """Stop the scheduled scanner owned by the current app lifespan."""
    global _started, _thread
    _stop_event.set()
    worker = _thread
    if worker is None:
        _started = False
        return True
    if worker is threading.current_thread():
        return False
    worker.join(timeout=max(0.0, timeout))
    stopped = not worker.is_alive()
    if stopped and _thread is worker:
        _thread = None
        _started = False
    return stopped


def scan_worker_status() -> dict:
    alive = bool(_thread is not None and _thread.is_alive())
    return {"started": _started, "alive": alive, "last_error": _last_error}
