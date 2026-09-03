"""记忆宫殿服务：用户级文件库 + 知识图谱抽取编排。

参照 semantica 的核心管线原生实现（复用平台设施）：

- Ingest/Parse：复用 steward SessionWorkspace 的多格式文档抽取；
- Split：段落感知分块（超长段硬切带 overlap）；
- Extract：LLM 严格 JSON 输出实体/关系（与对话共用模型选型）；
- 实体消解/增量：palace_graph 的 merge_key MERGE + 文件级溯源剥离重建；
- 检索：词法锚点 + 一跳邻域（平台无向量设施，双通道注入/只读工具消费）。

抽取是分钟级长任务：上传/重建只投递消息（NATS 优先，无 NATS 部署形态
降级为守护线程内联），执行侧以 (file_id, sha256) 成功记录幂等。
"""
from __future__ import annotations

import logging
import os
import re
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.auth.models import User
from app.data_channel.pipeline_tasks.dispatch import dispatch_super_assistant_palace_extract
from app.data_channel.steward.workspace import WorkspaceError
from app.shared.config import settings
from app.shared.database import SessionLocal
from app.super_assistant import palace_graph, palace_workspace, provider, reflection_service
from app.super_assistant.models import SuperAssistantPalaceBuild, SuperAssistantPalaceFile

logger = logging.getLogger(__name__)

# 单文件抽取的块数与单块长度上限：超长文档截断并在 build 行记录块数
_CHUNK_SIZE = 2400
_CHUNK_OVERLAP = 200
_MAX_CHUNKS = 60
# 注入 system prompt 的图谱段预算（字符）：与附件段同思路的硬边界
_SECTION_BUDGET = 6000
# running 超过该时长视为进程中断，允许重新领取
_STALE_RUNNING = timedelta(minutes=30)

_EXTRACT_PROMPT = """你是知识图谱抽取引擎。请从下面的文档片段中抽取实体和它们之间的关系。
要求：
1. 实体 name 使用原文中的规范名称；type 从这些类别中选一个：人物/组织/机构/地点/时间/概念/技术/产品/项目/事件/其他。
2. aliases 是该实体在文中的其它称谓（没有就给空数组）。
3. relation 用简短的中文短语表达（如：任职、负责、属于、位于、使用、包含）。
4. 只抽取文中明确出现的内容，不要发明；source 和 target 必须是你在 entities 里给出的实体名。
5. 每个片段最多抽取 20 个实体、15 条关系；跳过与文档主题无关的细节。

输出严格 JSON（不要任何解释、不要 markdown 围栏）：
{{"entities":[{{"name":"实体名","type":"类别","aliases":["别名"]}}],"relations":[{{"source":"实体A","target":"实体B","relation":"关系"}}]}}

文档片段：
{chunk}"""


# ---------------------------------------------------------------------------
# 分块与抽取结果清洗
# ---------------------------------------------------------------------------


