"""
数据管家编排循环 — LLM ⇄ n8n 受限工具集的对话回合

复用本体 agent 的 llm_bridge（中立消息协议）与 SSE 事件流约定：
  {"type": "meta",   "conversationId", "model"}
  {"type": "step",   "tool", "arguments", "summary", "durationMs", "error"?}
  {"type": "answer", "content", "touchedPipelineIds", "usage"}
  {"type": "error",  "message"}
  {"type": "done"}
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
from app.data_channel.steward import service
from app.data_channel.steward.models import (
    N8nPipeline, StewardConversation, StewardMessage, STATUS_ARCHIVED,
)
from app.data_channel.steward.node_catalog import catalog_digest
from app.data_channel.steward.toolkit import TOOL_DEFS, ToolRunner

logger = logging.getLogger(__name__)

_TOOL_RESULT_CAP = 9000    # 回填给 LLM 的单个工具结果长度上限（workflow JSON 较大）
_HISTORY_LIMIT = 12        # 携带的历史消息条数
_MAX_STEPS = 12            # 单回合最大工具步数


def _system_prompt(db: Session) -> str:
    records = (db.query(N8nPipeline)
               .filter(N8nPipeline.status != STATUS_ARCHIVED)
               .order_by(N8nPipeline.updated_at.desc()).limit(20).all())
    if records:
        lines = [f"- {r.name}（记录 {r.id}，{'已发布' if service.shadow_status(db, r) == 'published' else '未发布'}）"
                 for r in records]
        inventory = "当前受管流水线：\n" + "\n".join(lines)
    else:
        inventory = "当前还没有受管流水线。"

    return f"""你是 OntoPrompt 平台的「数据管家」——通过对话帮用户创建、编排基于 n8n 的数据流水线，替代手工画布编排。

# 你的世界观
平台把 n8n 作为数据流水线的执行引擎。你通过受限工具集操作 n8n 工作流；每个受管工作流对应流水线列表里的一条 n8n 流水线，生命周期只有「未发布 / 已发布」两态：
- 你负责两件事：**创建/纳管** n8n 工作流（录入为平台流水线，n8n 侧不激活），以及**编排完善未发布的流水线**。未发布的流水线不会被平台调度。
- **发布是用户的动作，且只发生在流水线列表的编辑向导里**（发布时激活 n8n 工作流、封版字段契约，此后可被任务池调度、产物入资产湖）。你没有发布/撤回发布的工具，绝不能声称流水线"已发布/已生效/已激活"。
- 已发布的流水线**编排封版**：你的修改会被拒绝。用户想改时，先引导 TA 在编辑向导中「撤回发布」，改完再重新发布。

{inventory}

# 平台数据流水线约定（重要）
1. **平台调度入口**：工作流应以 Webhook 触发器开头 —— parameters 建议 {{"httpMethod": "POST", "path": "ob-<流水线短名>", "responseMode": "lastNode"}}。平台运行该流水线时 POST 这个 webhook，并把**末节点输出的 items 作为行数据写入数据资产湖**（支持任务池的 overwrite/append/upsert 入库方式）。
2. 也可以再加一个 Schedule Trigger 让它在 n8n 内定时自跑，但注意：自跑产生的数据只有经平台触发的运行才会自动入湖；若需自跑回传，需用户提供平台 API Token，用 HTTP Request 节点回传，此时要向用户说明并索要 Token。
3. 末节点输出应是"一行一个 item、字段扁平"的表格形数据（用 Set/Code 节点整形），便于入湖后治理与映射。
4. 数据库/SaaS 节点的凭据无法由 API 创建：先告诉用户去 n8n 界面配置凭据，再在节点里引用凭据名。

# 常用节点速查（type / typeVersion）
{catalog_digest()}

