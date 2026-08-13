from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.auth.models import User
from app.deps import get_current_user, get_db
from app.shared.config import settings
from app.shared.database import Base
from app.super_assistant import memory_service, router
from app.super_assistant.memory_service import MemoryConflictError
from app.super_assistant.models import (
    SuperAssistantMemory,
    SuperAssistantMemoryProfile,
)


def _make_session(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'memory.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(
        bind=engine,
        tables=[
            User.__table__,
            SuperAssistantMemory.__table__,
            SuperAssistantMemoryProfile.__table__,
        ],
    )
    return engine, sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture()
def db(tmp_path):
    engine, SessionFactory = _make_session(tmp_path)
    with SessionFactory() as session:
        session.add_all([
            User(
                id="owner-1", username="owner1",
                email="owner1@example.com", password_hash="unused",
                role="editor",
            ),
            User(
                id="owner-2", username="owner2",
                email="owner2@example.com", password_hash="unused",
                role="editor",
            ),
        ])
        session.commit()
        yield session
    engine.dispose()


def _seed(
    db: Session,
    owner_id: str,
    content: str,
    **kwargs,
) -> SuperAssistantMemory:
    """种数据时绕过写入冲突检测（检测本身有独立用例覆盖）。"""
    return memory_service.create_memory(
        db,
        owner_id,
        content,
        conflict_check=False,
        **kwargs,
    )


def test_tokenize_cjk_bigrams_and_latin_words():
    assert memory_service._tokenize("我喜欢Python3") == [
        "我喜",
        "喜欢",
        "python3",
    ]
    assert memory_service._tokenize("用户user2024") == ["用户", "user2024"]
    # 孤立 CJK 单字保留；长度 < 2 的拉丁词丢弃
    assert memory_service._tokenize("我 a bb") == ["我", "bb"]
    assert memory_service._tokenize("！？。") == []


def test_relevant_memories_rank_by_tfidf_and_respect_cap(db):
    m1 = _seed(db, "owner-1", "用户喜欢喝黑咖啡")
    m2 = _seed(db, "owner-1", "今天天气不错适合出门散步")
    m3 = _seed(db, "owner-1", "咖啡豆的烘焙程度影响风味")

    hits = memory_service.relevant_memories(db, "owner-1", "咖啡")
    assert [item.id for item in hits] == [m1.id, m3.id]
    # 命中行 match_count + 1（flush 后同事务可见），未命中行不变
    assert (m1.match_count, m2.match_count, m3.match_count) == (1, 0, 1)

    capped = memory_service.relevant_memories(db, "owner-1", "咖啡", cap=1)
    assert [item.id for item in capped] == [m1.id]

    # 相似度为零的查询不命中任何记忆，也不增加计数
    assert memory_service.relevant_memories(db, "owner-1", "zzzqqq") == []
    assert (m1.match_count, m2.match_count, m3.match_count) == (2, 0, 1)


def test_relevant_memories_scores_effectiveness_factor(db):
    first = _seed(db, "owner-1", "用户喜欢喝黑咖啡")
    second = _seed(db, "owner-1", "用户喜欢喝黑咖啡。")
    # 两条内容分词结果相同，仅效果因子不同：比率高的排前面
    first.match_count = 10
    first.reference_count = 10
    second.match_count = 10
    second.reference_count = 0
    db.flush()

    hits = memory_service.relevant_memories(db, "owner-1", "咖啡")
    assert [item.id for item in hits] == [first.id, second.id]


def test_effectiveness_factor_maps_reference_ratio():
    memory = SuperAssistantMemory(owner_id="owner-1", content="x")
    memory.match_count = 0
    assert memory_service.effectiveness_factor(memory) == 1.0
    memory.match_count = 4
    memory.reference_count = 0
    assert memory_service.effectiveness_factor(memory) == 0.5
    memory.reference_count = 2
    assert memory_service.effectiveness_factor(memory) == 0.75
    memory.reference_count = 4
    assert memory_service.effectiveness_factor(memory) == 1.0
    # 比率超过 1 时按 1 封顶
    memory.reference_count = 8
    assert memory_service.effectiveness_factor(memory) == 1.0


