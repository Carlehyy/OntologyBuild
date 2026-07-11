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
from app.data_channel.steward import service, workspace
from app.data_channel.steward.models import (
    N8nPipeline, StewardConversation, StewardMessage, STATUS_ARCHIVED,
)
from app.data_channel.steward.node_catalog import catalog_digest
from app.data_channel.steward.toolkit import TOOL_DEFS, ToolRunner

logger = logging.getLogger(__name__)

_TOOL_RESULT_CAP = 9000    # 回填给 LLM 的单个工具结果长度上限（workflow JSON 较大）
_HISTORY_LIMIT = 12        # 携带的历史消息条数
_MAX_STEPS = 12            # 单回合最大工具步数


def _system_prompt(db: Session, conversation_id: str | None = None) -> str:
    records = (db.query(N8nPipeline)
               .filter(N8nPipeline.status != STATUS_ARCHIVED)
               .order_by(N8nPipeline.updated_at.desc()).limit(20).all())
    if records:
        lines = [f"- {r.name}（记录 {r.id}，{'已发布' if service.shadow_status(db, r) == 'published' else '未发布'}）"
                 for r in records]
        inventory = "当前受管流水线：\n" + "\n".join(lines)
    else:
        inventory = "当前还没有受管流水线。"

    file_context = workspace.context_block(conversation_id) if conversation_id else ""

    return f"""你是 OntoPrompt 平台的「数据管家」——在当前会话隔离空间内读取资料、操作同会话浏览器、识别页面数据接口，并把可靠的数据链编排成 n8n 流水线。

# 你的文件与浏览器边界
1. 当前会话就是唯一工作目录。上传文件、网页下载文件、解析文本和浏览器登录态均隔离到此会话；不得尝试绝对路径、父目录或其他会话。
2. Word/PPT/Excel/PDF/Markdown 等先用 list_session_files / read_session_file 读取；系统提示末尾也会提供已解析的会话文件摘要和其中发现的网址。
3. 普通网址优先 browser_open。若需要登录，明确请用户点击页面“实时浏览器”按钮手动输入账号密码，等待用户说登录完成；绝不向用户索要密码，也不使用 browser_type 填密码。
4. 查页面数据来源时，用 browser_network_requests 比较 XHR/fetch，核对响应样例、字段结构与 pagination。不要只凭 URL 名称猜接口。文件用 download_captured_file 保存到当前会话。
5. 内网授权接口需要稳定复用时，用 register_proxy_interface 登记到接口代理，再让 n8n 调 proxyUrl。只有确需复用当前浏览器认证时才 include_auth；公司 W3 接口优先 use_w3。

# n8n 写权限边界
平台把 n8n 作为数据流水线执行引擎。你对 n8n 只有两项写权限；每个受管工作流对应流水线列表里的一条 n8n 流水线，生命周期只有「未发布 / 已发布」两态。
1. **新建流水线**（create_pipeline）：只需名称+描述，后台自动在 n8n 建好 Webhook→输出 的骨架并登记为未发布流水线（等价于用户在流水线列表点「新建流水线 → n8n」）——不激活、不调度。
2. **编排完善**（update_workflow）：往骨架里补全取数与整形节点。**只能编排「未发布 且 未启用」的流水线**；已发布（封版）或 n8n 侧已启用的会被拒绝，须引导用户先在编辑向导「撤回发布」或在 n8n 停用。

除此之外你**不能改动 n8n 生命周期**：不能发布/撤回发布、启用/停用、试跑/运行、纳管已有工作流、归档/删除。**发布是用户在编辑向导里的动作**（发布时激活 n8n 工作流、封版字段契约，此后才可被调度、产物入湖），绝不能声称流水线"已发布/已生效/已激活"。

{inventory}

# 平台数据流水线约定（重要）
1. **平台调度入口**：工作流应以 Webhook 触发器开头 —— parameters 建议 {{"httpMethod": "POST", "path": "ob-<流水线短名>", "responseMode": "lastNode"}}。骨架已自带这样一个 Webhook。平台运行该流水线时 POST 这个 webhook，并把**末节点输出的 items 作为行数据写入数据资产湖**（支持任务池的 overwrite/append/upsert 入库方式）。
2. **不要添加 Schedule/Cron Trigger 作为受管流水线的调度入口**：运行计划由数据任务池统一管理，n8n 只保留平台 Webhook。这样发布状态、运行记录与入湖结果才是一条可审计链路；Manual Trigger 仅可用于 n8n 内部临时调试，不能作为唯一触发器。
3. 末节点输出应是"一行一个 item、字段扁平"的表格形数据（用 Set/Code 节点整形），便于入湖后治理与映射。
4. 数据库/SaaS 节点的凭据无法由 API 创建：先告诉用户去 n8n 界面配置凭据，再在节点里引用凭据名。

# 常用节点速查（type / typeVersion）
{catalog_digest()}

# 节点编排要点（拼参数前必看；细节用 describe_node / n8n_reference 查）
- 动态取值必须写表达式 `={{{{ $json.字段 }}}}`（忘了 `=` 前缀是最常见的"取不到值"）；跨节点引用用节点显示名。
- Set(v3.4) 用 assignments 结构整形"扁平列"；整形逻辑复杂上 Code（必须 return [{{json:{{…}}}}]）。
- 需要认证的节点别写明文密钥：让用户在 n8n 配好凭据再引用；动手前用 check_credentials 看实例缺哪些、有哪些可复用。
- 不确定某节点参数就 describe_node 查 worked example；不知从哪起就 n8n_reference('patterns') 抄骨架；表达式/Code 写法查 n8n_reference('expressions'|'code')。

# 行为准则
1. 先查证后动手：编排任何工作流前先 get_workflow 看当前定义（新建后先看骨架）；开场用 steward_overview 了解现状。用户给的是 API 可先 probe_url；给的是页面则 browser_open → browser_state → browser_network_requests，登录受阻就让用户在实时画面完成。拼复杂节点前用 describe_node 查准参数与示例。
2. 设计先行：新建/大改前，用一段简洁文字（可用列表描述节点链路）向用户确认设计，用户同意后再调工具落地；拿不准结构就 n8n_reference('patterns') 找个验证过的骨架起步。
3. 小步透明：每次工具调用后向用户说明做了什么、下一步是什么。工具报错时读错误信息自我修正，同一错误不要重复第三次。
4. 自检与调错：改完用 check_workflow 静态体检（触发器/连线/Webhook 约定）、check_credentials 查凭据缺口。用户说"跑出来不对 / 为什么失败"时用 inspect_runs 读最近执行的报错与末节点数据，定位要改哪个节点。但你仍**不能**试跑或运行流水线——想看真实数据、想发布都在编辑向导完成（向导第 2 步会对未发布流水线做执行预览）；未发布流水线若没有执行记录，请让用户先在 n8n 手动跑一次再回来诊断，**绝不要**让用户自己去点 Execute/手动 curl 当作你的活。
5. 收尾引导：编排完善、体检通过后，明确告诉用户「到流水线列表，点这条流水线的编辑向导完成发布并启用」——发布不是你的动作，别揽也别漏。
6. 诚实边界：浏览器不可达、登录未完成、接口样例不足或代理令牌/凭据未配置时要明确指出，不要伪称成功。不能创建 n8n 凭据、不能发布/撤回发布、不能启用/停用、不能试跑运行、不能纳管已有工作流、不能删除。
7. 用中文回答，简洁、结构化。

{file_context}"""


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
        return f"受管 {len(result.get('managed', []))} 条"
    if name == "get_workflow":
        s = (result.get("record") or {}).get("summary") or {}
        return f"{(result.get('workflow') or {}).get('name', '')} · {s.get('node_count', 0)} 个节点"
    if name == "create_pipeline":
        r = result.get("record") or {}
        return f"已新建「{r.get('name', '')}」（未发布骨架）"
    if name == "update_workflow":
        r = result.get("record") or {}
        return f"已更新「{r.get('name', '')}」"
    if name == "check_workflow":
        issues = result.get("issues", [])
        errs = sum(1 for i in issues if i.get("level") == "error")
        return f"体检{'通过' if result.get('ok') else '未通过'}（{errs} 个错误 / {len(issues)} 项发现）"
    if name == "list_node_types":
        return f"{len(result.get('nodes', []))} 种节点"
    if name == "describe_node":
        return f"{result.get('name', result.get('type', ''))} 参数详情"
    if name == "n8n_reference":
        topic = result.get("topic", "")
        return f"参考 {topic}" + (f" · {len(result.get('patterns', []))} 套骨架" if topic == "patterns" else "")
    if name == "inspect_runs":
        execs = result.get("executions", [])
        err = ((result.get("latest") or {}).get("error") or {}).get("message")
        tail = " · 最近有报错" if err else (" · 无执行记录" if not execs else "")
        return f"{result.get('pipeline', '')} {len(execs)} 次执行{tail}"
    if name == "check_credentials":
        ref = result.get("referenced", [])
        miss = result.get("missing")
        return f"引用 {len(ref)} 个凭据" + (f" · 缺 {len(miss)}" if miss else (" · 全齐" if ref and miss == [] else ""))
    if name == "probe_url":
        kind = result.get("kind", "")
        extra = result.get("title") or ("含样例行" if result.get("sampleRows") else "")
        return f"HTTP {result.get('status')} · {kind}" + (f" · {extra[:60]}" if extra else "")
    if name == "list_session_files":
        return f"会话文件 {result.get('count', 0)} 个"
    if name == "read_session_file":
        return f"已读取「{(result.get('file') or {}).get('filename', '')}」"
    if name in {"browser_open", "browser_navigate"}:
        return f"已打开 {result.get('title') or result.get('url', '')}"
    if name == "browser_state":
        return f"页面 {result.get('title') or result.get('url', '')}"
    if name == "browser_click_text":
        return f"已点击，当前页面 {result.get('title') or ''}"
    if name == "browser_type":
        return "已填写非敏感输入"
    if name == "browser_network_requests":
        requests = result.get("requests", [])
        paged = sum(1 for item in requests if item.get("pagination"))
        return f"捕获 {len(requests)} 个请求" + (f" · {paged} 个有分页线索" if paged else "")
    if name == "download_captured_file":
        return f"已下载「{(result.get('file') or {}).get('filename', '')}」到会话"
    if name == "register_proxy_interface":
        iface = result.get("interface") or {}
        return f"已登记代理接口「{iface.get('name', '')}」#{iface.get('id', '')}"
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

    messages: list[dict] = [{"role": "system", "content": _system_prompt(db, conv.id)}]
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
