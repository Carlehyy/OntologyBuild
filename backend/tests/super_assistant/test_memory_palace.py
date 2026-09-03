from __future__ import annotations

import json
import threading
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.auth.models import User
from app.deps import get_current_user, get_db
from app.shared.config import settings
from app.shared.database import Base
from app.super_assistant import palace_graph, palace_service, palace_tasks, palace_workspace, router, runtime
from app.super_assistant.models import SuperAssistantPalaceBuild, SuperAssistantPalaceFile

_TABLES = [User.__table__, SuperAssistantPalaceFile.__table__, SuperAssistantPalaceBuild.__table__]

_PREFIX = "/api/v2/super-assistant"


def _user(user_id: str, username: str) -> User:
    return User(
        id=user_id, username=username, email=f"{username}@example.com",
        password_hash="unused", role="editor",
    )


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "super_assistant_palace_workspace_root", str(tmp_path / "palace"))
    # 上传默认走 NATS 派发：HTTP 用例把派发替换为 no-op，不触发真实抽取
    monkeypatch.setattr(palace_service, "dispatch_super_assistant_palace_extract", lambda owner_id, file_id: None)
    engine = create_engine(
        f"sqlite:///{tmp_path / 'palace.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine, tables=_TABLES)
    TestingSession = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    with TestingSession() as db:
        db.add(_user("user-1", "owner"))
        db.add(_user("user-2", "other"))
        db.commit()

    def override_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    def make_client(user: User):
        app = FastAPI()
        app.include_router(router.router, prefix=_PREFIX)
        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_current_user] = lambda: user
        return TestClient(app)

    return SimpleNamespace(
        client=make_client(_user("user-1", "owner")),
        make_client=make_client,
        session=TestingSession,
        root=tmp_path / "palace",
    )


def _upload(client: TestClient, name: str, content: bytes, mime: str = "text/markdown"):
    return client.post(
        f"{_PREFIX}/palace/files",
        files={"file": (name, content, mime)},
    )


# ---------------------------------------------------------------------------
# HTTP：上传/列表/删除/重建 + 属权与白名单
# ---------------------------------------------------------------------------


def test_upload_list_delete_roundtrip_and_rebuild(env):
    rejected = _upload(env.client, "evil.exe", b"MZ", "application/octet-stream")
    assert rejected.status_code == 400

    created = _upload(env.client, "知识库.md", "# 知识\n张三 任职 ACME\n".encode())
    assert created.status_code == 201, created.text
    row = created.json()
    assert row["filename"] == "知识库.md"
    assert row["status"] == "pending"
    assert row["extractedChars"] > 0
    assert row["sha256"]

    listed = env.client.get(f"{_PREFIX}/palace/files")
    assert listed.status_code == 200
    assert [item["filename"] for item in listed.json()] == ["知识库.md"]

    # pending 状态下重建被 409 拒绝（已在队列中）
    assert env.client.post(f"{_PREFIX}/palace/files/{row['id']}/rebuild").status_code == 409

    deleted = env.client.delete(f"{_PREFIX}/palace/files/{row['id']}")
    assert deleted.status_code == 204
    assert env.client.get(f"{_PREFIX}/palace/files").json() == []
    assert env.client.delete(f"{_PREFIX}/palace/files/{row['id']}").status_code == 404

    # 文件落在 palace 独立根目录（uuid5 映射目录），不与其它用户混用
    dir_id = palace_workspace.user_dir_id("user-1")
    assert (env.root / dir_id / "files").exists()
    assert palace_workspace.user_dir_id("user-1") == dir_id
    assert palace_workspace.user_dir_id("user-2") != dir_id


def test_files_are_scoped_to_owner(env):
    artifact = _upload(env.client, "secret.md", "私有知识".encode()).json()
    other_client = env.make_client(_user("user-2", "other"))
    assert other_client.get(f"{_PREFIX}/palace/files").json() == []
    assert other_client.delete(f"{_PREFIX}/palace/files/{artifact['id']}").status_code == 404
    assert other_client.post(f"{_PREFIX}/palace/files/{artifact['id']}/rebuild").status_code == 404


