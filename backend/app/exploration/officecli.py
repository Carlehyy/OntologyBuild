"""OfficeCLI 的可选、受控适配器。

业务探索只向模型开放结构化白名单，不接受自由 shell 命令或宿主机路径。
读取命令始终针对临时副本，隔离 OfficeCLI 对 OOXML 的规范化写回；修改命令
在同文件系统的工作副本上完成，校验不退化后才原子替换原文件并递增版本。
OfficeCLI 未配置时，编排器不会向模型暴露本工具。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import PurePosixPath
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.config import settings
from app.exploration import workspace as W
from app.exploration.models import ExplorationAttachment, ExplorationSession

OFFICE_EXTENSIONS = {".docx", ".xlsx", ".pptx"}
READ_OPERATIONS = {"view", "get", "query", "validate"}
MUTATIONS = {"add", "set", "replace", "remove", "batch"}
VIEW_MODES = {"text", "outline", "annotated", "stats", "issues"}
EDIT_OPERATIONS = {"add", "set", "replace", "remove"}

MAX_CLI_OUTPUT_CHARS = 200_000
MAX_BATCH_EDITS = 20
MAX_VIEW_LINES = 500
MAX_GET_DEPTH = 4

# OfficeCLI 支持从本地路径、data URI 或 HTTP(S) 拉取素材。业务探索中的模型
# 不应获得宿主机/跨会话文件读取能力，因此素材类属性先全部关闭；后续如需图片，
# 应新增 resource_file_id，由服务端解析为当前会话内的受控副本。
_RESOURCE_PROP_PARTS = {
    "src", "source", "file", "path", "csv", "data", "image", "picture",
    "media", "video", "audio", "model", "model3d", "ole", "template", "from",
    "url", "uri", "href",
}


def executable() -> str | None:
    configured = (settings.exploration_officecli_path or "").strip()
    if configured:
        path = os.path.abspath(configured)
        return path if os.path.isfile(path) and os.access(path, os.X_OK) else None
    return shutil.which("officecli")


def available() -> bool:
    return executable() is not None


def _invoke(args: list[str]) -> tuple[str, str, int]:
    exe = executable()
    if not exe:
        raise HTTPException(503, "OfficeCLI 未配置，当前只能下载或读取已抽取文本")
    env = dict(os.environ)
    env["OFFICECLI_SKIP_UPDATE"] = "1"
    try:
        result = subprocess.run(
            [exe, *args], capture_output=True, text=True,
            timeout=30, check=False, env=env,
        )
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(504, "OfficeCLI 操作超时（30 秒）") from exc

    stdout = (result.stdout or "").strip()
    stderr = (result.stderr or "").strip()
    if len(stdout) > MAX_CLI_OUTPUT_CHARS:
        raise HTTPException(
            413,
            "OfficeCLI 返回内容过大，请缩小读取范围（start/end/max_lines、selector 或 find）",
        )
    return stdout, stderr, result.returncode


def _payload_error(payload: Any, stderr: str, returncode: int) -> str:
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict):
        message = str(error.get("error") or error.get("message") or "").strip()
        suggestion = str(error.get("suggestion") or "").strip()
        if suggestion:
            message = f"{message}；建议：{suggestion}" if message else suggestion
        if message:
            return message
    if error:
        return str(error)
    return stderr[:1000] or f"退出码 {returncode}"


def _run_text(args: list[str]) -> str:
    stdout, stderr, returncode = _invoke(args)
    if returncode != 0:
        combined = "\n".join(part for part in (stdout, stderr) if part)
        raise HTTPException(422, f"OfficeCLI 操作失败: {combined[:1000] or returncode}")
    return "\n".join(part for part in (stdout, stderr) if part)


def _run_json(args: list[str], *, allow_failure: bool = False) -> dict:
    stdout, stderr, returncode = _invoke(args)
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        combined = "\n".join(part for part in (stdout, stderr) if part)
        raise HTTPException(502, f"OfficeCLI 返回了无效 JSON: {combined[:1000]}") from exc
    if not isinstance(payload, dict):
        raise HTTPException(502, "OfficeCLI 返回格式异常：应为 JSON 对象")

    failed = returncode != 0 or payload.get("success") is False
    if failed and not allow_failure:
        raise HTTPException(422, f"OfficeCLI 操作失败: {_payload_error(payload, stderr, returncode)}")
    if stderr:
        payload["warnings"] = stderr[:4000]
    payload["returnCode"] = returncode
    return payload


def _selector(value: str | None) -> str:
    selector = str(value or "/").strip()
    if not selector or len(selector) > 500 or "\x00" in selector:
        raise HTTPException(422, "Office 元素路径为空或过长")
    return selector


def _bounded_int(value: int | None, *, name: str, minimum: int,
                 maximum: int) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(422, f"{name} 必须是整数") from exc
    if parsed < minimum or parsed > maximum:
        raise HTTPException(422, f"{name} 必须在 {minimum} 到 {maximum} 之间")
    return parsed


def _props_args(props: dict | None) -> list[str]:
    if props is not None and not isinstance(props, dict):
        raise HTTPException(422, "props 必须是对象")
    items = list((props or {}).items())
    if len(items) > 30:
        raise HTTPException(422, "单次 Office 操作最多允许 30 个属性")

    out: list[str] = []
    for key, value in items:
        name = str(key).strip()
        text = str(value)
        if not name or len(name) > 100 or len(text) > 4_000 or "\x00" in text:
            raise HTTPException(422, "Office 属性名称或值为空、过长或包含非法字符")
        # 同时识别 source_file、image-url、imageUrl 和 image.source 等写法。
        spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name)
        normalized_parts = set(re.findall(r"[a-z0-9]+", spaced.lower()))
        if normalized_parts & _RESOURCE_PROP_PARTS:
            raise HTTPException(
                422,
                f"属性「{name}」可能读取外部资源，业务探索暂不允许；请使用会话文件能力",
            )
        out.extend(["--prop", f"{name}={text}"])
    return out


def _require_office_file(db: Session, session_id: str,
                         file_ref: str, *, lock: bool = False) -> ExplorationAttachment:
    resolved = W.require_file(db, session_id, file_ref)
    if lock:
        resolved = (db.query(ExplorationAttachment)
                    .filter(ExplorationAttachment.id == resolved.id,
                            ExplorationAttachment.session_id == session_id)
                    .with_for_update().first())
        if not resolved:
            raise HTTPException(404, "会话文件不存在")
    suffix = PurePosixPath(resolved.relative_path or resolved.filename).suffix.lower()
    if suffix not in OFFICE_EXTENSIONS:
        raise HTTPException(415, "该文件不是 OfficeCLI 支持的 docx/xlsx/pptx")
    if not resolved.file_path or not os.path.isfile(resolved.file_path):
        raise HTTPException(410, "文件物理内容已丢失")
    return resolved


def _result(row: ExplorationAttachment, operation: str, payload: dict) -> dict:
    result = {
        "operation": operation,
        "id": row.id,
        "path": row.relative_path or row.filename,
        "version": row.version or 1,
        "result": payload.get("data"),
    }
    if payload.get("warnings"):
        result["warnings"] = payload["warnings"]
    return result


def _read(row: ExplorationAttachment, operation: str, *, selector: str,
          view: str, depth: int | None, find: str | None,
          start: int | None, end: int | None, max_lines: int | None,
          columns: str | None, cell_range: str | None) -> dict:
    suffix = PurePosixPath(row.relative_path or row.filename).suffix.lower()
    with tempfile.TemporaryDirectory(prefix="bx-office-read-") as temp:
        candidate = os.path.join(temp, f"document{suffix}")
        shutil.copy2(row.file_path, candidate)

        if operation == "view":
            mode = str(view or "outline").lower()
            if mode not in VIEW_MODES:
                raise HTTPException(422, f"不支持的 Office 查看模式: {mode}")
            args = ["view", candidate, mode]
            start = _bounded_int(start, name="start", minimum=1, maximum=1_000_000)
            end = _bounded_int(end, name="end", minimum=1, maximum=1_000_000)
            if start is not None and end is not None and end < start:
                raise HTTPException(422, "end 不能小于 start")
            default_lines = 120 if mode in {"text", "annotated"} else None
            max_lines = _bounded_int(
                max_lines if max_lines is not None else default_lines,
                name="max_lines", minimum=1, maximum=MAX_VIEW_LINES,
            )
            if start is not None:
                args.extend(["--start", str(start)])
            if end is not None:
                args.extend(["--end", str(end)])
            if max_lines is not None:
                args.extend(["--max-lines", str(max_lines)])
            if columns:
                columns = str(columns).strip()
                if len(columns) > 200:
                    raise HTTPException(422, "columns 过长")
                args.extend(["--cols", columns])
            if cell_range:
                cell_range = str(cell_range).strip()
                if len(cell_range) > 200:
                    raise HTTPException(422, "cell_range 过长")
                args.extend(["--range", cell_range])
            payload = _run_json([*args, "--json"])
        elif operation == "get":
            resolved_depth = _bounded_int(
                depth if depth is not None else 1,
                name="depth", minimum=0, maximum=MAX_GET_DEPTH,
            )
            payload = _run_json([
                "get", candidate, selector, "--depth", str(resolved_depth), "--json",
            ])
        elif operation == "query":
            args = ["query", candidate, selector]
            if find is not None:
                find = str(find)
                if not find or len(find) > 4_000:
                    raise HTTPException(422, "find 为空或过长")
                args.extend(["--find", find])
            payload = _run_json([*args, "--json"])
        else:  # validate
            payload = _run_json(["validate", candidate, "--json"], allow_failure=True)
    return _result(row, operation, payload)


def _edit_args(edit: dict, candidate: str) -> list[str]:
    if not isinstance(edit, dict):
        raise HTTPException(422, "批量编辑项必须是对象")
    operation = str(edit.get("operation") or "").lower()
    if operation not in EDIT_OPERATIONS:
        raise HTTPException(422, f"不支持的 Office 编辑操作: {operation}")
    selector = _selector(edit.get("selector"))
    props = edit.get("props") or {}
    prop_args = _props_args(props)

    if operation == "set":
        if not prop_args:
            raise HTTPException(422, "set 至少需要一个 props 属性")
        return ["set", candidate, selector, *prop_args, "--json"]
    if operation == "replace":
        find = str(edit.get("find") or "")
        replacement = edit.get("replacement")
        if not find or len(find) > 4_000 or replacement is None or len(str(replacement)) > 4_000:
            raise HTTPException(422, "replace 需要合法的 find 和 replacement")
        return [
            "set", candidate, selector,
            "--find", find, "--replace", str(replacement),
            *prop_args, "--json",
        ]
    if operation == "add":
        element_type = str(edit.get("element_type") or "").strip()
        if not element_type or len(element_type) > 80:
            raise HTTPException(422, "add 需要合法的 element_type")
        return [
            "add", candidate, selector, "--type", element_type,
            *prop_args, "--json",
        ]
    if selector == "/":
        raise HTTPException(422, "remove 不允许删除文档根节点")
    return ["remove", candidate, selector, *prop_args, "--json"]


def _validation(path: str) -> tuple[int, dict]:
    payload = _run_json(["validate", path, "--json"], allow_failure=True)
    failed = payload.get("success") is False or int(payload.get("returnCode") or 0) != 0
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    errors = data.get("errors") if isinstance(data.get("errors"), list) else []
    try:
        count = int(data.get("count", len(errors)))
    except (TypeError, ValueError):
        count = len(errors)
    # OfficeCLI 的成功结果目前是 data="OpenXML validation passed"；失败版本则
    # 可能只给 success=false/error 而没有 data.count。任何失败至少算一个错误，
    # 否则校验退化会被错误地当成 0 → 0。
    if failed:
        count = max(count, 1)
    return max(count, 0), payload


def _mutate(db: Session, row: ExplorationAttachment, operation: str,
            edits: list[dict], expected_version: int | None) -> dict:
    if expected_version is None:
        raise HTTPException(422, "修改 Office 文件必须传 expected_version")
    if row.version != expected_version:
        raise HTTPException(409, detail={
            "code": "workspace_version_conflict",
            "message": f"文件版本已从 {expected_version} 变为 {row.version}，请重新读取后再修改",
            "currentVersion": row.version,
        })
    if not edits or len(edits) > MAX_BATCH_EDITS:
        raise HTTPException(422, f"单次 Office 修改需要 1-{MAX_BATCH_EDITS} 个编辑项")

    # 在读取/校验原文件前先完成参数安全检查，危险素材属性不会触发任何 CLI。
    for edit in edits:
        _edit_args(edit, row.file_path or "document")

    target = row.file_path or ""
    parent = os.path.dirname(target)
    suffix = PurePosixPath(row.relative_path or row.filename).suffix.lower()
    outputs: list[dict] = []

    with tempfile.TemporaryDirectory(prefix=".bx-office-edit-", dir=parent) as temp:
        candidate = os.path.join(temp, f"document{suffix}")
        backup = os.path.join(temp, f"backup{suffix}")
        shutil.copy2(target, candidate)
        shutil.copy2(target, backup)

        before_count, _ = _validation(candidate)
        for edit in edits:
            payload = _run_json(_edit_args(edit, candidate))
            outputs.append({
                "operation": str(edit.get("operation") or "").lower(),
                "result": payload.get("data"),
                **({"warnings": payload["warnings"]} if payload.get("warnings") else {}),
            })

        after_count, validation = _validation(candidate)
        if after_count > before_count:
            raise HTTPException(422, detail={
                "code": "office_validation_regression",
                "message": (
                    f"修改使 OpenXML 校验错误从 {before_count} 个增加到 {after_count} 个，"
                    "已回滚，原文件未改变"
                ),
                "validation": validation.get("data"),
            })
        if os.path.getsize(candidate) > settings.max_upload_mb * 1024 * 1024:
            raise HTTPException(413, f"修改后文件超过大小限制 {settings.max_upload_mb}MB")

        replaced = False
        try:
            os.replace(candidate, target)
            replaced = True
            row = W.refresh_binary_metadata(db, row, source="agent")
        except Exception:
            if replaced and os.path.exists(backup):
                try:
                    os.replace(backup, target)
                finally:
                    db.rollback()
            raise

    validation_data = validation.get("data")
    validation_errors = (
        validation_data.get("errors", []) if isinstance(validation_data, dict) else []
    )
    return {
        "operation": operation,
        "updated": True,
        "id": row.id,
        "path": row.relative_path or row.filename,
        "version": row.version,
        "size": row.file_size,
        "edits": outputs,
        "validation": {
            "before": before_count,
            "after": after_count,
            "errors": validation_errors,
        },
    }


def operate(db: Session, session: ExplorationSession, operation: str,
            *, file_id: str | None = None, logical_path: str | None = None,
            selector: str = "/", element_type: str | None = None,
            props: dict | None = None, view: str = "outline",
            depth: int | None = None, find: str | None = None,
            replacement: str | None = None, expected_version: int | None = None,
            edits: list[dict] | None = None, start: int | None = None,
            end: int | None = None, max_lines: int | None = None,
            columns: str | None = None, cell_range: str | None = None) -> dict:
    op = str(operation or "").lower()
    if op == "create":
        try:
            logical = W.normalize_logical_path(logical_path or "")
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        suffix = PurePosixPath(logical).suffix.lower()
        if suffix not in OFFICE_EXTENSIONS:
            raise HTTPException(422, "OfficeCLI 仅创建 docx/xlsx/pptx")
        with tempfile.TemporaryDirectory(prefix="bx-office-create-") as temp:
            candidate = os.path.join(temp, f"document{suffix}")
            _run_text(["create", candidate])
            error_count, validation = _validation(candidate)
            if error_count:
                raise HTTPException(422, detail={
                    "code": "office_create_invalid",
                    "message": f"OfficeCLI 创建的文档存在 {error_count} 个校验错误",
                    "validation": validation.get("data"),
                })
            with open(candidate, "rb") as handle:
                row = W.create_bytes(db, session, logical, handle.read(), source="agent")
        return {
            "operation": "create", "created": True,
            "id": row.id, "path": row.relative_path,
            "version": row.version, "size": row.file_size,
        }

    if op in READ_OPERATIONS:
        row = _require_office_file(db, session.id, str(file_id or ""))
        return _read(
            row, op, selector=_selector(selector), view=view, depth=depth,
            find=find, start=start, end=end, max_lines=max_lines,
            columns=columns, cell_range=cell_range,
        )

    if op not in MUTATIONS:
        raise HTTPException(422, f"不支持的 Office 操作: {op}")
    row = _require_office_file(db, session.id, str(file_id or ""), lock=True)
    if op == "batch":
        batch_edits = edits or []
    else:
        batch_edits = [{
            "operation": op,
            "selector": selector,
            "element_type": element_type,
            "props": props or {},
            "find": find,
            "replacement": replacement,
        }]
    return _mutate(db, row, op, batch_edits, expected_version)
