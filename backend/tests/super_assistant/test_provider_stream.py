from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.super_assistant import provider


_CALL_KWARGS = {"provider": "openai", "model": "fake-model", "api_key": "sk-test"}
_MESSAGES = [{"role": "user", "content": "你好"}]


def _chunk(content=None, tool_deltas=None, finish_reason=None, usage=None):
    delta = SimpleNamespace(content=content, tool_calls=tool_deltas)
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=delta, finish_reason=finish_reason)],
        usage=usage,
    )


def _tool_delta(index, call_id=None, name=None, arguments=None):
    return SimpleNamespace(
        index=index, id=call_id,
        function=SimpleNamespace(name=name, arguments=arguments),
    )


def _completion(content, tool_calls=None, usage=None):
    message = SimpleNamespace(content=content, tool_calls=tool_calls or [])
    return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=usage)


class _FakeCompletions:
    def __init__(self, *, stream_chunks=None, stream_error=None, response=None):
        self._stream_chunks = stream_chunks or []
        self._stream_error = stream_error
        self._response = response

    def create(self, **kwargs):
        if kwargs.get("stream"):
            if self._stream_error is not None:
                raise self._stream_error
            return iter(self._stream_chunks)
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


def _patch_openai(monkeypatch, completions: _FakeCompletions) -> None:
    import openai

    monkeypatch.setattr(
        openai, "OpenAI",
        lambda **_kwargs: SimpleNamespace(chat=SimpleNamespace(completions=completions)),
    )


def test_openai_stream_aggregates_content_tool_calls_and_usage(monkeypatch):
    _patch_openai(monkeypatch, _FakeCompletions(stream_chunks=[
        _chunk(content="你"),
        _chunk(content="好"),
        _chunk(tool_deltas=[_tool_delta(0, call_id="call-1", name="web_")]),
        _chunk(tool_deltas=[_tool_delta(0, name="search", arguments='{"quer')]),
        _chunk(tool_deltas=[_tool_delta(0, arguments='y": "本体"}')], finish_reason="tool_calls"),
        SimpleNamespace(choices=[], usage=SimpleNamespace(prompt_tokens=11, completion_tokens=7)),
    ]))
    deltas: list[str] = []
    result = provider.chat_stream(_CALL_KWARGS, _MESSAGES, [], deltas.append)
    assert result["content"] == "你好"
    assert result["tool_calls"] == [{
        "id": "call-1", "name": "web_search", "arguments": {"query": "本体"},
    }]
    assert result["usage"] == {"inputTokens": 11, "outputTokens": 7}
    assert "".join(deltas) == "你好"  # 短回复在前缀窗口内，finish 时整体冲刷


def test_openai_stream_filters_think_prefix_across_chunks(monkeypatch):
    _patch_openai(monkeypatch, _FakeCompletions(stream_chunks=[
        _chunk(content="<th"),
        _chunk(content="ink>秘密推理"),
        _chunk(content="</thi"),
        _chunk(content="nk>"),
        _chunk(content="答"),
        _chunk(content="案"),
    ]))
    deltas: list[str] = []
    result = provider.chat_stream(_CALL_KWARGS, _MESSAGES, [], deltas.append)
    assert result["content"] == "答案"
    assert deltas == ["答", "案"]


def test_openai_stream_flushes_buffer_when_no_think_within_prefix(monkeypatch):
    payload = "正文" * 300  # 600 个 CJK 字符，超过 512 前缀窗口
    _patch_openai(monkeypatch, _FakeCompletions(stream_chunks=[_chunk(content=payload)]))
    deltas: list[str] = []
    result = provider.chat_stream(_CALL_KWARGS, _MESSAGES, [], deltas.append)
    assert result["content"] == payload
    assert "".join(deltas) == payload


