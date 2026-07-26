"""Sentinel daemon ownership follows the FastAPI application lifespan."""
from __future__ import annotations

import threading

from app.ontologies.sentinels import cdc, scan_worker


class _JoinableWorker:
    def __init__(self):
        self.alive = True
        self.join_timeouts: list[float] = []

    def is_alive(self) -> bool:
        return self.alive

    def join(self, timeout: float) -> None:
        self.join_timeouts.append(timeout)
        self.alive = False


def test_stop_cdc_worker_disables_and_joins_owned_daemon(monkeypatch):
    worker = _JoinableWorker()
    stop_event = threading.Event()
    monkeypatch.setattr(cdc, "_dispatch_worker", worker)
    monkeypatch.setattr(cdc, "_dispatch_stop_event", stop_event)
    monkeypatch.setattr(cdc, "_background_worker_enabled", True)

    stopped = cdc.stop_cdc_worker(timeout=0.25)

    assert stopped is True
    assert stop_event.is_set()
    assert worker.join_timeouts == [0.25]
    assert cdc._dispatch_worker is None
    assert cdc._background_worker_enabled is False


def test_stop_scan_worker_joins_and_resets_lifecycle_state(monkeypatch):
    worker = _JoinableWorker()
    stop_event = threading.Event()
    monkeypatch.setattr(scan_worker, "_thread", worker)
    monkeypatch.setattr(scan_worker, "_stop_event", stop_event)
    monkeypatch.setattr(scan_worker, "_started", True)

    stopped = scan_worker.stop_scan_worker(timeout=0.25)

    assert stopped is True
    assert stop_event.is_set()
    assert worker.join_timeouts == [0.25]
    assert scan_worker._thread is None
    assert scan_worker._started is False
