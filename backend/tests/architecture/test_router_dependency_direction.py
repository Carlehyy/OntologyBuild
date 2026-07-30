"""Keep HTTP adapters out of production dependency direction."""
from __future__ import annotations

import ast
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[2]
APP_DIR = BACKEND_DIR / "app"


def _is_router_module(module: str) -> bool:
    parts = [part for part in module.split(".") if part]
    leaf = parts[-1]
    return (
        "routers" in parts
        or leaf == "router"
        or leaf.endswith("_router")
    )


def _resolved_from_module(path: Path, node: ast.ImportFrom) -> str:
    module_parts = (node.module or "").split(".") if node.module else []
    if not node.level:
        return ".".join(module_parts)

    relative = path.relative_to(BACKEND_DIR).with_suffix("")
    package_parts = list(relative.parts[:-1])
    keep = max(0, len(package_parts) - (node.level - 1))
    return ".".join(package_parts[:keep] + module_parts)


def _router_imports(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(
                (node.lineno, alias.name)
                for alias in node.names
                if _is_router_module(alias.name)
            )
        elif isinstance(node, ast.ImportFrom):
            module = _resolved_from_module(path, node)
            if _is_router_module(module):
                imports.append((node.lineno, module))
                continue
            for alias in node.names:
                candidate = ".".join(
                    part
                    for part in (module, alias.name)
                    if part
                )
                if _is_router_module(candidate):
                    imports.append((node.lineno, candidate))
    return imports


def test_only_composition_roots_import_router_modules():
    """Services, tasks, and canonical routers consume domain services."""
    violations: list[str] = []
    for path in sorted(APP_DIR.rglob("*.py")):
        relative_path = path.relative_to(APP_DIR)
        if (
            relative_path == Path("main.py")
            or relative_path.parts[0] == "routers"
        ):
            continue
        for line, imported_module in _router_imports(path):
            violations.append(
                f"{path.relative_to(BACKEND_DIR)}:{line} imports "
                f"{imported_module}",
            )

    assert not violations, (
        "Only app/main.py and app/routers/** may compose HTTP router modules; "
        "all other production modules must depend on canonical contracts or "
        "services:\n"
        + "\n".join(violations)
    )


def test_router_era_data_channel_symbols_remain_compatibility_aliases():
    """Old imports and monkeypatch targets remain present during migration."""
    from app.data_channel.datasets import consumers, manual_contract
    from app.data_channel.datasets import router as dataset_router
    from app.data_channel.pipelines import dependency_service
    from app.data_channel.pipelines import router as pipeline_router

    assert dataset_router._dataset_consumers is consumers.dataset_consumers
    assert dataset_router._consumer_map is consumers.dataset_consumer_map
    assert (
        dataset_router.MANUAL_FIELD_KEY_RE
        is manual_contract.MANUAL_FIELD_KEY_RE
    )
    assert (
        dataset_router.MANUAL_FIELD_CONTRACT_VERSION
        == manual_contract.MANUAL_FIELD_CONTRACT_VERSION
    )
    assert dataset_router.CreateTableRequest is manual_contract.CreateTableRequest
    assert dataset_router.TableColumnDef is manual_contract.TableColumnDef
    assert dataset_router.RowEditOp is manual_contract.RowEditOp
    assert dataset_router.RowEditsRequest is manual_contract.RowEditsRequest
    assert dataset_router._build_manual_schema is manual_contract.build_manual_schema
    assert (
        dataset_router._serialize_manual_contract_rows
        is manual_contract.serialize_manual_contract_rows
    )
    assert (
        dataset_router._normalize_manual_contract_upload
        is manual_contract.normalize_manual_contract_upload
    )
    assert dataset_router._validate_manual_rows is manual_contract.validate_manual_rows
    assert (
        dataset_router._require_manual_dataset
        is manual_contract.require_manual_dataset
    )
    assert (
        pipeline_router._reject_if_sync_chain_refs
        is dependency_service.reject_if_sync_chain_refs
    )


def test_api_hub_router_symbols_remain_compatibility_aliases():
    from app.api_hub import interface_contracts, interface_service
    from app.api_hub.routers import interfaces as interface_router

    assert interface_router.KV is interface_contracts.KV
    assert interface_router.FileField is interface_contracts.FileField
    assert (
        interface_router.InterfaceParameter
        is interface_contracts.InterfaceParameter
    )
    assert interface_router.InterfaceIn is interface_contracts.InterfaceIn
    assert (
        interface_router.PreviewInterfaceIn
        is interface_contracts.PreviewInterfaceIn
    )
    assert (
        interface_router.DeleteGroupBody
        is interface_contracts.DeleteGroupBody
    )
    assert interface_router._row_to_dict is interface_service._row_to_dict
    assert interface_router._get_or_404 is interface_service._get_or_404
    assert interface_router._dump_kv is interface_service._dump_kv
    assert (
        interface_router._load_json_list
        is interface_service._load_json_list
    )
    assert (
        interface_router._normalize_publish_keys
        is interface_service._normalize_publish_keys
    )
    assert (
        interface_router._check_group_name
        is interface_service._check_group_name
    )
    assert (
        interface_router._validate_proxy_publish
        is interface_service._validate_proxy_publish
    )
    assert interface_router.create_interface is interface_service.create_interface
    assert interface_router.update_interface is interface_service.update_interface
    assert interface_router.delete_interface is interface_service.delete_interface
    assert interface_router.delete_group is interface_service.delete_group
