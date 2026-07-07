"""能力注册中心 — 数据模型

平台级的智能体能力管理：P1 先做 Skill（提示词能力包），P2 在同级
增加 MCP server 注册。agent（业务探索等）按 scope 挂载已启用的能力。

Skill = 教智能体「怎么做某类事」的 markdown 指令包 + 输出契约
（如「输出 ```mermaid erDiagram```」）。渐进披露：目录（description）
注入系统提示，全文（instructions）由 use_skill 工具按需取用。
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import String, DateTime, Text, JSON, Boolean
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class CapSkill(Base):
    __tablename__ = "cap_skills"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)

    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    # 一句话描述 —— 注入智能体系统提示的技能目录，写清楚「什么时候该用」
    description: Mapped[str] = mapped_column(Text, default="")
    # markdown 全文 —— use_skill 激活后回填给模型的完整操作指令与输出契约
    instructions: Mapped[str] = mapped_column(Text, default="")

    # 作用域：哪些 agent 可见，如 ["exploration"]；空列表 = 全部不挂载
    scopes: Mapped[list] = mapped_column(JSON, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # 内置技能：随平台 seed，可编辑不可删除
    builtin: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)
