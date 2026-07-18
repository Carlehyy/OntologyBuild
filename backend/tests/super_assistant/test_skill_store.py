from __future__ import annotations

import io
import stat
import zipfile

import pytest

from app.shared.config import settings
from app.super_assistant.skill_store import (
    SkillStoreError,
    build_manifest,
    import_skill_archive,
    parse_skill_markdown,
    read_text_file,
    render_skill_markdown,
    skill_directory,
    write_text_file,
)


@pytest.fixture()
def isolated_skill_root(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "super_assistant_skill_root", str(tmp_path / "skills"))
    return tmp_path / "skills"


def _archive(files: dict[str, str]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for path, content in files.items():
            archive.writestr(path, content)
    return output.getvalue()


def test_parse_and_render_standard_skill_markdown():
    content = render_skill_markdown(
        name="research_helper",
        display_name="研究助手",
        description="收集和整理资料",
        triggers=["研究", "调研"],
        instructions="# Workflow\n\nFollow the references.",
    )
    parsed = parse_skill_markdown(content)
    assert parsed["name"] == "research_helper"
    assert parsed["display_name"] == "研究助手"
    assert parsed["triggers"] == ["研究", "调研"]
    assert parsed["instructions"].startswith("# Workflow")


def test_imports_complete_folder_and_edits_nested_text_file(isolated_skill_root):
    folder = skill_directory("user-1", "skill-1")
    payload = _archive({
        "research-helper/SKILL.md": "---\nname: research_helper\ndescription: Research\n---\n\nUse references.",
        "research-helper/references/guide.md": "original",
        "research-helper/scripts/collect.py": "print('ok')",
        "research-helper/assets/icon.png": "not-a-real-png",
    })
    metadata = import_skill_archive(payload, folder)
    assert metadata["name"] == "research_helper"
    manifest = build_manifest(folder)
    assert {item["path"] for item in manifest} == {
        "SKILL.md", "references/guide.md", "scripts/collect.py", "assets/icon.png",
    }
    assert next(item for item in manifest if item["path"] == "assets/icon.png")["editable"] is False
    write_text_file(folder, "references/guide.md", "updated")
    assert read_text_file(folder, "references/guide.md") == "updated"


def test_rejects_zip_path_traversal(isolated_skill_root):
    folder = skill_directory("user-1", "skill-2")
    payload = _archive({
        "SKILL.md": "---\nname: safe_skill\n---\nbody",
        "../outside.txt": "bad",
    })
    with pytest.raises(SkillStoreError, match="相对路径"):
        import_skill_archive(payload, folder)
    assert not (isolated_skill_root.parent / "outside.txt").exists()


def test_rejects_symlink_entries(isolated_skill_root):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("SKILL.md", "---\nname: safe_skill\n---\nbody")
        link = zipfile.ZipInfo("scripts/link")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(link, "../../secret")
    with pytest.raises(SkillStoreError, match="符号链接"):
        import_skill_archive(output.getvalue(), skill_directory("user-1", "skill-3"))
