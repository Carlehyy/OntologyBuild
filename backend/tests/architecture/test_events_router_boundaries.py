"""Architecture contracts for the Event Registry HTTP boundary."""
from __future__ import annotations

import ast
from pathlib import Path

from app.events import attachment_service, query_service
from app.events import router as event_router


ROUTER_PATH = Path(event_router.__file__).resolve()


def _router_tree() -> ast.Module:
    return ast.parse(
        ROUTER_PATH.read_text(encoding="utf-8"),
        filename=str(ROUTER_PATH),
    )


def _attribute_name(node: ast.AST) -> str | None:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


def _endpoint_functions() -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    endpoints = {}
    for node in _router_tree().body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if any(
            isinstance(decorator, ast.Call)
            and (
                _attribute_name(decorator.func) or ""
            ).split(".")[-1] in {
                "get",
                "post",
                "put",
                "patch",
                "delete",
            }
            for decorator in node.decorator_list
        ):
            endpoints[node.name] = node
    return endpoints


def _calls(node: ast.AST) -> set[str]:
    return {
        name
        for call in ast.walk(node)
        if isinstance(call, ast.Call)
        and (name := _attribute_name(call.func))
    }


def test_event_router_has_no_orm_transaction_or_archive_implementation():
    source = ROUTER_PATH.read_text(encoding="utf-8")
    assert ".query(" not in source
    assert ".commit(" not in source
    assert ".rollback(" not in source
    assert ".flush(" not in source
    assert "zipfile" not in source
    assert "ValidationError" not in source
    assert "RegisteredEvent" not in source
    assert "EventAttachment" not in source
    assert "EventAuditLog" not in source
    assert "EventIngestKey" not in source


def test_event_endpoints_delegate_to_cohesive_domain_services():
    endpoints = _endpoint_functions()
    assert set(endpoints) == {
        "list_events",
        "create_event",
        "stats_summary",
        "list_keys",
        "create_key",
        "revoke_key",
        "get_event",
        "update_event",
        "change_status",
        "delete_event",
        "upload_attachment",
        "download_all_attachments",
        "download_attachment",
        "delete_attachment",
        "whoami",
        "ingest_events",
        "ingest_attachment",
    }
    expected_calls = {
        "list_events": "query_service.list_events",
        "create_event": "service.create_event",
        "stats_summary": "query_service.stats_summary",
        "list_keys": "query_service.list_ingest_keys",
        "create_key": "service.mint_ingest_key",
        "revoke_key": "query_service.revoke_ingest_key",
        "get_event": "query_service.event_detail",
        "update_event": "service.update_event",
        "change_status": "service.change_status",
        "delete_event": "service.change_status",
        "upload_attachment": "service.add_attachment",
        "download_all_attachments": "attachment_service.build_archive",
        "download_attachment": "attachment_service.attachment_for_download",
        "delete_attachment": "attachment_service.remove_attachment",
        "ingest_events": "ingest_service.ingest_events",
        "ingest_attachment": "attachment_service.add_ingest_attachment",
    }
    for endpoint_name, expected_call in expected_calls.items():
        assert expected_call in _calls(endpoints[endpoint_name])


def test_event_router_keeps_historical_helper_objects():
    assert event_router._require_event is query_service.require_event
    assert event_router._as_utc is query_service.as_utc
    assert (
        event_router._shanghai_day_start_utc
        is query_service.shanghai_day_start_utc
    )
    assert event_router._shanghai_date is query_service.shanghai_date
    assert (
        event_router._remove_temporary_archive
        is attachment_service.remove_temporary_archive
    )
    assert event_router._archive_name is attachment_service.archive_name


def test_stats_resolves_router_clock_at_request_time(monkeypatch):
    marker_now = object()
    marker_result = object()
    seen: list[tuple[object, object]] = []

    monkeypatch.setattr(event_router, "_now_utc", lambda: marker_now)

    def stats_summary(db, *, now_utc):
        seen.append((db, now_utc))
        return marker_result

    monkeypatch.setattr(query_service, "stats_summary", stats_summary)
    database = object()
    assert event_router.stats_summary(database, object()) == {
        "data": marker_result,
    }
    assert seen == [(database, marker_now)]
