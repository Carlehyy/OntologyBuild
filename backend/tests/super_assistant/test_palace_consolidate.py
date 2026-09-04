"""记忆宫殿聚类合并（palace_consolidate）与图谱增强的单元测试。

风格与 test_memory_palace.py 一致：全部 monkeypatch 图服务边界
（_service/run_cypher）与模型调用（provider.chat），不依赖真实
Neo4j/NATS/LLM。
"""
from __future__ import annotations

import asyncio
import json

import pytest

from app.super_assistant import palace_consolidate, palace_graph, palace_tasks

_OWNER = "user-1"


# ---------------------------------------------------------------------------
# find_consolidation_candidates：后缀归一 / alias 交叉 / 无候选
# ---------------------------------------------------------------------------


def _entity(name: str, *, key: str | None = None, aliases=None, mention: int = 1):
    return {
        "merge_key": key or palace_graph.entity_key(_OWNER, name),
        "name": name,
        "type": "组织",
        "aliases": list(aliases or []),
        "source_files": [],
        "mention_count": mention,
    }


def test_find_candidates_groups_by_org_suffix():
    entities = [
        _entity("智谱AI", mention=5),
        _entity("智谱AI公司", mention=2),
        _entity("智谱AI科技", mention=1),
        _entity("毫无关系的实体", mention=9),
    ]

    groups = palace_consolidate.find_consolidation_candidates(entities)

    # 去后缀后同为「智谱ai」成簇；簇内按 mention_count 降序
    assert len(groups) == 1
    assert [item["name"] for item in groups[0]] == ["智谱AI", "智谱AI公司", "智谱AI科技"]


def test_find_candidates_groups_by_alias_cross_match():
    entities = [
        _entity("张三", aliases=["老张"], mention=3),
        _entity("老张", mention=1),
        _entity("李四", aliases=["小李"], mention=2),  # alias 无对应用名实体，不成簇
    ]

    groups = palace_consolidate.find_consolidation_candidates(entities)

    assert len(groups) == 1
    assert [item["name"] for item in groups[0]] == ["张三", "老张"]


def test_find_candidates_empty_without_duplicates():
    entities = [
        _entity("张三", aliases=["老张"]),
        _entity("李四"),
        _entity("ACME"),
    ]

    assert palace_consolidate.find_consolidation_candidates(entities) == []
    assert palace_consolidate.find_consolidation_candidates([]) == []


def test_find_candidates_suffix_only_name_keeps_normalized_form():
    """名字仅剩后缀词时不继续剥空（防御：不能得到空 core 错误聚类）。"""
    entities = [_entity("公司", mention=1), _entity("集团", mention=2)]

    assert palace_consolidate.find_consolidation_candidates(entities) == []


# ---------------------------------------------------------------------------
# run_consolidation：LLM 确认成功 / 失败放弃 / 无模型放弃
# ---------------------------------------------------------------------------


def _palace_entities_fixture():
    return [
        _entity("智谱AI", aliases=["zhipu"], mention=5),
        _entity("智谱AI公司", mention=2),
        _entity("智谱AI科技", mention=1),
        _entity("张三", aliases=["老张"], mention=3),
        _entity("老张", mention=1),
    ]


@pytest.fixture
def no_model(monkeypatch):
    """钉住 palace_service._palace_call_kwargs（A1 并行契约，可能尚不存在）。"""
    from app.super_assistant import palace_service

    monkeypatch.setattr(
        palace_service, "_palace_call_kwargs", lambda db: {"model": "test"},
        raising=False,
    )


