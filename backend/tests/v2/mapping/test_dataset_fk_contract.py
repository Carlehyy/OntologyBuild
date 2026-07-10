"""本体映射必须引用统一资产表，人工数据集和成品集使用同一条路径。"""

from app.ontologies.mappings.models import OntologyLinkMapping, OntologyMapping


def _target(table, column: str) -> str:
    foreign_keys = list(table.__table__.c[column].foreign_keys)
    assert len(foreign_keys) == 1
    return foreign_keys[0].target_fullname


def test_object_mapping_references_canonical_dataset_table():
    assert _target(OntologyMapping, "curated_dataset_id") == "v2_datasets.id"


def test_link_mapping_references_canonical_dataset_table():
    assert _target(OntologyLinkMapping, "src_dataset_id") == "v2_datasets.id"
    assert _target(OntologyLinkMapping, "tgt_dataset_id") == "v2_datasets.id"
    assert _target(OntologyLinkMapping, "edge_dataset_id") == "v2_datasets.id"
