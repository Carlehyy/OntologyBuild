"""Alembic 必须能从空库独立建立数据管理主链路，不能依赖应用启动补表。"""

from datetime import datetime, timezone
import json
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text


def _alembic_config(backend: Path, db_path: Path) -> Config:
    cfg = Config(str(backend / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend / "alembic"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return cfg


def test_fresh_upgrade_builds_data_management_contract(tmp_path, monkeypatch):
    backend = Path(__file__).resolve().parents[2]
    db_path = tmp_path / "fresh-data-management.db"
    monkeypatch.delenv("DATABASE_URL", raising=False)
    cfg = _alembic_config(backend, db_path)

    command.upgrade(cfg, "head")

    engine = create_engine(f"sqlite:///{db_path}")
    inspector = inspect(engine)
    required = {
        "v2_n8n_pipelines",
        "v2_steward_conversations",
        "v2_steward_messages",
        "v2_pipeline_tasks",
        "v2_pipeline_runs",
        "v2_datasets",
        "v2_dataset_versions",
        "v2_storage_deletion_outbox",
        "v2_curated_reviews",
        "v2_ontology_mappings",
        "v2_manual_dataset_shares",
        "v2_manual_dataset_changes",
    }
    assert required <= set(inspector.get_table_names())
    assert "task_id" in {c["name"] for c in inspector.get_columns("v2_pipeline_runs")}
    assert "dataset_version_id" in {
        c["name"] for c in inspector.get_columns("v2_curated_reviews")}
    assert {"producer_pipeline_id", "output_key", "source_resource"} <= {
        c["name"] for c in inspector.get_columns("v2_datasets")}
    assert {"id", "storage_uri", "attempts", "last_error", "created_at", "updated_at"} <= {
        c["name"] for c in inspector.get_columns("v2_storage_deletion_outbox")}
    assert {"ix_v2_storage_deletion_outbox_storage_uri",
            "ix_v2_storage_deletion_outbox_created_at"} <= {
        i["name"] for i in inspector.get_indexes("v2_storage_deletion_outbox")}
    row_pk = next(
        c for c in inspector.get_columns("v2_curated_row_edits")
        if c["name"] == "row_pk")
    assert str(row_pk["type"]).upper() == "TEXT"

    def foreign_key(table: str, column: str) -> dict:
        match = next(
            fk for fk in inspector.get_foreign_keys(table)
            if fk["constrained_columns"] == [column]
        )
        return match

    assert foreign_key("v2_pipeline_tasks", "pipeline_id")["referred_table"] == "v2_pipelines"
    assert foreign_key("v2_pipeline_runs", "task_id")["referred_table"] == "v2_pipeline_tasks"
    review_dataset_fk = foreign_key("v2_curated_reviews", "curated_dataset_id")
    review_version_fk = foreign_key("v2_curated_reviews", "dataset_version_id")
    assert review_dataset_fk["referred_table"] == "v2_datasets"
    assert review_version_fk["referred_table"] == "v2_dataset_versions"
    assert (review_dataset_fk.get("options") or {}).get("ondelete", "").upper() == "RESTRICT"
    assert (review_version_fk.get("options") or {}).get("ondelete", "").upper() == "RESTRICT"
    assert foreign_key("v2_ontology_mappings", "curated_dataset_id")["referred_table"] == "v2_datasets"
    producer_fk = foreign_key("v2_datasets", "producer_pipeline_id")
    assert producer_fk["referred_table"] == "v2_pipelines"
    assert (producer_fk.get("options") or {}).get("ondelete", "").upper() == "RESTRICT"
    latest_fk = foreign_key("v2_datasets", "latest_version_id")
    assert latest_fk["referred_table"] == "v2_dataset_versions"
    assert (latest_fk.get("options") or {}).get("ondelete", "").upper() == "SET NULL"
    producer_index = next(
        index for index in inspector.get_indexes("v2_datasets")
        if index["name"] == "uq_datasets_producer_output"
    )
    assert bool(producer_index["unique"]) is True
    assert producer_index["column_names"] == ["producer_pipeline_id", "output_key"]
    assert "producer_pipeline_id IS NOT NULL" in str(
        (producer_index.get("dialect_options") or {}).get("sqlite_where", "")
    )
    source_index = next(
        index for index in inspector.get_indexes("v2_datasets")
        if index["name"] == "uq_datasets_connection_resource"
    )
    assert bool(source_index["unique"]) is True
    assert source_index["column_names"] == ["source_connection_id", "source_resource"]
    source_where = str(
        (source_index.get("dialect_options") or {}).get("sqlite_where", "")
    )
    assert "source_connection_id IS NOT NULL" in source_where
    assert "source_resource IS NOT NULL" in source_where
    engine.dispose()


def test_alembic_revision_graph_has_one_head(tmp_path, monkeypatch):
    backend = Path(__file__).resolve().parents[2]
    monkeypatch.delenv("DATABASE_URL", raising=False)
    cfg = _alembic_config(backend, tmp_path / "unused.db")

    heads = ScriptDirectory.from_config(cfg).get_heads()

    assert len(heads) == 1, f"Alembic 出现多 head，发布顺序不再唯一：{heads}"


def _seed_legacy_review(
    db_url: str,
    *,
    legacy_id: str,
    canonical_id: str,
    shared_name: str = "供应商主数据",
) -> None:
    now = datetime.now(timezone.utc)
    engine = create_engine(db_url)
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO v2_datasets "
            "(id,name,source_connection_id,kind,schema_json,latest_version_id,created_at,updated_at) "
            "VALUES (:id,:name,NULL,'curated','{}',:version_id,:now,:now)"
        ), {
            "id": canonical_id,
            "name": shared_name,
            "version_id": "canonical-version-1",
            "now": now,
        })
        conn.execute(text(
            "INSERT INTO v2_dataset_versions "
            "(id,dataset_id,version_no,rowcount,storage_uri,checksum,created_at) "
            "VALUES ('canonical-version-1',:dataset_id,1,2,'file:///real.parquet','checksum',:now)"
        ), {"dataset_id": canonical_id, "now": now})
        conn.execute(text(
            "INSERT INTO v2_curated_datasets "
            "(id,pipeline_id,name,schema_json,latest_version_id,quality_score,status,created_at,updated_at) "
            "VALUES (:id,NULL,:name,'{}',NULL,1.0,'pending_review',:now,:now)"
        ), {"id": legacy_id, "name": shared_name, "now": now})
        conn.execute(text(
            "INSERT INTO v2_curated_reviews "
            "(id,curated_dataset_id,dataset_version_id,reviewer_id,status,notes,decided_at,created_at) "
            "VALUES ('review-1',:dataset_id,NULL,NULL,'pending',NULL,NULL,:now)"
        ), {"dataset_id": legacy_id, "now": now})
    engine.dispose()


