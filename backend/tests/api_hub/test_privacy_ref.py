"""隐私变量占位符解析与注入的单元测试。

覆盖：
- resolve_privacy_refs 的替换/缺 key 报错/非 str 原样返回
- redact_privacy_refs 的审计脱敏
- executor._build_kwargs 在有 actor 时注入明文、snapshot 脱敏
- executor._build_kwargs 无 actor 时占位符原样发出站
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.api_hub import executor
from app.api_hub.executor import RequestOverrides, _build_kwargs
from app.api_hub.privacy_ref import (
    PRIVACY_REF_RE,
    redact_privacy_refs,
    resolve_privacy_refs,
)


# ---------------------------------------------------------------------------
# 纯函数：resolve_privacy_refs / redact_privacy_refs
# ---------------------------------------------------------------------------

class TestResolvePrivacyRefs:
    """resolve_privacy_refs 的替换逻辑（mock 掉 DB 查询）。"""

    def _make_user(self, uid="user-1"):
        return SimpleNamespace(id=uid, role="editor", is_active=True)

    def test_replaces_placeholder_with_plaintext(self):
        user = self._make_user()
        with patch(
            "app.api_hub.privacy_ref._load_plaintext",
            return_value={"cookie": "sid=secret123"},
        ):
            result = resolve_privacy_refs("Cookie: {{privacy:cookie}}", user)
        assert result == "Cookie: sid=secret123"

    def test_replaces_multiple_placeholders(self):
        user = self._make_user()
        with patch(
            "app.api_hub.privacy_ref._load_plaintext",
            return_value={"token": "abc", "key": "xyz"},
        ):
            result = resolve_privacy_refs(
                "{{privacy:token}} + {{privacy:key}}", user
            )
        assert result == "abc + xyz"

    def test_no_placeholder_returns_original(self):
        user = self._make_user()
        with patch(
            "app.api_hub.privacy_ref._load_plaintext",
            return_value={},
        ):
            result = resolve_privacy_refs("plain string", user)
        assert result == "plain string"

    def test_non_string_returns_original(self):
        user = self._make_user()
        # bytes / dict / None 原样返回
        assert resolve_privacy_refs(b"bytes", user) == b"bytes"
        assert resolve_privacy_refs({"x": 1}, user) == {"x": 1}
        assert resolve_privacy_refs(None, user) is None

    def test_missing_key_raises_value_error(self):
        """缺 key 绝不静默发空——fail-closed。"""
        user = self._make_user()
        with patch(
            "app.api_hub.privacy_ref._load_plaintext",
            return_value={"have": "value"},  # 缺 "missing"
        ):
            with pytest.raises(ValueError, match="隐私变量未配置"):
                resolve_privacy_refs("{{privacy:missing}}", user)

    def test_regex_does_not_match_single_brace(self):
        """单括号 {param} 是 path 参数，不是隐私引用。"""
        user = self._make_user()
        with patch(
            "app.api_hub.privacy_ref._load_plaintext",
            return_value={},
        ) as mock_load:
            result = resolve_privacy_refs("https://x/{id}/detail", user)
        assert result == "https://x/{id}/detail"
        # 不该查 DB
        mock_load.assert_not_called()

    def test_actor_none_skips_resolution(self):
        """无 actor（公开代理/n8n）时占位符原样返回。"""
        result = resolve_privacy_refs("{{privacy:cookie}}", None)
        assert result == "{{privacy:cookie}}"


class TestRedactPrivacyRefs:
    """审计快照脱敏：占位符 → ***，明文也脱敏（因为 snapshot 在解析后构造）。"""

    def test_redacts_placeholder(self):
        assert redact_privacy_refs("{{privacy:cookie}}") == "***"

    def test_redacts_multiple(self):
        assert redact_privacy_refs("{{privacy:a}}/{{privacy:b}}") == "***/***"

    def test_no_placeholder_unchanged(self):
        assert redact_privacy_refs("plain") == "plain"

    def test_non_string_unchanged(self):
        assert redact_privacy_refs(b"bytes") == b"bytes"
        assert redact_privacy_refs(None) is None

    def test_redacts_already_resolved_plaintext_with_marker(self):
        """如果明文里恰好含 {{privacy:}}（不可能但防御性测试），也会脱敏。"""
        text = "Cookie: sid={{privacy:cookie}}"
        assert "***" in redact_privacy_refs(text)


# ---------------------------------------------------------------------------
# executor._build_kwargs 集成：actor 注入 + snapshot 脱敏
# ---------------------------------------------------------------------------

class TestBuildKwargsPrivacyInjection:
    """_build_kwargs 在有/无 actor 时的行为。"""

    def _make_iface(self, **kw):
        iface = {
            "method": "GET",
            "url": "https://up.example/api",
            "query_params": [],
            "headers": [{"key": "X-Trace", "value": "test"}],
            "body_type": "none",
            "body_content": "",
        }
        iface.update(kw)
        return iface

    def test_with_actor_resolves_header_and_redacts_snapshot(self):
        user = SimpleNamespace(id="u1", role="editor", is_active=True)
        iface = self._make_iface(
            headers=[{"key": "Cookie", "value": "{{privacy:cookie}}"}],
        )
        overrides = RequestOverrides(source="ui", actor=user)
        with patch(
            "app.api_hub.privacy_ref._load_plaintext",
            return_value={"cookie": "sid=secret"},
        ):
            kwargs, snapshot = _build_kwargs(iface, overrides)
        # kwargs（发往上游）含明文
        assert kwargs["headers"]["Cookie"] == "sid=secret"
        # snapshot（审计）脱敏
        snap_headers = {h["key"]: h["value"] for h in snapshot["headers"]}
        assert snap_headers["Cookie"] == "***"

    def test_with_actor_resolves_body_and_redacts_snapshot(self):
        user = SimpleNamespace(id="u1", role="editor", is_active=True)
        iface = self._make_iface(
            body_type="raw",
            body_content='{"token": "{{privacy:tk}}"}',
        )
        overrides = RequestOverrides(source="ui", actor=user)
        with patch(
            "app.api_hub.privacy_ref._load_plaintext",
            return_value={"tk": "real-token"},
        ):
            kwargs, snapshot = _build_kwargs(iface, overrides)
        # body 明文发往上游
        assert b"real-token" in kwargs["data"]
        # snapshot 脱敏
        assert "real-token" not in str(snapshot["body_content"])
        assert "***" in str(snapshot["body_content"])

    def test_without_actor_placeholder_sent_as_is(self):
        """无 actor（公开代理路径）占位符原样发出站，snapshot 仍脱敏。"""
        iface = self._make_iface(
            headers=[{"key": "Cookie", "value": "{{privacy:cookie}}"}],
        )
        overrides = RequestOverrides(source="public_proxy")
        kwargs, snapshot = _build_kwargs(iface, overrides)
        # 占位符原样在上游 header 里（未被解析）
        assert kwargs["headers"]["Cookie"] == "{{privacy:cookie}}"
        # snapshot 里脱敏——不泄露变量名
        snap_headers = {h["key"]: h["value"] for h in snapshot["headers"]}
        assert snap_headers["Cookie"] == "***"

    def test_missing_privacy_var_raises_in_build_kwargs(self):
        """缺 key 时 _build_kwargs 抛 ValueError（被 run_interface 转成 400）。"""
        user = SimpleNamespace(id="u1", role="editor", is_active=True)
        iface = self._make_iface(
            headers=[{"key": "Cookie", "value": "{{privacy:missing}}"}],
        )
        overrides = RequestOverrides(source="ui", actor=user)
        with patch(
            "app.api_hub.privacy_ref._load_plaintext",
            return_value={},
        ):
            with pytest.raises(ValueError, match="隐私变量未配置"):
                _build_kwargs(iface, overrides)
