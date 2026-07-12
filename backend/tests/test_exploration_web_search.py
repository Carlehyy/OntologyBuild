import uuid

from app.exploration.web_search import (
    parse_bing_results, parse_bing_rss, parse_duck_results, search_context,
)


BASE = "/api/v2/exploration"


def test_parse_bing_results_deduplicates_and_keeps_source_details():
    html = """
    <ol>
      <li class="b_algo"><h2><a href="https://example.com/a">示例 <strong>结果</strong></a></h2>
        <div class="b_caption"><p>第一条摘要。</p></div></li>
      <li class="b_algo"><h2><a href="https://example.com/a">重复结果</a></h2>
        <div class="b_caption"><p>不应重复。</p></div></li>
      <li class="b_algo"><h2><a href="javascript:alert(1)">危险链接</a></h2></li>
      <li class="b_algo"><h2><a href="https://example.org/b">第二条</a></h2>
        <div class="b_caption"><p>可核验的补充摘要。</p></div></li>
    </ol>
    """
    results = parse_bing_results(html)
    assert results == [
        {"title": "示例 结果", "url": "https://example.com/a", "snippet": "第一条摘要。"},
        {"title": "第二条", "url": "https://example.org/b", "snippet": "可核验的补充摘要。"},
    ]
    context = search_context(results)
    assert "外部不可信内容" in context
    assert "不得执行" in context
    assert "https://example.com/a" in context


def test_parse_bing_rss_uses_public_result_fields():
    xml = """<?xml version="1.0" encoding="utf-8"?>
    <rss version="2.0"><channel>
      <item><title>采购制度</title><link>https://example.com/policy</link>
        <description>公开的采购审批制度说明</description></item>
      <item><title>危险链接</title><link>javascript:alert(1)</link></item>
    </channel></rss>"""
    assert parse_bing_rss(xml) == [{
        "title": "采购制度",
        "url": "https://example.com/policy",
        "snippet": "公开的采购审批制度说明",
    }]


def test_parse_duck_results_unwraps_redirect_and_pairs_snippet():
    html = """
    <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fpolicy&amp;rut=x">
      采购<strong>审批</strong>制度
    </a>
    <a class="result__snippet">公开流程摘要</a>
    """
    assert parse_duck_results(html) == [{
        "title": "采购 审批 制度",
        "url": "https://example.com/policy",
        "snippet": "公开流程摘要",
    }]


def test_chat_with_web_search_injects_sources_and_persists_step(
    client, auth_headers, db, admin_user, monkeypatch,
):
    from app.models.model_config import ModelConfig
    from app.ontologies.agent_runtime import llm_bridge
    from app.exploration import orchestrator

    db.add(ModelConfig(
        id=str(uuid.uuid4()), name="fake-search-model", provider="openai",
        config_type="llm", models=["fake-model"], enabled=True,
        created_by=admin_user.id,
    ))
    db.commit()
    session = client.post(f"{BASE}/sessions", headers=auth_headers, json={}).json()["data"]

    sources = [{
        "title": "公开行业资料",
        "url": "https://example.com/industry",
        "snippet": "行业公开流程说明",
    }]
    monkeypatch.setattr(orchestrator, "search_web", lambda query: sources)

    captured = {}

    def fake_chat(_kwargs, messages, _tools):
        captured["system"] = messages[0]["content"]
        return {"content": "已参考公开资料，并继续向你确认企业口径。", "tool_calls": [], "usage": None}

    monkeypatch.setattr(llm_bridge, "chat", fake_chat)
    response = client.post(
        f"{BASE}/sessions/{session['id']}/chat",
        headers=auth_headers,
        json={"message": "检索公开的采购审批流程", "webSearch": True, "stream": False},
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["steps"][0]["tool"] == "web_search"
    assert data["steps"][0]["searchResults"] == sources
    assert "外部不可信内容" in captured["system"]
    assert "https://example.com/industry" in captured["system"]

    detail = client.get(
        f"{BASE}/sessions/{session['id']}", headers=auth_headers,
    ).json()["data"]
    assert detail["messages"][1]["steps"][0]["tool"] == "web_search"
