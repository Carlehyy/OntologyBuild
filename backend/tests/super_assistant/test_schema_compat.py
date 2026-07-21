from __future__ import annotations

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from app.auth.models import User
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