def test_decay_factor_half_life_and_floor():
    now = datetime.now(timezone.utc)
    memory = SuperAssistantMemory(owner_id="owner-1", content="x")
    memory.created_at = now
    assert memory_service.decay_factor(memory, now=now) == 1.0
    memory.created_at = now - timedelta(days=30)
    assert memory_service.decay_factor(memory, now=now) == pytest.approx(0.5)
    # 60 天衰减到 0.25，被 0.3 下限托住
    memory.created_at = now - timedelta(days=60)
    assert memory_service.decay_factor(memory, now=now) == pytest.approx(0.3)
    memory.created_at = now - timedelta(days=365)
    assert memory_service.decay_factor(memory, now=now) == pytest.approx(0.3)
    # sqlite 读出的 naive datetime 按 UTC 处理
    memory.created_at = (now - timedelta(days=30)).replace(tzinfo=None)
    assert memory_service.decay_factor(memory, now=now) == pytest.approx(0.5)


def test_mark_referenced_increments_and_stamps(db):
    memory = _seed(db, "owner-1", "用户喜欢喝黑咖啡")
    assert memory.reference_count == 0
    assert memory.last_accessed_at is None

    memory_service.mark_referenced(db, [memory.id, memory.id])
    assert memory.reference_count == 1
    assert memory.last_accessed_at is not None

    memory_service.mark_referenced(db, [memory.id, "missing-id"])
    assert memory.reference_count == 2

    memory_service.mark_referenced(db, [])
    assert memory.reference_count == 2


def test_check_conflict_threshold(db):
    existing = _seed(db, "owner-1", "用户喜欢喝黑咖啡")

    with pytest.raises(MemoryConflictError) as conflict:
        memory_service.check_conflict(db, "owner-1", "用户喜欢喝黑咖啡")
    assert conflict.value.existing_id == existing.id
    assert conflict.value.similarity >= 0.75
    assert conflict.value.existing_content == existing.content

    # 部分重叠低于默认阈值不冲突；调低阈值则触发
    memory_service.check_conflict(db, "owner-1", "用户喜欢喝绿茶")
    with pytest.raises(MemoryConflictError):
        memory_service.check_conflict(
            db,
            "owner-1",
            "用户喜欢喝绿茶",
            threshold=0.1,
        )


def test_create_memory_conflict_skips_write(db):
    _seed(db, "owner-1", "用户喜欢喝黑咖啡")
    with pytest.raises(MemoryConflictError):
        memory_service.create_memory(db, "owner-1", "用户喜欢喝黑咖啡")
    count = db.query(SuperAssistantMemory).filter_by(owner_id="owner-1").count()
    assert count == 1


def test_supersedes_marks_old_memory_inactive(db):
    old = _seed(db, "owner-1", "用户喜欢喝黑咖啡")
    new = memory_service.create_memory(
        db,
        "owner-1",
        "用户改喝美式咖啡",
        supersedes=[old.id],
    )
    assert old.superseded is True
    assert new.supersedes == [old.id]

    # 检索与默认列表都不再返回被取代的旧记忆
    hits = memory_service.relevant_memories(db, "owner-1", "咖啡")
    assert [item.id for item in hits] == [new.id]
    assert [
        item.id for item in memory_service.list_memories(db, "owner-1")
    ] == [new.id]
    included = memory_service.list_memories(
        db,
        "owner-1",
        include_superseded=True,
    )
    assert {item.id for item in included} == {old.id, new.id}


def test_list_memories_zone_filter_and_order(db):
    older = _seed(db, "owner-1", "第一条普通记忆", zone="work")
    newer = _seed(db, "owner-1", "第二条普通记忆")
    memory_service.update_memory(db, "owner-1", older.id, tags=["刷新"])

    listed = memory_service.list_memories(db, "owner-1")
    assert [item.id for item in listed] == [older.id, newer.id]
    assert [
        item.id
        for item in memory_service.list_memories(db, "owner-1", zone="work")
    ] == [older.id]
    assert memory_service.list_memories(db, "owner-1", zone="core") == []


