def test_login_success(client, admin_user):
    r = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123"})
    assert r.status_code == 200
    assert "access_token" in r.json()["data"]

def test_login_wrong_password(client, admin_user):
    r = client.post("/api/v1/auth/login", json={"username": "admin", "password": "wrong"})
    assert r.status_code == 401

def test_register(client):
    r = client.post("/api/v1/auth/register",
                    json={"username": "newuser", "email": "new@test.com", "password": "pass123"})
    assert r.status_code == 201
    assert r.json()["data"]["username"] == "newuser"

def test_register_duplicate(client, admin_user):
    r = client.post("/api/v1/auth/register",
                    json={"username": "admin", "email": "other@test.com", "password": "pass123"})
    assert r.status_code == 409

def test_profile_requires_auth(client):
    r = client.get("/api/v1/auth/profile")
    assert r.status_code == 403

def test_profile_with_token(client, auth_headers):
    r = client.get("/api/v1/auth/profile", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["data"]["username"] == "admin"

def test_change_password(client, auth_headers):
    r = client.put("/api/v1/auth/password",
                   json={"current_password": "admin123", "new_password": "newpass456"},
                   headers=auth_headers)
    assert r.status_code == 200

def test_change_password_wrong_current(client, auth_headers):
    r = client.put("/api/v1/auth/password",
                   json={"current_password": "wrong", "new_password": "newpass"},
                   headers=auth_headers)
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# Token 生命周期语义锁定（零行为变更）：以下测试显式锁定当前 JWT 语义，
# 防止后续改动在无感知的情况下漂移。
# ---------------------------------------------------------------------------

def test_expired_token_is_rejected(client, admin_user, monkeypatch):
    from app.auth.service import create_access_token
    from app.config import settings

    monkeypatch.setattr(settings, "access_token_expire_minutes", -1)
    token = create_access_token({"sub": admin_user.id, "role": "admin"})
    monkeypatch.setattr(settings, "access_token_expire_minutes", 1440)

    r = client.get("/api/v1/auth/profile", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401

def test_deactivated_user_token_is_rejected(client, admin_user, auth_headers, db):
    admin_user.is_active = False
    db.commit()

    r = client.get("/api/v1/auth/profile", headers=auth_headers)
    assert r.status_code == 401

def test_password_change_revokes_existing_tokens(client, auth_headers):
    # token_version 会话吊销已落地：改密后旧 token 立即 401。本测试由批次1
    # 锁定的旧语义（改密后旧 token 有效至过期）按当时注释预留的条件翻转。
    r = client.put("/api/v1/auth/password",
                   json={"current_password": "admin123", "new_password": "newpass456"},
                   headers=auth_headers)
    assert r.status_code == 200

    assert client.get("/api/v1/auth/profile", headers=auth_headers).status_code == 401

    assert client.post("/api/v1/auth/login",
                       json={"username": "admin", "password": "admin123"}).status_code == 401
    relogin = client.post("/api/v1/auth/login",
                          json={"username": "admin", "password": "newpass456"})
    assert relogin.status_code == 200
    fresh_headers = {"Authorization": f"Bearer {relogin.json()['data']['access_token']}"}
    assert client.get("/api/v1/auth/profile", headers=fresh_headers).status_code == 200

def test_legacy_token_without_ver_claim_still_accepted(client, admin_user, auth_headers):
    # 升级兼容：无 ver claim 的存量 token 按 0 处理，与列默认一致，不强制重登
    from app.auth.service import create_access_token

    token = create_access_token({"sub": admin_user.id, "role": "admin"})
    r = client.get("/api/v1/auth/profile", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200

def test_register_closed_requires_admin(client, admin_user, auth_headers, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "allow_public_registration", False)
    body = {"username": "closeduser", "email": "closed@test.com", "password": "pass123"}

    assert client.post("/api/v1/auth/register", json=body).status_code == 403

    created = client.post("/api/v1/users", headers=auth_headers, json={
        "username": "plainuser",
        "email": "plain@test.com",
        "password": "plain123",
        "role": "viewer",
    })
    assert created.status_code == 201
    login = client.post("/api/v1/auth/login",
                        json={"username": "plainuser", "password": "plain123"})
    viewer_headers = {"Authorization": f"Bearer {login.json()['data']['access_token']}"}
    assert client.post("/api/v1/auth/register", json=body, headers=viewer_headers).status_code == 403

    assert client.post("/api/v1/auth/register", json=body, headers=auth_headers).status_code == 201
