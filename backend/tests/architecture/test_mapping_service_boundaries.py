"""Protect MappingService's compatibility surface during internal extraction."""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

from app.ontologies.mappings import candidate_discovery
from app.ontologies.mappings import entity_reconciliation
from app.ontologies.mappings import errors
from app.ontologies.mappings import identity_metadata
from app.ontologies.mappings import mapping_service as canonical
from app.ontologies.mappings import projection_adapter
from app.ontologies.mappings import relation_processing
from app.services.v2.mapping import mapping_service as legacy


BACKEND_DIR = Path(__file__).resolve().parents[2]
MAPPING_DIR = BACKEND_DIR / "app" / "ontologies" / "mappings"

PUBLIC_SIGNATURES = {
    "create_mapping": (
        "self",
        "ontology_id",
        "curated_dataset_id",
        "entity_class",
        "field_mapping",
        "primary_key_column",
        "confidence",
        "target_object_type_id",
    ),
    "get_mappings": ("self", "ontology_id"),
    "remove_mapping_projection": ("self", "mapping"),
    "remove_link_mapping_projection": ("self", "link_mapping"),
    "apply_mapping": (
        "self",
        "mapping_id",
        "data",
        "ontology_id",
        "source_dataset_version_id",
    ),
    "build_all": ("self", "ontology_id", "require_approved"),
}

# Existing tests and downstream callers patch these names on MappingService.
# Keeping the names on the facade is part of the migration contract even when
# their implementations move to cohesive same-domain modules.
PATCH_COMPAT_METHODS = {
    "_build_all_transaction",
    "_detect_alt_key_columns",
    "_discover_action_types",
    "_discover_logic_rules",
    "_display_name",
    "_infer_and_write_relations",
    "_llm_detect_fk",
    "_normalize_mapping",
    "_process_link_mappings",
    "_rebuild_neo4j_projection",
    "_row_identity_value",
    "_rows_to_entities",
    "_stable_row_id",
    "_write_v1_entities",
}

EXTRACTED_METHODS = {
    candidate_discovery.CandidateDiscoveryMixin: {
        "_upsert_v2_logic",
        "_readable_formula",
        "_upsert_v1_logic",
        "_discover_logic_rules",
        "_upsert_v2_action",
        "_upsert_v1_action",
        "_discover_action_types",
    },
    entity_reconciliation.EntityReconciliationMixin: {
        "_write_v1_entities",
        "_adopt_legacy_projection_ownership",
        "_reconcile_mapping_entities",
    },
    identity_metadata.IdentityMetadataMixin: {
        "_normalize_mapping",
        "_property_metadata",
        "_property_metadata_by_column",
        "_infer_property_type",
        "_dataset_primary_key",
        "_declared_pk_col",
        "_choose_pk_col",
        "_is_unique_col",
        "_pk_columns",
        "_has_complete_pk",
        "_is_unique_key",
        "_row_hash",
        "_normalize_fk_value",
        "_row_identity_value",
        "_lookup_identity_value",
        "_stable_row_id",
        "_stable_relation_id",
        "_infer_cardinality",
        "_has_display_value",
        "_join_display_parts",
        "_display_name",
        "_first_value",
        "_identity_columns",
        "_name_columns",
        "_has_cjk",
        "_instance_names",
        "_rows_to_entities",
    },
    projection_adapter.ProjectionAdapterMixin: {
        "_rebuild_neo4j_projection",
    },
    relation_processing.RelationProcessingMixin: {
        "_infer_and_write_relations",
        "_detect_alt_key_columns",
        "_infer_alt_key_relations",
        "_upsert_inferred_link_mapping",
        "_detect_fk_columns",
        "_llm_detect_fk",
        "_process_link_mappings",
        "_pk_value_to_eid",
        "_process_direct_fk_link",
        "_process_edge_table_link",
        "_record_link_mapping_versions",
    },
}