def test_run_consolidation_merges_confirmed_groups(monkeypatch, no_model):
    monkeypatch.setattr(palace_graph, "owner_entities", lambda owner_id, limit=2000: _palace_entities_fixture())

    merged: list[tuple[str, str, list[str]]] = []

    def fake_merge_entities(owner_id, canonical_key, absorbed_keys):
        merged.append((owner_id, canonical_key, list(absorbed_keys)))
        return len(absorbed_keys)

    monkeypatch.setattr(palace_graph, "merge_entities", fake_merge_entities)
    monkeypatch.setattr(
        palace_consolidate.provider, "chat",
        lambda call_kwargs, messages, tools: {
            "content": json.dumps({
                "groups": [
                    {"id": 1, "canonical": "智谱AI", "members": ["智谱AI公司", "智谱AI科技"]},
                    {"id": 2, "canonical": "张三", "members": ["老张"]},
                ],
            }, ensure_ascii=False),
        },
    )

    result = palace_consolidate.run_consolidation(None, _OWNER)

    # 候选 2 簇全被确认：canonical 取模型保留名，absorbed 按簇内序传递
    assert merged == [
        (_OWNER, palace_graph.entity_key(_OWNER, "智谱AI"), [
            palace_graph.entity_key(_OWNER, "智谱AI公司"),
            palace_graph.entity_key(_OWNER, "智谱AI科技"),
        ]),
        (_OWNER, palace_graph.entity_key(_OWNER, "张三"), [
            palace_graph.entity_key(_OWNER, "老张"),
        ]),
    ]
    assert result == {
        "candidates": 2,
        "merged_groups": [["智谱AI", "智谱AI公司", "智谱AI科技"], ["张三", "老张"]],
        "merged_entities": 3,
        "model_used": True,
    }


def test_run_consolidation_canonical_falls_back_to_top_mention(monkeypatch, no_model):
    monkeypatch.setattr(
        palace_graph, "owner_entities",
        lambda owner_id, limit=2000: _palace_entities_fixture(),
    )
    merged = []

    def fake_merge_entities(owner_id, canonical_key, absorbed_keys):
        merged.append(canonical_key)
        return len(absorbed_keys)

    monkeypatch.setattr(palace_graph, "merge_entities", fake_merge_entities)
    # 模型保留名不在簇内：回退 mention_count 最高者（智谱AI）
    monkeypatch.setattr(
        palace_consolidate.provider, "chat",
        lambda call_kwargs, messages, tools: {
            "content": json.dumps(
                {"groups": [{"id": 1, "canonical": "模型随口的名字"}]},
                ensure_ascii=False,
            ),
        },
    )

    result = palace_consolidate.run_consolidation(None, _OWNER)

    assert merged == [palace_graph.entity_key(_OWNER, "智谱AI")]
    assert result["merged_entities"] == 2
    assert result["model_used"] is True


def test_run_consolidation_llm_failure_skips_round(monkeypatch, no_model):
    monkeypatch.setattr(
        palace_graph, "owner_entities",
        lambda owner_id, limit=2000: _palace_entities_fixture(),
    )

    def forbidden_merge(*_args, **_kwargs):  # pragma: no cover - 防御断言
        raise AssertionError("LLM 失败时不得动图")

    monkeypatch.setattr(palace_graph, "merge_entities", forbidden_merge)

    def failing_chat(_kwargs, _messages, _tools):
        raise RuntimeError("model timeout")

    monkeypatch.setattr(palace_consolidate.provider, "chat", failing_chat)

    result = palace_consolidate.run_consolidation(None, _OWNER)

    assert result == {
        "candidates": 2, "merged_groups": [],
        "merged_entities": 0, "model_used": False,
    }


def test_run_consolidation_without_model_kwargs_skips_round(monkeypatch):
    """_palace_call_kwargs 缺失（并行批次未落地）或抛错：同样放弃本轮。"""
    from app.super_assistant import palace_service

    monkeypatch.setattr(
        palace_graph, "owner_entities",
        lambda owner_id, limit=2000: _palace_entities_fixture(),
    )

    def forbidden_chat(*_args, **_kwargs):  # pragma: no cover - 防御断言
        raise AssertionError("无模型调用参数时不得触达 LLM")

    monkeypatch.setattr(palace_consolidate.provider, "chat", forbidden_chat)

    def failing_kwargs(_db):
        raise RuntimeError("no model configured")

    monkeypatch.setattr(
        palace_service, "_palace_call_kwargs", failing_kwargs, raising=False,
    )

    result = palace_consolidate.run_consolidation(None, _OWNER)

    assert result["candidates"] == 2
    assert result["model_used"] is False
    assert result["merged_entities"] == 0


