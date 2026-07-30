from unittest.mock import patch


def test_legacy_llm_bridge_is_the_canonical_gateway_module():
    from app.model_configs import llm_gateway
    from app.ontologies.agent_runtime import llm_bridge

    assert llm_bridge is llm_gateway

    shared_symbols = (
        "chat",
        "LLMError",
        "_safe_error_message",
        "_failure_status",
        "_record_call",
        "_strip_think",
        "_chat_openai",
        "_chat_anthropic",
    )
    for symbol in shared_symbols:
        assert getattr(llm_bridge, symbol) is getattr(llm_gateway, symbol)


def test_patching_legacy_chat_replaces_the_canonical_gateway_chat():
    from app.model_configs import llm_gateway
    from app.ontologies.agent_runtime import llm_bridge

    original_chat = llm_gateway.chat
    expected = {"content": "PONG", "tool_calls": [], "usage": None}

    with patch(
        "app.ontologies.agent_runtime.llm_bridge.chat",
        return_value=expected,
    ) as patched_chat:
        assert llm_bridge.chat is patched_chat
        assert llm_gateway.chat is patched_chat
        assert llm_gateway.chat({}, [], []) == expected

    assert llm_gateway.chat is original_chat