def test_upgrade_rejects_same_name_identity_guess_with_audit_list(tmp_path, monkeypatch):
    """同名且已有真实版本也不能被当成同一资产，迁移必须要求人工核验 ID。"""
    backend = Path(__file__).resolve().parents[2]
    db_path = tmp_path / "legacy-name-collision.db"
    db_url = f"sqlite:///{db_path}"
    monkeypatch.delenv("DATABASE_URL", raising=False)
    cfg = _alembic_config(backend, db_path)
    command.upgrade(cfg, "0013_sentinel_action_idempotency")
    _seed_legacy_review(
        db_url,
        legacy_id="legacy-dataset-id",
        canonical_id="different-canonical-id",
    )

    with pytest.raises(RuntimeError) as exc_info:
        command.upgrade(cfg, "head")

    message = str(exc_info.value)
    assert "数据集身份预检失败" in message
    assert "v2_curated_reviews.curated_dataset_id" in message
    assert "legacy-dataset-id" in message
    assert "different-canonical-id" in message
    assert "version_count" in message

    # 失败必须是只读预检，不能把 review 偷偷改绑到同名 canonical 数据集。
    engine = create_engine(db_url)
    with engine.connect() as conn:
        review_ref = conn.execute(text(
            "SELECT curated_dataset_id FROM v2_curated_reviews WHERE id='review-1'"
        )).scalar_one()
        empty_shell = conn.execute(text(
            "SELECT 1 FROM v2_datasets WHERE id='legacy-dataset-id'"
        )).first()
    engine.dispose()
    assert review_ref == "legacy-dataset-id"
    assert empty_shell is None


def test_upgrade_accepts_only_proven_same_id_dataset_identity(tmp_path, monkeypatch):
    backend = Path(__file__).resolve().parents[2]
    db_path = tmp_path / "legacy-same-id.db"
    db_url = f"sqlite:///{db_path}"
    monkeypatch.delenv("DATABASE_URL", raising=False)
    cfg = _alembic_config(backend, db_path)
    command.upgrade(cfg, "0013_sentinel_action_idempotency")
    _seed_legacy_review(
        db_url,
        legacy_id="proven-dataset-id",
        canonical_id="proven-dataset-id",
    )

    command.upgrade(cfg, "head")

    engine = create_engine(db_url)
    inspector = inspect(engine)
    review_fks = {
        tuple(fk["constrained_columns"]): fk
        for fk in inspector.get_foreign_keys("v2_curated_reviews")
    }
    with engine.connect() as conn:
        review_ref = conn.execute(text(
            "SELECT curated_dataset_id FROM v2_curated_reviews WHERE id='review-1'"
        )).scalar_one()
    engine.dispose()
    assert review_ref == "proven-dataset-id"
    assert review_fks[("curated_dataset_id",)]["referred_table"] == "v2_datasets"
    assert (review_fks[("curated_dataset_id",)].get("options") or {}).get(
        "ondelete", "").upper() == "RESTRICT"