def test_run_consolidation_without_candidates_is_noop(monkeypatch, no_model):
    monkeypatch.setattr(palace_graph, "owner_entities", lambda owner_id, limit=2000: [])

    def forbidden_chat(*_args, **_kwargs):  # pragma: no cover - 防御断言
        raise AssertionError("无候选时不得触达 LLM")

    monkeypatch.setattr(palace_consolidate.provider, "chat", forbidden_chat)

    assert palace_consolidate.run_consolidation(None, _OWNER) == {
        "candidates": 0, "merged_groups": [], "merged_entities": 0, "model_used": False,
    }


# ---------------------------------------------------------------------------
# merge_entities：Cypher 序列（属性合并 → 边重定向 → 删除）
# ---------------------------------------------------------------------------


class _FakeGraphService:
    def __init__(self, first_result=None):
        self.calls: list[tuple[str, dict | None]] = []
        self._first_result = first_result or []

    def run_cypher(self, query, params=None):
        self.calls.append((query, params))
        if "RETURN count(b)" in query:
            return self._first_result
        return []

    def close(self):
        pass


def test_merge_entities_runs_three_cypher_statements(monkeypatch):
    service = _FakeGraphService(first_result=[{"c": 2}])
    monkeypatch.setattr(palace_graph, "_service", lambda: service)

    merged = palace_graph.merge_entities(
        _OWNER,
        palace_graph.entity_key(_OWNER, "智谱AI"),
        [palace_graph.entity_key(_OWNER, "智谱AI公司"),
         palace_graph.entity_key(_OWNER, "智谱AI科技")],
    )

    assert merged == 2
    assert len(service.calls) == 3
    prop_merge, redirect, delete = (query for query, _params in service.calls)
    assert "SET a.aliases" in prop_merge and "RETURN count(b)" in prop_merge
    assert "CREATE (a)-[nr:RELATED]->(other)" in redirect
    assert "SET nr = properties(r)" in redirect
    assert "DETACH DELETE b" in delete
    params = {
        "owner_id": _OWNER,
        "canonical": palace_graph.entity_key(_OWNER, "智谱AI"),
        "absorbed": [palace_graph.entity_key(_OWNER, "智谱AI公司"),
                     palace_graph.entity_key(_OWNER, "智谱AI科技")],
    }
    assert all(actual == params for _query, actual in service.calls)


def test_merge_entities_skips_empty_or_self_absorbed(monkeypatch):
    service = _FakeGraphService()
    monkeypatch.setattr(palace_graph, "_service", lambda: service)
    canonical = palace_graph.entity_key(_OWNER, "A")

    assert palace_graph.merge_entities(_OWNER, canonical, []) == 0
    assert palace_graph.merge_entities(_OWNER, canonical, [canonical, ""]) == 0
    assert service.calls == []


def test_owner_entities_queries_slim_rows(monkeypatch):
    service = _FakeGraphService()
    monkeypatch.setattr(palace_graph, "_service", lambda: service)

    palace_graph.owner_entities(_OWNER, limit=500)

    (query, params), = service.calls
    assert "RETURN n.merge_key AS merge_key" in query
    assert "n.mention_count, 0) DESC" in query
    assert params == {"owner_id": _OWNER, "limit": 500}


# ---------------------------------------------------------------------------
# record_match_counts / search：命中计数闭环
# ---------------------------------------------------------------------------


def test_record_match_counts_skips_empty_keys(monkeypatch):
    service = _FakeGraphService()
    monkeypatch.setattr(palace_graph, "_service", lambda: service)

    palace_graph.record_match_counts(_OWNER, [])

    assert service.calls == []


def _search_service(scan_rows, relation_rows):
    class _Service(_FakeGraphService):
        def __init__(self):
            super().__init__()
            self._scan_rows = scan_rows
            self._relation_rows = relation_rows
            self.fail_counter = False
            self.counter_calls: list[dict | None] = []

        def run_cypher(self, query, params=None):
            if "SET n.match_count" in query:
                self.counter_calls.append(params)
                if self.fail_counter:
                    raise RuntimeError("counter down")
                return []
            if "RETURN DISTINCT s.merge_key" in query:
                return self._relation_rows
            self.calls.append((query, params))
            return self._scan_rows

    return _Service()


