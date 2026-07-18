from __future__ import annotations

import io
import os
import re
import shutil
import stat
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from app.shared.config import settings


class SkillStoreError(ValueError):
    pass


_TEXT_SUFFIXES = {
    "", ".md", ".txt", ".json", ".yaml", ".yml", ".toml", ".csv",
    ".py", ".js", ".ts", ".tsx", ".jsx", ".sh", ".sql", ".xml",
    ".html", ".css", ".svg",
}
_SKILL_NAME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,99}$")


def skill_root() -> Path:
    configured = settings.super_assistant_skill_root.strip()
    root = Path(configured) if configured else Path(settings.uploads_dir) / "super-assistant" / "skills"
    return root.expanduser().resolve()


def skill_directory(owner_id: str, skill_id: str) -> Path:
    root = skill_root()
    path = (root / owner_id / skill_id).resolve()
    if not path.is_relative_to(root):
        raise SkillStoreError("Skill 存储路径无效")
    return path


def _safe_relative(value: str) -> PurePosixPath:
    clean = (value or "").replace("\\", "/").strip("/")
    path = PurePosixPath(clean)
    if not clean or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise SkillStoreError("文件路径必须是 Skill 目录内的相对路径")
    if any("\x00" in part for part in path.parts):
        raise SkillStoreError("文件路径无效")
    return path


def resolve_file(folder: str | Path, relative_path: str, *, must_exist: bool = False) -> Path:
    base = Path(folder).resolve()
    rel = _safe_relative(relative_path)
    current = base
    for part in rel.parts:
        current = current / part
        if current.exists() and current.is_symlink():
            raise SkillStoreError("Skill 目录不允许符号链接")
    resolved = current.resolve(strict=False)
    if not resolved.is_relative_to(base):
        raise SkillStoreError("文件路径越出 Skill 目录")
    if must_exist and (not resolved.exists() or not resolved.is_file()):
        raise SkillStoreError("文件不存在")
    return resolved


def parse_skill_markdown(content: str, fallback_name: str | None = None) -> dict[str, Any]:
    if not content.strip():
        raise SkillStoreError("SKILL.md 不能为空")
    metadata: dict[str, Any] = {}
    body = content
    if content.startswith("---"):
        lines = content.splitlines()
        end = next((index for index in range(1, len(lines)) if lines[index].strip() == "---"), None)
        if end is None:
            raise SkillStoreError("SKILL.md 的 YAML frontmatter 未闭合")
        try:
            loaded = yaml.safe_load("\n".join(lines[1:end])) or {}
        except yaml.YAMLError as exc:
            raise SkillStoreError(f"SKILL.md frontmatter 无法解析: {exc}") from exc
        if not isinstance(loaded, dict):
            raise SkillStoreError("SKILL.md frontmatter 必须是对象")
        metadata = loaded
        body = "\n".join(lines[end + 1:]).strip()

    name = str(metadata.get("name") or fallback_name or "").strip()
    if not _SKILL_NAME.fullmatch(name):
        raise SkillStoreError("Skill name 只能包含字母、数字、下划线和连字符")
    display_name = str(metadata.get("display_name") or metadata.get("title") or name).strip()
    description = str(metadata.get("description") or "").strip()
    raw_triggers = metadata.get("triggers") or []
    if isinstance(raw_triggers, str):
        raw_triggers = [item.strip() for item in raw_triggers.split(",") if item.strip()]
    if not isinstance(raw_triggers, list):
        raise SkillStoreError("triggers 必须是字符串数组")
    triggers = [str(item).strip() for item in raw_triggers if str(item).strip()][:100]
    return {
        "name": name,
        "display_name": display_name[:200] or name,
        "description": description[:4000],
        "triggers": triggers,
        "instructions": body,
    }


def render_skill_markdown(*, name: str, display_name: str, description: str,
                          triggers: list[str], instructions: str) -> str:
    metadata = {
        "name": name,
        "display_name": display_name,
        "description": description,
        "triggers": triggers,
    }
    frontmatter = yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False).strip()
    return f"---\n{frontmatter}\n---\n\n{instructions.strip()}\n"


def create_skill_folder(folder: Path, skill_markdown: str) -> None:
    if folder.exists():
        raise SkillStoreError("Skill 存储目录已存在")
    folder.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".skill-", dir=folder.parent))
    try:
        (stage / "SKILL.md").write_text(skill_markdown, encoding="utf-8")
        os.replace(stage, folder)
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise


