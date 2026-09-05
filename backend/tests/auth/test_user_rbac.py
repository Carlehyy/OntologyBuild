def _create_editor(client, auth_headers):
    response = client.post(
        "/api/v1/users",
        json={
            "username": "team_editor",
            "email": "team_editor@example.com",
            "password": "editor123",
            "role": "editor",
        },
        headers=auth_headers,
    )
    assert response.status_code == 201
    return response.json()["data"]


def _create_custom_user(client, auth_headers):
    response = client.post(
        "/api/v1/users",
        json={
            "username": "custom_member",
            "email": "custom_member@example.com",
            "password": "custom123",
            "role": "custom",
        },
        headers=auth_headers,
    )
    assert response.status_code == 201
    return response.json()["data"]


def _login(client, username, password):
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['data']['access_token']}"}


def test_admin_user_crud_updates_real_credentials(client, admin_user, auth_headers):
    user = _create_editor(client, auth_headers)

    response = client.put(
        f"/api/v1/users/{user['id']}",
        json={
            "username": "renamed_editor",
            "email": "renamed_editor@example.com",
            "password": "changed123",
            "role": "viewer",
        },
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert response.json()["data"]["username"] == "renamed_editor"
    assert response.json()["data"]["role"] == "viewer"

    assert client.post(
        "/api/v1/auth/login",
        json={"username": "team_editor", "password": "editor123"},
    ).status_code == 401
    assert client.post(
        "/api/v1/auth/login",
        json={"username": "renamed_editor", "password": "changed123"},
    ).status_code == 200


def test_role_menu_permissions_protect_pages_and_apis(client, admin_user, auth_headers):
    _create_editor(client, auth_headers)
    response = client.put(
        "/api/v1/users/roles/editor/menu-permissions",
        json={"menu_keys": ["models"]},
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert response.json()["data"]["menu_keys"] == ["models"]

    editor_headers = _login(client, "team_editor", "editor123")
    profile = client.get("/api/v1/auth/profile", headers=editor_headers)
    assert profile.status_code == 200
    assert profile.json()["data"]["menu_permissions"] == ["models"]

    denied = client.get("/api/v1/overview/stats", headers=editor_headers)
    assert denied.status_code == 403
    assert denied.json()["detail"]["code"] == "MENU_ACCESS_DENIED"

    allowed = client.get("/api/v1/models", headers=editor_headers)
    assert allowed.status_code == 200

    # System configuration is administrator-only even when called directly.
    settings = client.get(
        "/api/v1/settings/monitoring/overview",
        headers=editor_headers,
    )
    assert settings.status_code == 403


def test_custom_role_has_one_assignment_and_configurable_menu_scope(
    client, admin_user, auth_headers,
):
    multi_role = client.post(
        "/api/v1/users",
        json={
            "username": "invalid_multi_role",
            "email": "invalid_multi_role@example.com",
            "password": "custom123",
            "role": ["viewer", "custom"],
        },
        headers=auth_headers,
    )
    assert multi_role.status_code == 422

    user = _create_custom_user(client, auth_headers)
    assert user["role"] == "custom"
    assert "roles" not in user

    custom_headers = _login(client, "custom_member", "custom123")
    profile = client.get("/api/v1/auth/profile", headers=custom_headers)
    assert profile.status_code == 200
    assert profile.json()["data"]["role"] == "custom"
    assert profile.json()["data"]["menu_permissions"] == ["overview"]

    listed = client.get(
        "/api/v1/users/roles/menu-permissions",
        headers=auth_headers,
    )
    assert listed.status_code == 200
    assert [item["role"] for item in listed.json()["data"]] == [
        "editor", "viewer", "custom",
    ]

    updated = client.put(
        "/api/v1/users/roles/custom/menu-permissions",
        json={"menu_keys": ["models"]},
        headers=auth_headers,
    )
    assert updated.status_code == 200
    assert updated.json()["data"] == {"role": "custom", "menu_keys": ["models"]}

    profile = client.get("/api/v1/auth/profile", headers=custom_headers)
    assert profile.json()["data"]["menu_permissions"] == ["models"]
    assert client.get("/api/v1/overview/stats", headers=custom_headers).status_code == 403
    assert client.get("/api/v1/models", headers=custom_headers).status_code == 200


def test_regular_user_cannot_read_or_change_role_permissions(client, admin_user, auth_headers):
    _create_editor(client, auth_headers)
    editor_headers = _login(client, "team_editor", "editor123")

    assert client.get(
        "/api/v1/users/roles/menu-permissions",
        headers=editor_headers,
    ).status_code == 403
    assert client.put(
        "/api/v1/users/roles/viewer/menu-permissions",
        json={"menu_keys": ["overview"]},
        headers=editor_headers,
    ).status_code == 403


def test_plugin_community_has_an_independent_mcp_permission_boundary(
    client, admin_user, auth_headers, db,
):
    editor = _create_editor(client, auth_headers)
    response = client.put(
        "/api/v1/users/roles/editor/menu-permissions",
        json={"menu_keys": ["community.plugins"]},
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert response.json()["data"]["menu_keys"] == [
        "community", "community.plugins",
    ]

    editor_headers = _login(client, "team_editor", "editor123")
    assert client.get(
        "/api/v2/super-assistant/mcp-servers",
        headers=editor_headers,
    ).status_code == 403

    created = client.post(
        "/api/v2/community/mcp-servers",
        json={
            "name": "community_stdio",
            "transport": "stdio",
            "command": "npx",
            "args": ["-y", "@example/mcp-server"],
            "enabled": False,
            "require_confirmation": True,
        },
        headers=editor_headers,
    )
    assert created.status_code == 201, created.text
    server = created.json()
    assert server["name"] == "community_stdio"
    assert server["enabled"] is False

    from app.super_assistant.models import SuperAssistantMcpServer

    builtin = SuperAssistantMcpServer(
        owner_id=editor["id"],
        name="platform_minio",
        builtin_key="minio",
        transport="streamable_http",
        url="builtin://minio",
        header_names=[],
        args=[],
        env_names=[],
        enabled=True,
        require_confirmation=True,
        tool_manifest=[],
    )
    db.add(builtin)
    db.commit()
    db.refresh(builtin)

    listed = client.get(
        "/api/v2/community/mcp-servers",
        headers=editor_headers,
    )
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [server["id"]]
    assert client.patch(
        f"/api/v2/community/mcp-servers/{builtin.id}",
        json={"enabled": False},
        headers=editor_headers,
    ).status_code == 404
    assert client.post(
        "/api/v2/community/mcp-servers/platform-minio",
        headers=editor_headers,
    ).status_code not in {200, 201}

    disabled = client.patch(
        f"/api/v2/community/mcp-servers/{server['id']}",
        json={"require_confirmation": False},
        headers=editor_headers,
    )
    assert disabled.status_code == 200
    assert disabled.json()["require_confirmation"] is False

    assert client.delete(
        f"/api/v2/community/mcp-servers/{server['id']}",
        headers=editor_headers,
    ).status_code == 204


def test_authorized_pages_can_read_cross_module_reference_data(client, admin_user, auth_headers, db):
    editor = _create_editor(client, auth_headers)
    editor_headers = _login(client, "team_editor", "editor123")

    response = client.put(
        "/api/v1/users/roles/editor/menu-permissions",
        json={"menu_keys": ["agent"]},
        headers=auth_headers,
    )
    assert response.status_code == 200

    # Agent needs these read-only selectors even when their management pages are
    # hidden. Mutating model configuration still requires the Models menu.
    assert client.get("/api/v1/ontologies", headers=editor_headers).status_code == 200
    assert client.get("/api/v1/models", headers=editor_headers).status_code == 200
    assert client.delete("/api/v1/models/not-found", headers=editor_headers).status_code == 403

    response = client.put(
        "/api/v1/users/roles/editor/menu-permissions",
        json={"menu_keys": ["data.structured"]},
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert response.json()["data"]["menu_keys"] == ["data", "data.structured"]

    # Exercise the shared-read rule directly; the repository-wide test SQLite
    # fixture intentionally omits several late pipeline migration columns.
    from types import SimpleNamespace
    from fastapi import HTTPException
    from app.auth.models import User
    from app.shared.deps import require_menu_permission

    editor_user = db.query(User).filter(User.id == editor["id"]).one()
    guard = require_menu_permission(
        "data.pipelines",
        read_menu_keys=("data.structured",),
    )
    assert guard(
        request=SimpleNamespace(method="GET"),
        current_user=editor_user,
        db=db,
    ) is editor_user
    try:
        guard(
            request=SimpleNamespace(method="POST"),
            current_user=editor_user,
            db=db,
        )
    except HTTPException as exc:
        assert exc.status_code == 403
    else:
        raise AssertionError("pipeline mutation unexpectedly bypassed its menu permission")


def test_last_admin_cannot_remove_own_access(client, admin_user, auth_headers):
    response = client.put(
        f"/api/v1/users/{admin_user.id}",
        json={"role": "viewer"},
        headers=auth_headers,
    )
    assert response.status_code == 400

    response = client.delete(
        f"/api/v1/users/{admin_user.id}",
        headers=auth_headers,
    )
    assert response.status_code == 400


def test_non_admin_cannot_manage_users(client, admin_user, auth_headers):
    _create_editor(client, auth_headers)
    editor_headers = _login(client, "team_editor", "editor123")

    assert client.post(
        "/api/v1/users",
        json={
            "username": "extra_user",
            "email": "extra_user@example.com",
            "password": "extra123",
            "role": "viewer",
        },
        headers=editor_headers,
    ).status_code == 403
    assert client.get("/api/v1/users", headers=editor_headers).status_code == 403
    assert client.put(
        f"/api/v1/users/{admin_user.id}",
        json={"role": "viewer"},
        headers=editor_headers,
    ).status_code == 403
    assert client.delete(
        f"/api/v1/users/{admin_user.id}",
        headers=editor_headers,
    ).status_code == 403


def test_admin_password_reset_revokes_target_tokens(client, admin_user, auth_headers):
    # 管理员重置密码同样触发 token_version 吊销；仅改资料不吊销
    user = _create_editor(client, auth_headers)
    editor_headers = _login(client, "team_editor", "editor123")
    assert client.get("/api/v1/auth/profile", headers=editor_headers).status_code == 200

    # 仅改邮箱：会话保持
    renamed = client.put(
        f"/api/v1/users/{user['id']}",
        json={"email": "team_editor2@example.com"},
        headers=auth_headers,
    )
    assert renamed.status_code == 200
    assert client.get("/api/v1/auth/profile", headers=editor_headers).status_code == 200

    # 重置密码：旧会话立即失效，新口令可登录
    reset = client.put(
        f"/api/v1/users/{user['id']}",
        json={"password": "resetPass456"},
        headers=auth_headers,
    )
    assert reset.status_code == 200
    assert client.get("/api/v1/auth/profile", headers=editor_headers).status_code == 401
    assert _login(client, "team_editor", "resetPass456")
