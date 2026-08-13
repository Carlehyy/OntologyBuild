"""超级助手自我进化反思服务（对标 small-rust-hermes 的 hermes-reflect crate）。

三种反思：

- micro：每轮对话后的轻量反思，产出记忆/技能候选；达置信度阈值且无
  supersedes 的记忆候选在 auto-accept 开关开启时直接写入；
- focused：带人工提示的定向反思，只产出技能候选（be more generous）；
- full：整会话手动反思，产出总结 + 三类候选（记忆/技能/冲突），不做
  auto-accept，summary 仅记日志（运行记录有意不留 summary 字段）。

候选统一进入人工审批（``decide_candidate``）；NATS 消费侧的幂等锚点是
super_assistant_reflection_runs 的 (kind, message_id) 成功记录，full 反思
额外以 running 记录防止重复派发。LLM/解析异常记入 run.error，不向调用方
抛出（NATS handler 语义：业务异常内部消化）。
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.data_channel.pipeline_tasks.dispatch import (
    dispatch_super_assistant_reflection,
)
from app.model_configs.selector import llm_call_kwargs, select_llm_model_config
from app.shared.config import settings
from app.super_assistant import memory_service, provider
from app.super_assistant.models import (
    SuperAssistantConversation,
    SuperAssistantMemory,
    SuperAssistantMemoryProfile,
    SuperAssistantMessage,
    SuperAssistantReflectionCandidate,
    SuperAssistantReflectionRun,
    SuperAssistantSkill,
)

logger = logging.getLogger(__name__)

_CONFIDENCE_RANK = {"low": 0, "medium": 1, "high": 2}

# 显式教学意图关键词（中英文，大小写不敏感子串）：命中即触发 micro 反思
_TEACHING_KEYWORDS = (
    "记住", "以后", "偏好", "总是", "请记住", "不是", "不对", "错了",
    "remember", "always", "prefer", "don't", "wrong", "actually",
)

_CONFLICT_DECISIONS = {"new_supersedes", "keep_old", "skip"}

# full 反思的输入规模上限（对标 hermes 的全量反思窗口）
_FULL_TRANSCRIPT_CHARS = 12_000
_FULL_MEMORY_CAP = 100
_FULL_DECISION_CAP = 10
_FULL_MEMORY_CANDIDATE_CAP = 5
_FULL_SKILL_CANDIDATE_CAP = 3
_FULL_CONFLICT_CAP = 5


# ---------------------------------------------------------------------------
# 触发判定
# ---------------------------------------------------------------------------


def should_micro_reflect(
    db: Session,
    conversation_id: str,
    user_message_content: str,
) -> bool:
    """判断本轮用户消息后是否应触发 micro 反思。

    显式教学关键词命中即触发（绕过冷却）；否则统计自该会话最近一次
    micro run 以来的 complete 用户消息数（无 micro run 时按全量统计），
    达到 super_assistant_reflect_interval 阈值才触发。
    """
    if not settings.super_assistant_reflect_enabled:
        return False
    content = (user_message_content or "").lower()
    if any(keyword in content for keyword in _TEACHING_KEYWORDS):
        return True
    last_micro = (
        db.query(SuperAssistantReflectionRun)
        .filter(
            SuperAssistantReflectionRun.conversation_id == conversation_id,
            SuperAssistantReflectionRun.kind == "micro",
        )
        .order_by(SuperAssistantReflectionRun.created_at.desc())
        .first()
    )
    query = db.query(SuperAssistantMessage).filter(
        SuperAssistantMessage.conversation_id == conversation_id,
        SuperAssistantMessage.role == "user",
        SuperAssistantMessage.status == "complete",
    )
    if last_micro is not None:
        query = query.filter(
            SuperAssistantMessage.created_at > last_micro.created_at
        )
    return query.count() >= settings.super_assistant_reflect_interval


# ---------------------------------------------------------------------------
# LLM 输出解析（宽松 JSON）
# ---------------------------------------------------------------------------


def _strip_markdown_fence(text: str) -> str:
    """去掉整段 markdown 围栏（```json ... ```）；非围栏文本原样返回。"""
    stripped = (text or "").strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    body = lines[1:]
    if body and body[-1].strip().startswith("```"):
        body = body[:-1]
    return "\n".join(body)


def _extract_json_fragment(text: str, start: int) -> tuple[str, bool]:
    """从 start（'{' 的位置）扫描：返回 (片段, 是否括号配平)。

    配平时片段为完整对象；未配平（输出被截断）时片段一直到文本末尾。
    字符串内的括号不参与计数。
    """
    stack: list[str] = []
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "{[":
            stack.append(char)
        elif char in "}]":
            if not stack:
                break
            stack.pop()
            if not stack:
                return text[start:index + 1], True
    return text[start:], False


def _safe_cut_points(fragment: str) -> list[int]:
    """降序的安全截断点：完整值字符串之后、逗号/开括号/闭括号之后。

    键名字符串（后跟冒号）之后不算安全点——截在那里会留下没有值的键。
    """
    points: list[int] = []
    in_string = False
    escaped = False
    for index, char in enumerate(fragment):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
                lookahead = index + 1
                while (
                    lookahead < len(fragment)
                    and fragment[lookahead] in " \t\r\n"
                ):
                    lookahead += 1
                if (
                    lookahead >= len(fragment)
                    or fragment[lookahead] != ":"
                ):
                    points.append(index + 1)
        elif char == '"':
            in_string = True
        elif char in "{[,":
            points.append(index + 1)
        elif char in "}]":
            points.append(index + 1)
    points.sort(reverse=True)
    return points


def _open_closers(text: str) -> str | None:
    """返回 text 未闭合开括号对应的闭合串；括号不匹配或未闭合字符串返回 None。"""
    stack: list[str] = []
    in_string = False
    escaped = False
    for char in text:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "{[":
            stack.append(char)
        elif char in "}]":
            if not stack:
                return None
            opener = stack.pop()
            if (opener, char) not in {("{", "}"), ("[", "]")}:
                return None
    if in_string:
        return None
    return "".join(
        "}" if opener == "{" else "]" for opener in reversed(stack)
    )


def _repair_truncated_json(fragment: str) -> str:
    """截断 JSON 的保守修复：从最近的完整值/结构边界截断（丢弃尾部不完整
    字符串或键值对），补全未闭合的 []/{}；无法修复时抛 ValueError。"""
    for point in [len(fragment)] + _safe_cut_points(fragment):
        prefix = fragment[:point].rstrip()
        if prefix.endswith(","):
            prefix = prefix[:-1].rstrip()
        if not prefix or prefix.endswith(":"):
            continue
        closers = _open_closers(prefix)
        if closers is None:
            continue
        candidate = prefix + closers
        try:
            json.loads(candidate)
        except json.JSONDecodeError:
            continue
        return candidate
    raise ValueError("反思输出的 JSON 被截断且无法修复")


def _parse_json_loose(text: str) -> dict:
    """从 LLM 输出中提取首个 JSON 对象：容忍 markdown 围栏与截断。

    依次尝试文本中的每个 '{' 起点：括号配平时直接解析，未配平走保守
    修复；全部失败抛 ValueError。
    """
    cleaned = _strip_markdown_fence(text)
    for match in re.finditer(r"\{", cleaned):
        fragment, balanced = _extract_json_fragment(cleaned, match.start())
        try:
            parsed = (
                json.loads(fragment)
                if balanced
                else json.loads(_repair_truncated_json(fragment))
            )
        except ValueError:
            continue
        if isinstance(parsed, dict):
            return parsed
    raise ValueError("反思输出中没有可解析的 JSON 对象")


# ---------------------------------------------------------------------------
# 公共小工具
# ---------------------------------------------------------------------------


def _clip(text: str, limit: int) -> str:
    return (text or "")[:limit]


def _normalize_confidence(value) -> str:
    confidence = str(value or "").strip().lower()
    return confidence if confidence in _CONFIDENCE_RANK else "low"


def _reflection_call_kwargs(db: Session) -> dict:
    """反思调用与对话运行时共用同一模型选择逻辑。"""
    model_config = select_llm_model_config(
        db=db,
        purpose_tags=("super_assistant",),
        allow_vlm=False,
    )
    call_kwargs = llm_call_kwargs(model_config)
    if not call_kwargs:
        raise provider.ProviderError(
            "没有可用的文本模型，请先到“模型配置”启用一个 LLM"
        )
    return call_kwargs


def _reflect(call_kwargs: dict, prompt: str) -> dict:
    """一次非流式反思调用：要求严格 JSON 输出，宽松解析。"""
    result = provider.chat(
        call_kwargs,
        [{"role": "user", "content": prompt}],
        [],
    )
    return _parse_json_loose(str(result.get("content") or ""))


def _recompile_profile(db: Session, owner_id: str) -> None:
    """记忆/技能变更后重编译用户画像与记忆宫殿；失败仅记日志。"""
    try:
        call_kwargs = _reflection_call_kwargs(db)

        def llm_fn(prompt: str) -> str:
            result = provider.chat(
                call_kwargs,
                [{"role": "user", "content": prompt}],
                [],
            )
            return str(result.get("content") or "")

        memory_service.compile_profile_and_palace(db, owner_id, llm_fn)
    except Exception:
        logger.exception("重编译用户画像/记忆宫殿失败（owner=%s）", owner_id)


# ---------------------------------------------------------------------------
# 反思输入组装
# ---------------------------------------------------------------------------


def _conversation_messages(
    db: Session,
    conversation_id: str,
    limit: int | None = None,
) -> list[SuperAssistantMessage]:
    rows = (
        db.query(SuperAssistantMessage)
        .filter(
            SuperAssistantMessage.conversation_id == conversation_id,
            SuperAssistantMessage.status == "complete",
        )
        .order_by(SuperAssistantMessage.created_at.asc())
        .all()
    )
    return rows[-limit:] if limit else rows


def _micro_turn_messages(
    db: Session,
    conversation_id: str,
    message_id: str,
) -> list[SuperAssistantMessage]:
    """micro 反思的对话输入：锚点消息及其前一条 complete 用户消息。"""
    anchor = db.get(SuperAssistantMessage, message_id)
    if anchor is None or anchor.conversation_id != conversation_id:
        raise ValueError("反思锚点消息不存在")
    previous = (
        db.query(SuperAssistantMessage)
        .filter(
            SuperAssistantMessage.conversation_id == conversation_id,
            SuperAssistantMessage.role == "user",
            SuperAssistantMessage.status == "complete",
            SuperAssistantMessage.created_at < anchor.created_at,
        )
        .order_by(SuperAssistantMessage.created_at.desc())
        .first()
    )
    return [
        message for message in (previous, anchor) if message is not None
    ]


def _format_turn(
    messages: list[SuperAssistantMessage],
    content_chars: int,
    preview_chars: int = 0,
) -> str:
    lines: list[str] = []
    for message in messages:
        lines.append(f"[{message.role}] {_clip(message.content, content_chars)}")
        if not preview_chars:
            continue
        for step in message.steps or []:
            if not isinstance(step, dict):
                continue
            lines.append(
                f"  - 工具 {step.get('toolName') or '?'}（{step.get('status') or '?'}）: "
                + _clip(str(step.get("preview") or ""), preview_chars)
            )
    return "\n".join(lines)


def _enabled_skills(
    db: Session,
    owner_id: str,
) -> list[SuperAssistantSkill]:
    return (
        db.query(SuperAssistantSkill)
        .filter(
            SuperAssistantSkill.owner_id == owner_id,
            SuperAssistantSkill.enabled.is_(True),
        )
        .order_by(SuperAssistantSkill.name.asc())
        .all()
    )


def _memory_section(
    db: Session,
    owner_id: str,
    cap: int,
    with_id: bool,
) -> str:
    memories = memory_service.list_memories(db, owner_id)[:cap]
    if not memories:
        return "（无）"
    lines = []
    for memory in memories:
        prefix = f"- id={memory.id} " if with_id else "- "
        lines.append(
            f"{prefix}[{memory.zone}] "
            + memory_service._first_line(memory.content)
        )
    return "\n".join(lines)


def _decision_section(db: Session, owner_id: str) -> str:
    decisions = recent_decisions(db, owner_id, cap=_FULL_DECISION_CAP)
    if not decisions:
        return "（无）"
    return "\n".join(
        f"- [{item['kind']}] {item['decision']}"
        f"（confidence={item['confidence']}，{item['decided_at'] or '?'}）"
        f": {item['payload_summary']}"
        for item in decisions
    )


# ---------------------------------------------------------------------------
# 反思 prompt
# ---------------------------------------------------------------------------


def _micro_prompt(
    db: Session,
    owner_id: str,
    conversation_id: str,
    message_id: str,
) -> str:
    turn = _format_turn(
        _micro_turn_messages(db, conversation_id, message_id),
        content_chars=500,
        preview_chars=200,
    )
    skills = _enabled_skills(db, owner_id)[:10]
    skill_lines = "\n".join(f"- {skill.name}" for skill in skills) or "（无）"
    return f"""你是超级助手的自我进化反思器（micro）。阅读最近一轮对话，判断是否有值得长期记住的用户事实，或值得沉淀为可复用技能的做法。