def test_rebuild_dispatches_for_terminal_status(env):
    row = _upload(env.client, "知识库.md", "# 知识".encode()).json()
    with env.session() as db:
        db.get(SuperAssistantPalaceFile, row["id"]).status = "failed"
        db.commit()
    response = env.client.post(f"{_PREFIX}/palace/files/{row['id']}/rebuild")
    assert response.status_code == 202
    assert response.json() == {"dispatched": True}


# ---------------------------------------------------------------------------
# run_build：抽取管线 + 幂等 + 降级
# ---------------------------------------------------------------------------


def _seed_built_file(env, content: bytes = b""):
    created = _upload(env.client, "知识库.md", content or "# 知识\n张三 任职 ACME\n".encode())
    row = created.json()
    with env.session() as db:
        db.get(SuperAssistantPalaceFile, row["id"]).status = "pending"
        db.commit()
    return row


def test_run_build_extracts_merges_and_is_idempotent(env, monkeypatch):
    row = _seed_built_file(env)
    calls = {"extract": 0, "merge": 0, "remove": 0}

    def fake_extract_chunk(call_kwargs, chunk):
        calls["extract"] += 1
        return {
            "entities": [
                {"name": "张三", "type": "人物", "aliases": ["老张"]},
                {"name": "ACME", "type": "组织", "aliases": []},
            ],
            "relations": [{"source": "张三", "target": "ACME", "relation": "任职"}],
        }

    def fake_merge(owner_id, file_id, filename, entities, relations):
        calls["merge"] += 1
        return len(entities), len(relations)

    monkeypatch.setattr(palace_service, "extract_chunk", fake_extract_chunk)
    monkeypatch.setattr(palace_service.reflection_service, "_reflection_call_kwargs", lambda db: {})
    monkeypatch.setattr(palace_graph, "merge_extraction", fake_merge)
    monkeypatch.setattr(palace_graph, "remove_file_graph", lambda *args, **kwargs: calls.__setitem__("remove", calls["remove"] + 1))

    with env.session() as db:
        build = palace_service.run_build(db, "user-1", row["id"])
        assert build.status == "success"
        assert build.entity_count == 2
        assert build.relation_count == 1
        assert calls == {"extract": 1, "merge": 1, "remove": 0}
        file_row = db.get(SuperAssistantPalaceFile, row["id"])
        assert file_row.status == "built"
        assert file_row.entity_count == 2

        # 同一 (file_id, sha256) 的成功记录使重投幂等：不再抽取
        again = palace_service.run_build(db, "user-1", row["id"])
        assert again.id == build.id
        assert calls["extract"] == 1


def test_run_build_rebuild_strips_old_graph_contribution(env, monkeypatch):
    row = _seed_built_file(env)
    removed: list[tuple[str, str]] = []
    monkeypatch.setattr(
        palace_service, "extract_chunk",
        lambda call_kwargs, chunk: {"entities": [{"name": "张三", "type": "人物", "aliases": []}], "relations": []},
    )
    monkeypatch.setattr(palace_service.reflection_service, "_reflection_call_kwargs", lambda db: {})
    monkeypatch.setattr(palace_graph, "merge_extraction", lambda *a, **k: (1, 0))
    monkeypatch.setattr(
        palace_graph, "remove_file_graph",
        lambda owner_id, file_id, filename: removed.append((file_id, filename)),
    )
    with env.session() as db:
        palace_service.run_build(db, "user-1", row["id"])
        file_row = db.get(SuperAssistantPalaceFile, row["id"])
        assert file_row.status == "built"

        # 内容变更（模拟 hash 变化）触发重建：先剥离旧贡献再合并
        file_row.sha256 = "changed"
        db.commit()
        palace_service.run_build(db, "user-1", row["id"])
    assert removed == [(row["id"], "知识库.md")]


