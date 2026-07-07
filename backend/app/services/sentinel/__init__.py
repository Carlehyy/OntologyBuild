"""哨兵引擎 (Sentinel Engine) — 反应式本体运行时。

监听对象实体改动 → 跨对象条件评估 → 命中执行动作列表。
三入口：手动 / 变化驱动 / 定期扫描。
"""
from app.services.sentinel.cdc import register_cdc
from app.services.sentinel.engine import run_manual, run_for_change, run_scheduled
from app.services.sentinel.scan_worker import start_scan_worker

__all__ = ["register_cdc", "start_scan_worker",
           "run_manual", "run_for_change", "run_scheduled"]