## 最近一轮对话
{turn}

## 已有记忆（避免重复）
{_memory_section(db, owner_id, cap=20, with_id=False)}

## 已启用技能（避免重复）
{skill_lines}

只输出一个 JSON 对象，不要输出任何解释或 markdown 围栏：
{{"memory_candidates": [{{"content": "...", "zone": "core|work|episode|general", "tags": ["..."], "confidence": "low|medium", "supersedes": []}}], "skill_candidates": [{{"name": "kebab-case", "display_name": "...", "description": "...", "triggers": ["..."], "skill_md": "完整 SKILL.md（含 YAML frontmatter: name/description）"}}]}}

规则：
- 默认输出空数组；没有明确可复用事实或技能时不要硬造
- memory_candidates 至多 1 条，仅记录稳定、跨会话有用的用户事实或偏好；confidence 只能用 low 或 medium
- skill_candidates 至多 1 个，仅当用户明确要求沉淀或反复出现可复用流程时给出
- 不要记录已有记忆已覆盖的内容"""


def _focused_prompt(
    db: Session,
    owner_id: str,
    conversation_id: str,
    hint: str,
) -> str:
    transcript = _format_turn(
        _conversation_messages(db, conversation_id, limit=20),
        content_chars=300,
    )
    return f"""你是超级助手的自我进化反思器（focused）。基于最近对话与人工提示，评估是否应沉淀一个可复用技能。

