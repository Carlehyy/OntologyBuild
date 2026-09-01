"""隐私变量引用解析。

接口配置的 header 值、body 内容、URL 里可写 ``{{privacy:VAR_KEY}}`` 占位符，
执行时由执行器调用 :func:`resolve_privacy_refs` 把占位符替换为当前调用者
（数据所有者本人）的隐私变量明文值。

权限模型与 :mod:`app.auth.router.get_privacy_var_value` 完全对齐：只解析
当前 JWT 用户的隐私变量，跨用户对不存在的 key 一律报错，绝不静默发空。
解析后的明文只用于出站 HTTP 请求，不写进调用历史快照（快照里仍保留占位符）。
"""
from __future__ import annotations

import re
from typing import Any

from app.auth.crypto import decrypt_value
from app.auth.models import UserPrivacyVar
from app.database import SessionLocal

# 占位符语法：{{privacy:VAR_KEY}}，VAR_KEY 与隐私变量的 key 同字符集
# （创建端校验见 auth schemas，此处只做结构约束）。
PRIVACY_REF_RE = re.compile(r"\{\{privacy:([A-Za-z0-9_.\-]+)\}\}")
_REDACTED = "***"


def _is_admin(user) -> bool:
    return getattr(user, "role", None) == "admin"


def resolve_privacy_refs(value: Any, user) -> Any:
    """把 ``value`` 中的 ``{{privacy:KEY}}`` 替换为当前用户的隐私变量明文。

    - 仅处理 str；非 str（bytes / dict / list）原样返回。
    - 一次查询当前用户全部命中的 key，避免逐 key 打开 DB 会话。
    - 缺失的 key 抛 ``ValueError``，绝不静默发空值（避免凭据缺失时仍发出站）。
    """
    if not isinstance(value, str):
        return value
    keys = set(PRIVACY_REF_RE.findall(value))
    if not keys:
        return value
    if user is None:
        # 无 actor（公开代理 / n8n 内部代理）：占位符原样返回，fail-closed。
        return value

    # admin 视角不解析隐私变量：admin 不应被接口配置里的占位符触发解密，
    # 因为 admin 看得到所有人的接口，而占位符里的 key 不一定是 admin 自己的。
    # 若 admin 调用了含占位符的他人接口，按"缺 key"处理更安全。
    # —— 但在"接口私有可见"前提下，admin 调用的就是自己的接口，占位符就是
    # admin 自己的 key，此时正常解析。
    plaintext_map = _load_plaintext(keys, user)
    missing = sorted(keys - plaintext_map.keys())
    if missing:
        raise ValueError(
            "隐私变量未配置或未上报：" + ", ".join(missing)
        )
    return PRIVACY_REF_RE.sub(
        lambda m: plaintext_map[m.group(1)], value
    )


def _load_plaintext(keys: set[str], user) -> dict[str, str]:
    """批量解密当前用户命中的隐私变量明文。"""
    out: dict[str, str] = {}
    db = SessionLocal()
    try:
        rows = (
            db.query(UserPrivacyVar)
            .filter(
                UserPrivacyVar.user_id == user.id,
                UserPrivacyVar.key.in_(keys),
            )
            .all()
        )
        for row in rows:
            if not row.value_encrypted:
                continue
            try:
                out[row.key] = decrypt_value(row.value_encrypted)
            except Exception:  # noqa: BLE001
                # 解密失败按"无值"处理，由缺失校验统一报错
                continue
    finally:
        db.close()
    return out


def redact_privacy_refs(value: Any) -> Any:
    """把 ``value`` 中的 ``{{privacy:KEY}}`` 还原为 ``***``，用于审计快照。

    快照里保留占位符会让审计日志本身泄露"这个接口用了哪个隐私变量名"，
    因此统一替换为 ``***``。
    """
    if isinstance(value, str):
        return PRIVACY_REF_RE.sub(_REDACTED, value)
    return value