def test_crud_enforces_owner_isolation(db):
    mine = _seed(db, "owner-1", "用户喜欢喝黑咖啡")

    assert memory_service.list_memories(db, "owner-2") == []
    assert memory_service.relevant_memories(db, "owner-2", "咖啡") == []
    assert (
        memory_service.update_memory(
            db,
            "owner-2",
            mine.id,
            content="篡改内容",
        )
        is None
    )
    assert memory_service.delete_memory(db, "owner-2", mine.id) is None
    # owner-1 数据未受影响；冲突检测也不跨用户
    assert memory_service.list_memories(db, "owner-1")[0].content == (
        "用户喜欢喝黑咖啡"
    )
    memory_service.create_memory(db, "owner-2", "用户喜欢喝黑咖啡")

    assert memory_service.delete_memory(db, "owner-1", mine.id) is True
    assert memory_service.list_memories(db, "owner-1") == []


def test_update_memory_skips_self_and_detects_other_conflicts(db):
    first = _seed(db, "owner-1", "用户喜欢喝黑咖啡")
    second = _seed(db, "owner-1", "每天坚持晨跑五公里")

    # 仅标点变化：分词结果相同，排除自身后不会误报冲突
    updated = memory_service.update_memory(
        db,
        "owner-1",
        first.id,
        content="用户喜欢喝黑咖啡。",
    )
    assert updated is not None
    assert updated.content == "用户喜欢喝黑咖啡。"

    updated = memory_service.update_memory(
        db,
        "owner-1",
        first.id,
        zone="core",
        pinned=True,
        tags=["偏好"],
    )
    assert updated is not None
    assert (updated.zone, updated.pinned, updated.tags) == (
        "core",
        True,
        ["偏好"],
    )

    # 改成与另一条记忆重复：冲突且原内容不变
    with pytest.raises(MemoryConflictError) as conflict:
        memory_service.update_memory(
            db,
            "owner-1",
            second.id,
            content="用户喜欢喝黑咖啡。",
        )
    assert conflict.value.existing_id == first.id
    assert second.content == "每天坚持晨跑五公里"


def test_build_memory_prompt_classic_mode_and_empty_state(db):
    assert memory_service.build_memory_prompt_section(db, "owner-1") == ""

    _seed(db, "owner-1", "用户是二进制咖啡师，偏好黑咖啡", pinned=True)
    _seed(db, "owner-1", "正在推进超级助手记忆服务落地", zone="work")
    section = memory_service.build_memory_prompt_section(db, "owner-1")

    assert section.startswith("## Pinned memories")
    assert "用户是二进制咖啡师，偏好黑咖啡" in section
    assert "## Active memory index" in section
    assert "- [work] 正在推进超级助手记忆服务落地" in section
    # pinned 记忆不重复出现在索引中
    index_part = section.split("## Active memory index", 1)[1]
    assert "二进制咖啡师" not in index_part


def test_build_memory_prompt_index_cap(db, monkeypatch):
    monkeypatch.setattr(settings, "super_assistant_memory_index_cap", 1)
    _seed(db, "owner-1", "第一条普通记忆")
    _seed(db, "owner-1", "第二条普通记忆")
    _seed(db, "owner-1", "置顶记忆不占索引名额", pinned=True)

    section = memory_service.build_memory_prompt_section(db, "owner-1")
    index_lines = [
        line for line in section.splitlines() if line.startswith("- [")
    ]
    assert len(index_lines) == 1


def test_build_memory_prompt_appends_relevant_for_query(db):
    _seed(db, "owner-1", "用户喜欢喝黑咖啡")
    _seed(db, "owner-1", "今天天气不错适合出门散步")

    section = memory_service.build_memory_prompt_section(
        db,
        "owner-1",
        query_text="咖啡",
    )
    assert "## Relevant memories for this turn" in section
    relevant_part = section.split("## Relevant memories for this turn", 1)[1]
    assert "用户喜欢喝黑咖啡" in relevant_part
    assert "今天天气" not in relevant_part

    section = memory_service.build_memory_prompt_section(db, "owner-1")
    assert "## Relevant memories for this turn" not in section