## 最近对话
{transcript or "（无）"}

## 人工提示
{(hint or "").strip() or "（无）"}

只输出一个 JSON 对象，不要输出任何解释或 markdown 围栏：
{{"skill_candidates": [{{"name": "kebab-case", "display_name": "...", "description": "...", "triggers": ["..."], "skill_md": "完整 SKILL.md（含 YAML frontmatter: name/description）"}}]}}

规则：
- 可以更积极地给出技能候选（be more generous），但仍需与对话内容相关
- skill_candidates 至多 1 个；实在没有可沉淀的做法时输出空数组"""


def _full_prompt(
    db: Session,
    owner_id: str,
    conversation_id: str,
) -> str:
    transcript = _format_turn(
        _conversation_messages(db, conversation_id),
        content_chars=300,
    )
    if len(transcript) > _FULL_TRANSCRIPT_CHARS:
        transcript = (
            transcript[:_FULL_TRANSCRIPT_CHARS]
            + "\n……（内容过长，尾部已截断）"
        )
    skills = _enabled_skills(db, owner_id)
    skill_lines = (
        "\n".join(
            f"- {skill.name}: {memory_service._first_line(skill.description)}"
            for skill in skills
        )
        or "（无）"
    )
    return f"""你是超级助手的自我进化反思器（full）。通读整段会话、现有记忆、已启用技能与最近审批历史，产出一份结构化反思结果。

