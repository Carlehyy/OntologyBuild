"""Keep reviewed-data automation flowing through explicit read/write ports."""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

from app.data_channel.curated import approved_version_reader
from app.data_channel.curated import review_service
from app.data_channel.datasets import version_event_outbox
from app.data_channel.datasets import version_events
from app.ontologies.mappings import application
from app.ontologies.mappings import mapping_service


BACKEND_DIR = Path(__file__).resolve().parents[2]
APP_DIR = BACKEND_DIR / "app"
MAPPING_DIR = APP_DIR / "ontologies" / "mappings"

APPROVED_READER_COMPAT_NAMES = {
    "ReviewApprovalError",
    "_as_aware",
    "_coerce_review_value",
    "_dataset_schema",
    "_field_contract",
    "_field_type",
    "_version_by_id",
    "apply_all_row_edits",
    "current_version_review",
    "dataset_pk_columns",
    "encode_row_pk",
    "latest_dataset_version",
    "load_all_rows_with_edits",
    "load_rows_with_edits",
    "normalize_row_pk",
    "require_current_version_approved",
    "require_version_approved",
    "review_matches_version",
    "version_review",
}

OUTBOX_CONTRACT_NAMES = {
    "VERSION_PUBLISHED_EVENT",
    "CURATED_REVIEW_APPROVED_EVENT",
    "CURATED_REVIEW_PENDING_STATUS",
    "CURATED_REVIEW_PROCESSING_STATUS",
    "CURATED_REVIEW_RETRY_STATUS",
}


def _absolute_imports(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend((node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imports.append((node.lineno, node.module))
    return imports


def _forbidden_imports(
    paths: list[Path],
    forbidden_prefixes: tuple[str, ...],
) -> list[str]:
    violations: list[str] = []
    for path in paths:
        for line, imported_module in _absolute_imports(path):
            if any(
                imported_module == prefix
                or imported_module.startswith(f"{prefix}.")
                for prefix in forbidden_prefixes
            ):
                violations.append(
                    f"{path.relative_to(BACKEND_DIR)}:{line} imports "
                    f"{imported_module}"
                )
    return violations


def _imported_modules(path: Path) -> set[str]:
    return {module for _line, module in _absolute_imports(path)}


def test_review_service_preserves_approved_reader_import_compatibility():
    for name in APPROVED_READER_COMPAT_NAMES:
        assert getattr(review_service, name) is getattr(
            approved_version_reader,
            name,
        )


def test_event_dispatcher_reexports_the_writer_contract():
    for name in OUTBOX_CONTRACT_NAMES:
        assert getattr(version_events, name) is getattr(
            version_event_outbox,
            name,
        )


def test_projection_command_preserves_mapping_service_patch_compatibility():
    assert application.MappingService is mapping_service.MappingService

    signature = inspect.signature(application.rebuild_ontology_projection)
    assert tuple(signature.parameters) == (
        "db",
        "ontology_id",
        "require_approved",
    )
    assert (
        signature.parameters["require_approved"].kind
        is inspect.Parameter.KEYWORD_ONLY
    )
    assert signature.parameters["require_approved"].default is True


def test_review_read_write_and_mapping_command_modules_stay_bounded():
    limits = {
        APP_DIR / "data_channel" / "curated"
        / "approved_version_reader.py": 380,
        APP_DIR / "data_channel" / "curated" / "review_service.py": 550,
        APP_DIR / "data_channel" / "datasets"
        / "version_event_outbox.py": 80,
        MAPPING_DIR / "application.py": 60,
    }
    for path, maximum in limits.items():
        line_count = len(path.read_text(encoding="utf-8").splitlines())
        assert line_count < maximum, (
            f"{path.relative_to(BACKEND_DIR)} has grown to {line_count} lines; "
            "extract a cohesive policy/port before adding more workflow logic"
        )


def test_approval_enqueues_before_its_single_transaction_commit():
    path = APP_DIR / "data_channel" / "curated" / "review_service.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    review_class = next(
        node for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "ReviewService"
    )
    approve = next(
        node for node in review_class.body
        if isinstance(node, ast.FunctionDef) and node.name == "approve"
    )

    enqueue_lines = [
        node.lineno for node in ast.walk(approve)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "enqueue_curated_review_approved"
    ]
    commit_lines = [
        node.lineno for node in ast.walk(approve)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "commit"
    ]

    assert len(enqueue_lines) == 1
    assert len(commit_lines) == 1
    assert enqueue_lines[0] < commit_lines[0]


def test_outbox_writer_never_owns_transaction_completion():
    path = (
        APP_DIR
        / "data_channel"
        / "datasets"
        / "version_event_outbox.py"
    )
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    enqueue = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "enqueue_curated_review_approved"
    )
    transaction_calls = [
        node for node in ast.walk(enqueue)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"commit", "flush", "rollback"}
    ]
    assert not transaction_calls