def _seed_pipeline(conn, pipeline_id: str, now: datetime) -> None:
    conn.execute(text(
        "INSERT INTO v2_pipelines "
        "(id,name,domain,description,source_dataset_id,route,spec,definition,column_definitions,"
        "target_curated_ids,schedule_cron,status,enabled,branch,version,created_by,created_at,updated_at) "
        "VALUES (:id,:name,NULL,'',NULL,NULL,'{}',NULL,NULL,NULL,NULL,'published',1,'main',1,NULL,:now,:now)"
    ), {"id": pipeline_id, "name": f"pipeline-{pipeline_id}", "now": now})


def _seed_output_dataset(
    conn,
    *,
    dataset_id: str,
    name: str,
    pipeline_id: str,
    output_key: str,
    now: datetime,
) -> None:
    conn.execute(text(
        "INSERT INTO v2_datasets "
        "(id,name,source_connection_id,kind,schema_json,latest_version_id,created_at,updated_at) "
        "VALUES (:id,:name,NULL,'curated',:schema,NULL,:now,:now)"
    ), {
        "id": dataset_id,
        "name": name,
        "schema": json.dumps({"pipeline_id": pipeline_id, "output_key": output_key}),
        "now": now,
    })


def test_upgrade_backfills_only_existing_pipeline_output_identity(tmp_path, monkeypatch):
    backend = Path(__file__).resolve().parents[2]
    db_path = tmp_path / "output-identity.db"
    db_url = f"sqlite:///{db_path}"
    monkeypatch.delenv("DATABASE_URL", raising=False)
    cfg = _alembic_config(backend, db_path)
    command.upgrade(cfg, "0016_link_projection_lineage")
    engine = create_engine(db_url)
    now = datetime.now(timezone.utc)
    with engine.begin() as conn:
        _seed_pipeline(conn, "pipeline-real", now)
        _seed_output_dataset(
            conn,
            dataset_id="output-proven",
            name="已验证产物",
            pipeline_id="pipeline-real",
            output_key="main",
            now=now,
        )
        _seed_output_dataset(
            conn,
            dataset_id="output-dangling-json",
            name="悬空历史元数据",
            pipeline_id="pipeline-missing",
            output_key="main",
            now=now,
        )
    engine.dispose()

    command.upgrade(cfg, "head")

    engine = create_engine(db_url)
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT id,producer_pipeline_id,output_key FROM v2_datasets "
            "WHERE id IN ('output-proven','output-dangling-json') ORDER BY id"
        )).fetchall()
    engine.dispose()
    assert rows == [
        ("output-dangling-json", None, None),
        ("output-proven", "pipeline-real", "main"),
    ]


def test_upgrade_rejects_duplicate_pipeline_output_identity(tmp_path, monkeypatch):
    backend = Path(__file__).resolve().parents[2]
    db_path = tmp_path / "duplicate-output-identity.db"
    db_url = f"sqlite:///{db_path}"
    monkeypatch.delenv("DATABASE_URL", raising=False)
    cfg = _alembic_config(backend, db_path)
    command.upgrade(cfg, "0016_link_projection_lineage")
    engine = create_engine(db_url)
    now = datetime.now(timezone.utc)
    with engine.begin() as conn:
        _seed_pipeline(conn, "pipeline-real", now)
        _seed_output_dataset(
            conn,
            dataset_id="duplicate-a",
            name="重复产物 A",
            pipeline_id="pipeline-real",
            output_key="main",
            now=now,
        )
        _seed_output_dataset(
            conn,
            dataset_id="duplicate-b",
            name="重复产物 B",
            pipeline_id="pipeline-real",
            output_key="main",
            now=now,
        )
    engine.dispose()

    with pytest.raises(RuntimeError) as exc_info:
        command.upgrade(cfg, "head")

    message = str(exc_info.value)
    assert "同一流水线产物槽位对应多个数据资产" in message
    assert "duplicate-a" in message
    assert "duplicate-b" in message


def test_upgrade_quarantines_enabled_legacy_sync_tasks(tmp_path, monkeypatch):
    """升级后旧任务只能审计，不能绕过 n8n/PipelineTask 主链路继续偷跑。"""
    backend = Path(__file__).resolve().parents[2]
    db_path = tmp_path / "legacy-sync-quarantine.db"
    db_url = f"sqlite:///{db_path}"
    monkeypatch.delenv("DATABASE_URL", raising=False)
    cfg = _alembic_config(backend, db_path)
    command.upgrade(cfg, "0016_link_projection_lineage")

    engine = create_engine(db_url)
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO v2_data_sync_tasks "
            "(id,name,connection_id,sync_mode,schedule_type,enabled,status,"
            "last_error,created_at,updated_at) "
            "VALUES ('legacy-1','旧直连任务','conn-1','APPEND','MANUAL',1,"
            "'RUNNING',NULL,:now,:now)"
        ), {"now": datetime.now(timezone.utc)})
    engine.dispose()

    command.upgrade(cfg, "head")

    engine = create_engine(db_url)
    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT enabled,status,last_error FROM v2_data_sync_tasks "
            "WHERE id='legacy-1'"
        )).one()
    engine.dispose()
    assert bool(row.enabled) is False
    assert row.status == "running"
    assert "0017" in row.last_error
    assert "n8n" in row.last_error
