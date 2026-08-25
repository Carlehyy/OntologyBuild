"""评估引擎 — OpenJudge 优先，内置降级引擎兜底。

主路径：安装了 py-openjudge（Dockerfile 以 --no-deps 精确安装，仅启用本模块
实际用到的轻量依赖）时，直接使用 OpenJudge 官方评分器：
  RelevanceGrader / HallucinationGrader / InstructionFollowingGrader（通用层）
  TrajectoryComprehensiveGrader（轨迹层）
  ActionLoopDetectionGrader 的确定性算法（代码型，本地重实现零成本）

兜底路径：py-openjudge 不可导入时（如依赖漂移），用平台 llm_gateway 以同一套
评判标准（对齐 OpenJudge 公开 rubric）做 LLM-as-Judge，保证功能不中断。

两个引擎输出同构：{dim_key: {"raw": 原始分, "reason": 评判理由}}。
"""
from __future__ import annotations

import asyncio
import difflib
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

_OPENJUDGE_AVAILABLE = False

try:  # pragma: no cover - 视环境而定
    from openjudge.graders.agent.action.action_loop import ActionLoopDetectionGrader
    from openjudge.graders.agent.trajectory.trajectory_comprehensive import (
        TrajectoryComprehensiveGrader,
    )
    from openjudge.graders.common.hallucination import HallucinationGrader
    from openjudge.graders.common.instruction_following import InstructionFollowingGrader
    from openjudge.graders.common.relevance import RelevanceGrader
    from openjudge.graders.schema import GraderError
    from openjudge.models.openai_chat_model import OpenAIChatModel

    _OPENJUDGE_AVAILABLE = True
except Exception as _import_error:  # pragma: no cover
    GraderError = None
    OpenAIChatModel = None
    logger.warning("py-openjudge 不可用，助手评估将使用内置降级引擎：%s", _import_error)


def openjudge_available() -> bool:
    return _OPENJUDGE_AVAILABLE


# ---------------------------------------------------------------- 内置降级评判


_FALLBACK_PROMPTS = {
    "relevance": (
        "你是专业的数据标注员。判断「答复」是否切题回应了「用户问题」并给出有效内容。"
        "只依据内容本身打分：5=完全切题且信息有效；3=部分相关但未充分满足；1=完全答非所问。"
        "严格输出 JSON：{\"score\": 1-5 的整数, \"reason\": \"不超过80字的中文理由\"}"
    ),
    "hallucination": (
        "你是专业的数据标注员。检查「答复」是否包含无依据的编造信息（虚构实体、数据、结论）。"
        "若提供了工具观察/上下文，必须以它们为事实基准。"
        "5=完全有据可查；3=存在少量无法核验的表述；1=大量编造。"
        "严格输出 JSON：{\"score\": 1-5 的整数, \"reason\": \"不超过80字的中文理由\"}"
    ),
    "instruction_following": (
        "你是专业的数据标注员。检查「答复」是否遵循了「用户问题」中的显式指令、格式与约束。"
        "5=全部满足；3=遗漏或偏离部分要求；1=明显违背指令。"
        "严格输出 JSON：{\"score\": 1-5 的整数, \"reason\": \"不超过80字的中文理由\"}"
    ),
    "trajectory": (
        "你是 Agent 行为分析专家。下面是一次智能体执行的完整消息轨迹（含工具调用与结果）。"
        "评估整条执行路径的质量：是否选择了合理工具、有无绕路 / 冗余重复 / 死循环、"
        "最终答复是否建立在工具结果之上。"
        "严格输出 JSON：{\"score\": 0 或 1（1=路径合格高效，0=存在问题）, "
        "\"reason\": \"不超过100字的中文理由，指出具体步骤问题\"}"
    ),
}


def _gateway_judge(llm_kwargs: dict, system: str, user: str) -> str | None:
    """经平台 llm_gateway 做一次 LLM 评判（兼容全部 provider）。"""
    from app.model_configs.llm_gateway import chat as llm_chat

    result = llm_chat(llm_kwargs, [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ], [])
    return result.get("content")


