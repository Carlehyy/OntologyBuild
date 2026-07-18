"""Provider adapter owned by the Super Assistant module.

The normalized protocol mirrors Hermes' provider-neutral turn model while
reusing only the platform's model configuration records.
"""
from __future__ import annotations

import json
from typing import Any


class ProviderError(RuntimeError):
    pass


def chat(call_kwargs: dict[str, Any], messages: list[dict[str, Any]],
         tools: list[dict[str, Any]]) -> dict[str, Any]:
    provider = str(call_kwargs.get("provider") or "openai").lower()
    try:
        result = _chat_anthropic(call_kwargs, messages, tools) if provider == "anthropic" else _chat_openai(
            call_kwargs, messages, tools,
        )
    except ProviderError:
        raise
    except Exception as exc:
        raise ProviderError(
            f"模型调用失败 ({provider}/{call_kwargs.get('model') or 'unknown'}): {exc}"
        ) from exc
    content = result.get("content")
    if isinstance(content, str) and "</think>" in content:
        result["content"] = content.split("</think>", 1)[1].strip()
    return result


def _chat_openai(kw: dict[str, Any], messages: list[dict[str, Any]],
                 tools: list[dict[str, Any]]) -> dict[str, Any]:
    import openai

    client_kwargs: dict[str, Any] = {
        "api_key": kw.get("api_key") or "sk-none",
        "timeout": int(kw.get("timeout_seconds") or 120),
    }
    if kw.get("api_base"):
        client_kwargs["base_url"] = kw["api_base"]
    client = openai.OpenAI(**client_kwargs)

    provider_messages: list[dict[str, Any]] = []
    for message in messages:
        if message["role"] == "assistant" and message.get("tool_calls"):
            provider_messages.append({
                "role": "assistant",
                "content": message.get("content") or None,
                "tool_calls": [{
                    "id": call["id"],
                    "type": "function",
                    "function": {
                        "name": call["name"],
                        "arguments": json.dumps(call.get("arguments") or {}, ensure_ascii=False),
                    },
                } for call in message["tool_calls"]],
            })
        elif message["role"] == "tool":
            provider_messages.append({
                "role": "tool",
                "tool_call_id": message["tool_call_id"],
                "content": message.get("content") or "",
            })
        else:
            provider_messages.append({"role": message["role"], "content": message.get("content") or ""})

    create_kwargs: dict[str, Any] = {
        "model": kw["model"],
        "messages": provider_messages,
        "temperature": 0.2,
    }
    if kw.get("max_output_tokens"):
        create_kwargs["max_tokens"] = int(kw["max_output_tokens"])
    if tools:
        create_kwargs["tools"] = [{
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool["description"],
                "parameters": tool["parameters"],
            },
        } for tool in tools]
    response = client.chat.completions.create(**create_kwargs)
    if not response.choices:
        raise ProviderError("模型未返回任何候选结果")
    message = response.choices[0].message
    tool_calls = []
    for call in message.tool_calls or []:
        try:
            arguments = json.loads(call.function.arguments or "{}")
        except json.JSONDecodeError:
            arguments = {"_raw": call.function.arguments or ""}
        tool_calls.append({"id": call.id, "name": call.function.name, "arguments": arguments})
    usage = getattr(response, "usage", None)
    return {
        "content": message.content,
        "tool_calls": tool_calls,
        "usage": {
            "inputTokens": getattr(usage, "prompt_tokens", None),
            "outputTokens": getattr(usage, "completion_tokens", None),
        } if usage else {},
    }


def _chat_anthropic(kw: dict[str, Any], messages: list[dict[str, Any]],
                    tools: list[dict[str, Any]]) -> dict[str, Any]:
    import anthropic

    client_kwargs: dict[str, Any] = {
        "api_key": kw.get("api_key") or "",
        "timeout": int(kw.get("timeout_seconds") or 120),
    }
    if kw.get("api_base"):
        client_kwargs["base_url"] = kw["api_base"]
    client = anthropic.Anthropic(**client_kwargs)

    system = "\n\n".join(message.get("content") or "" for message in messages if message["role"] == "system")
    provider_messages: list[dict[str, Any]] = []
    for message in messages:
        if message["role"] == "system":
            continue
        if message["role"] == "assistant":
            blocks: list[dict[str, Any]] = []
            if message.get("content"):
                blocks.append({"type": "text", "text": message["content"]})
            blocks.extend({
                "type": "tool_use",
                "id": call["id"],
                "name": call["name"],
                "input": call.get("arguments") or {},
            } for call in message.get("tool_calls") or [])
            provider_messages.append({"role": "assistant", "content": blocks or [{"type": "text", "text": ""}]})
        elif message["role"] == "tool":
            provider_messages.append({
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": message["tool_call_id"],
                    "content": message.get("content") or "",
                }],
            })
        else:
            provider_messages.append({"role": "user", "content": message.get("content") or ""})

    create_kwargs: dict[str, Any] = {
        "model": kw["model"],
        "max_tokens": int(kw.get("max_output_tokens") or 4096),
        "temperature": 0.2,
        "system": system,
        "messages": provider_messages,
    }
    if tools:
        create_kwargs["tools"] = [{
            "name": tool["name"],
            "description": tool["description"],
            "input_schema": tool["parameters"],
        } for tool in tools]
    response = client.messages.create(**create_kwargs)
    text_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    for block in response.content:
        if block.type == "text":
            text_parts.append(block.text)
        elif block.type == "tool_use":
            tool_calls.append({"id": block.id, "name": block.name, "arguments": dict(block.input or {})})
    usage = getattr(response, "usage", None)
    return {
        "content": "\n".join(text_parts) or None,
        "tool_calls": tool_calls,
        "usage": {
            "inputTokens": getattr(usage, "input_tokens", None),
            "outputTokens": getattr(usage, "output_tokens", None),
        } if usage else {},
    }
