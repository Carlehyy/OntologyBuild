"""OfficeCLI 的可选、受控适配器。

不接受自由 shell 命令；只开放 view/create/add/set/remove 五种结构化操作，文件
始终由当前会话的数据库记录解析。OfficeCLI 未配置时，编排器不会向模型暴露工具。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import PurePosixPath

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.config import settings
from app.exploration import workspace as W
from app.exploration.models import ExplorationAttachment, ExplorationSession

OFFICE_EXTENSIONS = {".docx", ".xlsx", ".pptx"}
MUTATIONS = {"add", "set", "remove"}


def executable() -> str | None:
    configured = (settings.exploration_officecli_path or "").strip()
    if configured:
        path = os.path.abspath(configured)
        return path if os.path.isfile(path) and os.access(path, os.X_OK) else None
    return shutil.which("officecli")


def available() -> bool:
    return executable() is not None


def _run(args: list[str]) -> str:
    exe = executable()
    if not exe:
        raise HTTPException(503, "OfficeCLI 未配置，当前只能下载或读取已抽取文本")
    env = dict(os.environ)
    env["OFFICECLI_SKIP_UPDATE"] = "1"
    try:
        result = subprocess.run([exe, *args], capture_output=True, text=True,
                                timeout=30, check=False, env=env)
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(504, "OfficeCLI 操作超时（30 秒）") from exc
    output = ((result.stdout or "") + ("\n" + result.stderr if result.stderr else "")).strip()
    if result.returncode != 0:
        raise HTTPException(422, f"OfficeCLI 操作失败: {output[:1000] or result.returncode}")
    return output[:12_000]


def _props_args(props: dict | None) -> list[str]:
    out: list[str] = []
    for index, (key, value) in enumerate((props or {}).items()):
        if index >= 30:
            break
        name = str(key).strip()
        text = str(value)
        if not name or len(name) > 100 or len(text) > 4_000:
            raise HTTPException(422, "Office 属性名称或值过长")
        out.extend(["--prop", f"{name}={text}"])
    return out


def operate(db: Session, session: ExplorationSession, operation: str,
            *, file_id: str | None = None, logical_path: str | None = None,
            selector: str = "/", element_type: str | None = None,
            props: dict | None = None, view: str = "outline") -> dict:
    op = str(operation or "").lower()
    if op == "create":
        logical = W.normalize_logical_path(logical_path or "")
        suffix = PurePosixPath(logical).suffix.lower()
        if suffix not in OFFICE_EXTENSIONS:
            raise HTTPException(422, "OfficeCLI 仅创建 docx/xlsx/pptx")
        with tempfile.TemporaryDirectory(prefix="bx-office-") as temp:
            candidate = os.path.join(temp, f"document{suffix}")
            _run(["create", candidate])
            with open(candidate, "rb") as handle:
                row = W.create_bytes(db, session, logical, handle.read(), source="agent")
        return {"created": True, "id": row.id, "path": row.relative_path,
                "version": row.version, "size": row.file_size}

    row = W.require_file(db, session.id, str(file_id or ""))
    suffix = PurePosixPath(row.relative_path or row.filename).suffix.lower()
    if suffix not in OFFICE_EXTENSIONS:
        raise HTTPException(415, "该文件不是 OfficeCLI 支持的 docx/xlsx/pptx")
    if not row.file_path:
        raise HTTPException(410, "文件物理路径不存在")
    if len(selector) > 500:
        raise HTTPException(422, "Office 元素路径过长")

    if op == "view":
        mode = view if view in {"outline", "text", "html"} else "outline"
        return {"id": row.id, "path": row.relative_path or row.filename,
                "version": row.version, "output": _run(["view", row.file_path, mode])}
    if op == "set":
        output = _run(["set", row.file_path, selector, *_props_args(props)])
    elif op == "add":
        if not element_type or len(element_type) > 80:
            raise HTTPException(422, "add 需要合法的 element_type")
        output = _run(["add", row.file_path, selector, "--type", element_type,
                       *_props_args(props)])
    elif op == "remove":
        output = _run(["remove", row.file_path, selector])
    else:
        raise HTTPException(422, f"不支持的 Office 操作: {op}")
    row = W.refresh_binary_metadata(db, row, source="agent")
    return {"updated": True, "id": row.id, "path": row.relative_path or row.filename,
            "version": row.version, "size": row.file_size, "output": output}