def test_mapping_domain_cannot_depend_on_event_workers_or_legacy_facade():
    mapping_files = sorted(MAPPING_DIR.glob("*.py"))
    violations = _forbidden_imports(
        mapping_files,
        (
            "app.data_channel.curated.review_service",
            "app.data_channel.datasets.version_events",
            "app.data_channel.sync_tasks.incremental_orchestrator",
            "app.services.v2.mapping.mapping_service",
        ),
    )
    assert not violations, (
        "Mappings may consume the approved-version read port, but must not "
        "depend on review commands, event workers, orchestration, or its "
        "legacy facade:\n" + "\n".join(violations)
    )


def test_review_and_outbox_write_ports_do_not_depend_on_downstream_workers():
    review_path = (
        APP_DIR / "data_channel" / "curated" / "review_service.py"
    )
    reader_path = (
        APP_DIR / "data_channel" / "curated" / "approved_version_reader.py"
    )
    outbox_path = (
        APP_DIR
        / "data_channel"
        / "datasets"
        / "version_event_outbox.py"
    )
    violations = _forbidden_imports(
        [review_path],
        (
            "app.data_channel.datasets.version_events",
            "app.data_channel.sync_tasks.incremental_orchestrator",
            "app.ontologies.mappings",
        ),
    )
    violations.extend(_forbidden_imports(
        [reader_path, outbox_path],
        (
            "app.data_channel.curated.review_service",
            "app.data_channel.datasets.version_events",
            "app.data_channel.sync_tasks.incremental_orchestrator",
            "app.ontologies.mappings",
        ),
    ))
    assert not violations, (
        "Review/read/outbox writer modules are upstream ports and cannot "
        "reach event workers or ontology commands:\n" + "\n".join(violations)
    )


def test_dispatcher_orchestrator_and_projection_command_form_one_way_chain():
    dispatcher_path = (
        APP_DIR / "data_channel" / "datasets" / "version_events.py"
    )
    orchestrator_path = (
        APP_DIR
        / "data_channel"
        / "sync_tasks"
        / "incremental_orchestrator.py"
    )
    application_path = MAPPING_DIR / "application.py"

    dispatcher_imports = _imported_modules(dispatcher_path)
    orchestrator_imports = _imported_modules(orchestrator_path)
    application_imports = _imported_modules(application_path)
    assert (
        "app.data_channel.sync_tasks.incremental_orchestrator"
        in dispatcher_imports
    )
    assert "app.ontologies.mappings.application" in orchestrator_imports
    assert "app.ontologies.mappings.mapping_service" in application_imports

    violations = _forbidden_imports(
        [dispatcher_path],
        (
            "app.ontologies.mappings",
            "app.data_channel.curated.review_service",
        ),
    )
    violations.extend(_forbidden_imports(
        [orchestrator_path],
        (
            "app.data_channel.datasets.version_events",
            "app.data_channel.curated.review_service",
            "app.ontologies.mappings.mapping_service",
            "app.services.v2.mapping.mapping_service",
        ),
    ))
    violations.extend(_forbidden_imports(
        [application_path],
        (
            "app.data_channel",
            "app.services.v2.mapping.mapping_service",
        ),
    ))
    assert not violations, (
        "Durable automation must flow dispatcher -> orchestrator -> canonical "
        "mapping command without a reverse dependency:\n"
        + "\n".join(violations)
    )
