"""统一限额注册表（limits.py）的单元测试。

注册表是全部数值限额的单一事实源：默认值与迁移前常量一致（零行为变化），
环境变量可覆盖且夹取到 min/max 边界，非法值回退默认。
"""
import pytest


def test_defaults_match_legacy_constants(monkeypatch):
    from app.ontologies.agent_runtime.limits import limit

    expected = {
        "tool_result_display_cap": 6000,
        "tool_result_llm_cap": 8000,
        "graph_display_cap": 18000,
        "history_verbatim": 10,
        "history_digest_scan": 40,
        "search_scan_cap": 5000,
        "aggregate_scan_cap": 100_000,
        "citation_cap": 20,
        "snippet_fields": 3,
        "value_trunc": 200,
        "chart_point_cap": 20,
        "max_hops": 5,
        "hop_fanout_cap": 200,
        "frontier_cap": 500,
        "path_node_budget": 2000,
        "answer_verify_retries": 1,
        "answer_verify_unverified_floor": 0,
    }
    for key, value in expected.items():
        monkeypatch.delenv(f"AGENT_{key.upper()}", raising=False)
    # 环境变量名与 key 的映射并非全部一致，逐个删除对应 env
    from app.ontologies.agent_runtime import limits as limits_mod
    for item in limits_mod._LIMITS:
        monkeypatch.delenv(item.env, raising=False)
    for key, value in expected.items():
        assert limit(key) == value, key


def test_env_override_and_clamping(monkeypatch):
    from app.ontologies.agent_runtime.limits import limit

    monkeypatch.setenv("AGENT_HISTORY_VERBATIM", "25")
    assert limit("history_verbatim") == 25
    monkeypatch.setenv("AGENT_HISTORY_VERBATIM", "999999")
    assert limit("history_verbatim") == 100  # 夹取到 max
    monkeypatch.setenv("AGENT_HISTORY_VERBATIM", "-5")
    assert limit("history_verbatim") == 1    # 夹取到 min
    monkeypatch.setenv("AGENT_HISTORY_VERBATIM", "not-a-number")
    assert limit("history_verbatim") == 10   # 非法值回退默认
    monkeypatch.delenv("AGENT_HISTORY_VERBATIM")


def test_summary_covers_all_keys():
    from app.ontologies.agent_runtime.limits import limit_summary, _LIMITS

    summary = limit_summary()
    assert set(summary) == {item.key for item in _LIMITS}
    assert summary["history_verbatim"]["effective"] == 10
    assert summary["history_verbatim"]["env"] == "AGENT_HISTORY_VERBATIM"


def test_unknown_key_raises():
    from app.ontologies.agent_runtime.limits import limit

    with pytest.raises(KeyError):
        limit("does_not_exist")
