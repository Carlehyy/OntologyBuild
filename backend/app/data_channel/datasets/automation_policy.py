"""Eligibility policy for dataset-version driven automation.

This policy is shared by the durable event dispatcher and the incremental
orchestrator.  Keeping it independent prevents those two workflow modules from
depending on each other in opposite directions.
"""
from __future__ import annotations

from app.data_channel.datasets.models import Dataset, DatasetVersion
from app.data_channel.datasets.service import version_has_content


def manual_dataset_automation_eligibility(
    dataset: Dataset,
    version: DatasetVersion | None,
) -> tuple[bool, str]:
    """Return whether a manual version may drive ontology automation."""
    if dataset.kind == "curated":
        return False, "curated versions require the review-approved trigger"
    if (
        dataset.source_connection_id
        or dataset.producer_pipeline_id
        or dataset.name.startswith("SYNC::")
    ):
        return False, "dataset is maintained by a connection or pipeline"
    schema = (
        dataset.schema_json
        if isinstance(dataset.schema_json, dict)
        else {}
    )
    if not str(schema.get("primary_key") or "").strip():
        return False, "manual dataset has no primary-key contract"
    if version is None:
        return False, "dataset version is missing"
    if not version_has_content(version) or not version.checksum:
        return False, (
            "dataset version has no verifiable payload/checksum lineage"
        )
    return True, "eligible"
