import importlib.util
import json
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.exc import IntegrityError


def _load_migration():
    path = (
        Path(__file__).parents[2]
        / "alembic"
        / "versions"
        / "2026_07_28_0053_ontology_trial_claims.py"
    )
    spec = importlib.util.spec_from_file_location(
        "ontology_trial_claim_migration",
        path,
    )
    assert spec and spec.loader
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def test_trial_claim_migration_reclaims_running_rows_and_enforces_single_flight(
    tmp_path,
):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'trial-claims.db'}")
    metadata = sa.MetaData()
    versions = sa.Table(
        "ontology_versions",
        metadata,
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("base_release_id", sa.String(), nullable=True),
    )
    trials = sa.Table(
        "ontology_trial_runs",
        metadata,
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("version_id", sa.String(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("result_json", sa.JSON(), nullable=True),
    )
    metadata.create_all(engine)

    with engine.begin() as connection:
        connection.execute(versions.insert(), [
            {"id": "release-v1", "base_release_id": "release-v1"},
            {"id": "draft-v1-1", "base_release_id": "release-v1"},
            {"id": "release-v2", "base_release_id": "release-v2"},
            {"id": "draft-v2-1", "base_release_id": "release-v2"},
        ])
        connection.execute(trials.insert(), [
            {
                "id": "running-v1",
                "version_id": "draft-v1-1",
                "status": "running",
            },
            {
                "id": "running-v2",
                "version_id": "draft-v2-1",
                "status": "running",
            },
        ])

    migration = _load_migration()
    with engine.begin() as connection:
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()

    reflected = sa.MetaData()
    reflected.reflect(engine)
    migrated_trials = reflected.tables["ontology_trial_runs"]
    assert {
        "base_release_id",
        "claim_token",
        "lease_expires_at",
    }.issubset(migrated_trials.c.keys())

    with engine.connect() as connection:
        rows = connection.execute(sa.select(migrated_trials).order_by(
            migrated_trials.c.id,
        )).mappings().all()
        assert [row["status"] for row in rows] == ["stale", "stale"]
        assert [row["base_release_id"] for row in rows] == [
            "release-v1",
            "release-v2",
        ]
        for row in rows:
            assert row["completed_at"] is not None
            assert row["claim_token"] is None
            assert row["lease_expires_at"] is None
            result = row["result_json"]
            if isinstance(result, str):
                result = json.loads(result)
            assert result["errors"][0]["code"] == (
                "trial_run_upgrade_reclaimed"
            )

    with engine.begin() as connection:
        connection.execute(migrated_trials.insert().values(
            id="new-running-authority",
            version_id="draft-v1-1",
            status="running",
        ))
        with pytest.raises(IntegrityError):
            connection.execute(migrated_trials.insert().values(
                id="competing-running-authority",
                version_id="draft-v1-1",
                status="running",
            ))

    engine.dispose()