def test_mapping_service_public_method_signatures_remain_stable():
    public = {
        name
        for name in dir(canonical.MappingService)
        if not name.startswith("_")
        and callable(getattr(canonical.MappingService, name))
    }
    assert public == set(PUBLIC_SIGNATURES)

    for name, expected_parameters in PUBLIC_SIGNATURES.items():
        parameters = inspect.signature(
            getattr(canonical.MappingService, name)
        ).parameters
        assert tuple(parameters) == expected_parameters

    apply_parameters = inspect.signature(
        canonical.MappingService.apply_mapping
    ).parameters
    assert (
        apply_parameters["ontology_id"].kind
        is inspect.Parameter.KEYWORD_ONLY
    )
    assert (
        apply_parameters["source_dataset_version_id"].kind
        is inspect.Parameter.KEYWORD_ONLY
    )
    build_parameters = inspect.signature(
        canonical.MappingService.build_all
    ).parameters
    assert (
        build_parameters["require_approved"].kind
        is inspect.Parameter.KEYWORD_ONLY
    )


def test_mapping_service_keeps_existing_patch_targets():
    for name in PATCH_COMPAT_METHODS:
        assert callable(getattr(canonical.MappingService, name))


def test_legacy_mapping_facade_preserves_public_object_identity():
    for name in (
        "MappingService",
        "MappingSourceError",
        "MappingApplyError",
        "MappingReleaseScopeError",
        "MappingSentinelDispatchError",
        "load_mapping_source_rows",
    ):
        assert getattr(legacy, name) is getattr(canonical, name)


def test_mapping_service_inherits_extracted_behaviors_by_identity():
    direct_methods = {
        name
        for name, value in canonical.MappingService.__dict__.items()
        if callable(value)
    }
    for mixin, method_names in EXTRACTED_METHODS.items():
        assert issubclass(canonical.MappingService, mixin)
        assert direct_methods.isdisjoint(method_names)
        for name in method_names:
            assert (
                getattr(canonical.MappingService, name)
                is getattr(mixin, name)
            )


def test_mapping_extracted_modules_do_not_depend_on_facade_or_http_router():
    for filename in (
        "candidate_discovery.py",
        "entity_reconciliation.py",
        "errors.py",
        "identity_metadata.py",
        "projection_adapter.py",
        "projection_rebuild.py",
        "relation_processing.py",
    ):
        path = MAPPING_DIR / filename
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imports: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.append(node.module or "")
        assert "app.ontologies.mappings.mapping_service" not in imports
        assert "app.ontologies.mappings.router" not in imports


def test_mapping_facade_and_extracted_modules_stay_bounded():
    limits = {
        "mapping_service.py": 1000,
        "candidate_discovery.py": 450,
        "entity_reconciliation.py": 180,
        "identity_metadata.py": 600,
        "projection_adapter.py": 180,
        "projection_rebuild.py": 340,
        "relation_processing.py": 850,
    }
    for filename, maximum in limits.items():
        line_count = len(
            (MAPPING_DIR / filename).read_text(encoding="utf-8").splitlines()
        )
        assert line_count < maximum


def test_mapping_errors_are_reexported_without_duplicate_types():
    for name in (
        "MappingSourceError",
        "MappingApplyError",
        "MappingReleaseScopeError",
        "MappingSentinelDispatchError",
    ):
        assert getattr(canonical, name) is getattr(errors, name)


def test_mapping_package_has_no_import_time_dependency_cycle():
    module_paths = {
        f"app.ontologies.mappings.{path.stem}": path
        for path in MAPPING_DIR.glob("*.py")
        if path.name != "__init__.py"
    }
    edges = {module: set() for module in module_paths}
    for module, path in module_paths.items():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            if isinstance(node, ast.Import):
                candidates = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                candidates = [node.module or ""]
                candidates.extend(
                    f"{node.module}.{alias.name}"
                    for alias in node.names
                    if node.module
                )
            else:
                continue
            edges[module].update(
                candidate
                for candidate in candidates
                if candidate in module_paths and candidate != module
            )

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(module: str) -> None:
        assert module not in visiting, f"mapping import cycle at {module}"
        if module in visited:
            return
        visiting.add(module)
        for dependency in edges[module]:
            visit(dependency)
        visiting.remove(module)
        visited.add(module)

    for module in edges:
        visit(module)
