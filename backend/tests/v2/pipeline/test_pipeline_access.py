from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.data_channel.pipelines.models import Pipeline
from app.data_channel.pipelines.router import pipeline_access_guard


def _request(method: str, pipeline_id: str | None = None) -> Request:
    scope = {
        "type": "http",
        "method": method,
        "path": "/api/v2/pipelines",
        "headers": [],
        "path_params": ({"pipeline_id": pipeline_id} if pipeline_id else {}),
    }
    return Request(scope)


def test_pipeline_write_access_is_owner_scoped(db):
    pipeline = Pipeline(name="owned", created_by="owner", status="draft")
    db.add(pipeline)
    db.commit()

    owner = SimpleNamespace(id="owner", role="editor")
    other = SimpleNamespace(id="other", role="editor")
    admin = SimpleNamespace(id="admin", role="admin")
    viewer = SimpleNamespace(id="viewer", role="viewer")

    assert pipeline_access_guard(_request("PUT", pipeline.id), db, owner) is owner
    assert pipeline_access_guard(_request("DELETE", pipeline.id), db, admin) is admin
    assert pipeline_access_guard(_request("GET", pipeline.id), db, viewer) is viewer

    with pytest.raises(HTTPException) as exc:
        pipeline_access_guard(_request("PUT", pipeline.id), db, other)
    assert exc.value.status_code == 403

    with pytest.raises(HTTPException) as exc:
        pipeline_access_guard(_request("POST"), db, viewer)
    assert exc.value.status_code == 403


def test_legacy_unowned_pipeline_remains_editor_maintainable(db):
    pipeline = Pipeline(name="legacy", created_by=None, status="draft")
    db.add(pipeline)
    db.commit()

    editor = SimpleNamespace(id="editor", role="editor")
    assert pipeline_access_guard(_request("PUT", pipeline.id), db, editor) is editor