def test_run_build_failure_marks_file_failed(env, monkeypatch):
    row = _seed_built_file(env, content="# 空文本将被抽取为空".encode())  # 有文本，走 LLM 失败路径

    def boom(call_kwargs, chunk):
        raise RuntimeError("模型超时")

    monkeypatch.setattr(palace_service, "extract_chunk", boom)
    monkeypatch.setattr(palace_service.reflection_service, "_reflection_call_kwargs", lambda db: {})
    with env.session() as db:
        build = palace_service.run_build(db, "user-1", row["id"])
        assert build.status == "error"
        assert "模型超时" in build.error
        assert db.get(SuperAssistantPalaceFile, row["id"]).status == "failed"

        # 失败可重新领取：无成功记录 + 无在途 running
        second = palace_service.run_build(db, "user-1", row["id"])
        assert second.id != build.id


def test_stale_running_build_is_reclaimed(env):
    row = _seed_built_file(env)
    with env.session() as db:
        db.add(SuperAssistantPalaceBuild(
            owner_id="user-1", file_id=row["id"], content_hash=row["sha256"], status="running",
        ))
        db.commit()
        old = (
            db.query(SuperAssistantPalaceBuild)
            .filter(SuperAssistantPalaceBuild.file_id == row["id"])
            .one()
        )
        # 31 分钟前的 running 视为进程中断：收口为 error，允许重新领取
        old.created_at = old.created_at.replace(year=old.created_at.year - 1)
        db.commit()

        running = palace_service._active_running_build(db, row["id"])
        assert running is None
        statuses = [
            item.status
            for item in db.query(SuperAssistantPalaceBuild).filter(SuperAssistantPalaceBuild.file_id == row["id"]).all()
        ]
    assert statuses == ["error"]


def test_request_build_falls_back_to_inline_thread_without_nats(env, monkeypatch):
    row = _seed_built_file(env)
    done = threading.Event()
    recorded: list[tuple[str, str]] = []

    def fake_dispatch(owner_id, file_id):
        raise RuntimeError("后台任务派发失败：未配置 NATS_URL（JetStream 消息通道）")

    def fake_run_build(db, owner_id, file_id):
        recorded.append((owner_id, file_id))
        done.set()

    monkeypatch.setattr(palace_service, "dispatch_super_assistant_palace_extract", fake_dispatch)
    monkeypatch.setattr(palace_service, "run_build", fake_run_build)
    with env.session() as db:
        file_row = db.get(SuperAssistantPalaceFile, row["id"])
        result = palace_service.request_build(file_row)
    assert result == {"dispatched": False}
    assert done.wait(timeout=5)
    assert recorded == [("user-1", row["id"])]


def test_palace_extract_message_consumes_slot_and_swallows_errors(monkeypatch):
    # 单飞闸内执行且业务异常不外抛（nak 重投无意义）
    import asyncio

    def failing_run_build(db, owner_id, file_id):
        raise RuntimeError("boom")

    monkeypatch.setattr(palace_service, "run_build", failing_run_build)
    asyncio.run(palace_tasks.run_palace_extract_message({"owner_id": "u", "file_id": "f"}))


# ---------------------------------------------------------------------------
# 分块 / 清洗 / 关系解析
# ---------------------------------------------------------------------------


def test_split_chunks_paragraph_aware_with_overlap_and_cap():
    paragraphs = "\n\n".join(f"第{i}段" + "内容" * 20 for i in range(10))
    chunks = palace_service.split_chunks(paragraphs, size=120, overlap=20, max_chunks=4)
    assert len(chunks) == 4  # max_chunks 截断
    long_text = "字" * 300
    chunks = palace_service.split_chunks(long_text, size=100, overlap=20)
    assert len(chunks) == 4  # 100 + 3×(步进80)：100/100/100/60
    assert chunks[0] == "字" * 100
    assert len(chunks[3]) == 60
    assert chunks[1].startswith(chunks[0][80:])  # overlap 尾巴回接
    assert palace_service.split_chunks("") == []
    assert palace_service.split_chunks("   \n\n  ") == []


