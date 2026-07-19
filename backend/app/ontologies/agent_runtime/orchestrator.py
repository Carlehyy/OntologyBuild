"""
编排循环 (Orchestrator) — LLM ⇄ 受限工具集的 agent 回合

一次用户提问 = 一个回合（turn）。回合内 LLM 最多走 profile.max_steps 步工具
调用，每步产出一个可流式推送的事件；回合结束把完整轨迹（steps / citations /
proposals / token 用量）持久化为 AgentMessage —— 审计与回放的最小单元。

事件流协议（SSE 每条 data 一个 JSON）：
  {"type": "meta",   "conversationId", "model"}
  {"type": "step",   "tool", "arguments", "summary", "durationMs", "result", "error"?}
  {"type": "answer", "content", "citations", "proposals", "usage"}
  {"type": "error",  "message"}
  {"type": "done"}

step.result 是工具原始输出（过大自动截断），供前端「展开查看工具输入/输出」；
它随 steps 一起持久化，历史消息回放时同样可查。
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Iterator, Optional

from sqlalchemy.orm import Session

from app.model_configs.selector import select_llm_model_config, llm_call_kwargs
from app.ontologies.agent_runtime import llm_bridge
from app.ontologies.agent_runtime.boundary import ToolError, build_scope
from app.ontologies.agent_runtime.models import AgentConversation, AgentMessage
from app.ontologies.agent_runtime.toolkit import TOOL_DEFS, ToolRunner

logger = logging.getLogger(__name__)

_TOOL_RESULT_CAP = 8000     # 回填给 LLM 的单个工具结果长度上限
_RESULT_DISPLAY_CAP = 6000  # 推送/持久化给前端展示的工具结果长度上限
_GRAPH_DISPLAY_CAP = 18000  # 图谱步骤需保留结构供前端联动，仍限制单步持久化体积
_HISTORY_LIMIT = 10         # 逐字带入的最近消息条数
_HISTORY_DIGEST_SCAN = 40   # 为「对话回顾」额外回溯的条数（滑出窗口的更早提问压成摘要）


# 图表可视化能力（普通字符串，含 JSON 花括号，勿并入 f-string）
_CHARTS_GUIDE = """# 图表可视化
当工具查回的数据适合可视化时，你可以在回答里输出一个图表代码块，前端会把它渲染成交互式图表。
用法：另起一行，写一个语言标记为 chart 的围栏代码块，块内是一段 JSON：

```chart
{"type": "bar", "title": "各状态订单数", "data": [{"label": "待付款", "value": 12}, {"label": "已发货", "value": 34}]}
```

字段说明：
- type：bar 柱状（分类对比）| line 折线（时间趋势）| area 面积 | pie 饼图（占比构成）
- data：数组，每项 {"label": 分类或时间, "value": 数值}
- title / xLabel / yLabel：可选
- 多序列（可选）：加 "series": [{"key": "销量", "name": "销量"}, {"key": "利润", "name": "利润"}]，
  此时每个 data 项用这些 key 提供多个数值，如 {"label": "1月", "销量": 100, "利润": 20}

准则：
1. aggregate_objects 的统计结果，平台会依据真实数据自动渲染图表，你不必再为它输出 chart 代码块；仅当要可视化聚合之外的数据（如按历史事实画趋势）时才手写 chart 块。
2. 图表数据必须来自工具返回的真实结果，禁止编造数字。
3. 图表是文字结论的补充，仍要用文字说清关键结论，不能只甩一个图。
4. 数据点很少（1-2 个）、或本就是明细列表时，用表格或文字即可，不要为画图而画图。
5. 分类分布/对比优先 bar 或 pie；随时间或历史变化的趋势用 line 或 area。"""


def _system_prompt(scope) -> str:
    extra = (scope.profile.system_prompt_extra or "").strip()
    extra_block = ("\n# 附加指令\n" + extra) if extra else ""
    return f"""你是本体「{scope.ontology.name}」上的业务智能体，运行在 OntoPrompt 平台的授权边界内。

