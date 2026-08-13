from __future__ import annotations

import json
import math
import re
from collections import Counter
from collections.abc import Callable, Iterable
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.shared.config import settings
from app.super_assistant.models import (
    SuperAssistantMemory,
    SuperAssistantMemoryProfile,
)

# 30 天半衰期：得分按 0.5 ** (age_days / 30) 衰减，下限保护长期事实
_HALF_LIFE_DAYS = 30.0
_DECAY_FLOOR = 0.3
_INDEX_SNIPPET_LENGTH = 80

# CJK 连续串与拉丁/数字词分开捕获，便于对 CJK 做 bigram 切分
_TOKEN_RE = re.compile(
    r"(?P<cjk>[一-鿿㐀-䶿豈-﫿]+)|(?P<word>[A-Za-z0-9]+)"
)


class MemoryConflictError(Exception):
    """写入冲突：新内容与某条现存 active 记忆的余弦相似度达到阈值。"""

    def __init__(
        self,
        existing_id: str,
        similarity: float,
        existing_content: str,
    ) -> None:
        super().__init__(
            f"与现存记忆过于相似（相似度 {similarity:.2f}），请确认是更新还是新增"
        )
        self.existing_id = existing_id
        self.similarity = similarity
        self.existing_content = existing_content


def _tokenize(text: str) -> list[str]:
    """分词：连续 CJK 串滑动窗口转 bigram（孤立单字保留），拉丁/数字词
    小写化，丢弃长度 < 2 的 token。"""
    tokens: list[str] = []
    for match in _TOKEN_RE.finditer(text):
        piece = match.group(0)
        if match.lastgroup == "cjk":
            if len(piece) == 1:
                tokens.append(piece)
            else:
                tokens.extend(piece[i : i + 2] for i in range(len(piece) - 1))
        elif len(piece) >= 2:
            tokens.append(piece.lower())
    return tokens


def _idf(documents: list[list[str]]) -> dict[str, float]:
    """平滑 IDF：ln((N + 1) / (df + 1)) + 1。"""
    document_count = len(documents)
    df: Counter[str] = Counter()
    for tokens in documents:
        for term in set(tokens):
            df[term] += 1
    return {
        term: math.log((document_count + 1) / (count + 1)) + 1.0
        for term, count in df.items()
    }


def _cosine_similarity(
    query_tokens: list[str],
    document_tokens: list[str],
    idf: dict[str, float],
) -> float:
    """TF-IDF 向量余弦相似度；任一向量为空时返回 0。"""
    query_tf = Counter(query_tokens)
    document_tf = Counter(document_tokens)
    dot = 0.0
    query_norm = 0.0
    for term, count in query_tf.items():
        weight = count * idf.get(term, 0.0)
        query_norm += weight * weight
        if term in document_tf:
            dot += weight * document_tf[term] * idf.get(term, 0.0)
    document_norm = 0.0
    for term, count in document_tf.items():
        weight = count * idf.get(term, 0.0)
        document_norm += weight * weight
    if not query_norm or not document_norm:
        return 0.0
    return dot / math.sqrt(query_norm * document_norm)


def effectiveness_factor(memory: SuperAssistantMemory) -> float:
    """效果因子：reference/match 比率映射到 [0.5, 1.0]；未被检索过为 1.0。"""
    if memory.match_count <= 0:
        return 1.0
    ratio = memory.reference_count / memory.match_count
    return 0.5 + 0.5 * min(ratio, 1.0)


def decay_factor(
    memory: SuperAssistantMemory,
    now: datetime | None = None,
) -> float:
    """时间衰减：自 created_at 起 30 天半衰期，下限 0.3。"""
    current = now or datetime.now(timezone.utc)
    created = memory.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    age_days = max((current - created).total_seconds() / 86400.0, 0.0)
    return max(_DECAY_FLOOR, 0.5 ** (age_days / _HALF_LIFE_DAYS))


def _active_memories(
    db: Session,
    owner_id: str,
) -> list[SuperAssistantMemory]:
    return (
        db.query(SuperAssistantMemory)
        .filter(
            SuperAssistantMemory.owner_id == owner_id,
            SuperAssistantMemory.superseded.is_(False),
        )
        .order_by(SuperAssistantMemory.updated_at.desc())
        .all()
    )


def relevant_memories(
    db: Session,
    owner_id: str,
    query_text: str,
    cap: int | None = None,
) -> list[SuperAssistantMemory]:
    """TF-IDF 检索：得分 = 相似度 × 效果因子 × 时间衰减，降序取 cap。

    相似度 <= 0 的条目直接丢弃；命中行 match_count + 1（flush，不 commit）。
    """
    if cap is None:
        cap = settings.super_assistant_relevant_memory_cap
    memories = _active_memories(db, owner_id)
    query_tokens = _tokenize(query_text)
    if not memories or not query_tokens or cap <= 0:
        return []
    documents = [_tokenize(memory.content) for memory in memories]
    idf = _idf(documents + [query_tokens])
    now = datetime.now(timezone.utc)
    scored: list[tuple[float, SuperAssistantMemory]] = []
    for memory, tokens in zip(memories, documents):
        similarity = _cosine_similarity(query_tokens, tokens, idf)
        if similarity <= 0:
            continue
        score = (
            similarity
            * effectiveness_factor(memory)
            * decay_factor(memory, now=now)
        )
        scored.append((score, memory))
    scored.sort(key=lambda item: item[0], reverse=True)
    hits = [memory for _, memory in scored[:cap]]
    for memory in hits:
        memory.match_count += 1
    if hits:
        db.flush()
    return hits


