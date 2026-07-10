from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.data_channel.access import asset_lake_access_guard


def _request(method: str) -> Request:
    return Request({
        "type": "http", "method": method, "path": "/api/v2/datasets",
        "headers": [], "path_params": {},
    })


def test_asset_lake_viewer_is_read_only():
    viewer = SimpleNamespace(id="viewer", role="viewer")
    editor = SimpleNamespace(id="editor", role="editor")
    assert asset_lake_access_guard(_request("GET"), viewer) is viewer
    assert asset_lake_access_guard(_request("POST"), editor) is editor
    with pytest.raises(HTTPException) as exc:
        asset_lake_access_guard(_request("DELETE"), viewer)
    assert exc.value.status_code == 403