# 你的世界观
下面的本体技能卡是你对业务世界的全部认知。你只能通过平台提供的工具与这些对象、链接、动作交互；不存在其它数据源，不要假设本体之外还有表或字段。

{scope.skill_card()}

# 行为准则
1. 先查证后回答：任何涉及数据的结论都必须来自工具返回结果，禁止编造，也不要用本体之外的常识或训练记忆去补充业务事实。工具结果不足以回答时，明确说明还缺什么、需要用户补充或授权什么，而不是猜。多个结果相互矛盾时，指出差异并优先采信更具体、更有出处的来源。工具返回 partial=true 时，说明这是基于部分数据的估计。
2. 用中文回答，简洁、结构化；提到具体对象时带上它的标签（如订单号、名称），便于用户核对。
3. 统计类问题（总共/平均/最多/分布）优先用 aggregate_objects，不要拉列表自己数；已知关系序列的多跳问题用 traverse_path；明确询问两个具体实例之间有哪些路径时，用 find_paths，必要时先 search_objects 确认两端 id。
4. 用户问「为什么是这个值 / 谁改的 / 什么时候变的」，用 get_object_history 给出带出处的回答。
5. 需要修改数据时：只能用 propose_action 做预演并生成提案，真实执行由用户在界面上确认。绝不能声称你已经完成了修改。
6. 工具报错时，阅读错误信息里给出的可用选项，修正参数后重试；同一错误不要重复第三次。
7. 用户询问某字段拟议变化会波及哪些对象时，先用 analyze_change_impact 展示直接/间接关系可达范围。它是只读模拟，不代表确定业务因果；除非工具返回了受治理的因果规则，否则必须称为“关联范围”，不得声称这些对象一定会改变。
8. 管理哨兵时，只能管理来源为 assistant_dynamic 的动态哨兵。发布版本内置哨兵只读且绝不能生成其编辑、启停或删除提案。所有动态哨兵变更必须先用 propose_dynamic_sentinel_change 生成提案，等待用户在界面确认；创建后默认停用，必须通过当前发布版全量试跑才能启用。

