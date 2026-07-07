"""采集引擎注册表测试 — 新引擎接入点的行为契约"""
from app.data_channel.pipelines.engine_registry import (
    CANVAS_ENGINES,
    get_engine_runner,
    known_engines,
    register_engine,
)


def test_canvas_engine_aliases():
    for alias in (None, "", "canvas"):
        assert alias in CANVAS_ENGINES


def test_unknown_engine_returns_none():
    assert get_engine_runner("no-such-engine") is None


def test_runtime_registration():
    calls = []

    def fake_runner(db, pl, run, write_opts):
        calls.append((pl, write_opts))

    register_engine("acme-flow", fake_runner)
    runner = get_engine_runner("acme-flow")
    assert runner is fake_runner
    assert "acme-flow" in known_engines()
    runner(None, "pl", "run", {"mode": "append"})
    assert calls == [("pl", {"mode": "append"})]


def test_builtin_n8n_registered():
    assert "n8n" in known_engines()
