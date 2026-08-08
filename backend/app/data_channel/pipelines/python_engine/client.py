"""Jupyter Kernel Gateway 客户端 — Python 脚本流水线的执行通道。

网关部署为无平台凭据的独立服务（见 compose 的 python_kernel_gateway）。
每次执行：创建一个独立内核 → 经 WebSocket 通道发送一条 execute_request
（用户脚本 + 平台注入的收尾代码）→ 读取 iopub 直到 idle → 销毁内核。
收尾代码把用户脚本的 ``result`` 变量序列化到输出标记之间，这里从内核
stdout 提取并归一化为 list[dict]（与 n8n 引擎同一入湖数据形态）。
"""
from __future__ import annotations

import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# 与 n8n 引擎（steward/runner.py _MAX_ROWS）一致的单次输出安全上限
_MAX_ROWS = 50_000
# 回传展示用的 stdout 尾部长度（防止内核打印撑爆内存/响应体）
_STDOUT_TAIL_CHARS = 4000

_RESULT_BEGIN = "__OB_RESULT_BEGIN__"
_RESULT_END = "__OB_RESULT_END__"

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

# 注入在用户脚本之后的平台收尾代码：提取 result 并序列化到标记之间。
# 用户脚本抛错时（stop_on_error=True）收尾代码不会执行，错误经 iopub 回收。
_RESULT_EPILOGUE = f'''
# ── OntologyBuild 平台输出提取（自动注入，请勿删除） ──
import json as _ob_json
if "result" not in globals():
    raise NameError("脚本未定义输出变量 result：请将最终结果赋值为 result（list[dict]，每行一个 {{列名: 值}} 对象）")
_ob_result = result
if hasattr(_ob_result, "to_dict"):
    _ob_result = _ob_result.to_dict(orient="records")
print()
print("{_RESULT_BEGIN}")
print(_ob_json.dumps(_ob_result, ensure_ascii=False, default=str))
print("{_RESULT_END}")
'''


class PythonEngineError(Exception):
    """Python 引擎失败（网关未配置/不可达/超时/脚本异常/输出非法）。

    message 面向用户（中文），可直接落到 HTTP 响应或 run.error_log。
    """


@dataclass
class ScriptExecution:
    """一次脚本执行的结果快照。error 非空即失败，rows 此时为空。"""

    rows: list[dict] = field(default_factory=list)
    stdout: str = ""
    error: str | None = None
    traceback: str = ""
    duration_ms: int = 0
    kernel_id: str = ""


def normalize_rows(body: Any) -> list[dict]:
    """把脚本输出规整为 list[dict] 行数据（对齐 n8n 引擎的归一化语义）。"""
    if body is None:
        return []
    if isinstance(body, list):
        actual = len(body)
        if actual > _MAX_ROWS:
            raise PythonEngineError(
                f"脚本单次输出超过平台安全上限：上限 {_MAX_ROWS} 行，实际 {actual} 行。"
                "本次运行已失败且不会截断入湖；请在脚本中分页/分批输出。")
        rows = []
        for item in body:
            if isinstance(item, dict):
                rows.append(item)
            else:
                rows.append({"value": item})
        return rows
    if isinstance(body, dict):
        return [body]
    return [{"value": body}]


def execute_script(script: str, *, timeout: int | None = None) -> ScriptExecution:
    """在 Jupyter Kernel Gateway 上执行脚本并提取 result 行数据。

    基础设施类失败（未配置/不可达/超时/输出协议非法）抛 PythonEngineError；
    脚本自身异常不抛出，以 ScriptExecution.error + traceback 承载。
    """
    timeout = timeout or settings.python_script_timeout_seconds
    base_url = (settings.python_kernel_gateway_url or "").strip().rstrip("/")
    if not base_url:
        raise PythonEngineError(
            "Python 执行网关未配置（PYTHON_KERNEL_GATEWAY_URL）。"
            "请在部署环境中配置 Jupyter Kernel Gateway 服务地址后重试。")

    headers: dict[str, str] = {}
    token = (settings.python_kernel_gateway_auth_token or "").strip()
    if token:
        headers["Authorization"] = f"token {token}"

    started = time.monotonic()
    kernel_id = ""
    try:
        resp = httpx.post(f"{base_url}/api/kernels", headers=headers, timeout=30)
        resp.raise_for_status()
        kernel_id = str(resp.json().get("id") or "")
        if not kernel_id:
            raise PythonEngineError("Python 执行网关未返回内核 id，无法执行脚本。")
    except PythonEngineError:
        raise
    except Exception as exc:
        raise PythonEngineError(
            f"无法连接 Python 执行网关（{base_url}）：{exc}") from exc

    execution = ScriptExecution(kernel_id=kernel_id)
    try:
        stdout_text, error_name, error_value, traceback_text = _run_on_kernel(
            base_url, kernel_id, headers, script + _RESULT_EPILOGUE, timeout)
        execution.stdout = stdout_text[-_STDOUT_TAIL_CHARS:]
        execution.traceback = traceback_text
        if error_name:
            execution.error = f"脚本执行失败（{error_name}）：{error_value}"
        else:
            try:
                execution.rows = _extract_rows(stdout_text)
            except PythonEngineError as exc:
                execution.error = str(exc)
    finally:
        execution.duration_ms = int((time.monotonic() - started) * 1000)
        _delete_kernel(base_url, kernel_id, headers)
    return execution


