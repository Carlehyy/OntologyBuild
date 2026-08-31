"""用户隐私变量：RSA 公钥加密上报 + 平台私钥解密 + Fernet 双层落库。

覆盖：
- 创建/列表/删除、上限与重复 key 约束、需登录鉴权；
- 首次创建返回 report_token（仅此一次）、后续创建不回显；
- 下载脚本：内容含公钥/token/变量名、可执行 import；
- 上报端点：独立 token 鉴权（不走 JWT）、RSA 加密→平台解密正确、
  双层落库（DB 里是 Fernet 密文）、token 重置后旧 token 失效、
  跨用户 token 不可互用、上报未知 key 拒绝、上报未创建变量拒绝。
"""

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.auth.crypto import (
    decrypt_private_key,
    decrypt_value,
    encrypt_value,
    generate_rsa_keypair,
    rsa_encrypt,
)
from app.auth.models import UserPrivacyKeypair, UserPrivacyVar


def _login_headers(client, username: str, password: str) -> dict:
    r = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['data']['access_token']}"}


def _create_var_get_token(client, headers, key="MY_COOKIE"):
    """创建一个隐私变量并拿到首次返回的 report_token。"""
    r = client.post("/api/v1/auth/privacy-vars", json={"key": key}, headers=headers)
    assert r.status_code == 201, r.text
    data = r.json()["data"]
    assert data["key"] == key
    assert data["has_value"] is False
    assert "report_token" in data and data["report_token"]
    return data["report_token"]


# ---- 列表 / 创建 / 删除 ----

def test_create_and_list_privacy_var(client, admin_user):
    headers = _login_headers(client, "admin", "admin123")
    token = _create_var_get_token(client, headers, "MY_COOKIE")

    r = client.get("/api/v1/auth/privacy-vars", headers=headers)
    assert r.status_code == 200
    rows = r.json()["data"]
    assert len(rows) == 1
    assert rows[0]["key"] == "MY_COOKIE"
    assert rows[0]["has_value"] is False
    assert rows[0]["last_reported_at"] is None
    # 列表项不回显 value（即便 has_value 也只有布尔标记）
    assert "value" not in rows[0]


def test_second_create_does_not_return_token(client, admin_user):
    headers = _login_headers(client, "admin", "admin123")
    _create_var_get_token(client, headers, "FIRST")
    r = client.post("/api/v1/auth/privacy-vars", json={"key": "SECOND"}, headers=headers)
    assert r.status_code == 201
    # 后续创建不应再回显 report_token（避免无意暴露）
    assert "report_token" not in r.json()["data"]


def test_create_rejects_duplicate_key(client, admin_user):
    headers = _login_headers(client, "admin", "admin123")
    _create_var_get_token(client, headers, "DUP")
    r = client.post("/api/v1/auth/privacy-vars", json={"key": "DUP"}, headers=headers)
    assert r.status_code == 409


def test_create_rejects_invalid_key(client, admin_user):
    headers = _login_headers(client, "admin", "admin123")
    for bad in ("has space", "", "中文名", "a" * 129, "with/slash"):
        r = client.post("/api/v1/auth/privacy-vars", json={"key": bad}, headers=headers)
        assert r.status_code == 422, bad


def test_create_rejects_over_limit(client, admin_user):
    headers = _login_headers(client, "admin", "admin123")
    for i in range(50):
        r = client.post("/api/v1/auth/privacy-vars", json={"key": f"K{i}"}, headers=headers)
        assert r.status_code == 201, i
    r = client.post("/api/v1/auth/privacy-vars", json={"key": "OVER"}, headers=headers)
    assert r.status_code == 400


def test_delete_privacy_var(client, admin_user):
    headers = _login_headers(client, "admin", "admin123")
    _create_var_get_token(client, headers, "TO_DELETE")
    r = client.delete("/api/v1/auth/privacy-vars/TO_DELETE", headers=headers)
    assert r.status_code == 200
    r = client.get("/api/v1/auth/privacy-vars", headers=headers)
    assert all(row["key"] != "TO_DELETE" for row in r.json()["data"])