def test_sanitize_and_resolve_relations():
    payload = {
        "entities": [
            {"name": " 张三 ", "type": "人物", "aliases": ["老张", " ", 123]},
            {"name": "", "type": "人物"},  # 空名丢弃
            {"name": "张三", "type": "人物"},  # 重复（规范化后同键）合并
            "not-a-dict",
        ],
        "relations": [
            {"source": "张三", "target": "ACME", "relation": "任职"},  # 端点未抽取 → 丢弃
            {"source": "张三", "target": "张三", "relation": "自环"},  # 自环丢弃
        ],
    }
    entities = palace_service._sanitize_chunk_entities("u1", payload)
    assert len(entities) == 1
    assert entities[0]["name"] == "张三"
    assert entities[0]["aliases"] == ["老张", "123"]  # 非字符串别名按字符串归一

    entity_map = {item["key"]: item for item in entities}
    # 模拟另一块抽取出的实体：键必须是 entity_key 口径
    entity_map[palace_graph.entity_key("u1", "ACME")] = {
        "key": palace_graph.entity_key("u1", "ACME"), "name": "ACME",
    }
    # 自环被过滤；端点齐备的关系解析成功
    resolved = palace_service._resolve_relations("u1", entity_map, payload["relations"])
    assert [item["name"] for item in resolved] == ["任职"]
    assert resolved[0]["src_key"] == palace_graph.entity_key("u1", "张三")

    # 端点未抽取（不在累计实体集内）的关系整体丢弃
    assert palace_service._resolve_relations(
        "u1", entity_map, [{"source": "神秘人", "target": "ACME", "relation": "认识"}],
    ) == []

    raw = [{"source": "张三", "target": "ACME", "relation": "任职"}]
    resolved = palace_service._resolve_relations(
        "u1", entity_map, raw + raw,
    )
    assert len(resolved) == 1  # 同键去重
    assert resolved[0]["src_key"] == palace_graph.entity_key("u1", "张三")


def test_normalize_and_keys_are_owner_scoped():
    assert palace_graph.entity_key("u1", " 张三 ") == palace_graph.entity_key("u1", "张三")
    assert palace_graph.entity_key("u1", "ABC") != palace_graph.entity_key("u2", "ABC")
    assert palace_graph.relation_key("u1", "A", "任职", "B") != palace_graph.relation_key("u1", "A", "负责", "B")


# ---------------------------------------------------------------------------
# 助手消费：注入段 + 只读工具
# ---------------------------------------------------------------------------


def test_prompt_section_empty_without_built_files(env):
    row = _seed_built_file(env)  # status=pending
    with env.session() as db:
        assert palace_service.build_prompt_section(db, "user-1", query="张三") == ""


def test_prompt_section_formats_entities_and_relations(env, monkeypatch):
    row = _seed_built_file(env)
    with env.session() as db:
        db.get(SuperAssistantPalaceFile, row["id"]).status = "built"
        db.commit()

    def fake_search(owner_id, terms, **kwargs):
        return {
            "entities": [
                {"name": "张三", "type": "人物", "source_files": ["知识库.md"]},
                {"name": "ACME", "type": "组织", "source_files": ["知识库.md"]},
            ],
            "relations": [{"source": "张三", "target": "ACME", "name": "任职"}],
        }

    monkeypatch.setattr(palace_graph, "search", fake_search)
    with env.session() as db:
        section = palace_service.build_prompt_section(db, "user-1", query="张三在哪任职")
    assert "记忆宫殿知识图谱" in section
    assert "张三（人物；来源：知识库.md）" in section
    assert "张三 —任职→ ACME" in section
    assert "palace_graph_search" in section