def _run_on_kernel(
    base_url: str,
    kernel_id: str,
    headers: dict[str, str],
    code: str,
    timeout: int,
) -> tuple[str, str, str, str]:
    """经 Jupyter WebSocket 通道执行代码，回收 (stdout, ename, evalue, traceback)。"""
    import websocket  # websocket-client；延迟导入保持模块加载轻量

    ws_url = base_url.replace("https://", "wss://", 1).replace("http://", "ws://", 1)
    msg_id = uuid.uuid4().hex
    request = {
        "header": {
            "msg_id": msg_id,
            "username": "ontologybuild",
            "session": uuid.uuid4().hex,
            "msg_type": "execute_request",
            "version": "5.3",
        },
        "parent_header": {},
        "metadata": {},
        "content": {
            "code": code,
            "silent": False,
            "store_history": False,
            "user_expressions": {},
            "allow_stdin": False,
            "stop_on_error": True,
        },
        "channel": "shell",
    }
    header_lines = [f"{key}: {value}" for key, value in headers.items()]
    deadline = time.monotonic() + timeout
    stdout_parts: list[str] = []
    error_name = ""
    error_value = ""
    traceback_text = ""

    try:
        ws = websocket.create_connection(
            f"{ws_url}/api/kernels/{kernel_id}/channels",
            header=header_lines,
            timeout=30,
        )
    except PythonEngineError:
        raise
    except Exception as exc:
        raise PythonEngineError(f"无法连接 Python 执行网关的内核通道：{exc}") from exc

    try:
        ws.send(json.dumps(request))
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise PythonEngineError(
                    f"脚本执行超过平台时限（{timeout} 秒），本次执行已终止。")
            ws.settimeout(min(remaining, 5.0))
            try:
                frame = ws.recv()
            except websocket.WebSocketTimeoutException:
                continue
            if not frame:
                continue
            try:
                message = json.loads(frame)
            except ValueError:
                continue
            # 只回收本次请求的消息；网关广播的内核状态消息没有该 parent。
            if (message.get("parent_header") or {}).get("msg_id") != msg_id:
                continue
            channel = message.get("channel")
            msg_type = (message.get("header") or {}).get("msg_type")
            content = message.get("content") or {}
            if channel == "iopub":
                if msg_type == "stream":
                    stdout_parts.append(str(content.get("text") or ""))
                elif msg_type == "error":
                    error_name = str(content.get("ename") or "Error")
                    error_value = str(content.get("evalue") or "")
                    traceback_text = _clean_traceback(content.get("traceback") or [])
                elif msg_type == "status" and content.get("execution_state") == "idle":
                    break
            elif channel == "shell" and msg_type == "execute_reply":
                if content.get("status") == "error" and not error_name:
                    error_name = str(content.get("ename") or "Error")
                    error_value = str(content.get("evalue") or "")
                    traceback_text = _clean_traceback(content.get("traceback") or [])
    finally:
        try:
            ws.close()
        except Exception:  # noqa: BLE001 — 关闭失败不影响内核销毁兜底
            pass
    return "".join(stdout_parts), error_name, error_value, traceback_text


def _clean_traceback(lines: list) -> str:
    """去掉 Jupyter traceback 里的 ANSI 颜色码，便于页面直接展示。"""
    return _ANSI_RE.sub("", "\n".join(str(line) for line in lines))


def _extract_rows(stdout: str) -> list[dict]:
    begin = stdout.rfind(_RESULT_BEGIN)
    end = stdout.rfind(_RESULT_END)
    if begin == -1 or end == -1 or end < begin:
        raise PythonEngineError(
            "脚本执行完成但未检测到平台输出标记：请确保最终结果赋值给变量 result"
            "（list[dict]，每行一个 {列名: 值} 对象）。")
    raw = stdout[begin + len(_RESULT_BEGIN):end].strip()
    try:
        body = json.loads(raw)
    except ValueError as exc:
        raise PythonEngineError(f"脚本输出的 result 无法解析为 JSON：{exc}") from exc
    return normalize_rows(body)


def _delete_kernel(base_url: str, kernel_id: str, headers: dict[str, str]) -> None:
    if not kernel_id:
        return
    try:
        httpx.delete(f"{base_url}/api/kernels/{kernel_id}", headers=headers, timeout=10)
    except Exception:  # noqa: BLE001 — 清理失败由网关侧空闲回收兜底
        logger.warning("销毁 Python 内核失败（%s）", kernel_id, exc_info=True)