{_CHARTS_GUIDE}
{extra_block}"""


def _summarize(name: str, result: dict) -> str:
    """步骤的一句话摘要，给前端时间线用。"""
    if "error" in result:
        return str(result["error"])[:120]
    if name == "search_objects":
        return f"{result.get('objectType', '')} 命中 {result.get('total', 0)} 条，返回 {result.get('returned', 0)} 条"
    if name == "get_object":
        return f"{result.get('objectType', '')} · {len(result.get('links', []))} 组关联"
    if name == "traverse_links":
        return f"{result.get('from', '')} —{result.get('linkType', '')}→ {result.get('returned', 0)} 个对象"
    if name == "traverse_path":
        return f"{result.get('from', '')} 经 {len(result.get('path') or [])} 跳 → {result.get('returned', 0)} 个终点对象"
    if name == "find_paths":
        return f"{result.get('sourceLabel', '')} → {result.get('targetLabel', '')}，找到 {len(result.get('paths') or [])} 条路径"
    if name == "analyze_change_impact":
        summary = result.get("summary") or {}
        return f"关联影响预演：直接 {summary.get('direct', 0)}，间接 {summary.get('indirect', 0)}"
    if name == "aggregate_objects":
        if "groups" in result:
            return f"{result.get('objectType', '')} 按 {result.get('groupBy')} 分 {len(result['groups'])} 组"
        return f"{result.get('objectType', '')} {result.get('metric')} = {result.get('value')}"
    if name == "get_object_history":
        return f"{result.get('instance', '')} 的 {len(result.get('facts', []))} 条事实"
    if name == "list_actions":
        return f"{len(result.get('actions', []))} 个可用动作"
    if name == "list_dynamic_sentinels":
        return f"{len(result.get('sentinels', []))} 个助手动态哨兵"
    if name == "propose_dynamic_sentinel_change":
        p = result.get("proposal") or {}
        return f"动态哨兵{p.get('operation', '')}提案：{p.get('sentinelName', '')}"
    if name == "propose_action":
        p = result.get("proposal") or {}
        ok = p.get("status") == "success"
        return f"提案「{p.get('actionName', '')}」预演{'通过' if ok else '未通过'}（{len(p.get('effects') or [])} 项效果）"
    return "完成"


def _compact_graph_node(node: dict) -> dict:
    """移除仅用于详情面板的重字段，保留图谱渲染和节点定位所需字段。"""
    keys = (
        "id", "entityId", "kind", "label", "secondaryLabel", "objectTypeId",
        "objectTypeLabel", "instanceId", "propertyName", "propertyType", "value",
    )
    return {key: node[key] for key in keys if key in node}


def _compact_graph_edge(edge: dict) -> dict:
    keys = ("id", "entityId", "kind", "source", "target", "label", "linkTypeId")
    return {key: edge[key] for key in keys if key in edge}


def _compact_path(path: dict) -> dict:
    keys = ("nodeIds", "edgeIds", "hops")
    return {key: path[key] for key in keys if key in path}


def _graph_display_result(result: dict) -> dict:
    """为助手联动保留可解析图结构，同时按相关性限制消息与数据库体积。"""
    node_limit, edge_limit, impact_limit = 80, 120, 60
    compact = {key: value for key, value in result.items()
               if key not in {"nodes", "edges", "paths", "impacts"}}
    compact["nodes"] = [_compact_graph_node(node)
                        for node in (result.get("nodes") or [])[:node_limit]]
    compact["edges"] = [_compact_graph_edge(edge)
                        for edge in (result.get("edges") or [])[:edge_limit]]
    if "paths" in result:
        compact["paths"] = [_compact_path(path) for path in (result.get("paths") or [])[:5]]
    if "impacts" in result:
        impacts = []
        for item in (result.get("impacts") or [])[:impact_limit]:
            retained = {key: item[key] for key in (
                "instanceId", "label", "objectType", "depth", "classification", "certainty",
            ) if key in item}
            retained["path"] = _compact_path(item.get("path") or {})
            impacts.append(retained)
        compact["impacts"] = impacts

    original_counts = {
        "nodes": len(result.get("nodes") or []),
        "edges": len(result.get("edges") or []),
        "impacts": len(result.get("impacts") or []),
    }
    displayed_counts = {
        "nodes": len(compact["nodes"]),
        "edges": len(compact["edges"]),
        "impacts": len(compact.get("impacts") or []),
    }
    compact["visualizationTruncated"] = original_counts != displayed_counts
    compact["visualizationCounts"] = {
        "available": original_counts,
        "displayed": displayed_counts,
    }

    # 极密图谱继续按层级收紧，但始终保留合法、可直接渲染的 JSON，而不是字符串预览。
    payload = json.dumps(compact, ensure_ascii=False, default=str)
    if len(payload) > _GRAPH_DISPLAY_CAP:
        compact["nodes"] = compact["nodes"][:40]
        compact["edges"] = compact["edges"][:60]
        if "impacts" in compact:
            compact["impacts"] = compact["impacts"][:30]
        compact["visualizationTruncated"] = True
        compact["visualizationCounts"]["displayed"] = {
            "nodes": len(compact["nodes"]),
            "edges": len(compact["edges"]),
            "impacts": len(compact.get("impacts") or []),
        }
    if len(json.dumps(compact, ensure_ascii=False, default=str)) > _GRAPH_DISPLAY_CAP:
        compact["nodes"] = compact["nodes"][:32]
        compact["edges"] = compact["edges"][:48]
        if "impacts" in compact:
            compact["impacts"] = compact["impacts"][:24]
        compact["visualizationCounts"]["displayed"] = {
            "nodes": len(compact["nodes"]),
            "edges": len(compact["edges"]),
            "impacts": len(compact.get("impacts") or []),
        }
    return compact


def _display_result(result: dict):
    """给前端「查看工具输出」用的结果；过大则截断，避免消息体与 DB 膨胀。"""
    try:
        payload = json.dumps(result, ensure_ascii=False, default=str)
    except Exception:  # noqa: BLE001
        return {"_note": "结果无法序列化展示"}
    if len(payload) <= _RESULT_DISPLAY_CAP:
        return result
    if result.get("kind") in {"path", "impact"}:
        return _graph_display_result(result)
    return {
        "_truncated": True,
        "_note": f"结果较大（约 {len(payload)} 字符），此处仅展示前 {_RESULT_DISPLAY_CAP} 字符预览。",
        "preview": payload[:_RESULT_DISPLAY_CAP],
    }


def _history_digest(older: list) -> Optional[str]:
    """把滑出窗口的更早消息压成一段轻量「对话回顾」——只保留早前的提问，
    维持长对话里的指代与延续。不额外调用 LLM、不落新表、不跨会话，纯会话内续接。"""
    qs = [(m.content or "").strip() for m in older
          if m.role == "user" and (m.content or "").strip()]
    if not qs:
        return None
    lines = "\n".join(f"- {q[:80]}" for q in qs[-15:])
    return "# 对话回顾（更早轮次的提问，帮助理解指代与延续；不必逐条重新回答）\n" + lines


def run_agent_turn(db: Session, ontology_id: str, user, question: str,
                   conversation_id: Optional[str] = None,
                   model_id: Optional[str] = None,
                   release_id: Optional[str] = None) -> Iterator[dict]:
    """执行一个回合，yield 事件流。所有异常都转成 error 事件，绝不让 SSE 中途裸断。"""
    try:
        yield from _run(
            db, ontology_id, user, question, conversation_id, model_id, release_id)
    except GeneratorExit:
        # 浏览器刷新/离开会主动关闭 SSE。生成器关闭后不能在 finally 中继续 yield，
        # 否则 Python 会抛出 ``generator ignored GeneratorExit`` 并污染服务日志。
        return
    except Exception as e:  # noqa: BLE001
        logger.exception("agent 回合失败")
        yield {"type": "error", "message": f"智能体执行失败: {e}"}
    yield {"type": "done"}


def _run(db: Session, ontology_id: str, user, question: str,
         conversation_id: Optional[str], model_id: Optional[str],
         release_id: Optional[str]) -> Iterator[dict]:
    try:
        ontology, profile, scope = build_scope(
            db, ontology_id, release_id=release_id)
    except ToolError as e:
        yield {"type": "error", "message": str(e)}
        return

    if not profile.enabled:
        yield {"type": "error", "message": "该本体的智能体已被停用，请联系管理员在边界配置中启用。"}
        return

    cfg = select_llm_model_config(db, model_id=model_id or profile.default_model_id)
    call_kwargs = llm_call_kwargs(cfg)
    if not call_kwargs:
        yield {"type": "error",
               "message": "尚未配置可用的 LLM。请先到「模型配置」添加一个对话模型（OpenAI 兼容或 Anthropic）。"}
        return

    user_id = getattr(user, "id", None)
    conv = None
    if conversation_id:
        conv = db.query(AgentConversation).filter(
            AgentConversation.id == conversation_id,
            AgentConversation.ontology_id == ontology_id,
            AgentConversation.user_id == user_id,
            AgentConversation.ontology_release_id == scope.release_id,
        ).first()
    if not conv:
        conv = AgentConversation(
                                 ontology_id=ontology_id,
                                 ontology_release_id=scope.release_id,
                                 user_id=user_id,
                                 title=question.strip()[:60] or "新对话")
        db.add(conv)
        db.flush()

    recent = (db.query(AgentMessage)
              .filter(AgentMessage.conversation_id == conv.id)
              .order_by(AgentMessage.created_at.desc())
              .limit(_HISTORY_DIGEST_SCAN).all())[::-1]
    verbatim = recent[-_HISTORY_LIMIT:]
    older = recent[:-_HISTORY_LIMIT] if len(recent) > _HISTORY_LIMIT else []

    db.add(AgentMessage(conversation_id=conv.id, role="user", content=question))
    db.commit()

    yield {"type": "meta", "conversationId": conv.id,
           "model": call_kwargs.get("model"), "releaseId": scope.release_id}

    messages: list[dict] = [{"role": "system", "content": _system_prompt(scope)}]
    digest = _history_digest(older)
    if digest:
        messages.append({"role": "system", "content": digest})
    for m in verbatim:
        if m.role in ("user", "assistant") and (m.content or "").strip():
            messages.append({"role": m.role, "content": m.content})
    messages.append({"role": "user", "content": question})

    runner = ToolRunner(db, scope)
    steps: list[dict] = []
    usage_total = {"inputTokens": 0, "outputTokens": 0}
    answer: Optional[str] = None
    max_steps = max(1, profile.max_steps or 8)

    for _ in range(max_steps):
        try:
            resp = llm_bridge.chat(call_kwargs, messages, TOOL_DEFS)
        except llm_bridge.LLMError as e:
            answer = None
            yield {"type": "error", "message": str(e)}
            _persist_assistant(db, conv, f"[执行中断] {e}", steps, runner, call_kwargs, usage_total)
            return

        for k in usage_total:
            if resp.get("usage") and resp["usage"].get(k):
                usage_total[k] += resp["usage"][k]

        if not resp["tool_calls"]:
            answer = resp.get("content") or "（模型未给出回答）"
            break

        messages.append({"role": "assistant", "content": resp.get("content"),
                         "tool_calls": resp["tool_calls"]})
        for tc in resp["tool_calls"]:
            started = time.time()
            try:
                result = runner.run(tc["name"], tc.get("arguments") or {})
            except ToolError as e:
                result = {"error": str(e)}
            except Exception as e:  # noqa: BLE001 — 工具内部意外不摧毁回合
                logger.exception("工具 %s 执行异常", tc["name"])
                result = {"error": f"工具内部错误: {e}"}
            duration = int((time.time() - started) * 1000)

            step = {"tool": tc["name"], "arguments": tc.get("arguments") or {},
                    "summary": _summarize(tc["name"], result), "durationMs": duration,
                    "result": _display_result(result)}
            if "error" in result:
                step["error"] = result["error"]
            steps.append(step)
            yield {"type": "step", **step}

            payload = json.dumps(result, ensure_ascii=False, default=str)
            if len(payload) > _TOOL_RESULT_CAP:
                payload = payload[:_TOOL_RESULT_CAP] + '…（结果过长已截断，请缩小查询范围）"}'
            messages.append({"role": "tool", "tool_call_id": tc["id"],
                             "name": tc["name"], "content": payload})
    else:
        answer = f"已达到单回合最大步数（{max_steps} 步）仍未得出结论。请把问题拆小一点，或在边界配置中调大 max_steps。"

    _persist_assistant(db, conv, answer or "", steps, runner, call_kwargs, usage_total)
    yield {"type": "answer", "content": answer,
           "citations": runner.citations, "proposals": runner.proposals,
           "usage": usage_total}


def _persist_assistant(db: Session, conv: AgentConversation, content: str,
                       steps: list, runner: ToolRunner, call_kwargs: dict,
                       usage: dict) -> None:
    db.add(AgentMessage(
        conversation_id=conv.id, role="assistant", content=content,
        steps=steps, citations=runner.citations, proposals=runner.proposals,
        model=call_kwargs.get("model"), token_usage=usage,
    ))
    conv.updated_at = datetime.now(timezone.utc)
    db.commit()