def test_openai_stream_holds_short_reply_until_finish(monkeypatch):
    _patch_openai(monkeypatch, _FakeCompletions(stream_chunks=[
        _chunk(content="你"), _chunk(content="好"),
    ]))
    deltas: list[str] = []
    result = provider.chat_stream(_CALL_KWARGS, _MESSAGES, [], deltas.append)
    assert result["content"] == "你好"
    assert "".join(deltas) == "你好"


def test_openai_stream_drops_unclosed_think_content(monkeypatch):
    _patch_openai(monkeypatch, _FakeCompletions(stream_chunks=[
        _chunk(content="<think>只有推理没有结论"),
    ]))
    deltas: list[str] = []
    result = provider.chat_stream(_CALL_KWARGS, _MESSAGES, [], deltas.append)
    assert deltas == []
    assert result["content"] == "<think>只有推理没有结论"


def test_openai_stream_marks_truncated_tool_arguments(monkeypatch):
    _patch_openai(monkeypatch, _FakeCompletions(stream_chunks=[
        _chunk(tool_deltas=[_tool_delta(0, call_id="call-1", name="web_search", arguments='{"query": "本')]),
        _chunk(finish_reason="length"),
    ]))
    result = provider.chat_stream(_CALL_KWARGS, _MESSAGES, [], None)
    assert result["tool_calls"] == [{
        "id": "call-1", "name": "web_search",
        "arguments": {"_truncated": True, "_raw": '{"query": "本'},
    }]


def test_openai_stream_keeps_raw_arguments_when_not_truncated(monkeypatch):
    _patch_openai(monkeypatch, _FakeCompletions(stream_chunks=[
        _chunk(tool_deltas=[_tool_delta(0, call_id="call-1", name="web_search", arguments="{bad")]),
        _chunk(finish_reason="stop"),
    ]))
    result = provider.chat_stream(_CALL_KWARGS, _MESSAGES, [], None)
    assert result["tool_calls"][0]["arguments"] == {"_raw": "{bad"}


def test_openai_stream_falls_back_to_non_streaming(monkeypatch):
    _patch_openai(monkeypatch, _FakeCompletions(
        stream_error=ConnectionError("stream reset"),
        response=_completion("<think>推理</think>完整答复"),
    ))
    deltas: list[str] = []
    result = provider.chat_stream(_CALL_KWARGS, _MESSAGES, [], deltas.append)
    assert result["content"] == "完整答复"
    assert deltas == ["完整答复"]


def test_openai_stream_raises_provider_error_when_fallback_also_fails(monkeypatch):
    _patch_openai(monkeypatch, _FakeCompletions(
        stream_error=ConnectionError("stream reset"),
        response=RuntimeError("upstream down"),
    ))
    with pytest.raises(provider.ProviderError, match="模型调用失败"):
        provider.chat_stream(_CALL_KWARGS, _MESSAGES, [], None)


class _FakeAnthropicStream:
    def __init__(self, events, final_message):
        self._events = events
        self._final_message = final_message

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def __iter__(self):
        return iter(self._events)

    def get_final_message(self):
        return self._final_message


def _patch_anthropic(monkeypatch, *, events=None, final=None, stream_error=None, create_response=None):
    import anthropic

    def _stream(**_kwargs):
        if stream_error is not None:
            raise stream_error
        return _FakeAnthropicStream(events or [], final)

    def _create(**_kwargs):
        if isinstance(create_response, Exception):
            raise create_response
        return create_response

    monkeypatch.setattr(
        anthropic, "Anthropic",
        lambda **_kwargs: SimpleNamespace(messages=SimpleNamespace(stream=_stream, create=_create)),
    )


def _anthropic_kwargs():
    return {"provider": "anthropic", "model": "claude-fake", "api_key": "ak-test"}


