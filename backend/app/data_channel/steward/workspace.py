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


def extracted_text(
    conversation_id: str,
    artifact_id: str,
    cap: int = 40_000,
    *,
    offset: int = 0,
) -> str:
    require_file(conversation_id, artifact_id)
    path = _within(conversation_id, f".meta/extracted/{artifact_id}.md")
    if not path.exists():
        return ""
    start = max(0, int(offset or 0))
    limit = max(1, min(cap, _TEXT_CAP))
    return path.read_text("utf-8", errors="replace")[start:start + limit]


def _context_terms(query: str | None) -> list[str]:
    raw = (query or "").lower()
    terms = re.findall(r"[a-z0-9_.-]{2,}|[\u4e00-\u9fff]{2,}", raw)
    expanded: list[str] = []
    for term in terms:
        expanded.append(term)
        if "\u4e00" <= term[0] <= "\u9fff" and len(term) > 6:
            expanded.extend(term[index:index + 2] for index in range(0, len(term) - 1, 2))
    return list(dict.fromkeys(expanded))[:20]


def _relevant_excerpt(text: str, terms: list[str], cap: int) -> str:
    if len(text) <= cap:
        return text
    if not terms:
        head = max(1, int(cap * 0.7))
        tail = max(1, cap - head)
        return (
            text[:head]
            + f"\n…（中段省略 {len(text) - cap} 字符；可用 read_session_file 点名读取）…\n"
            + text[-tail:]
        )

    lowered = text.lower()
    matches = sorted({
        index
        for term in terms
        for index in [lowered.find(term)]
        if index >= 0
    })
    if not matches:
        return _relevant_excerpt(text, [], cap)

    # Keep a small document header for orientation, then windows around the
    # earliest distinct matches. This avoids the oldest files/first characters
    # permanently consuming the entire model context.
    header_cap = min(800, cap // 4)
    parts = [text[:header_cap]]
    remaining = cap - header_cap
    for index in matches[:6]:
        if remaining <= 0:
            break
        window = min(2_000, remaining)
        start = max(0, index - window // 3)
        end = min(len(text), start + window)
        parts.append(text[start:end])
        remaining -= end - start
    return (
        "\n…（相关片段）…\n".join(parts)
        + "\n…（文件已按当前问题提取相关片段；完整文本可用 read_session_file 读取）"
    )


def context_block(
    conversation_id: str,
    total_cap: int = 50_000,
    *,
    query: str | None = None,
) -> str:
    """Build a query-aware file context with an explicit catalog and omissions."""
    total_cap = max(0, int(total_cap or 0))
    rows = list_files(conversation_id)
    if not rows or total_cap <= 0:
        return ""

    terms = _context_terms(query)
    candidates: list[tuple[int, str, dict, str]] = []
    for row in rows:
        text = extracted_text(conversation_id, row["id"], _TEXT_CAP)
        haystack = f"{row.get('filename', '')}\n{text}".lower()
        score = sum(min(8, haystack.count(term)) for term in terms)
        created_at = str(row.get("createdAt") or "")
        candidates.append((score, created_at, row, text))
    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)

    catalog_lines = [
        (
            f"- {row.get('filename')}（artifact_id={row.get('id')}，"
            f"解析 {row.get('extractedChars', 0)} 字符，来源={row.get('source') or 'unknown'}）"
        )
        for _, _, row, _ in candidates
    ]
    catalog = "# 当前会话文件目录（只能在本会话使用）\n" + "\n".join(catalog_lines)
    catalog_budget = min(total_cap, max(120, total_cap // 5))
    catalog = catalog[:catalog_budget]
    remaining = max(0, total_cap - len(catalog))

    parts: list[str] = []
    included = 0
    for _, _, row, text in candidates:
        if remaining <= 200:
            break
        url_note = "\n发现网址：" + "、".join(row.get("urls") or []) if row.get("urls") else ""
        if not text and not url_note:
            continue
        cap = min(12_000, remaining)
        excerpt = _relevant_excerpt(text, terms, max(1, cap - len(url_note)))
        body = excerpt + url_note
        parts.append(f"## 会话文件相关片段：{row['filename']}\n{body}")
        remaining -= len(body)
        included += 1

    omitted = max(0, len(candidates) - included)
    suffix = (
        f"\n\n（另有 {omitted} 个文件未展开；目录中的 artifact_id 可交给 "
        "read_session_file 按需读取。）"
        if omitted else ""
    )
    block = catalog + ("\n\n" + "\n\n".join(parts) if parts else "") + suffix
    # ``total_cap`` is a hard model-view boundary. Headers and omission notes
    # are part of the same budget, so enforce it once more on the final block.
    return block[:total_cap]


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