def _clip_str(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def split_chunks(
    text: str,
    *,
    size: int = _CHUNK_SIZE,
    overlap: int = _CHUNK_OVERLAP,
    max_chunks: int = _MAX_CHUNKS,
) -> list[str]:
    """段落感知分块：优先按空行段落聚合，超长段硬切保留 overlap 尾巴。"""
    cleaned = (text or "").strip()
    if not cleaned:
        return []
    chunks: list[str] = []
    current = ""
    for paragraph in re.split(r"\n{2,}", cleaned):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        if current and len(current) + len(paragraph) + 2 <= size:
            current = f"{current}\n\n{paragraph}"
            continue
        if current:
            chunks.append(current)
        while len(paragraph) > size:
            chunks.append(paragraph[:size])
            paragraph = paragraph[size - overlap:]
        current = paragraph
    if current:
        chunks.append(current)
    return chunks[:max_chunks]


def _sanitize_chunk_entities(owner_id: str, payload: dict) -> list[dict]:
    """单块实体清洗：去空白、限长、按 merge_key 去重。"""
    entities: list[dict] = []
    seen: set[str] = set()
    for item in payload.get("entities") or []:
        if not isinstance(item, dict):
            continue
        name = _clip_str(item.get("name"), 80)
        if not name:
            continue
        key = palace_graph.entity_key(owner_id, name)
        if key in seen:
            continue
        seen.add(key)
        aliases = [
            alias
            for alias in (_clip_str(value, 40) for value in (item.get("aliases") or []))
            if alias
        ][:6]
        entities.append({
            "key": key,
            "name": name,
            "type": _clip_str(item.get("type"), 30) or "其他",
            "aliases": aliases,
            "mentions": 1,
        })
    return entities


def _resolve_relations(
    owner_id: str,
    entity_map: dict[str, dict],
    raw_relations: list[dict],
) -> list[dict]:
    """关系端点必须落在全文件累计的实体集内（跨块可引用），按 merge_key 去重。"""
    resolved: list[dict] = []
    seen: set[str] = set()
    for item in raw_relations:
        source = _clip_str(item.get("source"), 80)
        target = _clip_str(item.get("target"), 80)
        relation = _clip_str(item.get("relation"), 40)
        if not source or not target or not relation:
            continue
        if palace_graph.normalize_name(source) == palace_graph.normalize_name(target):
            continue
        src_key = palace_graph.entity_key(owner_id, source)
        tgt_key = palace_graph.entity_key(owner_id, target)
        key = palace_graph.relation_key(owner_id, source, relation, target)
        if key in seen or src_key not in entity_map or tgt_key not in entity_map:
            continue
        seen.add(key)
        resolved.append({"key": key, "src_key": src_key, "tgt_key": tgt_key, "name": relation})
    return resolved


def extract_chunk(call_kwargs: dict, chunk: str) -> dict:
    """单块 LLM 抽取：要求严格 JSON，宽松解析（复用反思链路的解析器）。"""
    result = provider.chat(
        call_kwargs,
        [{"role": "user", "content": _EXTRACT_PROMPT.format(chunk=chunk)}],
        [],
    )
    return reflection_service._parse_json_loose(str(result.get("content") or ""))


# ---------------------------------------------------------------------------
# 幂等锚点（与 reflection_runs 同一模式）
# ---------------------------------------------------------------------------


def _latest_success_build(db: Session, file_id: str, content_hash: str) -> SuperAssistantPalaceBuild | None:
    return (
        db.query(SuperAssistantPalaceBuild)
        .filter(
            SuperAssistantPalaceBuild.file_id == file_id,
            SuperAssistantPalaceBuild.content_hash == content_hash,
            SuperAssistantPalaceBuild.status == "success",
        )
        .order_by(SuperAssistantPalaceBuild.created_at.desc())
        .first()
    )


def _active_running_build(db: Session, file_id: str) -> SuperAssistantPalaceBuild | None:
    """30 分钟内的 running 视为在途执行；更早的判定为进程中断并收口为 error。"""
    threshold = datetime.now(timezone.utc) - _STALE_RUNNING
    stale: list[SuperAssistantPalaceBuild] = []
    running: SuperAssistantPalaceBuild | None = None
    rows = (
        db.query(SuperAssistantPalaceBuild)
        .filter(SuperAssistantPalaceBuild.file_id == file_id, SuperAssistantPalaceBuild.status == "running")
        .order_by(SuperAssistantPalaceBuild.created_at.desc())
        .all()
    )
    for row in rows:
        created = row.created_at if row.created_at.tzinfo else row.created_at.replace(tzinfo=timezone.utc)
        if created >= threshold:
            running = running or row
        else:
            stale.append(row)
    for row in stale:
        row.status = "error"
        row.error = "执行进程中断（超时回收）"
        row.finished_at = datetime.now(timezone.utc)
    if stale:
        db.commit()
    return running


def _new_build(db: Session, owner_id: str, file_id: str, content_hash: str) -> SuperAssistantPalaceBuild:
    build = SuperAssistantPalaceBuild(
        owner_id=owner_id, file_id=file_id, content_hash=content_hash, status="running",
    )
    db.add(build)
    db.commit()
    db.refresh(build)
    return build


# ---------------------------------------------------------------------------
# 抽取执行（NATS handler / 内联降级共用）
# ---------------------------------------------------------------------------


def run_build(db: Session, owner_id: str, file_id: str) -> SuperAssistantPalaceBuild:
    """执行一次图谱抽取；异常记入 build/file 行，不向上抛。"""
    file_row = db.get(SuperAssistantPalaceFile, file_id)
    if file_row is None or file_row.owner_id != owner_id:
        raise ValueError("记忆宫殿文件不存在")
    existing = _latest_success_build(db, file_id, file_row.sha256)
    if existing is not None and file_row.status == "built":
        return existing
    if (running := _active_running_build(db, file_id)) is not None:
        return running

    build = _new_build(db, owner_id, file_id, file_row.sha256)
    was_built = file_row.status == "built"
    file_row.status = "building"
    file_row.error = None
    db.commit()
    try:
        text = palace_workspace.user_workspace(owner_id).extracted_text(
            palace_workspace.user_dir_id(owner_id), file_row.artifact_id, cap=200_000,
        )
        if not text.strip():
            raise ValueError("文件没有可抽取的文本（解析结果为空）")
        chunks = split_chunks(text)
        if not chunks:
            raise ValueError("文件没有可抽取的文本（解析结果为空）")
        call_kwargs = reflection_service._reflection_call_kwargs(db)
        entity_map: dict[str, dict] = {}
        raw_relations: list[dict] = []
        parsed_chunks = 0
        last_chunk_error: str | None = None
        for chunk in chunks:
            try:
                payload = extract_chunk(call_kwargs, chunk)
            except Exception as exc:
                last_chunk_error = str(exc)
                logger.warning("记忆宫殿抽取块失败（file=%s），跳过该块", file_id, exc_info=True)
                continue
            parsed_chunks += 1
            for entity in _sanitize_chunk_entities(owner_id, payload):
                known = entity_map.get(entity["key"])
                if known is None:
                    entity_map[entity["key"]] = entity
                else:
                    known["mentions"] += 1
            raw_relations.extend(
                item for item in (payload.get("relations") or []) if isinstance(item, dict)
            )
        if parsed_chunks == 0:
            raise ValueError(f"全部文档片段抽取失败：{last_chunk_error or '模型输出无法解析'}")
        relations = _resolve_relations(owner_id, entity_map, raw_relations)

        # 增量重建：仅此前建过图的文件需要先剥离旧贡献（首建为无操作跳过）
        if was_built:
            palace_graph.remove_file_graph(owner_id, file_id, file_row.filename)
        entity_count, relation_count = palace_graph.merge_extraction(
            owner_id, file_id, file_row.filename, list(entity_map.values()), relations,
        )
    except Exception as exc:
        db.rollback()
        build = db.get(SuperAssistantPalaceBuild, build.id) or build
        file_row = db.get(SuperAssistantPalaceFile, file_id) or file_row
        build.status = "error"
        build.error = str(exc)[:2000]
        build.finished_at = datetime.now(timezone.utc)
        file_row.status = "failed"
        file_row.error = str(exc)[:2000]
        db.commit()
        logger.warning("记忆宫殿抽取失败（file=%s）: %s", file_id, exc)
        return build

    build.status = "success"
    build.chunk_count = len(chunks)
    build.entity_count = entity_count
    build.relation_count = relation_count
    build.finished_at = datetime.now(timezone.utc)
    file_row.status = "built"
    file_row.error = None
    file_row.entity_count = entity_count
    file_row.relation_count = relation_count
    db.commit()
    return build


def request_build(file_row: SuperAssistantPalaceFile) -> dict:
    """投递抽取任务：NATS 优先；未配置 NATS_URL 的部署形态降级为守护线程。

    上传/重建请求绝不同步执行分钟级抽取。
    """
    try:
        dispatch_super_assistant_palace_extract(file_row.owner_id, file_row.id)
        return {"dispatched": True}
    except RuntimeError as exc:
        if "NATS_URL" not in str(exc):
            raise

    def _inline() -> None:
        build_db = SessionLocal()
        try:
            run_build(build_db, file_row.owner_id, file_row.id)
        except Exception:
            logger.exception("内联记忆宫殿抽取失败（file=%s）", file_row.id)
        finally:
            build_db.close()

    threading.Thread(target=_inline, daemon=True, name="sa-palace-build").start()
    return {"dispatched": False}


# ---------------------------------------------------------------------------
# HTTP service 层
# ---------------------------------------------------------------------------


def _file_dict(row: SuperAssistantPalaceFile) -> dict:
    return {
        "id": row.id,
        "filename": row.filename,
        "mimeType": row.mime_type,
        "size": row.size,
        "sha256": row.sha256,
        "extractedChars": row.extracted_chars,
        "status": row.status,
        "error": row.error,
        "entityCount": row.entity_count,
        "relationCount": row.relation_count,
        "createdAt": row.created_at,
        "updatedAt": row.updated_at,
    }


def list_files(db: Session, owner_id: str) -> list[dict]:
    rows = (
        db.query(SuperAssistantPalaceFile)
        .filter(SuperAssistantPalaceFile.owner_id == owner_id)
        .order_by(SuperAssistantPalaceFile.created_at.desc())
        .all()
    )
    return [_file_dict(row) for row in rows]


def _owned_file(db: Session, owner_id: str, file_id: str) -> SuperAssistantPalaceFile:
    row = db.get(SuperAssistantPalaceFile, file_id)
    if row is None or row.owner_id != owner_id:
        raise HTTPException(404, "记忆宫殿文件不存在")
    return row


def upload_file(db: Session, current_user: User, upload: UploadFile) -> dict:
    extension = os.path.splitext(upload.filename or "")[1].lower().lstrip(".")
    allowed = {
        item.strip().lower()
        for item in settings.allowed_upload_extensions.split(",")
        if item.strip()
    }
    if extension not in allowed:
        raise HTTPException(
            400,
            (
                f"不支持的文件类型 .{extension}"
                f"（允许: {settings.allowed_upload_extensions}）"
            ),
        )
    owner_id = current_user.id
    workspace = palace_workspace.user_workspace(owner_id)
    try:
        artifact = workspace.save_stream(
            palace_workspace.user_dir_id(owner_id),
            upload.filename or "palace-file",
            upload.file,
            source="palace-upload",
            mime_type=upload.content_type,
        )
    except WorkspaceError as exc:
        raise HTTPException(422, str(exc)) from exc
    row = SuperAssistantPalaceFile(
        owner_id=owner_id,
        filename=str(artifact.get("filename") or upload.filename or "palace-file"),
        artifact_id=str(artifact.get("id")),
        mime_type=str(artifact.get("mimeType") or "application/octet-stream"),
        size=int(artifact.get("size") or 0),
        sha256=str(artifact.get("sha256") or ""),
        extracted_chars=int(artifact.get("extractedChars") or 0),
        status="pending",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    request_build(row)
    return _file_dict(row)


def delete_palace_file(db: Session, owner_id: str, file_id: str) -> None:
    row = _owned_file(db, owner_id, file_id)
    try:
        palace_workspace.user_workspace(owner_id).delete_file(
            palace_workspace.user_dir_id(owner_id), row.artifact_id,
        )
    except WorkspaceError as exc:
        raise HTTPException(404, str(exc)) from exc
    # 图谱清理尽力而为：Neo4j 短暂不可用时文件仍可删除，残留溯源由重建兜底
    try:
        palace_graph.remove_file_graph(owner_id, file_id, row.filename)
    except Exception:
        logger.warning("记忆宫殿图谱清理失败（file=%s）", file_id, exc_info=True)
    db.delete(row)
    db.commit()


def rebuild_file(db: Session, owner_id: str, file_id: str) -> dict:
    row = _owned_file(db, owner_id, file_id)
    if row.status in {"pending", "building"}:
        raise HTTPException(409, "该文件正在抽取队列中，请稍候")
    result = request_build(row)
    return result


def graph_overview(db: Session, owner_id: str) -> dict:
    """图谱可视化数据；Neo4j 不可用时返回 available=False 而不是 5xx。"""
    built = (
        db.query(SuperAssistantPalaceFile)
        .filter(
            SuperAssistantPalaceFile.owner_id == owner_id,
            SuperAssistantPalaceFile.status == "built",
        )
        .count()
    )
    if not built:
        return {"available": True, "nodes": [], "edges": [], "totals": {"entities": 0, "relations": 0}, "truncated": False}
    try:
        payload = palace_graph.owner_graph(owner_id)
    except palace_graph.PalaceGraphUnavailable:
        logger.warning("记忆宫殿图谱读取失败：Neo4j 不可用（owner=%s）", owner_id)
        return {"available": False, "nodes": [], "edges": [], "totals": {"entities": 0, "relations": 0}, "truncated": False}
    return {"available": True, **payload}


# ---------------------------------------------------------------------------
# 助手消费：system prompt 注入 + 只读工具
# ---------------------------------------------------------------------------


def _format_source_files(source_files: list[str]) -> str:
    names = [str(name) for name in source_files if str(name)][:4]
    return "、".join(names)


def build_prompt_section(db: Session, owner_id: str, query: str = "") -> str:
    """组装注入 system prompt 的图谱段；无已建图文件时返回 ""。

    先做一次轻量 DB 计数探测，避免每轮对话都触碰 Neo4j。
    """
    built = (
        db.query(SuperAssistantPalaceFile)
        .filter(
            SuperAssistantPalaceFile.owner_id == owner_id,
            SuperAssistantPalaceFile.status == "built",
        )
        .count()
    )
    if not built:
        return ""
    from app.data_channel.steward.workspace import _context_terms

    terms = _context_terms(query)
    if not terms:
        return ""
    result = palace_graph.search(owner_id, terms)
    entities = result.get("entities") or []
    relations = result.get("relations") or []
    if not entities and not relations:
        return ""
    lines = ["# 记忆宫殿知识图谱（用户上传文档沉淀的长期知识，跨会话可用）"]
    if entities:
        lines.append("## 相关实体")
        for entity in entities[:20]:
            source = _format_source_files(entity.get("source_files") or [])
            suffix = f"；来源：{source}" if source else ""
            lines.append(f"- {entity.get('name')}（{entity.get('type') or '其他'}{suffix}）")
    if relations:
        lines.append("## 相关关系")
        for relation in relations[:30]:
            source = relation.get("source_name") or relation.get("source")
            target = relation.get("target_name") or relation.get("target")
            lines.append(f"- {source} —{relation.get('name')}→ {target}")
    lines.append("（图谱其余内容可用 palace_graph_search 检索，文件清单可用 palace_graph_files 查看。）")
    return "\n".join(lines)[:_SECTION_BUDGET]


def search_for_tool(owner_id: str, query: str) -> dict:
    from app.data_channel.steward.workspace import _context_terms

    return palace_graph.search(owner_id, _context_terms(query))


def list_files_for_tool(db: Session, owner_id: str) -> list[dict]:
    return [
        {
            "id": row.id,
            "filename": row.filename,
            "status": row.status,
            "entityCount": row.entity_count,
            "relationCount": row.relation_count,
            "createdAt": row.created_at.isoformat() if row.created_at else None,
        }
        for row in (
            db.query(SuperAssistantPalaceFile)
            .filter(SuperAssistantPalaceFile.owner_id == owner_id)
            .order_by(SuperAssistantPalaceFile.created_at.desc())
            .all()
        )
    ]