def test_search_records_anchor_match_counts_and_passes_through(monkeypatch):
    scan_rows = [
        {"id": palace_graph.entity_key(_OWNER, "张三"), "name": "张三", "type": "人物",
         "aliases": ["老张"], "source_files": ["a.md", "a.md"], "match_count": 0},
        {"id": palace_graph.entity_key(_OWNER, "ACME公司"), "name": "ACME公司", "type": "组织",
         "aliases": [], "source_files": ["a.md"], "match_count": 0},
    ]
    relations = [{
        "source": palace_graph.entity_key(_OWNER, "张三"),
        "target": palace_graph.entity_key(_OWNER, "ACME公司"),
        "source_name": "张三", "target_name": "ACME公司", "name": "任职",
        "source_files": ["a.md", "a.md"],
    }]
    service = _search_service(scan_rows, relations)
    monkeypatch.setattr(palace_graph, "_service", lambda: service)

    result = palace_graph.search(_OWNER, ["张三"])

    # 锚点（且只有锚点）被计数一次
    assert service.counter_calls == [
        {"keys": [palace_graph.entity_key(_OWNER, "张三")]},
    ]
    assert [item["name"] for item in result["entities"]] == ["张三", "ACME公司"]
    # 实体行透传 match_count，source_files 读取侧去重
    assert all("match_count" in item for item in result["entities"])
    assert all(item["source_files"] == ["a.md"] for item in result["entities"])
    assert result["relations"][0]["source_files"] == ["a.md"]


def test_search_survives_counter_failure(monkeypatch):
    anchor_id = palace_graph.entity_key(_OWNER, "张三")
    neighbor_id = palace_graph.entity_key(_OWNER, "ACME公司")
    scan_rows = [
        {"id": anchor_id, "name": "张三", "type": "人物",
         "aliases": [], "source_files": [], "match_count": 0},
        {"id": neighbor_id, "name": "ACME公司", "type": "组织",
         "aliases": [], "source_files": [], "match_count": 0},
    ]
    relations = [{
        "source": anchor_id, "target": neighbor_id,
        "source_name": "张三", "target_name": "ACME公司",
        "name": "任职", "source_files": [],
    }]
    service = _search_service(scan_rows, relations)
    service.fail_counter = True
    monkeypatch.setattr(palace_graph, "_service", lambda: service)

    result = palace_graph.search(_OWNER, ["张三"])

    # 计数尝试过一次但失败被吞掉，检索结果不受影响
    assert len(service.counter_calls) == 1
    assert [item["name"] for item in result["entities"]] == ["张三", "ACME公司"]
    assert [item["name"] for item in result["relations"]] == ["任职"]


def test_search_without_hits_does_not_count(monkeypatch):
    scan_rows = [
        {"id": palace_graph.entity_key(_OWNER, "张三"), "name": "张三", "type": "人物",
         "aliases": [], "source_files": [], "match_count": 0},
    ]
    service = _search_service(scan_rows, [])
    monkeypatch.setattr(palace_graph, "_service", lambda: service)

    result = palace_graph.search(_OWNER, ["完全不相干的词"])

    assert result == {"entities": [], "relations": []}
    assert service.counter_calls == []


# ---------------------------------------------------------------------------
# 抽取并发闸：settings 防御读取
# ---------------------------------------------------------------------------


def test_palace_semaphore_reads_settings_defensively(monkeypatch):
    # pydantic Settings 不允许 delattr 不存在字段：用替换模块级 settings
    # 对象的方式模拟「配置项未就绪」，避免 monkeypatch teardown 副作用
    class _BareSettings:
        pass

    class _ConcurrentSettings:
        super_assistant_palace_extract_concurrency = 3

    monkeypatch.setattr(palace_tasks, "_PALACE_SLOT", None)
    monkeypatch.setattr("app.shared.config.settings", _BareSettings())
    # 配置项未就绪：防御回退并发 1
    assert palace_tasks._palace_semaphore()._value == 1

    monkeypatch.setattr(palace_tasks, "_PALACE_SLOT", None)
    monkeypatch.setattr("app.shared.config.settings", _ConcurrentSettings())
    assert palace_tasks._palace_semaphore()._value == 3
    # 模块级缓存：同一闸复用，不随调用重建
    assert palace_tasks._palace_semaphore() is palace_tasks._palace_semaphore()


