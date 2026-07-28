"""fence concurrent ontology trial runs with durable claims

Revision ID: 0053_ontology_trial_claims
Revises: 0052_sentinel_cdc_protocol
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "0053_ontology_trial_claims"
down_revision = "0052_sentinel_cdc_protocol"
branch_labels = None
depends_on = None


TABLE = "ontology_trial_runs"
RUNNING_INDEX = "uq_ontology_trial_runs_running_version"
BASE_FK = "fk_ontology_trial_runs_base_release_id"


def _columns() -> set[str]:
    inspector = inspect(op.get_bind())
    if not inspector.has_table(TABLE):
        return set()
    return {
        column["name"]
        for column in inspector.get_columns(TABLE)
    }


def _indexes() -> set[str]:
    inspector = inspect(op.get_bind())
    if not inspector.has_table(TABLE):
        return set()
    return {
        index["name"]
        for index in inspector.get_indexes(TABLE)
        if index.get("name")
    }


def _foreign_keys() -> set[str]:
    inspector = inspect(op.get_bind())
    if not inspector.has_table(TABLE):
        return set()
    return {
        foreign_key["name"]
        for foreign_key in inspector.get_foreign_keys(TABLE)
        if foreign_key.get("name")
    }


def upgrade() -> None:
    inspector = inspect(op.get_bind())
    if not inspector.has_table(TABLE):
        return

    columns = _columns()
    missing_columns = {
        "base_release_id",
        "claim_token",
        "lease_expires_at",
    } - columns
    missing_fk = (
        "base_release_id" in missing_columns
        or BASE_FK not in _foreign_keys()
    )
    if missing_columns or missing_fk:
        with op.batch_alter_table(TABLE) as batch:
            if "base_release_id" in missing_columns:
                batch.add_column(sa.Column(
                    "base_release_id", sa.String(), nullable=True,
                ))
            if "claim_token" in missing_columns:
                batch.add_column(sa.Column(
                    "claim_token", sa.String(length=36), nullable=True,
                ))
            if "lease_expires_at" in missing_columns:
                batch.add_column(sa.Column(
                    "lease_expires_at", sa.DateTime(), nullable=True,
                ))
            if missing_fk:
                batch.create_foreign_key(
                    BASE_FK,
                    "ontology_versions",
                    ["base_release_id"],
                    ["id"],
                    ondelete="SET NULL",
                )

    # Preserve the release baseline of historical completed trials so an
    # in-flight deployment upgrade does not invalidate an already reviewed
    # candidate merely because the column did not exist when it ran.
    op.execute(sa.text(
        "UPDATE ontology_trial_runs "
        "SET base_release_id = ("
        "SELECT ontology_versions.base_release_id "
        "FROM ontology_versions "
        "WHERE ontology_versions.id = ontology_trial_runs.version_id"
        ") "
        "WHERE base_release_id IS NULL"
    ))

    # A process-local running claim cannot be inherited safely by a new
    # deployment. Terminalize it before installing the single-flight index;
    # users may immediately retry and the old worker has no claim token with
    # which to publish a late result.
    reclaimed_result = {
        "counts": {
            "objects": 0,
            "links": 0,
            "facts": 0,
            "datasets": 0,
        },
        "errors": [{
            "code": "trial_run_upgrade_reclaimed",
            "kind": "trialRun",
            "message": "部署升级已安全回收旧试跑执行权；请重新试跑",
        }],
        "warnings": [],
        "samples": {"objects": [], "links": []},
        "actionsExecuted": 0,
        "sideEffects": "blocked",
    }
    reclaim_statement = sa.text(
        "UPDATE ontology_trial_runs "
        "SET status = 'stale', "
        "completed_at = COALESCE(completed_at, CURRENT_TIMESTAMP), "
        "claim_token = NULL, "
        "lease_expires_at = NULL, "
        "result_json = :reclaimed_result "
        "WHERE status = 'running'"
    ).bindparams(sa.bindparam(
        "reclaimed_result",
        type_=sa.JSON(),
    ))
    op.get_bind().execute(
        reclaim_statement,
        {"reclaimed_result": reclaimed_result},
    )

    if RUNNING_INDEX not in _indexes():
        op.create_index(
            RUNNING_INDEX,
            TABLE,
            ["version_id"],
            unique=True,
            postgresql_where=sa.text("status = 'running'"),
            sqlite_where=sa.text("status = 'running'"),
        )


def downgrade() -> None:
    inspector = inspect(op.get_bind())
    if not inspector.has_table(TABLE):
        return
    if RUNNING_INDEX in _indexes():
        op.drop_index(RUNNING_INDEX, table_name=TABLE)

    columns = _columns()
    removable = [
        column
        for column in (
            "lease_expires_at",
            "claim_token",
            "base_release_id",
        )
        if column in columns
    ]
    if removable:
        with op.batch_alter_table(TABLE) as batch:
            for column in removable:
                batch.drop_column(column)