def test_build_memory_prompt_three_state_priority(db):
    _seed(db, "owner-1", "用户喜欢喝黑咖啡")
    profile = SuperAssistantMemoryProfile(
        owner_id="owner-1",
        profile="画像：咖啡爱好者",
    )
    db.add(profile)
    db.commit()

    section = memory_service.build_memory_prompt_section(db, "owner-1")
    assert section.startswith("## User Profile")
    assert "画像：咖啡爱好者" in section
    assert "## Active memory index" not in section

    profile.palace_index = "宫殿索引：general 1 条"
    db.commit()
    section = memory_service.build_memory_prompt_section(db, "owner-1")
    assert section.startswith("## Memory Palace")
    assert "宫殿索引：general 1 条" in section
    assert "## User Profile" not in section
    # 宫殿模式附带主动查阅工具指引
    assert "palace_zones" in section
    assert "palace_read_zone" in section
    assert "palace_recall" in section


def test_compile_profile_and_palace_with_mock_llm(db):
    _seed(db, "owner-1", "用户是后端工程师，偏好简洁设计", zone="core")
    _seed(db, "owner-1", "正在推进超级助手记忆服务落地", zone="work")

    prompts: list[str] = []

    def fake_llm(prompt: str) -> str:
        prompts.append(prompt)
        return "LLM画像" if "用户画像" in prompt else "LLM宫殿索引"

    profile = memory_service.compile_profile_and_palace(
        db,
        "owner-1",
        fake_llm,
    )
    assert profile.profile == "LLM画像"
    assert profile.palace_index == "LLM宫殿索引"
    assert profile.compiled_at is not None
    assert len(prompts) == 2
    # 画像 prompt 只携带 core/pinned 记忆；宫殿 prompt 携带 zone 汇总
    assert "后端工程师" in prompts[0]
    assert "记忆服务落地" not in prompts[0]
    assert "core（1 条）" in prompts[1]
    assert "work（1 条）" in prompts[1]


def test_compile_without_core_memories_leaves_profile_empty(db):
    _seed(db, "owner-1", "正在推进超级助手记忆服务落地", zone="work")
    profile = memory_service.compile_profile_and_palace(
        db,
        "owner-1",
        lambda prompt: "宫殿",
    )
    assert profile.profile is None
    assert profile.palace_index == "宫殿"


def test_compile_profile_and_palace_falls_back_on_llm_failure(db):
    _seed(db, "owner-1", "用户是后端工程师", zone="core")
    _seed(db, "owner-1", "正在推进超级助手记忆服务落地", zone="work")
    db.add(SuperAssistantMemoryProfile(owner_id="owner-1", profile="旧画像"))
    db.commit()

    def failing_llm(prompt: str) -> str:
        raise RuntimeError("LLM 不可用")

    profile = memory_service.compile_profile_and_palace(
        db,
        "owner-1",
        failing_llm,
    )
    # LLM 失败：profile 保持不变，palace_index 退化为代码生成的 zone 汇总
    assert profile.profile == "旧画像"
    assert profile.palace_index is not None
    assert "### core（1 条）" in profile.palace_index
    assert "### work（1 条）" in profile.palace_index
    assert "用户是后端工程师" in profile.palace_index


def test_compile_profile_and_palace_clears_fields_without_memories(db):
    db.add(SuperAssistantMemoryProfile(
        owner_id="owner-1",
        profile="旧画像",
        palace_index="旧索引",
    ))
    db.commit()

    def unexpected_llm(prompt: str) -> str:
        raise AssertionError("无记忆时不应调用 LLM")

    profile = memory_service.compile_profile_and_palace(
        db,
        "owner-1",
        unexpected_llm,
    )
    assert profile.profile is None
    assert profile.palace_index is None
    assert profile.compiled_at is not None


@pytest.fixture()
def client_factory(tmp_path):
    engine, SessionFactory = _make_session(tmp_path)
    with SessionFactory() as session:
        session.add_all([
            User(
                id="owner-1", username="owner1",
                email="owner1@example.com", password_hash="unused",
                role="editor",
            ),
            User(
                id="owner-2", username="owner2",
                email="owner2@example.com", password_hash="unused",
                role="editor",
            ),
        ])
        session.commit()

    def override_db():
        session = SessionFactory()
        try:
            yield session
        finally:
            session.close()

    app = FastAPI()
    app.include_router(router.router, prefix="/api/v2/super-assistant")
    app.dependency_overrides[get_db] = override_db

    def make_client(user_id: str) -> TestClient:
        app.dependency_overrides[get_current_user] = lambda: User(
            id=user_id,
            username=f"user-{user_id}",
            email=f"{user_id}@example.com",
            password_hash="unused",
            role="editor",
        )
        return TestClient(app)

    yield make_client
    engine.dispose()


