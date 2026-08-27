"""个人资料自助更新与用户私有环境变量（MYW-56）。"""

from sqlalchemy.orm import Session

from app.auth.crypto import decrypt_value
from app.auth.models import UserEnvVar


def _login_headers(client, username: str, password: str) -> dict:
    r = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['data']['access_token']}"}


# ---- PUT /api/v1/auth/profile：邮箱自助更新，用户名不可自改 ----

def test_update_profile_email_success(client, admin_user):
    headers = _login_headers(client, "admin", "admin123")
    r = client.put("/api/v1/auth/profile", json={"email": "renamed@test.com"}, headers=headers)
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["email"] == "renamed@test.com"
    assert data["username"] == "admin"

    r = client.get("/api/v1/auth/profile", headers=headers)
    assert r.status_code == 200
    assert r.json()["data"]["email"] == "renamed@test.com"


def test_update_profile_keeps_same_email(client, admin_user):
    headers = _login_headers(client, "admin", "admin123")
    r = client.put("/api/v1/auth/profile", json={"email": admin_user.email}, headers=headers)
    assert r.status_code == 200


def test_update_profile_rejects_duplicate_email(client, admin_user, editor_user):
    headers = _login_headers(client, "admin", "admin123")
    r = client.put(
        "/api/v1/auth/profile",
        json={"email": editor_user.email},
        headers=headers,
    )
    assert r.status_code == 409


def test_update_profile_ignores_username(client, admin_user):
    headers = _login_headers(client, "admin", "admin123")
    r = client.put(
        "/api/v1/auth/profile",
        json={"email": "still-admin@test.com", "username": "attacker"},
        headers=headers,
    )
    assert r.status_code == 200
    assert r.json()["data"]["username"] == "admin"


def test_update_profile_rejects_invalid_email(client, admin_user):
    headers = _login_headers(client, "admin", "admin123")
    r = client.put("/api/v1/auth/profile", json={"email": "not-an-email"}, headers=headers)
    assert r.status_code == 422


def test_update_profile_requires_auth(client):
    r = client.put("/api/v1/auth/profile", json={"email": "anon@test.com"})
    assert r.status_code == 403


# ---- 用户私有环境变量 /api/v1/auth/env-vars ----

def test_env_vars_roundtrip_and_encrypted_at_rest(client, admin_user, db: Session):
    headers = _login_headers(client, "admin", "admin123")
    items = [
        {"key": "API_KEY", "value": "sk-secret-123"},
        {"key": "REGION", "value": "cn-east-1"},
        {"key": "EMPTY_OK", "value": ""},
    ]
    r = client.put("/api/v1/auth/env-vars", json={"items": items}, headers=headers)
    assert r.status_code == 200
    # 列表按 key 排序返回
    assert [(row["key"], row["value"]) for row in r.json()["data"]] == sorted(
        [(item["key"], item["value"]) for item in items]
    )

    r = client.get("/api/v1/auth/env-vars", headers=headers)
    assert r.status_code == 200
    got = {row["key"]: row["value"] for row in r.json()["data"]}
    assert got == {"API_KEY": "sk-secret-123", "REGION": "cn-east-1", "EMPTY_OK": ""}

    # 落库为密文而非明文
    rows = db.query(UserEnvVar).filter(UserEnvVar.user_id == admin_user.id).all()
    assert len(rows) == 3
    for row in rows:
        assert "sk-secret-123" != row.value_encrypted
        if row.key == "API_KEY":
            assert row.value_encrypted.startswith("gAAAAA")  # Fernet token 前缀
            assert decrypt_value(row.value_encrypted) == "sk-secret-123"
        if row.key == "EMPTY_OK":
            assert row.value_encrypted == ""


def test_env_vars_replace_all_scoped_per_user(client, admin_user, editor_user, db: Session):
    admin_headers = _login_headers(client, "admin", "admin123")
    editor_headers = _login_headers(client, "editor", "editor123")

    def put(headers, items):
        return client.put("/api/v1/auth/env-vars", json={"items": items}, headers=headers)

    assert put(editor_headers, [{"key": "EDITOR_ONLY", "value": "e"}]).status_code == 200
    assert put(admin_headers, [
        {"key": "A", "value": "1"},
        {"key": "B", "value": "2"},
    ]).status_code == 200

    # 全量保存语义：再次保存时未携带的 key 被移除，且不影响其他用户
    r = put(admin_headers, [{"key": "C", "value": "3"}])
    assert r.status_code == 200
    keys = lambda headers: {
        row["key"]
        for row in client.get("/api/v1/auth/env-vars", headers=headers).json()["data"]
    }
    assert keys(admin_headers) == {"C"}
    assert keys(editor_headers) == {"EDITOR_ONLY"}


def test_env_vars_rejects_duplicate_keys_in_payload(client, admin_user):
    headers = _login_headers(client, "admin", "admin123")
    r = client.put(
        "/api/v1/auth/env-vars",
        json={"items": [{"key": "DUP", "value": "1"}, {"key": "DUP", "value": "2"}]},
        headers=headers,
    )
    assert r.status_code == 400


def test_env_vars_rejects_invalid_key(client, admin_user):
    headers = _login_headers(client, "admin", "admin123")
    for bad_key in ("has space", "", "中文名", "a" * 129):
        r = client.put(
            "/api/v1/auth/env-vars",
            json={"items": [{"key": bad_key, "value": "v"}]},
            headers=headers,
        )
        assert r.status_code == 422, bad_key


def test_env_vars_rejects_over_limit_items(client, admin_user):
    headers = _login_headers(client, "admin", "admin123")
    items = [{"key": f"K{i}", "value": str(i)} for i in range(51)]
    r = client.put("/api/v1/auth/env-vars", json={"items": items}, headers=headers)
    assert r.status_code == 422


def test_env_vars_accepts_boundary_items(client, admin_user):
    headers = _login_headers(client, "admin", "admin123")
    items = [{"key": f"K{i}", "value": str(i)} for i in range(50)]
    r = client.put("/api/v1/auth/env-vars", json={"items": items}, headers=headers)
    assert r.status_code == 200
    long_value = "v" * 4096
    r = client.put(
        "/api/v1/auth/env-vars",
        json={"items": [{"key": "BIG", "value": long_value}]},
        headers=headers,
    )
    assert r.status_code == 200


def test_env_vars_requires_auth(client):
    r = client.get("/api/v1/auth/env-vars")
    assert r.status_code == 403
    r = client.put("/api/v1/auth/env-vars", json={"items": []})
    assert r.status_code == 403