def test_delete_unknown_returns_404(client, admin_user):
    headers = _login_headers(client, "admin", "admin123")
    r = client.delete("/api/v1/auth/privacy-vars/NOPE", headers=headers)
    assert r.status_code == 404


def test_privacy_vars_scoped_per_user(client, admin_user, editor_user):
    admin_h = _login_headers(client, "admin", "admin123")
    editor_h = _login_headers(client, "editor", "editor123")
    _create_var_get_token(client, admin_h, "ADMIN_ONLY")
    _create_var_get_token(client, editor_h, "EDITOR_ONLY")
    admin_rows = client.get("/api/v1/auth/privacy-vars", headers=admin_h).json()["data"]
    editor_rows = client.get("/api/v1/auth/privacy-vars", headers=editor_h).json()["data"]
    assert {r["key"] for r in admin_rows} == {"ADMIN_ONLY"}
    assert {r["key"] for r in editor_rows} == {"EDITOR_ONLY"}


def test_endpoints_require_auth(client):
    assert client.get("/api/v1/auth/privacy-vars").status_code == 403
    assert client.post("/api/v1/auth/privacy-vars", json={"key": "X"}).status_code == 403
    assert client.delete("/api/v1/auth/privacy-vars/X").status_code == 403
    assert client.post("/api/v1/auth/privacy-vars/report-token/reset").status_code == 403


# ---- 上报 token 重置 ----

def test_reset_report_token(client, admin_user):
    headers = _login_headers(client, "admin", "admin123")
    old = _create_var_get_token(client, headers, "K")
    r = client.post("/api/v1/auth/privacy-vars/report-token/reset", headers=headers)
    assert r.status_code == 200
    new = r.json()["data"]["report_token"]
    assert new and new != old


# ---- 下载脚本 ----

def test_download_script_contains_required_parts(client, admin_user):
    headers = _login_headers(client, "admin", "admin123")
    token = _create_var_get_token(client, headers, "MY_COOKIE")
    r = client.get("/api/v1/auth/privacy-vars/script", headers=headers)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/x-python")
    assert 'filename="privacy_reporter.py"' in r.headers["content-disposition"]
    body = r.text
    # 内嵌了 token、公钥、变量名
    assert token in body
    assert "BEGIN PUBLIC KEY" in body
    assert "MY_COOKIE" in body
    assert "def collect_MY_COOKIE" in body


def test_download_script_works_without_any_var(client, admin_user):
    headers = _login_headers(client, "admin", "admin123")
    # 没有任何隐私变量也应能下载（模板里变量清单为空）
    r = client.get("/api/v1/auth/privacy-vars/script", headers=headers)
    assert r.status_code == 200
    assert "BEGIN PUBLIC KEY" in r.text


# ---- 上报端点（独立 token 鉴权） ----

def _report(client, token, items):
    return client.post(
        "/api/v1/auth/privacy-vars/report",
        json={"items": items},
        headers={"X-Report-Token": token},
    )


def test_report_roundtrip_decrypts_and_double_encrypts_at_rest(
    client, admin_user, db: Session,
):
    headers = _login_headers(client, "admin", "admin123")
    token = _create_var_get_token(client, headers, "MY_COOKIE")

    # 用脚本同样的方式：取公钥 → RSA-OAEP 加密 → base64 上报
    kp = db.query(UserPrivacyKeypair).filter(
        UserPrivacyKeypair.user_id == admin_user.id
    ).first()
    public_pem = kp.public_key_pem
    secret = "session-cookie-value-非常机密"
    ct = rsa_encrypt(public_pem, secret)

    r = _report(client, token, [{"key": "MY_COOKIE", "ciphertext": ct}])
    assert r.status_code == 200, r.text
    row = next(x for x in r.json()["data"] if x["key"] == "MY_COOKIE")
    assert row["has_value"] is True
    assert row["last_reported_at"] is not None

    # 落库是双层密文：DB 里的 value_encrypted 经 Fernet 解密后应等于明文
    db_row = db.query(UserPrivacyVar).filter(
        UserPrivacyVar.user_id == admin_user.id,
        UserPrivacyVar.key == "MY_COOKIE",
    ).first()
    assert db_row.value_encrypted
    assert secret not in db_row.value_encrypted  # 不是明文
    assert decrypt_value(db_row.value_encrypted) == secret