## 会话记录
{transcript or "（无）"}

## 现有记忆（active）
{_memory_section(db, owner_id, cap=_FULL_MEMORY_CAP, with_id=True)}

## 已启用技能
{skill_lines}

## 最近审批历史（meta-reflection：避免重复被否决的方向）
{_decision_section(db, owner_id)}

只输出一个 JSON 对象，不要输出任何解释或 markdown 围栏：
{{"summary": "200 字以内的会话与成长总结", "memory_candidates": [{{"content": "...", "zone": "core|work|episode|general", "tags": ["..."], "confidence": "low|medium|high", "supersedes": ["被取代的记忆 id"]}}], "skill_candidates": [{{"name": "kebab-case", "display_name": "...", "description": "...", "triggers": ["..."], "skill_md": "完整 SKILL.md（含 YAML frontmatter）"}}], "conflicts": [{{"memory_id": "现有记忆 id", "conflict_kind": "contradiction|outdated|duplicate", "explain": "...", "options": ["..."], "candidate_content": "建议写入的新内容"}}]}}

规则：
- summary 必填；三类候选默认输出空数组
- memory_candidates 至多 5 条；supersedes 无取代对象时给空数组
- skill_candidates 至多 3 个；conflicts 至多 5 条
- conflicts 仅在新信息与某条现有记忆明显矛盾时给出，memory_id 必须来自“现有记忆”列表
- 不要重复审批历史中已被 reject/skip 的方向"""


# ---------------------------------------------------------------------------
# 候选落库与 auto-accept
# ---------------------------------------------------------------------------


def _add_candidate(
    db: Session,
    run: SuperAssistantReflectionRun,
    kind: str,
    payload: dict,
    confidence: str = "medium",
) -> SuperAssistantReflectionCandidate:
    candidate = SuperAssistantReflectionCandidate(
        run_id=run.id,
        owner_id=run.owner_id,
        conversation_id=run.conversation_id,
        kind=kind,
        status="pending",
        confidence=_normalize_confidence(confidence),
        payload=payload,
    )
    db.add(candidate)
    db.flush()
    return candidate


def _memory_payload(raw: dict) -> dict:
    return {
        "content": str(raw.get("content") or "").strip(),
        "zone": str(raw.get("zone") or "general").strip() or "general",
        "tags": [
            str(tag) for tag in (raw.get("tags") or []) if str(tag).strip()
        ][:20],
        "pinned": bool(raw.get("pinned") or False),
        "confidence": _normalize_confidence(raw.get("confidence")),
        "supersedes": [
            str(item) for item in (raw.get("supersedes") or []) if item
        ],
    }


def _skill_payload(raw: dict) -> dict:
    files = []
    for entry in (raw.get("files") or [])[:10]:
        if not isinstance(entry, dict):
            continue
        path = str(entry.get("path") or "").strip()
        if path and entry.get("content") is not None:
            files.append({"path": path, "content": str(entry["content"])})
    return {
        "name": str(raw.get("name") or "").strip(),
        "display_name": str(raw.get("display_name") or "").strip(),
        "description": str(raw.get("description") or "").strip(),
        "triggers": [
            str(item) for item in (raw.get("triggers") or [])
            if str(item).strip()
        ][:20],
        "skill_md": str(raw.get("skill_md") or "").strip(),
        "files": files,
    }


def _try_auto_accept(
    db: Session,
    run: SuperAssistantReflectionRun,
    payload: dict,
) -> bool:
    """auto-accept：开关开启、置信度达阈值、无 supersedes 时直接写入记忆。

    冲突检测不通过（MemoryConflictError）时回退为人工审批候选；
    其余写入异常向上抛（计入 run error）。
    """
    if payload["supersedes"]:
        return False
    profile = db.get(SuperAssistantMemoryProfile, run.owner_id)
    if profile is not None and not profile.auto_accept_enabled:
        return False
    threshold = _CONFIDENCE_RANK.get(
        str(settings.super_assistant_auto_accept_min_confidence)
        .strip()
        .lower(),
        1,
    )
    if _CONFIDENCE_RANK[payload["confidence"]] < threshold:
        return False
    try:
        memory = memory_service.create_memory(
            db,
            run.owner_id,
            payload["content"],
            zone=payload["zone"],
            pinned=payload["pinned"],
            confidence=payload["confidence"],
            source="reflection",
            tags=payload["tags"],
        )
    except memory_service.MemoryConflictError:
        return False
    logger.info("micro 反思自动接受记忆 %s（run=%s）", memory.id, run.id)
    _recompile_profile(db, run.owner_id)
    return True


def _process_memory_candidates(
    db: Session,
    run: SuperAssistantReflectionRun,
    items: list,
    *,
    auto_accept: bool,
    cap: int,
) -> int:
    pending = 0
    for raw in items[:cap]:
        if not isinstance(raw, dict):
            continue
        payload = _memory_payload(raw)
        if not payload["content"]:
            continue
        if auto_accept and _try_auto_accept(db, run, payload):
            continue
        _add_candidate(
            db, run, "memory", payload, confidence=payload["confidence"]
        )
        pending += 1
    return pending


def _process_skill_candidates(
    db: Session,
    run: SuperAssistantReflectionRun,
    items: list,
    *,
    cap: int,
) -> int:
    pending = 0
    for raw in items[:cap]:
        if not isinstance(raw, dict):
            continue
        payload = _skill_payload(raw)
        if not payload["skill_md"]:
            continue
        _add_candidate(db, run, "skill", payload, confidence="medium")
        pending += 1
    return pending


def _process_conflicts(
    db: Session,
    run: SuperAssistantReflectionRun,
    items: list,
    *,
    cap: int,
) -> int:
    pending = 0
    for raw in items[:cap]:
        if not isinstance(raw, dict):
            continue
        memory_id = str(raw.get("memory_id") or "").strip()
        candidate_content = str(raw.get("candidate_content") or "").strip()
        if not memory_id or not candidate_content:
            continue
        payload = {
            "memory_id": memory_id,
            "conflict_kind": str(raw.get("conflict_kind") or "").strip()
            or "contradiction",
            "explain": str(raw.get("explain") or "").strip(),
            "options": [
                str(option) for option in (raw.get("options") or [])
                if str(option).strip()
            ],
            "candidate_content": candidate_content,
        }
        _add_candidate(db, run, "conflict", payload, confidence="medium")
        pending += 1
    return pending


def stage_memory_candidate(
    db: Session,
    owner_id: str,
    conversation_id: str,
    payload: dict,
    confidence: str = "medium",
) -> SuperAssistantReflectionCandidate:
    """Agent 工具（memory_save）直写候选：挂 kind="manual" 的一次性 run。

    与 micro/focused 不同，manual run 没有 message_id 幂等锚——每次调用都是
    一次独立的 agent 意图，run 直接以成功态落库，候选进入待审批。
    """
    run = SuperAssistantReflectionRun(
        owner_id=owner_id,
        conversation_id=conversation_id,
        kind="manual",
        status="success",
        candidate_count=1,
        finished_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.flush()
    candidate = _add_candidate(
        db, run, "memory", _memory_payload(payload), confidence=confidence
    )
    db.commit()
    db.refresh(candidate)
    return candidate


# ---------------------------------------------------------------------------
# 三种反思执行
# ---------------------------------------------------------------------------


def _latest_successful_run(
    db: Session,
    kind: str,
    message_id: str,
) -> SuperAssistantReflectionRun | None:
    return (
        db.query(SuperAssistantReflectionRun)
        .filter(
            SuperAssistantReflectionRun.kind == kind,
            SuperAssistantReflectionRun.message_id == message_id,
            SuperAssistantReflectionRun.status == "success",
        )
        .order_by(SuperAssistantReflectionRun.created_at.desc())
        .first()
    )


def _new_run(
    db: Session,
    owner_id: str,
    conversation_id: str,
    message_id: str | None,
    kind: str,
) -> SuperAssistantReflectionRun:
    """创建 running 状态的 run 并立即 commit：NATS 重投时靠它幂等。"""
    run = SuperAssistantReflectionRun(
        owner_id=owner_id,
        conversation_id=conversation_id,
        message_id=message_id,
        kind=kind,
        status="running",
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def _finish_run(
    db: Session,
    run: SuperAssistantReflectionRun,
    candidate_count: int,
) -> SuperAssistantReflectionRun:
    run.status = "success"
    run.candidate_count = candidate_count
    run.finished_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(run)
    return run


def _fail_run(
    db: Session,
    run: SuperAssistantReflectionRun,
    exc: Exception,
) -> SuperAssistantReflectionRun:
    # 先 rollback：丢弃异常前已 flush 的部分候选（候选不动），再记 error
    db.rollback()
    run.status = "error"
    run.error = str(exc)[:2000]
    run.finished_at = datetime.now(timezone.utc)
    db.commit()
    logger.warning("反思 run %s（%s）失败: %s", run.id, run.kind, exc)
    return run


def run_micro_reflection(
    db: Session,
    owner_id: str,
    conversation_id: str,
    message_id: str,
) -> SuperAssistantReflectionRun:
    """执行一轮 micro 反思；同 (kind="micro", message_id) 已有成功 run 时
    直接返回既有 run（NATS 重投幂等）。异常记入 run.error，不向上抛。"""
    existing = _latest_successful_run(db, "micro", message_id)
    if existing is not None:
        return existing
    run = _new_run(db, owner_id, conversation_id, message_id, "micro")
    try:
        call_kwargs = _reflection_call_kwargs(db)
        prompt = _micro_prompt(db, owner_id, conversation_id, message_id)
        output = _reflect(call_kwargs, prompt)
        pending = _process_memory_candidates(
            db, run, output.get("memory_candidates") or [],
            auto_accept=True, cap=1,
        )
        pending += _process_skill_candidates(
            db, run, output.get("skill_candidates") or [], cap=1,
        )
    except Exception as exc:
        return _fail_run(db, run, exc)
    return _finish_run(db, run, pending)


def run_focused_reflection(
    db: Session,
    owner_id: str,
    conversation_id: str,
    message_id: str,
    hint: str = "",
) -> SuperAssistantReflectionRun:
    """执行一轮 focused 反思（定向技能产出，至多 1 个候选，不做
    auto-accept）；幂等语义同 micro。"""
    existing = _latest_successful_run(db, "focused", message_id)
    if existing is not None:
        return existing
    run = _new_run(db, owner_id, conversation_id, message_id, "focused")
    try:
        call_kwargs = _reflection_call_kwargs(db)
        prompt = _focused_prompt(db, owner_id, conversation_id, hint)
        output = _reflect(call_kwargs, prompt)
        pending = _process_skill_candidates(
            db, run, output.get("skill_candidates") or [], cap=1,
        )
    except Exception as exc:
        return _fail_run(db, run, exc)
    return _finish_run(db, run, pending)


def run_full_reflection(
    db: Session,
    owner_id: str,
    conversation_id: str,
) -> SuperAssistantReflectionRun:
    """执行一轮 full 反思；同会话已有 running 的 full run 时直接返回它
    （防重复派发）。三类候选全部落 pending，不做 auto-accept。"""
    running = (
        db.query(SuperAssistantReflectionRun)
        .filter(
            SuperAssistantReflectionRun.conversation_id == conversation_id,
            SuperAssistantReflectionRun.kind == "full",
            SuperAssistantReflectionRun.status == "running",
        )
        .order_by(SuperAssistantReflectionRun.created_at.desc())
        .first()
    )
    if running is not None:
        return running
    run = _new_run(db, owner_id, conversation_id, None, "full")
    try:
        call_kwargs = _reflection_call_kwargs(db)
        prompt = _full_prompt(db, owner_id, conversation_id)
        output = _reflect(call_kwargs, prompt)
        summary = str(output.get("summary") or "").strip()
        if summary:
            logger.info("full 反思总结（run=%s）: %s", run.id, summary)
        pending = _process_memory_candidates(
            db, run, output.get("memory_candidates") or [],
            auto_accept=False, cap=_FULL_MEMORY_CANDIDATE_CAP,
        )
        pending += _process_skill_candidates(
            db, run, output.get("skill_candidates") or [],
            cap=_FULL_SKILL_CANDIDATE_CAP,
        )
        pending += _process_conflicts(
            db, run, output.get("conflicts") or [], cap=_FULL_CONFLICT_CAP,
        )
    except Exception as exc:
        return _fail_run(db, run, exc)
    return _finish_run(db, run, pending)


# ---------------------------------------------------------------------------
# 审批决策
# ---------------------------------------------------------------------------


def _create_skill_from_payload(
    db: Session,
    owner_id: str,
    payload: dict,
) -> SuperAssistantSkill:
    """复用 skill_service 的事务化创建路径落地技能候选（含伴随文件）。"""
    from app.auth.models import User
    from app.super_assistant import skill_service
    from app.super_assistant.schemas import SkillCreate, SkillFileContent
    from app.super_assistant.skill_store import parse_skill_markdown

    user = db.get(User, owner_id)
    if user is None:
        raise ValueError("候选归属用户不存在")
    skill_md = str(payload.get("skill_md") or "").strip()
    if not skill_md:
        raise ValueError("技能候选缺少 skill_md")
    # SkillStoreError 是 ValueError 子类：frontmatter 非法 → 400
    metadata = parse_skill_markdown(skill_md)
    duplicate = (
        db.query(SuperAssistantSkill)
        .filter(
            SuperAssistantSkill.owner_id == owner_id,
            SuperAssistantSkill.name == metadata["name"],
        )
        .first()
    )
    if duplicate is not None:
        raise ValueError("同名 Skill 已存在")
    try:
        item = skill_service.create_skill(
            SkillCreate(
                name=metadata["name"],
                description=metadata["description"],
                content=metadata["content"],
                enabled=True,
            ),
            db,
            user,
        )
        for entry in payload.get("files") or []:
            path = str(entry.get("path") or "").strip()
            if not path or path == "SKILL.md":
                continue
            skill_service.put_skill_file(
                item.id,
                path,
                SkillFileContent(content=str(entry.get("content") or "")),
                db,
                user,
            )
    except HTTPException as exc:
        if exc.status_code == 409:
            raise ValueError("同名 Skill 已存在") from exc
        raise ValueError(str(exc.detail)) from exc
    return item


def _decide_memory(
    db: Session,
    candidate: SuperAssistantReflectionCandidate,
    payload: dict,
    action: str,
) -> str:
    if action == "reject":
        return "rejected"
    if action != "accept":
        raise ValueError(f"不支持的记忆候选操作: {action}")
    content = str(payload.get("content") or "").strip()
    if not content:
        raise ValueError("记忆候选缺少 content")
    supersedes = [
        str(item) for item in (payload.get("supersedes") or []) if item
    ]
    memory_service.create_memory(
        db,
        candidate.owner_id,
        content,
        zone=str(payload.get("zone") or "general"),
        pinned=bool(payload.get("pinned") or False),
        confidence=str(payload.get("confidence") or candidate.confidence),
        source="reflection",
        tags=[str(tag) for tag in (payload.get("tags") or [])],
        supersedes=supersedes,
        # 带 supersedes 的候选是对旧记忆的显式更新：旧记忆在冲突检测时
        # 仍是 active，检查会对自己误报，故跳过
        conflict_check=not supersedes,
    )
    _recompile_profile(db, candidate.owner_id)
    return "accepted"


def _decide_skill(
    db: Session,
    candidate: SuperAssistantReflectionCandidate,
    payload: dict,
    action: str,
) -> str:
    if action == "reject":
        return "rejected"
    if action != "accept":
        raise ValueError(f"不支持的技能候选操作: {action}")
    _create_skill_from_payload(db, candidate.owner_id, payload)
    _recompile_profile(db, candidate.owner_id)
    return "accepted"


def _decide_conflict(
    db: Session,
    candidate: SuperAssistantReflectionCandidate,
    payload: dict,
    action: str,
) -> str:
    if action not in _CONFLICT_DECISIONS:
        raise ValueError(f"不支持的冲突候选操作: {action}")
    if action != "new_supersedes":
        # keep_old/skip：不改记忆，decision 记原值，状态记 rejected
        return "rejected"
    content = str(
        payload.get("content") or payload.get("candidate_content") or ""
    ).strip()
    if not content:
        raise ValueError("冲突候选缺少 candidate_content")
    memory_id = str(payload.get("memory_id") or "").strip()
    if not memory_id:
        raise ValueError("冲突候选缺少 memory_id")
    memory_service.create_memory(
        db,
        candidate.owner_id,
        content,
        zone=str(payload.get("zone") or "general"),
        confidence=candidate.confidence,
        source="reflection",
        supersedes=[memory_id],
        conflict_check=False,
    )
    _recompile_profile(db, candidate.owner_id)
    return "accepted"


def decide_candidate(
    db: Session,
    owner_id: str,
    candidate_id: str,
    decision: str,
    edited_payload: dict | None = None,
) -> SuperAssistantReflectionCandidate:
    """审批一个反思候选：接受则落记忆/建技能/按冲突策略写记忆。

    ValueError 消息契约（router 依此映射状态码）："候选不存在"/"会话不
    存在"→404，"候选已处理"/"同名 Skill 已存在"→409，其余→400。
    edited_payload 的键覆盖原 payload。
    """
    candidate = (
        db.query(SuperAssistantReflectionCandidate)
        .filter(
            SuperAssistantReflectionCandidate.id == candidate_id,
            SuperAssistantReflectionCandidate.owner_id == owner_id,
        )
        .first()
    )
    if candidate is None:
        raise ValueError("候选不存在")
    if candidate.status != "pending":
        raise ValueError("候选已处理")
    action = str(decision or "").strip()
    payload = dict(candidate.payload or {})
    if isinstance(edited_payload, dict):
        payload.update(edited_payload)
    if candidate.kind == "memory":
        status = _decide_memory(db, candidate, payload, action)
    elif candidate.kind == "skill":
        status = _decide_skill(db, candidate, payload, action)
    elif candidate.kind == "conflict":
        status = _decide_conflict(db, candidate, payload, action)
    else:
        raise ValueError(f"未知的候选类型: {candidate.kind}")
    candidate.status = status
    candidate.decision = action
    candidate.decided_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(candidate)
    return candidate


def _payload_summary(payload: dict) -> str:
    """候选 payload 的一行摘要（首行 80 字符）。"""
    if not isinstance(payload, dict):
        return ""
    text = str(
        payload.get("content")
        or payload.get("candidate_content")
        or payload.get("description")
        or payload.get("name")
        or json.dumps(payload, ensure_ascii=False)
    )
    stripped = text.strip()
    first = stripped.splitlines()[0] if stripped else ""
    return first[:80]


def recent_decisions(
    db: Session,
    owner_id: str,
    cap: int = 10,
) -> list[dict]:
    """最近审批历史：已处理的候选按 decided_at 倒序取 cap 条。"""
    rows = (
        db.query(SuperAssistantReflectionCandidate)
        .filter(
            SuperAssistantReflectionCandidate.owner_id == owner_id,
            SuperAssistantReflectionCandidate.status != "pending",
        )
        .order_by(SuperAssistantReflectionCandidate.decided_at.desc())
        .limit(cap)
        .all()
    )
    return [
        {
            "kind": row.kind,
            "decision": row.decision,
            "confidence": row.confidence,
            "decided_at": (
                row.decided_at.isoformat() if row.decided_at else None
            ),
            "payload_summary": _payload_summary(row.payload),
        }
        for row in rows
    ]


# ---------------------------------------------------------------------------
# 端点支撑：候选列表 / full 触发 / 反思设置
# ---------------------------------------------------------------------------


def list_candidates(
    db: Session,
    owner_id: str,
    status: str = "pending",
) -> list[SuperAssistantReflectionCandidate]:
    """按创建时间升序列出候选；status="all" 时不过滤状态。"""
    query = db.query(SuperAssistantReflectionCandidate).filter(
        SuperAssistantReflectionCandidate.owner_id == owner_id,
    )
    if status != "all":
        query = query.filter(
            SuperAssistantReflectionCandidate.status == status
        )
    return query.order_by(
        SuperAssistantReflectionCandidate.created_at.asc()
    ).all()


def request_full_reflection(
    db: Session,
    owner_id: str,
    conversation_id: str,
) -> dict:
    """触发 full 反思：默认 NATS 派发；仅当派发失败原因为未配置
    NATS_URL 时降级为同步内联执行（无 NATS 的部署形态也可用）。"""
    conversation = (
        db.query(SuperAssistantConversation)
        .filter(
            SuperAssistantConversation.id == conversation_id,
            SuperAssistantConversation.owner_id == owner_id,
        )
        .first()
    )
    if conversation is None:
        raise ValueError("会话不存在")
    try:
        dispatch_super_assistant_reflection(
            "full",
            {"owner_id": owner_id, "conversation_id": conversation_id},
        )
    except RuntimeError as exc:
        if "未配置 NATS_URL" not in str(exc):
            raise
        logger.info(
            "未配置 NATS_URL，full 反思降级为同步执行（conversation=%s）",
            conversation_id,
        )
        run = run_full_reflection(db, owner_id, conversation_id)
        return {"dispatched": False, "runId": run.id}
    return {"dispatched": True, "runId": None}


def get_reflection_settings(db: Session, owner_id: str) -> dict:
    """反思设置视图：无 profile 行时 auto_accept_enabled 按默认 True。"""
    profile = db.get(SuperAssistantMemoryProfile, owner_id)
    memory_count = (
        db.query(SuperAssistantMemory)
        .filter(
            SuperAssistantMemory.owner_id == owner_id,
            SuperAssistantMemory.superseded.is_(False),
        )
        .count()
    )
    pending_count = (
        db.query(SuperAssistantReflectionCandidate)
        .filter(
            SuperAssistantReflectionCandidate.owner_id == owner_id,
            SuperAssistantReflectionCandidate.status == "pending",
        )
        .count()
    )
    return {
        "auto_accept_enabled": (
            profile.auto_accept_enabled if profile is not None else True
        ),
        "palace_index": profile.palace_index if profile is not None else None,
        "profile": profile.profile if profile is not None else None,
        "memory_count": memory_count,
        "pending_count": pending_count,
    }


def update_reflection_settings(
    db: Session,
    owner_id: str,
    auto_accept_enabled: bool,
) -> dict:
    """upsert profile 行的 auto_accept_enabled，返回最新设置视图。"""
    profile = db.get(SuperAssistantMemoryProfile, owner_id)
    if profile is None:
        profile = SuperAssistantMemoryProfile(owner_id=owner_id)
        db.add(profile)
    profile.auto_accept_enabled = bool(auto_accept_enabled)
    db.commit()
    return get_reflection_settings(db, owner_id)
