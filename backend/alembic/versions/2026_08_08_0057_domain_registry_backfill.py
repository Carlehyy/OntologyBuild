"""backfill the canonical domain registry from existing ontologies

Revision ID: 0057_domain_registry_backfill
Revises: 0056_ontology_projection_fence
"""

from datetime import datetime, timezone
import uuid

from alembic import op
import sqlalchemy as sa


revision = "0057_domain_registry_backfill"
down_revision = "0056_ontology_projection_fence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    projects = sa.table(
        "ontology_projects",
        sa.column("domain", sa.String(length=100)),
        sa.column("created_by", sa.String()),
    )
    domains = sa.table(
        "domains",
        sa.column("id", sa.String()),
        sa.column("name", sa.String(length=200)),
        sa.column("description", sa.Text()),
        sa.column("created_by", sa.String()),
        sa.column("created_at", sa.DateTime()),
        sa.column("updated_at", sa.DateTime()),
    )

    existing_names = set(connection.execute(
        sa.select(domains.c.name),
    ).scalars())
    ontology_domains = connection.execute(
        sa.select(
            projects.c.domain,
            sa.func.min(projects.c.created_by).label("created_by"),
        )
        .where(
            projects.c.domain.is_not(None),
            sa.func.trim(projects.c.domain) != "",
        )
        .group_by(projects.c.domain),
    ).mappings()

    now = datetime.now(timezone.utc)
    for row in ontology_domains:
        name = row["domain"]
        if name in existing_names:
            continue
        connection.execute(domains.insert().values(
            id=str(uuid.uuid4()),
            name=name,
            description="由存量本体领域自动补录",
            created_by=row["created_by"],
            created_at=now,
            updated_at=now,
        ))
        existing_names.add(name)


def downgrade() -> None:
    # This is a data repair, not transient seed data.  Removing a repaired row
    # would recreate an orphaned ontology domain, so downgrade intentionally
    # preserves the registry entries.
    pass
