def test_steward_file_preview_returns_extracted_text(client, auth_headers):
    created = client.post(
        "/api/v2/steward/conversations",
        json={"title": "文件预览测试"},
        headers=auth_headers,
    )
    assert created.status_code == 201
    conversation_id = created.json()["data"]["id"]

    uploaded = client.post(
        f"/api/v2/steward/conversations/{conversation_id}/files",
        files={"file": ("pipeline-notes.md", b"# Pipeline Notes\n\nPreview content", "text/markdown")},
        headers=auth_headers,
    )
    assert uploaded.status_code == 201
    artifact = uploaded.json()["data"]

    preview = client.get(
        f"/api/v2/steward/conversations/{conversation_id}/files/{artifact['id']}/preview",
        headers=auth_headers,
    )
    assert preview.status_code == 200
    payload = preview.json()["data"]
    assert payload["file"]["filename"] == "pipeline-notes.md"
    assert "Preview content" in payload["content"]
    assert payload["previewable"] is True
    assert payload["truncated"] is False

    deleted = client.delete(
        f"/api/v2/steward/conversations/{conversation_id}",
        headers=auth_headers,
    )
    assert deleted.status_code == 204