# 行为准则
1. 先查证后动手：改任何工作流前先 get_workflow 看当前定义；开场用 steward_overview 了解现状。用户给了数据源网址/API 时，先用 probe_url 探测真实形态（是否 JSON、字段结构），再据此设计——你没有通用的浏览网页能力，probe_url 是唯一的对外探测手段；探测不到就如实说，并请用户提供样例数据或 API 文档。
2. 设计先行：创建/大改前，用一段简洁文字（可用列表描述节点链路）向用户确认设计，用户同意后再调工具落地。
3. 小步透明：每次工具调用后向用户说明做了什么、下一步是什么。工具报错时读错误信息自我修正，同一错误不要重复第三次。
4. 测试用 test_run：用户说「测一下 / 看看数据对不对 / 跑跑看」就直接调 test_run（它会临时激活工作流、POST 生产 Webhook、取回真实行数据再还原，不写资产湖）。**绝不要**用 probe_url 去打 Webhook，也**绝不要**让用户自己去 n8n 界面点 Execute 或手动 curl——那是你能自己做的事。试跑失败就用 get_execution 查执行详情定位原因，改好再试。
5. 收尾引导：编排完善、体检与试跑都通过后，明确告诉用户「到流水线列表，点这条流水线的编辑向导完成发布并启用」——发布不是你的动作，别揽也别漏。
6. 诚实边界：你不能创建凭据、不能发布/撤回发布、不能删除记录（归档/删除在界面由用户操作）。做不到的事直说。
7. 用中文回答，简洁、结构化。"""


def _summarize(name: str, result: dict) -> str:
    # 只有真正非空的 error 才算失败：test_run 等工具成功时也带 "error": None 键，
    # 用 "error" in result 会把成功误判成失败（摘要显示 "None"）
    if result.get("error"):
        return str(result["error"])[:120]
    if name == "steward_overview":
        n8n = result.get("n8n", {})
        p = result.get("pipelines", {})
        state = "可达" if n8n.get("reachable") else ("未配置" if not n8n.get("configured") else "不可达")
        return f"n8n {state} · 受管流水线 {p.get('total', 0)} 条"
    if name == "list_pipelines":
        return f"受管 {len(result.get('managed', []))} 条 · 未纳管 {len(result.get('unmanaged', []))} 条"
    if name == "get_workflow":
        s = (result.get("record") or {}).get("summary") or {}
        return f"{(result.get('workflow') or {}).get('name', '')} · {s.get('node_count', 0)} 个节点"
    if name == "create_workflow":
        r = result.get("record") or {}
        return f"已创建「{r.get('name', '')}」（{(r.get('summary') or {}).get('node_count', 0)} 个节点，未发布）"
    if name == "update_workflow":
        r = result.get("record") or {}
        return f"已更新「{r.get('name', '')}」"
    if name == "adopt_workflow":
        return f"已纳管「{(result.get('record') or {}).get('name', '')}」"
    if name == "check_workflow":
        issues = result.get("issues", [])
        errs = sum(1 for i in issues if i.get("level") == "error")
        return f"体检{'通过' if result.get('ok') else '未通过'}（{errs} 个错误 / {len(issues)} 项发现）"
    if name == "test_run":
        cols = result.get("columns") or []
        return f"试跑成功 · {result.get('rows', 0)} 行" + (f" · 列 {len(cols)} 个" if cols else "")
    if name == "list_executions":
        return f"{result.get('pipeline', '')} 最近 {len(result.get('executions', []))} 次执行"
    if name == "get_execution":
        return f"执行 {result.get('id', '')} · {result.get('status', '')}"
    if name == "list_node_types":
        return f"{len(result.get('nodes', []))} 种节点"
    if name == "probe_url":
        kind = result.get("kind", "")
        extra = result.get("title") or ("含样例行" if result.get("sampleRows") else "")
        return f"HTTP {result.get('status')} · {kind}" + (f" · {extra[:60]}" if extra else "")
    return "完成"


def run_steward_turn(db: Session, user, question: str,
                     conversation_id: Optional[str] = None,
                     model_id: Optional[str] = None) -> Iterator[dict]:
    """执行一个回合，yield 事件流。所有异常都转成 error 事件，绝不让 SSE 中途裸断。"""
    try:
        yield from _run(db, user, question, conversation_id, model_id)
    except Exception as e:  # noqa: BLE001
        logger.exception("数据管家回合失败")
        yield {"type": "error", "message": f"数据管家执行失败: {e}"}
    finally:
        yield {"type": "done"}


def _run(db: Session, user, question: str,
         conversation_id: Optional[str], model_id: Optional[str]) -> Iterator[dict]:
    cfg = select_llm_model_config(db, model_id=model_id)
    call_kwargs = llm_call_kwargs(cfg)
    if not call_kwargs:
        yield {"type": "error",
               "message": "尚未配置可用的 LLM。请先到「模型配置」添加一个对话模型（OpenAI 兼容或 Anthropic）。"}
        return

    user_id = getattr(user, "id", None)
    conv = None
    if conversation_id:
        conv = db.query(StewardConversation).filter(
            StewardConversation.id == conversation_id).first()
    if not conv:
        conv = StewardConversation(user_id=user_id,
                                   title=question.strip()[:60] or "新对话")
        db.add(conv)
        db.flush()

    history = (db.query(StewardMessage)
               .filter(StewardMessage.conversation_id == conv.id)
               .order_by(StewardMessage.created_at.desc())
               .limit(_HISTORY_LIMIT).all())[::-1]

    db.add(StewardMessage(conversation_id=conv.id, role="user", content=question))
    db.commit()

    yield {"type": "meta", "conversationId": conv.id, "model": call_kwargs.get("model")}

    messages: list[dict] = [{"role": "system", "content": _system_prompt(db)}]
    for m in history:
        if m.role in ("user", "assistant") and (m.content or "").strip():
            messages.append({"role": m.role, "content": m.content})
    messages.append({"role": "user", "content": question})

    runner = ToolRunner(db, user_id, conv.id)
    steps: list[dict] = []
    usage_total = {"inputTokens": 0, "outputTokens": 0}
    answer: Optional[str] = None

    for _ in range(_MAX_STEPS):
        try:
            resp = llm_bridge.chat(call_kwargs, messages, TOOL_DEFS)
        except llm_bridge.LLMError as e:
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
            except Exception as e:  # noqa: BLE001 — 工具内部意外不摧毁回合
                logger.exception("数据管家工具 %s 执行异常", tc["name"])
                result = {"error": f"工具内部错误: {e}"}
            duration = int((time.time() - started) * 1000)

            step = {"tool": tc["name"], "arguments": tc.get("arguments") or {},
                    "summary": _summarize(tc["name"], result), "durationMs": duration}
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
        answer = f"已达到单回合最大步数（{_MAX_STEPS} 步）仍未完成。请把任务拆小一点再继续。"

    _persist_assistant(db, conv, answer or "", steps, runner, call_kwargs, usage_total)
    yield {"type": "answer", "content": answer,
           "touchedPipelineIds": runner.touched_pipeline_ids,
           "usage": usage_total}


def _persist_assistant(db: Session, conv: StewardConversation, content: str,
                       steps: list, runner: ToolRunner, call_kwargs: dict,
                       usage: dict) -> None:
    db.add(StewardMessage(
        conversation_id=conv.id, role="assistant", content=content,
        steps=steps, touched_pipeline_ids=runner.touched_pipeline_ids,
        model=call_kwargs.get("model"), token_usage=usage,
    ))
    conv.updated_at = datetime.now(timezone.utc)
    db.commit()
