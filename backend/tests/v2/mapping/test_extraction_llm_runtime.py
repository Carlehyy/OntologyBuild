from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from app.data_channel.transforms import router as extraction_router
from app.ontologies.extraction.extraction_service import (
    LLMExtractionError,
    LLMExtractionService,
)


def test_unconfigured_extraction_uses_explicit_rules_mode_without_network():
    service = LLMExtractionService()

    result = service.extract_pipeline(
        "华星科技有限公司与远洋银行开展合作。",
        "ontology-id",
    )

    assert service.available is False
    assert service.model_name is None
    assert result["method"] == "deterministic_rules"
    assert "No enabled text LLM" in result["note"]


def test_configured_llm_failure_never_silently_switches_to_rules(monkeypatch):
    service = LLMExtractionService({
        "provider": "openai",
        "model": "configured-model",
        "api_key": "test-key",
    })
    monkeypatch.setattr(
        "app.model_configs.llm_gateway.chat",
        MagicMock(side_effect=RuntimeError("provider unavailable")),
    )

    with pytest.raises(LLMExtractionError, match="LLM"):
        service.extract_pipeline("有效文本", "ontology-id")


def test_extraction_route_surfaces_configured_llm_failure(monkeypatch):
    service = MagicMock(available=True, model_name="configured-model")
    service.extract_pipeline.side_effect = LLMExtractionError(
        "已配置的 LLM 抽取失败，未切换到规则模式"
    )
    monkeypatch.setattr(
        extraction_router,
        "get_llm_extraction_service",
        lambda _db: service,
    )

    with pytest.raises(HTTPException) as exc_info:
        extraction_router.run_extraction(
            "ontology-id",
            {"text": "有效文本"},
            db=MagicMock(),
        )

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail["code"] == "llm_extraction_failed"


def test_configured_nl_to_cypher_failure_is_not_returned_as_empty_success(
    monkeypatch,
):
    service = LLMExtractionService({
        "provider": "openai",
        "model": "configured-model",
        "api_key": "test-key",
    })
    monkeypatch.setattr(
        "app.model_configs.llm_gateway.chat",
        MagicMock(return_value={"content": "not-json"}),
    )

    with pytest.raises(LLMExtractionError, match="JSON"):
        service.nl_to_cypher("查询供应商", "ontology-id")


def test_nl_to_cypher_route_surfaces_configured_llm_failure(monkeypatch):
    service = MagicMock(available=True, model_name="configured-model")
    service.nl_to_cypher.side_effect = LLMExtractionError(
        "已配置的 LLM 查询翻译失败"
    )
    monkeypatch.setattr(
        extraction_router,
        "get_llm_extraction_service",
        lambda _db: service,
    )

    with pytest.raises(HTTPException) as exc_info:
        extraction_router.nl_to_cypher(
            "ontology-id",
            {"question": "查询供应商"},
            db=MagicMock(),
        )

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail["code"] == (
        "llm_query_translation_failed"
    )
