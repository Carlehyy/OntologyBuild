"""工单 API 契约测试：提交、可见性 scope、管理员处理（必填评论）与附件。"""
import io

from app.shared.config import settings


def _login(client, username: str, password: str) -> dict:
    response = client.post(
        "/api/v1/auth/login", json={"username": username, "password": password})
    token = response.json()["data"]["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _create(client, headers, *, title="登录页偶尔白屏", content="生产环境偶发", submitter=None):
    return client.post(
        "/api/v2/tickets",
        json={"title": title, "content": content},
        headers=headers,
    )


def test_create_ticket_defaults_to_pending_and_records_submitter(
    client, auth_headers, admin_user,
):
    response = _create(client, auth_headers)
    assert response.status_code == 201
    data = response.json()["data"]
    assert data["status"] == "pending"
    assert data["submitterId"] == admin_user.id
    assert data["submitterName"] == "admin"
    assert data["ticketNo"].startswith("TK-")
    assert data["attachmentCount"] == 0


def test_create_ticket_rejects_blank_fields(client, auth_headers):
    assert _create(client, auth_headers, title="  ").status_code == 422
    assert _create(client, auth_headers, content=" ").status_code == 422


def test_non_admin_only_sees_own_tickets(client, auth_headers, editor_user):
    editor_headers = _login(client, "editor", "editor123")
    mine = _create(client, editor_headers, title="编辑者的工单", content="自己的反馈")
    assert mine.status_code == 201
    _create(client, auth_headers, title="管理员的工单", content="别人的反馈")

    editor_list = client.get("/api/v2/tickets", headers=editor_headers).json()["data"]
    assert editor_list["total"] == 1
    assert editor_list["items"][0]["title"] == "编辑者的工单"

    admin_list = client.get("/api/v2/tickets", headers=auth_headers).json()["data"]
    assert admin_list["total"] == 2

    # 详情可见性同 scope：非提交人访问他人工单被拒
    admin_ticket_id = next(
        item["id"] for item in admin_list["items"] if item["title"] == "管理员的工单")
    denied = client.get(
        f"/api/v2/tickets/{admin_ticket_id}", headers=editor_headers)
    assert denied.status_code == 403


def test_progress_requires_admin_and_non_empty_comment(
    client, auth_headers, editor_user,
):
    editor_headers = _login(client, "editor", "editor123")
    created = _create(client, editor_headers).json()["data"]
    ticket_id = created["id"]

    # 非管理员不能处理
    forbidden = client.post(
        f"/api/v2/tickets/{ticket_id}/progress",
        json={"status": "verifying", "comment": "开始核查"},
        headers=editor_headers,
    )
    assert forbidden.status_code == 403

    # 管理员处理必须带非空评论
    blank = client.post(
        f"/api/v2/tickets/{ticket_id}/progress",
        json={"status": "verifying", "comment": "   "},
        headers=auth_headers,
    )
    assert blank.status_code == 422

    # 非法状态
    invalid = client.post(
        f"/api/v2/tickets/{ticket_id}/progress",
        json={"status": "done", "comment": "不存在的状态"},
        headers=auth_headers,
    )
    assert invalid.status_code == 422

    # 合法处理：状态迁移落轨迹，评论保留
    progress = client.post(
        f"/api/v2/tickets/{ticket_id}/progress",
        json={"status": "verifying", "comment": "已复现，联系用户提供浏览器版本"},
        headers=auth_headers,
    )
    assert progress.status_code == 200
    data = progress.json()["data"]
    assert data["status"] == "verifying"
    assert len(data["progressLogs"]) == 1
    log = data["progressLogs"][0]
    assert log["fromStatus"] == "pending"
    assert log["toStatus"] == "verifying"
    assert log["comment"] == "已复现，联系用户提供浏览器版本"
    assert log["actorName"] == "admin"

    # 同状态补充评论也允许（仅追加轨迹，不改状态）
    again = client.post(
        f"/api/v2/tickets/{ticket_id}/progress",
        json={"status": "verifying", "comment": "等待用户补充信息"},
        headers=auth_headers,
    ).json()["data"]
    assert again["status"] == "verifying"
    assert len(again["progressLogs"]) == 2
    assert again["progressLogs"][1]["fromStatus"] == "verifying"


def test_list_filters_by_status_and_query(client, auth_headers):
    first = _create(client, auth_headers, title="白屏问题", content="登录后偶发").json()["data"]
    _create(client, auth_headers, title="导出慢", content="导出 CSV 需要很久")
    client.post(
        f"/api/v2/tickets/{first['id']}/progress",
        json={"status": "completed", "comment": "已修复上线"},
        headers=auth_headers,
    )

    pending_only = client.get(
        "/api/v2/tickets", params={"status": "pending"}, headers=auth_headers).json()["data"]
    assert pending_only["total"] == 1
    assert pending_only["items"][0]["title"] == "导出慢"

    search = client.get(
        "/api/v2/tickets", params={"q": "白屏"}, headers=auth_headers).json()["data"]
    assert search["total"] == 1
    assert search["items"][0]["ticketNo"] == first["ticketNo"]

    bad_status = client.get(
        "/api/v2/tickets", params={"status": "done"}, headers=auth_headers)
    assert bad_status.status_code == 422


def test_stats_summary_scopes_by_role(client, auth_headers, editor_user):
    editor_headers = _login(client, "editor", "editor123")
    _create(client, editor_headers)
    _create(client, auth_headers)

    editor_stats = client.get(
        "/api/v2/tickets/stats/summary", headers=editor_headers).json()["data"]
    assert editor_stats["total"] == 1
    assert editor_stats["byStatus"]["pending"] == 1

    admin_stats = client.get(
        "/api/v2/tickets/stats/summary", headers=auth_headers).json()["data"]
    assert admin_stats["total"] == 2
    assert set(admin_stats["byStatus"]) == {
        "pending", "verifying", "accepted", "completed", "cancelled",
    }


def test_attachment_upload_download_and_visibility(
    client, auth_headers, editor_user, tmp_path, monkeypatch,
):
    monkeypatch.setattr(settings, "uploads_dir", str(tmp_path))
    editor_headers = _login(client, "editor", "editor123")
    ticket = _create(client, editor_headers).json()["data"]

    upload = client.post(
        f"/api/v2/tickets/{ticket['id']}/attachments",
        files={"file": ("截图.png", io.BytesIO(b"png-bytes"), "image/png")},
        headers=editor_headers,
    )
    assert upload.status_code == 201
    att = upload.json()["data"]
    assert att["filename"] == "截图.png"
    assert att["fileSize"] == len(b"png-bytes")
    assert att["sha256"]

    # 提交人可下载，内容一致
    downloaded = client.get(
        f"/api/v2/tickets/{ticket['id']}/attachments/{att['id']}/download",
        headers=editor_headers,
    )
    assert downloaded.status_code == 200
    assert downloaded.content == b"png-bytes"

    # 管理员可见附件详情
    detail = client.get(
        f"/api/v2/tickets/{ticket['id']}", headers=auth_headers).json()["data"]
    assert [item["filename"] for item in detail["attachments"]] == ["截图.png"]

    # 列表计数
    listed = client.get("/api/v2/tickets", headers=editor_headers).json()["data"]
    assert listed["items"][0]["attachmentCount"] == 1


def test_attachment_rejects_disallowed_extension(
    client, auth_headers, tmp_path, monkeypatch,
):
    monkeypatch.setattr(settings, "uploads_dir", str(tmp_path))
    monkeypatch.setattr(settings, "ticket_attachment_extensions", "png,jpg")
    ticket = _create(client, auth_headers).json()["data"]

    rejected = client.post(
        f"/api/v2/tickets/{ticket['id']}/attachments",
        files={"file": ("恶意.exe", io.BytesIO(b"MZ"), "application/octet-stream")},
        headers=auth_headers,
    )
    assert rejected.status_code == 400
    assert "不支持" in rejected.json()["detail"]
