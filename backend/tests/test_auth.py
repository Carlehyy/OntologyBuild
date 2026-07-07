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
