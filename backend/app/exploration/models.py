"""业务探索 (Business Exploration) — 数据模型

对话式业务建模/需求建模：AI 引导澄清，把已确认的业务知识实时沉淀为
六类结构化模型（对象/主体/行为/事件/规则/场景）组成的「业务画布」，
再从画布生成需求文档，最终转化为本体草稿（人审后落地）。

五张表：
  - ExplorationSession    探索会话（画布挂在会话上，随对话演进）
  - ExplorationMessage    消息（含工具调用轨迹，审计单元）
  - ExplorationDocument   需求文档（画布快照 + markdown，版本递增）
  - ExplorationDraft      本体草稿（转化产物 + 校验报告，应用前的人审对象）
  - ExplorationAttachment 会话附件（上传的参考资料，仅本会话可见，注入对话上下文）
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import String, DateTime, Text, JSON, Integer
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ExplorationSession(Base):
    __tablename__ = "bx_sessions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String, nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(200), default="新的业务探索")

    # 业务画布：{"objects": [...], "actors": [...], "behaviors": [...],
    #           "events": [...], "rules": [...], "scenarios": [...]}
    canvas: Mapped[dict] = mapped_column(JSON, default=dict)
    canvas_version: Mapped[int] = mapped_column(Integer, default=0)

    status: Mapped[str] = mapped_column(String(20), default="active")

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class ExplorationMessage(Base):
    __tablename__ = "bx_messages"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    session_id: Mapped[str] = mapped_column(String, nullable=False, index=True)

    role: Mapped[str] = mapped_column(String(20), nullable=False)   # user | assistant
    content: Mapped[str] = mapped_column(Text, default="")
    # [{tool, arguments, summary, durationMs, error?}]
    steps: Mapped[list] = mapped_column(JSON, default=list)

    model: Mapped[str] = mapped_column(String(200), nullable=True)
    token_usage: Mapped[dict] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class ExplorationDocument(Base):
    __tablename__ = "bx_documents"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    session_id: Mapped[str] = mapped_column(String, nullable=False, index=True)

    title: Mapped[str] = mapped_column(String(300), default="业务需求文档")
    content_md: Mapped[str] = mapped_column(Text, default="")
    # 生成时刻的画布快照 —— 草稿转化以此为源，保证文档与草稿同源可追溯
    canvas_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    version: Mapped[int] = mapped_column(Integer, default=1)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class ExplorationAttachment(Base):
    """会话级参考资料。文件上传后确定性转为文本，随每个对话回合注入 LLM 上下文，
    帮助引导师从真实材料中提炼业务模型。严格绑定 session_id —— 跨会话不可见，
    随会话删除一并清理（磁盘文件 + 记录）。"""
    __tablename__ = "bx_attachments"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    session_id: Mapped[str] = mapped_column(String, nullable=False, index=True)

    filename: Mapped[str] = mapped_column(String(400), default="")
    mime_type: Mapped[str] = mapped_column(String(200), nullable=True)
    file_size: Mapped[int] = mapped_column(Integer, default=0)
    file_path: Mapped[str] = mapped_column(String, nullable=True)  # 磁盘路径，删除时清理

    # 转换后的文本（注入对话上下文的内容源）+ 原始字符数（截断前）
    extracted_text: Mapped[str] = mapped_column(Text, default="")
    char_count: Mapped[int] = mapped_column(Integer, default=0)

    status: Mapped[str] = mapped_column(String(20), default="ready")  # ready | failed
    error: Mapped[str] = mapped_column(String(500), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class ExplorationDraft(Base):
    __tablename__ = "bx_drafts"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    session_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    document_id: Mapped[str] = mapped_column(String, nullable=False, index=True)

    # None = 应用时新建本体；否则保守合并进该本体（只新增、同名跳过）
    target_ontology_id: Mapped[str] = mapped_column(String, nullable=True)

    # {"objectTypes": [...], "linkTypes": [...], "actions": [...]}，元素带 key/sourceRefs/conflict
    draft: Mapped[dict] = mapped_column(JSON, default=dict)
    # {"warnings": [...], "conflicts": [...], "scenarioCoverage": [...], "llmRefined": bool}
    report: Mapped[dict] = mapped_column(JSON, default=dict)

    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft | applied | discarded
    applied_ontology_id: Mapped[str] = mapped_column(String, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)