def _parse_judge_json(content: str | None) -> dict:
    """容错解析评委输出的 JSON。"""
    if not content:
        return {}
    from json_repair import repair_json

    try:
        data = json.loads(repair_json(content))
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def _fallback_trace_text(trace) -> tuple[str, str]:
    """组装降级评判的用户提示词，返回 (dim 无关的轨迹证据, 全量轨迹文本)。"""
    evidence = ""
    if getattr(trace, "actions", None):
        steps_text = "\n".join(
            f"- {a['name']}({'失败' if a.get('failed') else '成功'}): {a.get('preview', '')[:200]}"
            for a in trace.actions[-12:]
        )
        evidence = f"\n\n本次会话的工具调用记录（可作为事实依据）：\n{steps_text}"
    transcript = "\n".join(
        f"[{m['role']}] {str(m.get('content') or '')[:1500]}"
        for m in trace.openai_messages[-40:]
    )
    return evidence, transcript


def fallback_score_dim(dim_key: str, llm_kwargs: dict, trace) -> dict:
    """单维度内置降级评判（对齐 OpenJudge 公开 rubric），同步执行。"""
    if dim_key == "action_loop":
        return {"raw": detect_action_loop(trace.actions), "reason": ""}
    if dim_key == "trajectory":
        _, transcript = _fallback_trace_text(trace)
        content = _gateway_judge(llm_kwargs, _FALLBACK_PROMPTS[dim_key],
                                 f"用户问题：{trace.query}\n\n完整轨迹：\n{transcript}")
    else:
        evidence, _ = _fallback_trace_text(trace)
        content = _gateway_judge(
            llm_kwargs, _FALLBACK_PROMPTS[dim_key],
            f"用户问题：\n{trace.query}\n\n助手答复：\n{trace.response}{evidence}",
        )
    data = _parse_judge_json(content)
    raw = data.get("score")
    try:
        raw = float(raw)
    except (TypeError, ValueError):
        raw = None
    return {"raw": raw, "reason": str(data.get("reason") or "")}


# ---------------------------------------------------------------- OpenJudge


class OpenJudgeEngine:
    """基于 OpenJudge 官方评分器的评估引擎。

    官方评分器依赖 OpenAI 结构化输出（chat.completions.parse）；部分
    OpenAI 兼容端点（如 DeepSeek / dashscope 非 qwen 模型）不支持该能力，
    评分器会返回 GraderError。此时自动降级到平台 llm_gateway 的同标准
    LLM 评判，保证每个维度都产出分数；降级会在理由中注明。
    """

    name = "openjudge"

    def __init__(self, model_config) -> None:
        from app.model_configs.selector import llm_call_kwargs

        kwargs = llm_call_kwargs(model_config)
        if not kwargs:
            raise ValueError("所选 judge 模型配置缺少模型名或 API Key，请到「模型配置」检查。")
        provider = str(kwargs.get("provider") or "").lower()
        if provider == "anthropic":
            raise ValueError(
                "OpenJudge 评分器需要 OpenAI 兼容接口；请选择 openai/compatible 类型的模型配置作为 judge 模型。"
            )
        self._kwargs = kwargs
        self._model = OpenAIChatModel(
            model=kwargs["model"],
            api_key=kwargs.get("api_key") or "",
            base_url=kwargs.get("api_base") or None,
            timeout=120,
        )
        self._llm_graders = {
            "relevance": RelevanceGrader(model=self._model),
            "hallucination": HallucinationGrader(model=self._model),
            "instruction_following": InstructionFollowingGrader(model=self._model),
        }
        self._trajectory_grader = TrajectoryComprehensiveGrader(model=self._model)

    async def _score(self, grader: Any, **data) -> tuple[float | None, str]:
        try:
            result = await grader.aevaluate(**data)
        except Exception as exc:  # 单维度失败不拖垮整份报告
            logger.warning("OpenJudge 评分器执行异常：%s", exc)
            return None, f"OpenJudge 执行异常：{exc}"
        if GraderError is not None and isinstance(result, GraderError):
            return None, f"OpenJudge 执行失败：{getattr(result, 'error', '') or result}"
        score = getattr(result, "score", None)
        reason = str(getattr(result, "reason", "") or "")
        return (float(score) if score is not None else None), reason

    async def evaluate(self, dim_keys: list[str], trace) -> dict[str, dict]:
        results: dict[str, dict] = {}
        loop = asyncio.get_event_loop()

        async def _run(key: str):
            if key in self._llm_graders:
                payload: dict[str, Any] | None = None
                if key == "instruction_following":
                    # 官方签名要求 instruction 字段（而非 query）
                    raw, reason = await self._score(
                        self._llm_graders[key],
                        instruction=trace.query, response=trace.response,
                    )
                else:
                    raw, reason = await self._score(
                        self._llm_graders[key], query=trace.query, response=trace.response
                    )
                if raw is not None:
                    payload = {"raw": raw, "reason": reason}
                else:
                    # 结构化输出不受支持等原因 → 平台网关同标准重评
                    note = f"（OpenJudge 直评未产出：{reason}）" if reason else ""
                    fallback = await loop.run_in_executor(
                        None, fallback_score_dim, key, self._kwargs, trace
                    )
                    if fallback["raw"] is not None:
                        payload = {"raw": fallback["raw"],
                                   "reason": f"内置评判{note}"}
            elif key == "trajectory":
                raw, reason = await self._score(
                    self._trajectory_grader, messages=trace.openai_messages
                )
                payload = None
                if raw is not None:
                    payload = {"raw": raw, "reason": reason}
                else:
                    note = f"（OpenJudge 直评未产出：{reason}）" if reason else ""
                    fallback = await loop.run_in_executor(
                        None, fallback_score_dim, key, self._kwargs, trace
                    )
                    if fallback["raw"] is not None:
                        payload = {"raw": fallback["raw"],
                                   "reason": f"内置评判{note}"}
            elif key == "action_loop":
                payload = {"raw": detect_action_loop(trace.actions), "reason": ""}
            if payload is not None:
                results[key] = payload

        await asyncio.gather(*[_run(k) for k in dim_keys])
        return results


