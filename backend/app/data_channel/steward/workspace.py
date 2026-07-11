"""Conversation-scoped filesystem for the data steward.

Every path is derived from a conversation UUID and resolved below one configured
root.  Browser downloads, uploaded documents, extracted text and capture logs
therefore share one isolation boundary.  Callers never receive an absolute path.
"""
from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import shutil
import tempfile
import threading
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO, Iterable

from app.config import settings
from app.services.document_service import convert_document


_URL_RE = re.compile(r"https?://[^\s<>'\"\]\[(){}]+", re.IGNORECASE)
_SAFE_NAME_RE = re.compile(r"[^\w.()\-\u4e00-\u9fff]+", re.UNICODE)
_TEXT_CAP = 200_000
_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


class WorkspaceError(ValueError):
    pass


def _session_lock(conversation_id: str) -> threading.RLock:
    cid = _validate_conversation_id(conversation_id)
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(cid, threading.RLock())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _root() -> Path:
    configured = (getattr(settings, "steward_workspace_root", "") or "").strip()
    base = Path(configured) if configured else Path(settings.uploads_dir) / "steward-sessions"
    return base.expanduser().resolve()


def _validate_conversation_id(conversation_id: str) -> str:
    try:
        return str(uuid.UUID(str(conversation_id)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise WorkspaceError("会话 id 不合法") from exc


def session_root(conversation_id: str, *, create: bool = True) -> Path:
    cid = _validate_conversation_id(conversation_id)
    root = _root()
    path = (root / cid).resolve()
    if path.parent != root:
        raise WorkspaceError("会话目录越界")
    if create:
        (path / "files").mkdir(parents=True, exist_ok=True)
        (path / ".meta" / "extracted").mkdir(parents=True, exist_ok=True)
        (path / ".browser").mkdir(parents=True, exist_ok=True)
    return path


def _within(conversation_id: str, relative: str, *, create_parent: bool = False) -> Path:
    base = session_root(conversation_id)
    target = (base / relative).resolve()
    if target != base and base not in target.parents:
        raise WorkspaceError("文件路径越过当前会话边界")
    if create_parent:
        target.parent.mkdir(parents=True, exist_ok=True)
    return target


def safe_filename(name: str | None, fallback: str = "file") -> str:
    raw = Path((name or "").replace("\\", "/")).name.strip()
    cleaned = _SAFE_NAME_RE.sub("_", raw).strip("._ ")
    if not cleaned:
        cleaned = fallback
    return cleaned[:180]


def _manifest_path(conversation_id: str) -> Path:
    return _within(conversation_id, ".meta/manifest.json", create_parent=True)


def _load_manifest(conversation_id: str) -> list[dict]:
    path = _manifest_path(conversation_id)
    if not path.exists():
        return []
    try:
        value = json.loads(path.read_text("utf-8"))
        return value if isinstance(value, list) else []
    except (OSError, ValueError):
        return []


def _write_manifest(conversation_id: str, rows: list[dict]) -> None:
    path = _manifest_path(conversation_id)
    fd, tmp_name = tempfile.mkstemp(prefix="manifest-", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(rows, fh, ensure_ascii=False, indent=2)
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def _unique_relative(conversation_id: str, filename: str) -> tuple[str, Path]:
    name = safe_filename(filename)
    stem, suffix = Path(name).stem, Path(name).suffix
    candidate = name
    index = 1
    while _within(conversation_id, f"files/{candidate}").exists():
        candidate = f"{stem}_{index}{suffix}"
        index += 1
    relative = f"files/{candidate}"
    return relative, _within(conversation_id, relative, create_parent=True)


def _urls(text: str) -> list[str]:
    out: list[str] = []
    for match in _URL_RE.findall(text or ""):
        url = match.rstrip(".,;:!?，。；：！？")
        if url not in out:
            out.append(url)
        if len(out) >= 50:
            break
    return out


def _register_existing_file(
    conversation_id: str, relative: str, path: Path, *, size: int, digest: str,
    source: str, mime_type: str | None, source_url: str | None, extract: bool,
) -> dict:
    artifact_id = str(uuid.uuid4())
    mime = mime_type or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    extracted = ""
    error = None
    if extract:
        converted = convert_document(str(path), mime)
        if converted.ok:
            extracted = (converted.content or "")[:_TEXT_CAP]
            _within(
                conversation_id, f".meta/extracted/{artifact_id}.md", create_parent=True
            ).write_text(extracted, encoding="utf-8")
        elif path.suffix.lower() in {".md", ".txt", ".csv", ".pdf", ".docx", ".pptx", ".xlsx", ".xls"}:
            error = converted.error or "文件解析失败"
    row = {
        "id": artifact_id,
        "filename": path.name,
        "relativePath": relative,
        "source": source,
        "sourceUrl": source_url,
        "mimeType": mime,
        "size": size,
        "sha256": digest,
        "extractedChars": len(extracted),
        "extractError": error,
        "urls": _urls(extracted),
        "createdAt": _now(),
    }
    rows = _load_manifest(conversation_id)
    rows.append(row)
    _write_manifest(conversation_id, rows)
    return row


def save_bytes(
    conversation_id: str,
    filename: str,
    content: bytes,
    *,
    source: str,
    mime_type: str | None = None,
    source_url: str | None = None,
    extract: bool = True,
) -> dict:
    with _session_lock(conversation_id):
        max_bytes = int(settings.max_upload_mb) * 1024 * 1024
        if len(content) > max_bytes:
            raise WorkspaceError(f"文件超过大小限制 {settings.max_upload_mb}MB")
        relative, path = _unique_relative(conversation_id, filename)
        path.write_bytes(content)
        return _register_existing_file(
            conversation_id, relative, path, size=len(content),
            digest=hashlib.sha256(content).hexdigest(), source=source,
            mime_type=mime_type, source_url=source_url, extract=extract,
        )


def save_stream(
    conversation_id: str,
    filename: str,
    stream: BinaryIO | Iterable[bytes],
    *,
    source: str,
    mime_type: str | None = None,
    source_url: str | None = None,
    extract: bool = True,
) -> dict:
    with _session_lock(conversation_id):
        relative, path = _unique_relative(conversation_id, filename)
        total = 0
        digest = hashlib.sha256()
        max_bytes = int(settings.max_upload_mb) * 1024 * 1024
        iterator = stream if not hasattr(stream, "read") else iter(lambda: stream.read(1024 * 1024), b"")
        try:
            with path.open("wb") as output:
                for chunk in iterator:
                    total += len(chunk)
                    if total > max_bytes:
                        raise WorkspaceError(f"文件超过大小限制 {settings.max_upload_mb}MB")
                    output.write(chunk)
                    digest.update(chunk)
        except Exception:
            path.unlink(missing_ok=True)
            raise
        return _register_existing_file(
            conversation_id, relative, path, size=total, digest=digest.hexdigest(),
            source=source, mime_type=mime_type, source_url=source_url, extract=extract,
        )


def list_files(conversation_id: str) -> list[dict]:
    with _session_lock(conversation_id):
        base = session_root(conversation_id)
        rows = []
        changed = False
        for row in _load_manifest(conversation_id):
            try:
                path = _within(conversation_id, row.get("relativePath") or "")
            except WorkspaceError:
                changed = True
                continue
            if path.is_file() and base in path.parents:
                rows.append(row)
            else:
                changed = True
        if changed:
            _write_manifest(conversation_id, rows)
        return rows


def require_file(conversation_id: str, artifact_id: str) -> tuple[dict, Path]:
    row = next((item for item in list_files(conversation_id) if item.get("id") == artifact_id), None)
    if not row:
        raise WorkspaceError("会话文件不存在")
    return row, _within(conversation_id, row["relativePath"])


def delete_file(conversation_id: str, artifact_id: str) -> None:
    with _session_lock(conversation_id):
        rows = _load_manifest(conversation_id)
        row = next((item for item in rows if item.get("id") == artifact_id), None)
        if not row:
            raise WorkspaceError("会话文件不存在")
        path = _within(conversation_id, row["relativePath"])
        if path.exists():
            path.unlink()
        extracted = _within(conversation_id, f".meta/extracted/{artifact_id}.md")
        if extracted.exists():
            extracted.unlink()
        _write_manifest(conversation_id, [item for item in rows if item.get("id") != artifact_id])


def extracted_text(conversation_id: str, artifact_id: str, cap: int = 40_000) -> str:
    require_file(conversation_id, artifact_id)
    path = _within(conversation_id, f".meta/extracted/{artifact_id}.md")
    if not path.exists():
        return ""
    return path.read_text("utf-8", errors="replace")[: max(1, min(cap, _TEXT_CAP))]


def context_block(conversation_id: str, total_cap: int = 50_000) -> str:
    parts: list[str] = []
    remaining = total_cap
    for row in list_files(conversation_id):
        if remaining <= 0:
            break
        text = extracted_text(conversation_id, row["id"], min(12_000, remaining))
        url_note = "\n发现网址：" + "、".join(row.get("urls") or []) if row.get("urls") else ""
        if text or url_note:
            body = text + url_note
            parts.append(f"## 会话文件：{row['filename']}\n{body}")
            remaining -= len(body)
    if not parts:
        return ""
    return "# 当前会话文件（只能在本会话使用）\n" + "\n\n".join(parts)


def archive_path(conversation_id: str) -> Path:
    """Build a ZIP containing user-visible files and a redacted manifest.

    Browser cookies/storage state and raw capture headers live below hidden
    directories and are deliberately excluded from the export.
    """
    base = session_root(conversation_id)
    target = _within(conversation_id, ".meta/session-files.zip", create_parent=True)
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        manifest = []
        for row in list_files(conversation_id):
            path = _within(conversation_id, row["relativePath"])
            zf.write(path, arcname=f"files/{path.name}")
            manifest.append({k: v for k, v in row.items() if k != "relativePath"})
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    if base not in target.parents:
        raise WorkspaceError("打包路径越界")
    return target


def remove_session(conversation_id: str) -> None:
    cid = _validate_conversation_id(conversation_id)
    lock = _session_lock(cid)
    with lock:
        path = session_root(cid, create=False)
        if path.exists():
            shutil.rmtree(path)
    with _LOCKS_GUARD:
        _LOCKS.pop(cid, None)


def captures_path(conversation_id: str) -> Path:
    return _within(conversation_id, ".browser/captures.jsonl", create_parent=True)


def append_capture(conversation_id: str, capture: dict) -> None:
    with _session_lock(conversation_id):
        path = captures_path(conversation_id)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(capture, ensure_ascii=False, default=str) + "\n")


def load_captures(conversation_id: str, limit: int = 300) -> list[dict]:
    with _session_lock(conversation_id):
        path = captures_path(conversation_id)
        if not path.exists():
            return []
        rows: list[dict] = []
        for line in path.read_text("utf-8", errors="replace").splitlines()[-max(1, limit):]:
            try:
                value = json.loads(line)
                if isinstance(value, dict):
                    rows.append(value)
            except ValueError:
                continue
        return rows


def require_capture(conversation_id: str, capture_id: str) -> dict:
    capture = next((c for c in reversed(load_captures(conversation_id)) if c.get("id") == capture_id), None)
    if not capture:
        raise WorkspaceError("未找到该网络请求；请先在会话浏览器中打开相关页面")
    return capture


def storage_state_path(conversation_id: str) -> Path:
    return _within(conversation_id, ".browser/storage-state.json", create_parent=True)