def mark_referenced(
    db: Session,
    memory_ids: Iterable[str],
) -> None:
    """记录引用：reference_count + 1 并刷新 last_accessed_at（flush，不 commit）。"""
    ids = [memory_id for memory_id in dict.fromkeys(memory_ids) if memory_id]
    if not ids:
        return
    now = datetime.now(timezone.utc)
    rows = (
        db.query(SuperAssistantMemory)
        .filter(SuperAssistantMemory.id.in_(ids))
        .all()
    )
    for row in rows:
        row.reference_count += 1
        row.last_accessed_at = now
    db.flush()


def check_conflict(
    db: Session,
    owner_id: str,
    content: str,
    threshold: float = 0.75,
    *,
    exclude_memory_id: str | None = None,
) -> None:
    """写入冲突检测：与最相似 active 记忆的余弦相似度 ≥ threshold 时
    抛 MemoryConflictError。"""
    memories = [
        memory
        for memory in _active_memories(db, owner_id)
        if memory.id != exclude_memory_id
    ]
    query_tokens = _tokenize(content)
    if not memories or not query_tokens:
        return
    documents = [_tokenize(memory.content) for memory in memories]
    idf = _idf(documents + [query_tokens])
    best_memory: SuperAssistantMemory | None = None
    best_similarity = 0.0
    for memory, tokens in zip(memories, documents):
        similarity = _cosine_similarity(query_tokens, tokens, idf)
        if similarity > best_similarity:
            best_memory = memory
            best_similarity = similarity
    if best_memory is not None and best_similarity >= threshold:
        raise MemoryConflictError(
            best_memory.id,
            best_similarity,
            best_memory.content,
        )


def create_memory(
    db: Session,
    owner_id: str,
    content: str,
    zone: str = "general",
    pinned: bool = False,
    confidence: str = "medium",
    source: str = "user",
    tags: list[str] | None = None,
    supersedes: list[str] | None = None,
    conflict_check: bool = True,
) -> SuperAssistantMemory:
    """写入记忆：先做冲突检测，落库后把 supersedes 指向的同 owner 旧记忆
    置为 superseded（留档审计，不再参与检索）。"""
    if conflict_check:
        check_conflict(db, owner_id, content)
    memory = SuperAssistantMemory(
        owner_id=owner_id,
        content=content,
        zone=zone,
        pinned=pinned,
        confidence=confidence,
        source=source,
        tags=list(tags or []),
        supersedes=list(supersedes or []),
    )
    db.add(memory)
    if supersedes:
        replaced = (
            db.query(SuperAssistantMemory)
            .filter(
                SuperAssistantMemory.id.in_(supersedes),
                SuperAssistantMemory.owner_id == owner_id,
            )
            .all()
        )
        for old in replaced:
            old.superseded = True
    db.commit()
    db.refresh(memory)
    return memory


def list_memories(
    db: Session,
    owner_id: str,
    zone: str | None = None,
    include_superseded: bool = False,
) -> list[SuperAssistantMemory]:
    """按 updated_at 倒序列出记忆；默认只返回 active。"""
    query = db.query(SuperAssistantMemory).filter(
        SuperAssistantMemory.owner_id == owner_id,
    )
    if zone is not None:
        query = query.filter(SuperAssistantMemory.zone == zone)
    if not include_superseded:
        query = query.filter(SuperAssistantMemory.superseded.is_(False))
    return query.order_by(SuperAssistantMemory.updated_at.desc()).all()


def update_memory(
    db: Session,
    owner_id: str,
    memory_id: str,
    *,
    content: str | None = None,
    zone: str | None = None,
    pinned: bool | None = None,
    tags: list[str] | None = None,
) -> SuperAssistantMemory | None:
    """更新记忆；content 变化时重跑冲突检测（排除自身）。找不到返回 None。"""
    memory = (
        db.query(SuperAssistantMemory)
        .filter(
            SuperAssistantMemory.id == memory_id,
            SuperAssistantMemory.owner_id == owner_id,
        )
        .first()
    )
    if memory is None:
        return None
    if content is not None and content != memory.content:
        check_conflict(db, owner_id, content, exclude_memory_id=memory.id)
        memory.content = content
    if zone is not None:
        memory.zone = zone
    if pinned is not None:
        memory.pinned = pinned
    if tags is not None:
        memory.tags = list(tags)
    db.commit()
    db.refresh(memory)
    return memory


def delete_memory(
    db: Session,
    owner_id: str,
    memory_id: str,
) -> bool | None:
    """硬删除记忆；找不到返回 None。"""
    memory = (
        db.query(SuperAssistantMemory)
        .filter(
            SuperAssistantMemory.id == memory_id,
            SuperAssistantMemory.owner_id == owner_id,
        )
        .first()
    )
    if memory is None:
        return None
    db.delete(memory)
    db.commit()
    return True


