"""Curated catalog exposes the same identity contract consumed by mappings."""
from __future__ import annotations

import uuid

from app.data_channel.curated.router import get_curated, list_curated
from app.models.v2.dataset import Dataset


def test_curated_list_and_detail_expose_canonical_composite_primary_key(db):
    dataset = Dataset(
        id=str(uuid.uuid4()),
        name="tenant orders",
        kind="curated",
        schema_json={"primary_key": "tenant_id, order_id"},
    )
    db.add(dataset)
    db.commit()

    listed = list_curated(db)
    detail = get_curated(dataset.id, db)

    item = next(row for row in listed if row.id == dataset.id)
    assert item.primary_key == "tenant_id,order_id"
    assert detail.primary_key == "tenant_id,order_id"