def import_skill_archive(data: bytes, folder: Path) -> dict[str, Any]:
    max_bytes = settings.super_assistant_max_skill_archive_mb * 1024 * 1024
    if not data or len(data) > max_bytes:
        raise SkillStoreError(f"ZIP 包必须小于 {settings.super_assistant_max_skill_archive_mb} MB")
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise SkillStoreError("上传文件不是有效的 ZIP 包") from exc

    entries: list[tuple[zipfile.ZipInfo, PurePosixPath]] = []
    total = 0
    for info in archive.infolist():
        if info.is_dir() or info.filename.startswith("__MACOSX/"):
            continue
        mode = info.external_attr >> 16
        if stat.S_ISLNK(mode):
            raise SkillStoreError("Skill ZIP 不允许包含符号链接")
        rel = _safe_relative(info.filename)
        if info.file_size > settings.super_assistant_max_skill_file_mb * 1024 * 1024:
            raise SkillStoreError(f"文件 {rel} 超过单文件大小限制")
        total += info.file_size
        if total > max_bytes * 2:
            raise SkillStoreError("Skill ZIP 解压后体积过大")
        entries.append((info, rel))
    if not entries or len(entries) > settings.super_assistant_max_skill_files:
        raise SkillStoreError(f"Skill 文件数必须在 1 到 {settings.super_assistant_max_skill_files} 之间")

    # Accept either SKILL.md at archive root or one conventional enclosing folder.
    paths = [rel for _, rel in entries]
    prefix: str | None = None
    if PurePosixPath("SKILL.md") not in paths:
        first_parts = {path.parts[0] for path in paths if len(path.parts) > 1}
        if len(first_parts) == 1:
            candidate = next(iter(first_parts))
            stripped = [PurePosixPath(*path.parts[1:]) for path in paths]
            if PurePosixPath("SKILL.md") in stripped:
                prefix = candidate
    normalized: list[tuple[zipfile.ZipInfo, PurePosixPath]] = []
    for info, rel in entries:
        if prefix:
            if rel.parts[0] != prefix or len(rel.parts) == 1:
                continue
            rel = PurePosixPath(*rel.parts[1:])
        normalized.append((info, rel))
    if PurePosixPath("SKILL.md") not in [rel for _, rel in normalized]:
        raise SkillStoreError("Skill 目录必须包含根文件 SKILL.md")

    if folder.exists():
        raise SkillStoreError("Skill 存储目录已存在")
    folder.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".skill-import-", dir=folder.parent))
    try:
        for info, rel in normalized:
            target = resolve_file(stage, rel.as_posix())
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, target.open("wb") as destination:
                shutil.copyfileobj(source, destination)
        content = (stage / "SKILL.md").read_text(encoding="utf-8")
        metadata = parse_skill_markdown(content)
        os.replace(stage, folder)
        return metadata
    except UnicodeDecodeError as exc:
        raise SkillStoreError("SKILL.md 必须使用 UTF-8 编码") from exc
    finally:
        archive.close()
        if stage.exists():
            shutil.rmtree(stage, ignore_errors=True)


def build_manifest(folder: str | Path) -> list[dict[str, Any]]:
    base = Path(folder).resolve()
    if not base.exists():
        raise SkillStoreError("Skill 目录不存在")
    manifest: list[dict[str, Any]] = []
    for path in sorted(base.rglob("*")):
        if path.is_symlink():
            raise SkillStoreError("Skill 目录不允许符号链接")
        if not path.is_file():
            continue
        rel = path.relative_to(base).as_posix()
        manifest.append({
            "path": rel,
            "size": path.stat().st_size,
            "editable": path.suffix.lower() in _TEXT_SUFFIXES,
        })
    if not any(item["path"] == "SKILL.md" for item in manifest):
        raise SkillStoreError("Skill 目录缺少 SKILL.md")
    return manifest


def read_text_file(folder: str | Path, relative_path: str) -> str:
    path = resolve_file(folder, relative_path, must_exist=True)
    if path.suffix.lower() not in _TEXT_SUFFIXES:
        raise SkillStoreError("该文件不是可在线编辑的文本类型")
    if path.stat().st_size > settings.super_assistant_max_skill_file_mb * 1024 * 1024:
        raise SkillStoreError("文件超过在线读取限制")
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise SkillStoreError("文件不是 UTF-8 文本") from exc


def write_text_file(folder: str | Path, relative_path: str, content: str) -> None:
    path = resolve_file(folder, relative_path)
    if path.suffix.lower() not in _TEXT_SUFFIXES:
        raise SkillStoreError("该文件类型不支持在线编辑")
    encoded = content.encode("utf-8")
    if len(encoded) > settings.super_assistant_max_skill_file_mb * 1024 * 1024:
        raise SkillStoreError("文件超过在线编辑大小限制")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".edit-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(encoded)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def delete_file(folder: str | Path, relative_path: str) -> None:
    if _safe_relative(relative_path) == PurePosixPath("SKILL.md"):
        raise SkillStoreError("不能删除 Skill 的必需文件 SKILL.md")
    path = resolve_file(folder, relative_path, must_exist=True)
    path.unlink()
    base = Path(folder).resolve()
    parent = path.parent
    while parent != base and not any(parent.iterdir()):
        parent.rmdir()
        parent = parent.parent


def delete_skill_folder(folder: str | Path) -> None:
    base = Path(folder).resolve()
    root = skill_root()
    if not base.is_relative_to(root) or base == root:
        raise SkillStoreError("拒绝删除不受管控的路径")
    if base.exists():
        shutil.rmtree(base)


def export_skill_archive(folder: str | Path) -> bytes:
    base = Path(folder).resolve()
    manifest = build_manifest(base)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for item in manifest:
            path = resolve_file(base, item["path"], must_exist=True)
            archive.write(path, arcname=item["path"])
    return output.getvalue()