def test_memory_endpoints_full_lifecycle(client_factory):
    client = client_factory("owner-1")

    created = client.post("/api/v2/super-assistant/memories", json={
        "content": "用户偏好黑咖啡",
        "zone": "core",
        "pinned": True,
        "tags": ["偏好"],
    })
    assert created.status_code == 201, created.text
    memory = created.json()
    assert memory["source"] == "user"
    assert memory["confidence"] == "high"
    assert memory["zone"] == "core"
    assert memory["pinned"] is True
    assert memory["tags"] == ["偏好"]
    assert memory["match_count"] == 0
    assert memory["reference_count"] == 0
    assert memory["superseded"] is False

    # 重复写入：409 + 冲突详情
    conflict = client.post(
        "/api/v2/super-assistant/memories",
        json={"content": "用户偏好黑咖啡"},
    )
    assert conflict.status_code == 409
    payload = conflict.json()
    assert payload["detail"]
    assert payload["existing"]["id"] == memory["id"]
    assert payload["existing"]["content"] == "用户偏好黑咖啡"
    assert payload["existing"]["similarity"] >= 0.75

    # 参数校验
    assert client.post(
        "/api/v2/super-assistant/memories",
        json={"content": ""},
    ).status_code == 422

    listed = client.get("/api/v2/super-assistant/memories")
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [memory["id"]]
    assert client.get(
        "/api/v2/super-assistant/memories",
        params={"zone": "work"},
    ).json() == []
    assert client.get(
        "/api/v2/super-assistant/memories",
        params={"zone": "core"},
    ).json()[0]["id"] == memory["id"]

    updated = client.patch(
        f"/api/v2/super-assistant/memories/{memory['id']}",
        json={"zone": "work", "pinned": False, "tags": ["焦点"]},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["zone"] == "work"
    assert updated.json()["pinned"] is False
    assert updated.json()["tags"] == ["焦点"]
    assert updated.json()["confidence"] == "high"

    # 他人不可见、不可改、不可删
    other = client_factory("owner-2")
    assert other.get("/api/v2/super-assistant/memories").json() == []
    assert other.patch(
        f"/api/v2/super-assistant/memories/{memory['id']}",
        json={"zone": "core"},
    ).status_code == 404
    assert other.delete(
        f"/api/v2/super-assistant/memories/{memory['id']}"
    ).status_code == 404

    client = client_factory("owner-1")
    assert client.delete(
        f"/api/v2/super-assistant/memories/{memory['id']}"
    ).status_code == 204
    assert client.get("/api/v2/super-assistant/memories").json() == []
    assert client.delete(
        f"/api/v2/super-assistant/memories/{memory['id']}"
    ).status_code == 404
    assert client.patch(
        f"/api/v2/super-assistant/memories/{memory['id']}",
        json={"zone": "core"},
    ).status_code == 404


def test_memory_patch_conflict_returns_409(client_factory):
    client = client_factory("owner-1")
    first = client.post(
        "/api/v2/super-assistant/memories",
        json={"content": "用户偏好黑咖啡"},
    ).json()
    second = client.post(
        "/api/v2/super-assistant/memories",
        json={"content": "每天坚持晨跑五公里"},
    ).json()

    conflict = client.patch(
        f"/api/v2/super-assistant/memories/{second['id']}",
        json={"content": "用户偏好黑咖啡"},
    )
    assert conflict.status_code == 409
    assert conflict.json()["existing"]["id"] == first["id"]

    # 内容未变化时不触发冲突检测，其他字段正常更新
    ok = client.patch(
        f"/api/v2/super-assistant/memories/{first['id']}",
        json={"content": "用户偏好黑咖啡", "zone": "core"},
    )
    assert ok.status_code == 200
    assert ok.json()["zone"] == "core"
