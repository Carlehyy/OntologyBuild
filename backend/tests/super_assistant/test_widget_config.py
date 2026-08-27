"""悬浮 AI 助手页面可见范围配置（/api/v2/super-assistant/widget-config）。"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.auth.models import User
from app.deps import get_current_user, get_db
from app.shared.database import Base
from app.super_assistant import widget_config
from app.super_assistant.models import SuperAssistantWidgetConfig


def _make_client(tmp_path, role: str) -> TestClient:
    engine = create_engine(
        f"sqlite:///{tmp_path / f'widget-config-{role}.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine, tables=[
        User.__table__,
        SuperAssistantWidgetConfig.__table__,
    ])
    TestingSession = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    with TestingSession() as db:
        db.add(User(
            id=f"user-{role}", username=role, email=f"{role}@example.com",
            password_hash="unused", role=role,
        ))
        db.commit()

    def override_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(widget_config.router, prefix="/api/v2/super-assistant")
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: User(
        id=f"user-{role}", username=role, email=f"{role}@example.com",
        password_hash="unused", role=role,
    )
    return TestClient(app)


def test_get_defaults_to_empty_hidden_list_for_any_user(tmp_path):
    # 普通角色也可读：可见范围是平台级配置，与 super_assistant 菜单权限无关
    client = _make_client(tmp_path, role="viewer")
    resp = client.get("/api/v2/super-assistant/widget-config")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"hidden_menu_keys": [], "updated_at": None}


def test_put_requires_admin(tmp_path):
    client = _make_client(tmp_path, role="editor")
    resp = client.put(
        "/api/v2/super-assistant/widget-config",
        json={"hidden_menu_keys": ["events"]},
    )
    assert resp.status_code == 403
    # 未写入任何配置
    assert client.get("/api/v2/super-assistant/widget-config").json()["hidden_menu_keys"] == []


def test_admin_put_roundtrips_and_upserts(tmp_path):
    client = _make_client(tmp_path, role="admin")

    created = client.put(
        "/api/v2/super-assistant/widget-config",
        json={"hidden_menu_keys": ["events", "data.pipelines", "events"]},
    )
    assert created.status_code == 200, created.text
    # 重复键被归一去重
    assert created.json()["hidden_menu_keys"] == ["events", "data.pipelines"]
    assert created.json()["updated_at"]

    fetched = client.get("/api/v2/super-assistant/widget-config")
    assert fetched.json()["hidden_menu_keys"] == ["events", "data.pipelines"]

    updated = client.put(
        "/api/v2/super-assistant/widget-config",
        json={"hidden_menu_keys": ["ontologies"]},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["hidden_menu_keys"] == ["ontologies"]
    assert client.get("/api/v2/super-assistant/widget-config").json()["hidden_menu_keys"] == ["ontologies"]

    # 清空名单 = 全部页面恢复可见
    cleared = client.put(
        "/api/v2/super-assistant/widget-config",
        json={"hidden_menu_keys": []},
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["hidden_menu_keys"] == []


def test_put_rejects_blank_and_overlong_keys(tmp_path):
    client = _make_client(tmp_path, role="admin")
    assert client.put(
        "/api/v2/super-assistant/widget-config",
        json={"hidden_menu_keys": ["  "]},
    ).status_code == 422
    assert client.put(
        "/api/v2/super-assistant/widget-config",
        json={"hidden_menu_keys": ["x" * 101]},
    ).status_code == 422
