"""Legacy ontology task routes must not execute work inside the API process."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from app.ontologies.audit import router as audit_router
from app.ontologies.audit.schemas import AuditRequest
from app.routers import extraction as extraction_router
from app.schemas.extraction import ExtractionRequest
from app.tasks import audit as audit_tasks
from app.tasks import extraction as extraction_tasks


def _query_returning(*, first=None, all_rows=None):
    query = MagicMock()
    filtered = query.filter.return_value
    filtered.first.return_value = first
    filtered.all.return_value = list(all_rows or [])
    return query


def test_extraction_dispatch_failure_marks_task_failed_and_restores_project(
    monkeypatch,
):
    project = SimpleNamespace(id="ontology-dispatch", status="draft")
    db = MagicMock()
    db.query.side_effect = [
        _query_returning(first=project),
        _query_returning(all_rows=[SimpleNamespace(id="file-1")]),
    ]
    monkeypatch.setattr(
        extraction_router,
        "combine_converted_files",
        lambda _files: ("content", None),
    )
    monkeypatch.setattr(
        extraction_tasks.run_extraction,
        "delay",
        lambda *_args: (_ for _ in ()).throw(ConnectionError("broker secret")),
    )

    with pytest.raises(HTTPException) as exc_info:
        extraction_router.start_extraction(
            project.id,
            ExtractionRequest(
                prompt_id="prompt-1",
                model_id="model-1",
                model_name="model",
            ),
            db,
        )

    task = db.add.call_args.args[0]
    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == task.error
    assert "broker secret" not in str(exc_info.value.detail)
    assert task.status == "failed"
    assert task.progress == {"stage": "dispatch_failed", "pct": 0}
    assert project.status == "draft"


def test_audit_dispatch_failure_marks_task_failed(monkeypatch):
    project = SimpleNamespace(id="ontology-audit-dispatch")
    db = MagicMock()
    db.query.return_value = _query_returning(first=project)
    monkeypatch.setattr(
        audit_tasks.run_audit,
        "delay",
        lambda *_args: (_ for _ in ()).throw(ConnectionError("broker secret")),
    )

    with pytest.raises(HTTPException) as exc_info:
        audit_router.start_audit(
            project.id,
            AuditRequest(model_id="model-1", model_name="model"),
            db,
        )

    task = db.add.call_args.args[0]
    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == task.error
    assert "broker secret" not in str(exc_info.value.detail)
    assert task.status == "failed"
    assert task.progress == {"stage": "dispatch_failed", "pct": 0}