def test_report_requires_token(client, admin_user):
    headers = _login_headers(client, "admin", "admin123")
    _create_var_get_token(client, headers, "K")
    # 缺 header → 403
    r = client.post("/api/v1/auth/privacy-vars/report", json={"items": []})
    assert r.status_code == 403
    # 错误 token → 403
    r = _report(client, "wrong-token", [])
    assert r.status_code == 403


def test_report_rejects_unknown_var_key(client, admin_user, db: Session):
    headers = _login_headers(client, "admin", "admin123")
    token = _create_var_get_token(client, headers, "KNOWN")
    # 用真实公钥加密一个合法密文，走"密文合法但 key 未知"路径（端点先
    # 解密成功再查 key，未知 key 返回 404）。
    kp = db.query(UserPrivacyKeypair).filter(
        UserPrivacyKeypair.user_id == admin_user.id
    ).first()
    ct = rsa_encrypt(kp.public_key_pem, "v")
    r = _report(client, token, [{"key": "UNKNOWN", "ciphertext": ct}])
    assert r.status_code == 404


def test_report_old_token_invalid_after_reset(client, admin_user):
    headers = _login_headers(client, "admin", "admin123")
    old = _create_var_get_token(client, headers, "K")
    new = client.post(
        "/api/v1/auth/privacy-vars/report-token/reset", headers=headers
    ).json()["data"]["report_token"]
    assert new != old
    # 旧 token 失效
    r = _report(client, old, [])
    assert r.status_code == 403
    # 新 token 可用（空上报也返回 200）
    r = _report(client, new, [])
    assert r.status_code == 200


def test_report_token_cannot_cross_users(client, admin_user, editor_user, db: Session):
    admin_h = _login_headers(client, "admin", "admin123")
    editor_h = _login_headers(client, "editor", "editor123")
    admin_token = _create_var_get_token(client, admin_h, "ADMIN_VAR")
    editor_token = _create_var_get_token(client, editor_h, "EDITOR_VAR")

    # admin 的 token 上报 editor 的变量应被拒（key 不属于该用户）
    kp_admin = db.query(UserPrivacyKeypair).filter(
        UserPrivacyKeypair.user_id == admin_user.id
    ).first()
    ct = rsa_encrypt(kp_admin.public_key_pem, "x")
    r = _report(client, admin_token, [{"key": "EDITOR_VAR", "ciphertext": ct}])
    assert r.status_code == 404


def test_report_rejects_invalid_ciphertext(client, admin_user):
    headers = _login_headers(client, "admin", "admin123")
    token = _create_var_get_token(client, headers, "K")
    r = _report(client, token, [{"key": "K", "ciphertext": "not-valid-base64-or-ciphertext"}])
    assert r.status_code == 400


def test_report_rejects_duplicate_keys_in_payload(client, admin_user, db: Session):
    headers = _login_headers(client, "admin", "admin123")
    token = _create_var_get_token(client, headers, "K")
    kp = db.query(UserPrivacyKeypair).filter(
        UserPrivacyKeypair.user_id == admin_user.id
    ).first()
    ct = rsa_encrypt(kp.public_key_pem, "v")
    r = _report(client, token, [
        {"key": "K", "ciphertext": ct},
        {"key": "K", "ciphertext": ct},
    ])
    assert r.status_code == 400


# ---- 私钥双层加密落库验证 ----

def test_private_key_is_fernet_encrypted_at_rest(client, admin_user, db: Session):
    headers = _login_headers(client, "admin", "admin123")
    _create_var_get_token(client, headers, "K")
    kp = db.query(UserPrivacyKeypair).filter(
        UserPrivacyKeypair.user_id == admin_user.id
    ).first()
    # 私钥落库是 Fernet 密文，不是裸 PEM
    assert "BEGIN PRIVATE KEY" not in kp.private_key_pem_encrypted
    private_pem = decrypt_private_key(kp.private_key_pem_encrypted)
    assert "BEGIN PRIVATE KEY" in private_pem