def test_prompt_section_respects_budget(env, monkeypatch):
    row = _seed_built_file(env)
    with env.session() as db:
        db.get(SuperAssistantPalaceFile, row["id"]).status = "built"
        db.commit()
    monkeypatch.setattr(palace_graph, "search", lambda *a, **k: {
        "entities": [{"name": f"实体{i}", "type": "概念", "source_files": []} for i in range(500)],
        "relations": [],
    })
    with env.session() as db:
        section = palace_service.build_prompt_section(db, "user-1", query="实体")
    assert len(section) <= palace_service._SECTION_BUDGET


def test_palace_tools_are_read_only_and_fast_fail(env, monkeypatch):
    tools = runtime._builtin_tools()
    names = [tool["name"] for tool in tools]
    assert {"palace_graph_search", "palace_graph_files"} <= set(names)
    assert {"palace_graph_search", "palace_graph_files"} <= runtime._READ_ONLY_BUILTIN_TOOLS

    row = _seed_built_file(env)
    context = {
        "owner_id": "user-1",
        "conversation_id": str(row["id"]),
        "assistant_message_id": "assistant-1",
        "call_kwargs": {},
    }
    with env.session() as db:
        listed = json.loads(runtime._execute_builtin_tool(
            db, name="palace_graph_files", arguments={}, **context,
        ))
    assert [item["filename"] for item in listed["files"]] == ["知识库.md"]

    def unavailable(owner_id, query):
        raise palace_graph.PalaceGraphUnavailable("Neo4j 不可用")

    monkeypatch.setattr(palace_service, "search_for_tool", unavailable)
    failed = json.loads(runtime._execute_builtin_tool(
        None, name="palace_graph_search", arguments={"query": "张三"}, **context,
    ))
    assert "error" in failed
    assert "Neo4j" in failed["error"]

    empty = json.loads(runtime._execute_builtin_tool(
        None, name="palace_graph_search", arguments={"query": " "}, **context,
    ))
    assert "error" in empty


def test_system_prompt_appends_palace_section_after_files():
    prompt = runtime._system_prompt(
        [], memory_section="MEM", file_section="FILES", palace_section="PALACE",
    )
    assert prompt.index("FILES") < prompt.index("PALACE")
    assert "PALACE" not in runtime._system_prompt([], memory_section="MEM", file_section="FILES")
    assert "palace_graph_search" in runtime._system_prompt([])


# ---------------------------------------------------------------------------
# 图谱视图端点：Neo4j 不可用降级为 available=false
# ---------------------------------------------------------------------------


def test_graph_overview_degrades_when_neo4j_unavailable(env, monkeypatch):
    row = _seed_built_file(env)
    with env.session() as db:
        db.get(SuperAssistantPalaceFile, row["id"]).status = "built"
        db.commit()

    def unavailable(owner_id, **kwargs):
        raise palace_graph.PalaceGraphUnavailable("Neo4j 不可用")

    monkeypatch.setattr(palace_graph, "owner_graph", unavailable)
    response = env.client.get(f"{_PREFIX}/palace/graph")
    assert response.status_code == 200
    payload = response.json()
    assert payload["available"] is False
    assert payload["nodes"] == []

    # 无已建图文件时不触碰 Neo4j，直接返回空视图
    monkeypatch.setattr(palace_graph, "owner_graph", lambda *a, **k: (_ for _ in ()).throw(AssertionError("不应触碰 Neo4j")))
    env.client.delete(f"{_PREFIX}/palace/files/{row['id']}")
    response = env.client.get(f"{_PREFIX}/palace/graph")
    assert response.status_code == 200
    assert response.json() == {
        "available": True, "nodes": [], "edges": [],
        "totals": {"entities": 0, "relations": 0}, "truncated": False,
    }
