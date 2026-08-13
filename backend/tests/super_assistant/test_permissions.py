from __future__ import annotations

from app.shared.config import settings
from app.super_assistant.permissions import ToolPermissionChecker


def test_deny_wins_over_allow():
    checker = ToolPermissionChecker(allow_csv="*", deny_csv="web_*")
    assert checker.is_allowed("web_fetch") is False
    assert checker.is_allowed("web_search") is False
    assert checker.is_allowed("use_skill") is True
    assert checker.is_allowed("mcp__minio__list_buckets") is True


def test_allow_whitelist_blocks_unlisted_tools():
    checker = ToolPermissionChecker(allow_csv="mcp__minio__*,use_skill")
    assert checker.is_allowed("mcp__minio__list_buckets") is True
    assert checker.is_allowed("use_skill") is True
    assert checker.is_allowed("web_fetch") is False
    assert checker.is_allowed("read_skill_file") is False


def test_empty_rules_allow_everything():
    checker = ToolPermissionChecker()
    assert checker.is_allowed("web_fetch") is True
    assert checker.is_allowed("mcp__minio__delete_bucket") is True


def test_glob_matching_is_case_sensitive():
    checker = ToolPermissionChecker(allow_csv="WEB_*")
    assert checker.is_allowed("web_fetch") is False
    assert checker.is_allowed("WEB_FETCH") is True


def test_rules_are_stripped_and_blanks_skipped():
    checker = ToolPermissionChecker(allow_csv=" web_* , , mcp__minio__delete_* ")
    assert checker.allow == ["web_*", "mcp__minio__delete_*"]


def test_specific_glob_matches_mcp_namespaced_names():
    checker = ToolPermissionChecker(deny_csv="mcp__minio__delete_*")
    assert checker.is_allowed("mcp__minio__delete_bucket") is False
    assert checker.is_allowed("mcp__minio__list_buckets") is True


def test_from_settings_reads_allow_and_deny(monkeypatch):
    monkeypatch.setattr(settings, "super_assistant_tool_allow", "web_*")
    monkeypatch.setattr(settings, "super_assistant_tool_deny", "web_fetch")
    checker = ToolPermissionChecker.from_settings()
    assert checker.allow == ["web_*"]
    assert checker.deny == ["web_fetch"]
    assert checker.is_allowed("web_fetch") is False
    assert checker.is_allowed("web_search") is True
    assert checker.is_allowed("use_skill") is False
