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
    settings = client.get("/api/v1/settings/rules", headers=editor_headers)
    assert settings.status_code == 403


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
