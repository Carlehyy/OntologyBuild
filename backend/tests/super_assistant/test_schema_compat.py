from __future__ import annotations

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from app.auth.models import User
from app.data_channel.datasets.models import DatasetVersion
from app.models.ontology_formal import ActionExecutionLog
from app.models.sentinel import Notification, Sentinel, SentinelCdcOutbox
from app.ontologies.versions.models import OntologyTrialObject
from app.settings.object_storage.models import MinioConfig, MinioOperationAudit
from app.shared.database import Base
from app.shared.schema_compat import (
    assert_critical_schema,
    repair_development_schema,
)
from app.super_assistant.models import SuperAssistantMcpServer


def test_legacy_development_database_repairs_mcp_columns(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    Base.metadata.create_all(bind=engine, tables=[
        User.__table__,
        SuperAssistantMcpServer.__table__,
        MinioConfig.__table__,
        MinioOperationAudit.__table__,
        DatasetVersion.__table__,
        ActionExecutionLog.__table__,
        SentinelCdcOutbox.__table__,
    ])

    # Reproduce a database created before the 0034 transport expansion and the
    # 0040 built-in MinIO migration.
    with engine.begin() as connection:
        for column_name in (
            "builtin_key", "command", "args", "env_encrypted", "env_names",
        ):
            connection.execute(text(
                "ALTER TABLE super_assistant_mcp_servers "
                f"DROP COLUMN {column_name}"
            ))

    with engine.connect() as connection:
        with pytest.raises(RuntimeError, match=(
            r"super_assistant_mcp_servers\.builtin_key.*alembic upgrade head"
        )):
            assert_critical_schema(connection, Base.metadata)

        assert repair_development_schema(connection) == [
            "super_assistant_mcp_servers.command",
            "super_assistant_mcp_servers.args",
            "super_assistant_mcp_servers.env_encrypted",
            "super_assistant_mcp_servers.env_names",
            "super_assistant_mcp_servers.builtin_key",
        ]
        assert repair_development_schema(connection) == []
        assert_critical_schema(connection, Base.metadata)

    columns = {
        column["name"]
        for column in inspect(engine).get_columns("super_assistant_mcp_servers")
    }
    assert {
        "command", "args", "env_encrypted", "env_names", "builtin_key",
    } <= columns

    Session = sessionmaker(bind=engine)
    with Session() as db:
        db.add(User(
            id="user-1",
            username="owner",
            email="owner@example.com",
            password_hash="unused",
            role="editor",
        ))
        db.add(SuperAssistantMcpServer(
            owner_id="user-1",
            name="openontology_minio",
            transport="streamable_http",
            url="http://127.0.0.1:5174/mcp/minio",
        ))
        db.commit()
        saved = db.query(SuperAssistantMcpServer).one()
        assert saved.builtin_key is None


def test_legacy_development_database_repairs_dataset_payload_columns(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy-dataset.db'}")
    Base.metadata.create_all(bind=engine, tables=[
        User.__table__,
        SuperAssistantMcpServer.__table__,
        MinioConfig.__table__,
        MinioOperationAudit.__table__,
        DatasetVersion.__table__,
        ActionExecutionLog.__table__,
        SentinelCdcOutbox.__table__,
    ])
    with engine.begin() as connection:
        connection.execute(text(
            "ALTER TABLE v2_dataset_versions DROP COLUMN data_blob"))
        connection.execute(text(
            "ALTER TABLE v2_dataset_versions DROP COLUMN data_size"))

    with engine.connect() as connection:
        with pytest.raises(RuntimeError, match=(
            r"v2_dataset_versions\.data_blob.*data_size.*alembic upgrade head"
        )):
            assert_critical_schema(connection, Base.metadata)

        assert repair_development_schema(connection) == [
            "v2_dataset_versions.data_blob",
            "v2_dataset_versions.data_size",
        ]
        assert_critical_schema(connection, Base.metadata)

    columns = {
        column["name"]
        for column in inspect(engine).get_columns("v2_dataset_versions")
    }
    assert {"data_blob", "data_size"} <= columns


def test_legacy_development_database_repairs_sentinel_runtime_columns(
        tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy-sentinel.db'}")
    Base.metadata.create_all(bind=engine, tables=[
        User.__table__,
        SuperAssistantMcpServer.__table__,
        MinioConfig.__table__,
        MinioOperationAudit.__table__,
        DatasetVersion.__table__,
        ActionExecutionLog.__table__,
        Sentinel.__table__,
        SentinelCdcOutbox.__table__,
        Notification.__table__,
        OntologyTrialObject.__table__,
    ])
    with engine.begin() as connection:
        connection.execute(text(
            "ALTER TABLE fo_action_logs DROP COLUMN target_snapshot"))
        connection.execute(text(
            "ALTER TABLE sentinel_cdc_outbox DROP COLUMN mapping_ids"))
        connection.execute(text(
            "DROP INDEX ix_sentinel_cdc_outbox_release_status"))
        connection.execute(text(
            "DROP INDEX ix_sentinel_cdc_outbox_control_ready"))
        connection.execute(text(
            "DROP INDEX uq_sentinel_cdc_outbox_dedupe_key"))
        connection.execute(text(
            "ALTER TABLE sentinel_cdc_outbox "
            "DROP COLUMN ontology_release_id"))
        connection.execute(text(
            "ALTER TABLE sentinel_cdc_outbox DROP COLUMN event_kind"))
        connection.execute(text(
            "ALTER TABLE sentinel_cdc_outbox DROP COLUMN sentinel_id"))
        connection.execute(text(
            "ALTER TABLE sentinel_cdc_outbox DROP COLUMN dedupe_key"))
        connection.execute(text(
            "ALTER TABLE sentinels DROP COLUMN enable_generation"))
        for index_name in (
            "ix_sentinel_notifications_ontology_release_id",
            "ix_sentinel_notifications_sentinel_id",
            "ix_sentinel_notifications_action_log_id",
        ):
            connection.execute(text(f"DROP INDEX {index_name}"))
        connection.execute(text(
            "ALTER TABLE sentinel_notifications "
            "DROP COLUMN ontology_release_id"))
        connection.execute(text(
            "ALTER TABLE sentinel_notifications DROP COLUMN sentinel_id"))
        connection.execute(text(
            "ALTER TABLE sentinel_notifications DROP COLUMN action_log_id"))
        connection.execute(text(
            "ALTER TABLE ontology_trial_objects DROP COLUMN computed"))

    with engine.connect() as connection:
        with pytest.raises(RuntimeError, match=(
            r"fo_action_logs\.target_snapshot.*"
            r"sentinel_cdc_outbox\.ontology_release_id.*"
            r"sentinel_cdc_outbox\.event_kind.*"
            r"sentinel_cdc_outbox\.mapping_ids")):
            assert_critical_schema(connection, Base.metadata)
        assert repair_development_schema(connection) == [
            "fo_action_logs.target_snapshot",
            "sentinel_cdc_outbox.mapping_ids",
            "sentinel_cdc_outbox.ontology_release_id",
            "sentinel_cdc_outbox.event_kind",
            "sentinel_cdc_outbox.sentinel_id",
            "sentinel_cdc_outbox.dedupe_key",
            "sentinels.enable_generation",
            "ontology_trial_objects.computed",
            "sentinel_notifications.ontology_release_id",
            "sentinel_notifications.sentinel_id",
            "sentinel_notifications.action_log_id",
        ]
        assert repair_development_schema(connection) == []
        assert_critical_schema(connection, Base.metadata)