def test_anthropic_stream_aggregates_text_tool_use_and_usage(monkeypatch):
    events = [
        SimpleNamespace(type="content_block_start", index=0,
                        content_block=SimpleNamespace(type="text")),
        SimpleNamespace(type="content_block_delta", index=0,
                        delta=SimpleNamespace(type="text_delta", text="你")),
        SimpleNamespace(type="content_block_delta", index=0,
                        delta=SimpleNamespace(type="text_delta", text="好")),
        SimpleNamespace(type="content_block_start", index=1,
                        content_block=SimpleNamespace(type="tool_use", id="toolu_1", name="web_search")),
        SimpleNamespace(type="content_block_delta", index=1,
                        delta=SimpleNamespace(type="input_json_delta", partial_json='{"q": ')),
        SimpleNamespace(type="content_block_delta", index=1,
                        delta=SimpleNamespace(type="input_json_delta", partial_json="1}")),
        SimpleNamespace(type="content_block_stop", index=1),
    ]
    final = SimpleNamespace(
        stop_reason="end_turn",
        usage=SimpleNamespace(input_tokens=9, output_tokens=5),
    )
    _patch_anthropic(monkeypatch, events=events, final=final)
    deltas: list[str] = []
    result = provider.chat_stream(_anthropic_kwargs(), _MESSAGES, [], deltas.append)
    assert result["content"] == "你好"
    assert result["tool_calls"] == [{"id": "toolu_1", "name": "web_search", "arguments": {"q": 1}}]
    assert result["usage"] == {"inputTokens": 9, "outputTokens": 5}
    assert "".join(deltas) == "你好"  # 短回复在前缀窗口内，finish 时整体冲刷


def test_anthropic_stream_filters_think_prefix(monkeypatch):
    events = [
        SimpleNamespace(type="content_block_start", index=0,
                        content_block=SimpleNamespace(type="text")),
        SimpleNamespace(type="content_block_delta", index=0,
                        delta=SimpleNamespace(type="text_delta", text="<think>推")),
        SimpleNamespace(type="content_block_delta", index=0,
                        delta=SimpleNamespace(type="text_delta", text="理</think>结论")),
    ]
    final = SimpleNamespace(stop_reason="end_turn", usage=None)
    _patch_anthropic(monkeypatch, events=events, final=final)
    deltas: list[str] = []
    result = provider.chat_stream(_anthropic_kwargs(), _MESSAGES, [], deltas.append)
    assert result["content"] == "结论"
    assert deltas == ["结论"]


def test_anthropic_stream_marks_truncated_tool_arguments(monkeypatch):
    events = [
        SimpleNamespace(type="content_block_start", index=0,
                        content_block=SimpleNamespace(type="tool_use", id="toolu_1", name="web_fetch")),
        SimpleNamespace(type="content_block_delta", index=0,
                        delta=SimpleNamespace(type="input_json_delta", partial_json='{"url": "ht')),
        SimpleNamespace(type="content_block_stop", index=0),
    ]
    final = SimpleNamespace(stop_reason="max_tokens", usage=None)
    _patch_anthropic(monkeypatch, events=events, final=final)
    result = provider.chat_stream(_anthropic_kwargs(), _MESSAGES, [], None)
    assert result["tool_calls"] == [{
        "id": "toolu_1", "name": "web_fetch",
        "arguments": {"_truncated": True, "_raw": '{"url": "ht'},
    }]


def test_anthropic_stream_falls_back_to_non_streaming(monkeypatch):
    response = SimpleNamespace(
        content=[SimpleNamespace(type="text", text="回退答复")],
        usage=SimpleNamespace(input_tokens=3, output_tokens=2),
    )
    _patch_anthropic(
        monkeypatch,
        stream_error=RuntimeError("stream broken"),
        create_response=response,
    )
    deltas: list[str] = []
    result = provider.chat_stream(_anthropic_kwargs(), _MESSAGES, [], deltas.append)
    assert result["content"] == "回退答复"
    assert result["usage"] == {"inputTokens": 3, "outputTokens": 2}
    assert deltas == ["回退答复"]
