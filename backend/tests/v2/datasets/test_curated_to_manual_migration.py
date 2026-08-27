"""成品数据集 → 人工数据集 异步迁移：API、任务执行与数据一致性。

需求：在数据资产湖成品数据集操作列新增「迁移」，二次确认后把该成品
数据集的结构与最新版本数据异步拷贝为人工数据集；进度可在任务列表查看。
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy.orm import sessionmaker

from app import database as database_module
from app.config import settings
from app.data_channel.pipeline_tasks import dispatch as dispatch_module
from app.data_channel.datasets.migration_jobs import read_status
from app.data_channel.datasets.service import DatasetService, rows_to_parquet_bytes
from app.main import app
from app.models.v2.dataset import Dataset
from app.routers.v2 import curated as curated_module
from app.routers.v2 import datasets as datasets_module


@pytest.fixture
def api(client, db):
    def _override():
        yield db

    for mod in (datasets_module, curated_module):
        app.dependency_overrides[mod.get_db] = _override
    yield client
    for mod in (datasets_module, curated_module):
        app.dependency_overrides.pop(mod.get_db, None)


@pytest.fixture
def task_db(db, monkeypatch):
    """run_migration 在 executor 线程内自建 Session；测试指向同一测试库。"""
    task_session = sessionmaker(bind=db.get_bind())
    monkeypatch.setattr(database_module, "SessionLocal", task_session)


def _make_curated(db, rows: list[dict], *, pk: str | None = None,
                  name: str | None = None) -> str:
    schema: dict = {}
    if pk:
        schema["primary_key"] = pk
    if rows:
        columns = list(rows[0].keys())
        schema["columns"] = columns
        schema["columns_typed"] = [
            {"name": column, "type": (
                "integer" if isinstance(rows[0][column], int) else "string")}
            for column in columns
        ]
        schema["field_names"] = {column: column for column in columns}
    ds = Dataset(
        name=name or f"curated-{uuid.uuid4().hex[:6]}",
        kind="curated",
        schema_json=schema,
    )
    db.add(ds)
    db.commit()
    db.refresh(ds)
    DatasetService(db).create_version(
        ds.id, rows_to_parquet_bytes(rows), rowcount=len(rows))
    return ds.id


def _login(client, username, password):
    r = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    return {"Authorization": f"Bearer {r.json()['data']['access_token']}"}


# ── 提交与派发 ─────────────────────────────────────────────────
def test_start_migration_queues_background_job(api, auth_headers, db, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "uploads_dir", str(tmp_path / "uploads"))
    dispatched = []
    monkeypatch.setattr(
        dispatch_module, "dispatch_task",
        lambda subject, payload: dispatched.append((subject, payload)),
    )
    ds_id = _make_curated(db, [{"id": "1", "name": "a"}], pk="id")

    response = api.post(
        "/api/v2/datasets/migrations",
        json={"curated_dataset_id": ds_id},
        headers=auth_headers,
    )

    assert response.status_code == 202, response.text
    job = response.json()["data"]
    assert job["status"] == "queued"
    assert job["source_dataset_name"].startswith("curated-")
    assert job["target_name"] == f"{job['source_dataset_name']}（人工副本）"
    assert dispatched == [(
        "task.dataset.migrate",
        {"job_id": job["job_id"], "source_dataset_id": ds_id},
    )]


def test_start_migration_unknown_dataset_is_404(api, auth_headers, db, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "uploads_dir", str(tmp_path / "uploads"))
    monkeypatch.setattr(dispatch_module, "dispatch_task", lambda *_: None)

    response = api.post(
        "/api/v2/datasets/migrations",
        json={"curated_dataset_id": "missing-id"},
        headers=auth_headers,
    )
    assert response.status_code == 404, response.text
    assert "成品数据集不存在" in response.text


def test_start_migration_dispatch_failure_is_fail_closed(
    api, auth_headers, db, monkeypatch, tmp_path,
):
    """投递失败时任务标 failed 并返回 503，不产生「排队中」的假象。"""
    monkeypatch.setattr(settings, "uploads_dir", str(tmp_path / "uploads"))
    ds_id = _make_curated(db, [{"id": "1"}], pk="id")

    def channel_down(*_args, **_kwargs):
        raise RuntimeError("nats secret detail")

    monkeypatch.setattr(dispatch_module, "dispatch_task", channel_down)

    response = api.post(
        "/api/v2/datasets/migrations",
        json={"curated_dataset_id": ds_id},
        headers=auth_headers,
    )
    assert response.status_code == 503, response.text


# ── 任务执行与数据一致性 ────────────────────────────────────────
def test_migration_copies_structure_and_data_into_manual_dataset(
    api, auth_headers, db, task_db, monkeypatch, tmp_path,
):
    monkeypatch.setattr(settings, "uploads_dir", str(tmp_path / "uploads"))
    monkeypatch.setattr(
        dispatch_module, "dispatch_task", lambda *_a, **_k: None)
    # 平台对 curated 行读取的统一口径是展示字符串（与预览/导出一致）
    rows = [
        {"id": "1", "名称": "泵机", "数量": "10"},
        {"id": "2", "名称": "阀门", "数量": "5"},
    ]
    ds_id = _make_curated(db, rows, pk="id")

    started = api.post(
        "/api/v2/datasets/migrations",
        json={"curated_dataset_id": ds_id},
        headers=auth_headers,
    ).json()["data"]

    from app.data_channel.datasets import migration_service

    migration_service.run_migration(started["job_id"], ds_id)

    status = read_status(started["job_id"])
    assert status["status"] == "completed", status
    result = status["result"]
    assert result["rowcount"] == 2
    assert result["columns"] == ["id", "名称", "数量"]
    assert result["primary_key"] == "id"

    target_id = result["id"]
    preview = api.get(
        f"/api/v2/datasets/{target_id}/preview", headers=auth_headers).json()
    data = preview.get("data", preview)
    assert data["total_rows"] == 2
    assert sorted(data["rows"], key=lambda row: row["id"]) == rows

    overview = api.get(
        "/api/v2/datasets/overview?source=manual&paginated=true",
        headers=auth_headers,
    ).json()
    items = overview.get("data", overview)["items"]
    copied = next(item for item in items if item["id"] == target_id)
    assert copied["name"] == started["target_name"]
    assert copied["primary_key"] == "id"
    assert copied["rowcount"] == 2

    source = db.query(Dataset).filter(Dataset.id == ds_id).first()
    assert source.kind == "curated"  # 源资产保持不变


def test_migration_applies_approved_row_edits(api, auth_headers, db, task_db, monkeypatch, tmp_path):
    """副本内容 = 最新版本 + 已批准的行级审核修改（与页面预览一致）。"""
    from app.data_channel.curated.review_service import ReviewService
    from app.data_channel.datasets import migration_service

    monkeypatch.setattr(settings, "uploads_dir", str(tmp_path / "uploads"))
    monkeypatch.setattr(dispatch_module, "dispatch_task", lambda *_a, **_k: None)
    ds_id = _make_curated(db, [
        {"id": "1", "name": "审核前"},
        {"id": "2", "name": "保持不变"},
    ], pk="id")
    service = ReviewService(db)
    review = service.start_review(ds_id)
    service.batch_edit_rows(review.id, [{
        "row_pk": "1", "field_name": "name",
        "old_value": "审核前", "new_value": "审核后",
    }])
    service.approve(review.id)

    started = api.post(
        "/api/v2/datasets/migrations",
        json={"curated_dataset_id": ds_id},
        headers=auth_headers,
    ).json()["data"]
    migration_service.run_migration(started["job_id"], ds_id)

    target_id = read_status(started["job_id"])["result"]["id"]
    preview = api.get(
        f"/api/v2/datasets/{target_id}/preview", headers=auth_headers).json()
    data = preview.get("data", preview)
    by_id = {row["id"]: row for row in data["rows"]}
    assert by_id["1"]["name"] == "审核后"
    assert by_id["2"]["name"] == "保持不变"


def test_repeated_migration_gets_numbered_copy_name(api, auth_headers, db, task_db, monkeypatch, tmp_path):
    from app.data_channel.datasets import migration_service

    monkeypatch.setattr(settings, "uploads_dir", str(tmp_path / "uploads"))
    monkeypatch.setattr(dispatch_module, "dispatch_task", lambda *_a, **_k: None)
    ds_id = _make_curated(db, [{"id": "1"}], pk="id")

    first = api.post(
        "/api/v2/datasets/migrations",
        json={"curated_dataset_id": ds_id},
        headers=auth_headers,
    ).json()["data"]
    migration_service.run_migration(first["job_id"], ds_id)

    second = api.post(
        "/api/v2/datasets/migrations",
        json={"curated_dataset_id": ds_id},
        headers=auth_headers,
    ).json()["data"]
    assert second["target_name"].endswith("（人工副本 2）")
    migration_service.run_migration(second["job_id"], ds_id)
    assert read_status(second["job_id"])["status"] == "completed"


def test_migration_source_missing_fails_job_with_reason(
    api, auth_headers, db, task_db, monkeypatch, tmp_path,
):
    from app.data_channel.datasets import migration_service

    monkeypatch.setattr(settings, "uploads_dir", str(tmp_path / "uploads"))
    monkeypatch.setattr(dispatch_module, "dispatch_task", lambda *_a, **_k: None)
    ds_id = _make_curated(db, [{"id": "1"}], pk="id")
    started = api.post(
        "/api/v2/datasets/migrations",
        json={"curated_dataset_id": ds_id},
        headers=auth_headers,
    ).json()["data"]
    db.query(Dataset).filter(Dataset.id == ds_id).delete()
    db.commit()

    migration_service.run_migration(started["job_id"], ds_id)

    status = read_status(started["job_id"])
    assert status["status"] == "failed"
    assert "源成品数据集不存在" in (status["error"] or "")


# ── 任务查询 ───────────────────────────────────────────────────
def test_migration_list_scoped_by_owner(api, auth_headers, client, db, monkeypatch, tmp_path):
    from app.models.user import User
    from app.services.auth_service import hash_password

    monkeypatch.setattr(settings, "uploads_dir", str(tmp_path / "uploads"))
    monkeypatch.setattr(dispatch_module, "dispatch_task", lambda *_a, **_k: None)
    ds_id = _make_curated(db, [{"id": "1"}], pk="id")

    # admin 用户由 conftest 的 auth_headers 提供；这里补一个 editor 对照
    editor = User(id=str(uuid.uuid4()), username=f"editor-{uuid.uuid4().hex[:6]}",
                  email="editor2@test.com", role="editor",
                  password_hash=hash_password("editor123"))
    db.add(editor)
    db.commit()
    editor_headers = _login(client, editor.username, "editor123")

    api.post("/api/v2/datasets/migrations",
             json={"curated_dataset_id": ds_id}, headers=auth_headers)

    mine = api.get("/api/v2/datasets/migrations", headers=auth_headers)
    assert mine.status_code == 200
    mine_jobs = mine.json()["data"]
    assert len(mine_jobs) == 1

    theirs = api.get("/api/v2/datasets/migrations", headers=editor_headers)
    assert theirs.status_code == 200
    assert theirs.json()["data"] == []

    # 单任务详情同样只允许 owner 访问
    denied = api.get(
        f"/api/v2/datasets/migrations/{mine_jobs[0]['job_id']}",
        headers=editor_headers,
    )
    assert denied.status_code == 403
