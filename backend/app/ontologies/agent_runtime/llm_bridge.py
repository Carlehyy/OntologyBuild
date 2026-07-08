"""
LLM 桥接 — 把统一的「消息 + 工具」协议翻译成各 provider 的 tool-calling API

中立消息格式（orchestrator 只认识这个）：
  {"role": "system"|"user", "content": str}
  {"role": "assistant", "content": str|None, "tool_calls": [{"id","name","arguments": dict}]}
  {"role": "tool", "tool_call_id": str, "name": str, "content": str}

返回：{"content": str|None, "tool_calls": [...], "usage": {"inputTokens","outputTokens"}}

provider 取自模型配置（openai | compatible | anthropic）。DeepSeek / Qwen /
Moonshot 等国内主流服务都兼容 OpenAI tools 协议，走 openai 分支即可。
"""
from __future__ import annotations

import json
import time
from typing import Any


class LLMError(Exception):
    """LLM 调用失败 — 配置错误 / 网络 / provider 不支持工具调用等。"""


def chat(call_kwargs: dict, messages: list[dict], tools: list[dict]) -> dict[str, Any]:
    provider = (call_kwargs.get("provider") or "openai").lower()
    model_name = call_kwargs.get("model", "unknown")
    model_config_id = call_kwargs.get("model_config_id")
    started = time.monotonic()
    status = "success"
    error_msg = None
    try:
        if provider == "anthropic":
            return _chat_anthropic(call_kwargs, messages, tools)
        return _chat_openai(call_kwargs, messages, tools)
    except LLMError as e:
        status = "error"
        error_msg = str(e)
        raise
    except Exception as e:  # noqa: BLE001 — provider SDK 的异常统一收口
        status = "error"
        error_msg = str(e)
        raise LLMError(f"LLM 调用失败({provider}/{call_kwargs.get('model')}): {e}") from e
    finally:
        try:
            latency_ms = int((time.monotonic() - started) * 1000)
            if model_config_id:
                _record_call(model_config_id, model_name, provider, status, latency_ms, error_msg)
        except Exception:
            pass  # 统计记录失败不影响主流程


def _record_call(model_config_id: str, model_name: str, provider: str,
                 status: str, latency_ms: int, error_message: str | None) -> None:
    from app.database import SessionLocal
    from app.model_configs.models import ModelCallLog

    db = SessionLocal()
    try:
        log = ModelCallLog(
            model_config_id=model_config_id,
            model_name=model_name,
            provider=provider,
            status=status,
            latency_ms=latency_ms,
            error_message=error_message,
        )
        db.add(log)
        db.commit()
    finally:
        db.close()


def _chat_openai(kw: dict, messages: list[dict], tools: list[dict]) -> dict:
    import openai

    client_kwargs: dict = {"api_key": kw.get("api_key") or "sk-none", "timeout": 120}
    if kw.get("api_base"):
        client_kwargs["base_url"] = kw["api_base"]
    client = openai.OpenAI(**client_kwargs)

    oai_msgs = []
    for m in messages:
        if m["role"] == "assistant" and m.get("tool_calls"):
            oai_msgs.append({
                "role": "assistant",
                "content": m.get("content") or None,
                "tool_calls": [{
                    "id": tc["id"], "type": "function",
                    "function": {"name": tc["name"],
                                 "arguments": json.dumps(tc.get("arguments") or {}, ensure_ascii=False)},
                } for tc in m["tool_calls"]],
            })
        elif m["role"] == "tool":
            oai_msgs.append({"role": "tool", "tool_call_id": m["tool_call_id"],
                             "content": m["content"]})
        else:
            oai_msgs.append({"role": m["role"], "content": m.get("content") or ""})

    create_kwargs: dict = {"model": kw["model"], "messages": oai_msgs, "temperature": 0.2}
    if tools:  # 空 tools 列表部分 provider 会直接报错，纯文本调用时省略
        create_kwargs["tools"] = [{"type": "function",
                                   "function": {"name": t["name"], "description": t["description"],
                                                "parameters": t["parameters"]}} for t in tools]
    resp = client.chat.completions.create(**create_kwargs)
    msg = resp.choices[0].message
    tool_calls = []
    for tc in (msg.tool_calls or []):
        try:
            args = json.loads(tc.function.arguments or "{}")
        except json.JSONDecodeError:
            args = {"_raw": tc.function.arguments}
        tool_calls.append({"id": tc.id, "name": tc.function.name, "arguments": args})
    usage = getattr(resp, "usage", None)
    return {
        "content": msg.content,
        "tool_calls": tool_calls,
        "usage": {"inputTokens": getattr(usage, "prompt_tokens", None),
                  "outputTokens": getattr(usage, "completion_tokens", None)} if usage else None,
    }


def _chat_anthropic(kw: dict, messages: list[dict], tools: list[dict]) -> dict:
    import anthropic

    client_kwargs: dict = {"api_key": kw.get("api_key") or ""}
    if kw.get("api_base"):
        client_kwargs["base_url"] = kw["api_base"]
    client = anthropic.Anthropic(**client_kwargs)

    system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
    aa_msgs: list[dict] = []
    for m in messages:
        if m["role"] == "system":
            continue
        if m["role"] == "assistant":
            blocks: list[dict] = []
            if m.get("content"):
                blocks.append({"type": "text", "text": m["content"]})
            for tc in (m.get("tool_calls") or []):
                blocks.append({"type": "tool_use", "id": tc["id"], "name": tc["name"],
                               "input": tc.get("arguments") or {}})
            aa_msgs.append({"role": "assistant", "content": blocks or [{"type": "text", "text": ""}]})
        elif m["role"] == "tool":
            aa_msgs.append({"role": "user", "content": [{
                "type": "tool_result", "tool_use_id": m["tool_call_id"],
                "content": m["content"],
            }]})
        else:
            aa_msgs.append({"role": "user", "content": m.get("content") or ""})

    create_kwargs: dict = {"model": kw["model"], "max_tokens": 4096, "temperature": 0.2,
                           "system": system, "messages": aa_msgs}
    if tools:  # 纯文本调用时省略 tools
        create_kwargs["tools"] = [{"name": t["name"], "description": t["description"],
                                   "input_schema": t["parameters"]} for t in tools]
    resp = client.messages.create(**create_kwargs)
    text_parts, tool_calls = [], []
    for block in resp.content:
        if block.type == "text":
            text_parts.append(block.text)
        elif block.type == "tool_use":
            tool_calls.append({"id": block.id, "name": block.name,
                               "arguments": dict(block.input or {})})
    usage = getattr(resp, "usage", None)
    return {
        "content": "\n".join(text_parts) or None,
        "tool_calls": tool_calls,
        "usage": {"inputTokens": getattr(usage, "input_tokens", None),
                  "outputTokens": getattr(usage, "output_tokens", None)} if usage else None,
    }