def detect_action_loop(actions: list[dict], similarity_threshold: float = 0.9) -> float:
    """ActionLoopDetectionGrader 的确定性算法（成对相似度检测，零成本）。

    判定规则：同名工具且参数高度雷同才算重复动作（与 OpenJudge 的
    相似度惩罚思路一致）；不同工具之间不构成循环。
    返回 1.0（无循环）到 0.0（大量重复动作）。
    """
    if len(actions) < 2:
        return 1.0

    def fingerprint(action: dict) -> str:
        return json.dumps(
            {"args": action.get("arguments") or {}},
            ensure_ascii=False, sort_keys=True, default=str,
        )

    similar_pairs = 0
    total_pairs = 0
    for i in range(len(actions)):
        for j in range(i + 1, len(actions)):
            total_pairs += 1
            if str(actions[i].get("name")) != str(actions[j].get("name")):
                continue  # 不同工具不构成重复
            ratio = difflib.SequenceMatcher(
                None, fingerprint(actions[i]), fingerprint(actions[j])
            ).ratio()
            if ratio >= similarity_threshold:
                similar_pairs += 1
    if total_pairs == 0:
        return 1.0
    return round(1.0 - similar_pairs / total_pairs, 3)


# ---------------------------------------------------------------- 内置降级引擎


class FallbackEngine:
    """内置 LLM-as-Judge 引擎（评判标准对齐 OpenJudge 公开 rubric）。

    py-openjudge 不可导入时的兜底路径；与 OpenJudgeEngine 的降级通道
    共用同一套提示词与解析逻辑（fallback_score_dim）。
    """

    name = "builtin"

    def __init__(self, model_config) -> None:
        from app.model_configs.selector import llm_call_kwargs

        kwargs = llm_call_kwargs(model_config)
        if not kwargs:
            raise ValueError("所选 judge 模型配置缺少模型名或 API Key，请到「模型配置」检查。")
        self._kwargs = kwargs

    async def evaluate(self, dim_keys: list[str], trace) -> dict[str, dict]:
        loop = asyncio.get_event_loop()
        tasks = [
            loop.run_in_executor(None, fallback_score_dim, key, self._kwargs, trace)
            for key in dim_keys
        ]
        done = await asyncio.gather(*tasks)
        return dict(zip(dim_keys, done))


def build_engine(model_config):
    """构造评估引擎：OpenJudge 可用则优先。"""
    if _OPENJUDGE_AVAILABLE:
        return OpenJudgeEngine(model_config)
    return FallbackEngine(model_config)