def _index_snippet(content: str) -> str:
    """索引行摘要：压平空白后截取前 80 字符。"""
    return " ".join(content.split())[:_INDEX_SNIPPET_LENGTH]


def _first_line(content: str) -> str:
    """首行摘要：取内容第一行前 80 字符。"""
    stripped = content.strip()
    first = stripped.splitlines()[0] if stripped else ""
    return first[:_INDEX_SNIPPET_LENGTH]


def build_memory_prompt_section(
    db: Session,
    owner_id: str,
    query_text: str = "",
) -> str:
    """prompt 注入三态（对标 hermes）：宫殿索引 > 用户画像 > 经典模式。

    经典模式 = pinned 全文 + active 索引（一行一条，有上限）；
    query_text 非空时追加本轮相关记忆全文。三种模式内容为空时返回空串。
    """
    profile_row = db.get(SuperAssistantMemoryProfile, owner_id)
    if profile_row is not None and profile_row.palace_index:
        return (
            "## Memory Palace\n\n"
            + profile_row.palace_index.strip()
            + "\n\n记忆宫殿已启用：可用 palace_zones 查看记忆分区、"
            "palace_read_zone 读取某个分区的完整内容、"
            "palace_recall 按关键词主动回忆具体记忆。"
        )
    if profile_row is not None and profile_row.profile:
        return f"## User Profile\n\n{profile_row.profile.strip()}"

    memories = _active_memories(db, owner_id)
    pinned = [memory for memory in memories if memory.pinned]
    index = [memory for memory in memories if not memory.pinned][
        : settings.super_assistant_memory_index_cap
    ]
    sections: list[str] = []
    if pinned:
        sections.append(
            "## Pinned memories\n\n"
            + "\n\n".join(memory.content.strip() for memory in pinned)
        )
    if index:
        lines = "\n".join(
            f"- [{memory.zone}] {_index_snippet(memory.content)}"
            for memory in index
        )
        sections.append("## Active memory index\n\n" + lines)
    if query_text:
        relevant = relevant_memories(db, owner_id, query_text)
        if relevant:
            sections.append(
                "## Relevant memories for this turn\n\n"
                + "\n\n".join(memory.content.strip() for memory in relevant)
            )
    return "\n\n".join(sections)


def _summarize_zones(memories: list[SuperAssistantMemory]) -> str:
    """宫殿索引的代码生成版：每 zone 一节，含名称、条数与至多 3 条首行摘要。"""
    zones: dict[str, list[SuperAssistantMemory]] = {}
    for memory in memories:
        zones.setdefault(memory.zone, []).append(memory)
    sections: list[str] = []
    for zone in sorted(zones):
        items = zones[zone]
        lines = [f"### {zone}（{len(items)} 条）"]
        lines.extend(f"- {_first_line(item.content)}" for item in items[:3])
        sections.append("\n".join(lines))
    return "\n\n".join(sections)


def _profile_prompt(memories: list[SuperAssistantMemory]) -> str:
    payload = [
        {"zone": memory.zone, "content": memory.content}
        for memory in memories
    ]
    return (
        "根据以下关于用户的核心记忆与置顶记忆，编译一段简短的用户画像"
        "（200 字以内，覆盖身份、偏好与当前关注）。只输出画像正文。\n\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
    )


def _palace_prompt(zone_summary: str) -> str:
    return (
        "根据以下按分区汇总的用户记忆，生成一份记忆宫殿索引：每个分区一节，"
        "给出分区名称、条数与内容提要，便于后续决定查阅哪个分区。"
        "只输出索引正文。\n\n"
        + zone_summary
    )


def compile_profile_and_palace(
    db: Session,
    owner_id: str,
    llm_fn: Callable[[str], str],
) -> SuperAssistantMemoryProfile:
    """LLM 编译：zone==core 与 pinned 记忆 → 用户画像；全部 active 记忆
    按 zone 汇总 → 宫殿索引。

    LLM 调用失败时 palace_index 退化为代码直接生成的 zone 汇总（不调
    LLM），profile 保持不变；无 active 记忆时两个字段置 None。
    """
    profile_row = db.get(SuperAssistantMemoryProfile, owner_id)
    if profile_row is None:
        profile_row = SuperAssistantMemoryProfile(owner_id=owner_id)
        db.add(profile_row)
    memories = _active_memories(db, owner_id)
    if not memories:
        profile_row.profile = None
        profile_row.palace_index = None
    else:
        zone_summary = _summarize_zones(memories)
        core = [
            memory
            for memory in memories
            if memory.zone == "core" or memory.pinned
        ]
        try:
            new_profile = llm_fn(_profile_prompt(core)) if core else None
            new_palace = llm_fn(_palace_prompt(zone_summary))
        except Exception:
            profile_row.palace_index = zone_summary
        else:
            profile_row.profile = new_profile
            profile_row.palace_index = new_palace
    profile_row.compiled_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(profile_row)
    return profile_row
