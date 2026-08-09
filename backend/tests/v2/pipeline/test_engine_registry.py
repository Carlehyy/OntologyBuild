"""采集引擎注册表测试 — 新引擎接入点的行为契约"""
from app.data_channel.pipelines.engine_registry import (
    get_engine_runner,
    known_engines,
    register_engine,
)


def test_unknown_engine_returns_none():
    assert get_engine_runner("no-such-engine") is None
    # canvas 已下线：别名不再注册，与未知引擎同等处理
    assert get_engine_runner("canvas") is None


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