# ---------------------------------------------------------------------------
# NATS handler：独立 Session + 异常内消化 + 不占抽取闸
# ---------------------------------------------------------------------------


def test_palace_consolidate_message_runs_service_and_closes_session(monkeypatch):
    recorded = []

    class _FakeDB:
        def close(self):
            recorded.append("closed")

    monkeypatch.setattr("app.database.SessionLocal", lambda: _FakeDB())

    def fake_run_consolidation(db, owner_id):
        recorded.append((db is not None, owner_id))
        return {"candidates": 1, "merged_groups": [["a", "b"]],
                "merged_entities": 1, "model_used": True}

    monkeypatch.setattr(
        palace_consolidate, "run_consolidation", fake_run_consolidation,
    )

    asyncio.run(palace_tasks.run_palace_consolidate_message({"owner_id": _OWNER}))

    assert recorded == [(True, _OWNER), "closed"]


def test_palace_consolidate_message_swallows_errors(monkeypatch):
    class _FakeDB:
        def close(self):
            pass

    monkeypatch.setattr("app.database.SessionLocal", lambda: _FakeDB())

    def failing_run_consolidation(db, owner_id):
        raise RuntimeError("boom")

    monkeypatch.setattr(palace_consolidate, "run_consolidation", failing_run_consolidation)

    # 异常内消化：不向外抛（nak 重投无意义）
    asyncio.run(palace_tasks.run_palace_consolidate_message({"owner_id": _OWNER}))


# ---------------------------------------------------------------------------
# 调度：每天 03:00 + settings 开关 + 全 owner 派发
# ---------------------------------------------------------------------------


def test_scheduler_daily_job_and_enabled_switch(monkeypatch):
    # 用普通对象替换 palace_consolidate.settings（pydantic Settings 不允许
    # delattr 未声明字段，直接 setattr 不存在的键会在 teardown 炸掉）
    class _Switch:
        def __init__(self, enabled: bool):
            self.super_assistant_palace_consolidate_enabled = enabled

    palace_consolidate.shutdown()
    try:
        monkeypatch.setattr(palace_consolidate, "settings", _Switch(False))
        palace_consolidate.start()
        assert palace_consolidate._scheduler is None  # 开关关闭为 no-op

        monkeypatch.setattr(palace_consolidate, "settings", _Switch(True))
        palace_consolidate.start()
        assert palace_consolidate._scheduler is not None
        assert palace_consolidate._scheduler.running
        job = palace_consolidate._scheduler.get_job(palace_consolidate._JOB_ID)
        assert job is not None
        assert job.max_instances == 1
        assert job.coalesce is True
        # 每天 03:00（本地时区）
        trigger_text = str(job.trigger)
        assert "hour='3'" in trigger_text
        assert "minute='0'" in trigger_text

        palace_consolidate.start()  # 幂等：重复 start 不重建
        job_again = palace_consolidate._scheduler.get_job(palace_consolidate._JOB_ID)
        assert job_again is job
    finally:
        palace_consolidate.shutdown()
    assert palace_consolidate._scheduler is None


def test_dispatch_daily_consolidation_covers_built_owners(monkeypatch):
    class _Query:
        def filter(self, *args):
            return self

        def distinct(self):
            return self

        def all(self):
            return [("u1",), ("u2",)]

    class _FakeDB:
        def query(self, *_args):
            return _Query()

        def close(self):
            pass

    monkeypatch.setattr("app.shared.database.SessionLocal", lambda: _FakeDB())

    dispatched = []

    def fake_dispatch(owner_id):
        if owner_id == "u2":
            raise RuntimeError("channel down")  # 单个失败不阻断其余
        dispatched.append(owner_id)

    monkeypatch.setattr(
        "app.data_channel.pipeline_tasks.dispatch."
        "dispatch_super_assistant_palace_consolidate",
        fake_dispatch,
    )

    # u1 派发成功、u2 失败仅记日志：函数整体不抛
    palace_consolidate._dispatch_daily_consolidation()

    assert dispatched == ["u1"]
