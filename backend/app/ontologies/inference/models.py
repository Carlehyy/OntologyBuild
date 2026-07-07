"""
推理运行 + 规则影子试跑 + 动作发射模型
"""
import uuid
from datetime import datetime, timezone
from sqlalchemy import String, DateTime, Text, JSON, Float, Boolean, ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class ShadowRun(Base):
    """规则影子试跑记录 — 上线前的安全验证"""
    __tablename__ = "shadow_runs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    ontology_id: Mapped[str] = mapped_column(String, ForeignKey("ontology_projects.id", ondelete="CASCADE"), nullable=False)
    rule_id: Mapped[str] = mapped_column(String, nullable=False)  # 对应 logic_rules.id
    rule_name: Mapped[str] = mapped_column(String(200), nullable=False)

    # 状态: running, completed, failed
    status: Mapped[str] = mapped_column(String(20), default="running")

    # 试跑结果
    total_entities_checked: Mapped[int] = mapped_column(Integer, default=0)
    entities_matched: Mapped[int] = mapped_column(Integer, default=0)
    match_samples: Mapped[list] = mapped_column(JSON, default=list)  # 命中样本
    quality_score: Mapped[float] = mapped_column(Float, default=0.0)  # 0-1

    # 判定: pass, fail
    verdict: Mapped[str] = mapped_column(String(10), nullable=True)
    verdict_reason: Mapped[str] = mapped_column(Text, nullable=True)

    created_by: Mapped[str] = mapped_column(String, ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    completed_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)


class InferenceRun(Base):
    """推理运行批次 — 在图上做模式匹配"""
    __tablename__ = "inference_runs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    ontology_id: Mapped[str] = mapped_column(String, ForeignKey("ontology_projects.id", ondelete="CASCADE"), nullable=False)

    # 名称
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=True)

    # 触发方式: manual, scheduled
    trigger_type: Mapped[str] = mapped_column(String(20), default="manual")

    # 状态: running, completed, failed
    status: Mapped[str] = mapped_column(String(20), default="running")

    # 运行的规则ID列表
    rule_ids: Mapped[list] = mapped_column(JSON, default=list)

    # 命中结果统计
    total_checked: Mapped[int] = mapped_column(Integer, default=0)
    total_matched: Mapped[int] = mapped_column(Integer, default=0)
    total_actions_fired: Mapped[int] = mapped_column(Integer, default=0)

    created_by: Mapped[str] = mapped_column(String, ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    completed_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)


class InferenceResult(Base):
    """推理结果项 — 带证据链"""
    __tablename__ = "inference_results"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    run_id: Mapped[str] = mapped_column(String, ForeignKey("inference_runs.id", ondelete="CASCADE"), nullable=False)
    ontology_id: Mapped[str] = mapped_column(String, ForeignKey("ontology_projects.id", ondelete="CASCADE"), nullable=False)
    rule_id: Mapped[str] = mapped_column(String, nullable=False)
    rule_name: Mapped[str] = mapped_column(String(200), nullable=False)

    # 命中实体
    entity_id: Mapped[str] = mapped_column(String, nullable=False)
    entity_name: Mapped[str] = mapped_column(String(200), nullable=True)

    # 证据链: [{type: "path", nodes: [...], edges: [...]}, ...]
    evidence_chain: Mapped[list] = mapped_column(JSON, default=list)

    # 匹配详情
    match_detail: Mapped[dict] = mapped_column(JSON, default=dict)

    # 置信度
    confidence: Mapped[float] = mapped_column(Float, default=1.0)

    # 是否已处理 (幂等)
    processed: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class ActionFiring(Base):
    """动作发射记录"""
    __tablename__ = "action_firings"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    ontology_id: Mapped[str] = mapped_column(String, ForeignKey("ontology_projects.id", ondelete="CASCADE"), nullable=False)
    inference_result_id: Mapped[str] = mapped_column(String, ForeignKey("inference_results.id"), nullable=True)

    # 动作信息
    action_id: Mapped[str] = mapped_column(String, nullable=False)
    action_name: Mapped[str] = mapped_column(String(200), nullable=False)
    action_type: Mapped[str] = mapped_column(String(50), nullable=False)  # notify, risk, event, webhook

    # 发射内容
    payload: Mapped[dict] = mapped_column(JSON, default=dict)

    # 状态: pending, sent, failed, acknowledged
    status: Mapped[str] = mapped_column(String(20), default="pending")

    # 响应
    response_info: Mapped[dict] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    sent_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)


class AuditLog(Base):
    """全链路审计日志"""
    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    ontology_id: Mapped[str] = mapped_column(String, ForeignKey("ontology_projects.id", ondelete="CASCADE"), nullable=True)

    # 事件类型: extraction, edit, mapping, inference, action_fired, login, export, publish, rollback
    event_type: Mapped[str] = mapped_column(String(30), nullable=False)

    # 事件子类型
    event_subtype: Mapped[str] = mapped_column(String(50), nullable=True)

    # 操作者
    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"), nullable=True)
    user_name: Mapped[str] = mapped_column(String(50), nullable=True)

    # 事件描述
    description: Mapped[str] = mapped_column(Text, nullable=False)

    # 对象类型和ID
    object_type: Mapped[str] = mapped_column(String(30), nullable=True)
    object_id: Mapped[str] = mapped_column(String, nullable=True)

    # 变更前后
    before_state: Mapped[dict] = mapped_column(JSON, nullable=True)
    after_state: Mapped[dict] = mapped_column(JSON, nullable=True)

    # 元数据
    meta: Mapped[dict] = mapped_column(JSON, default=dict)

    # IP、UA
    ip_address: Mapped[str] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str] = mapped_column(String(500), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
