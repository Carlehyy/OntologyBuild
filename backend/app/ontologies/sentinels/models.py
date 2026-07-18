"""
哨兵引擎模型 (Sentinel Engine) — 反应式本体运行时

哨兵 = 跨对象触发条件 → 执行一组动作。独立于动作的一等公民，可引用多个对象类型。
参考 Palantir Foundry 的 Automate 与 ITGC 平台的规则引擎(Skill)。

  - Sentinel         哨兵：监听绑定(可跨对象) + 链接约束 + 条件表达式 → 动作列表
  - SentinelFiring   哨兵触发日志：何时、由谁(手动/变化/定时)、命中了什么、动作结果
  - Notification     通知：动作 notification 副作用的落地点(可查询的内部收件箱)
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    String, DateTime, ForeignKey, Text, JSON, Boolean, Integer, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Sentinel(Base):
    """哨兵 — 跨对象触发条件 → 执行动作列表。

    bindings 示例(跨对象)：
      [{"alias": "a", "objectTypeId": "<订单>", "filter": "a.status == 'submitted'"},
       {"alias": "b", "objectTypeId": "<商家>", "filter": null}]
    links 示例(绑定间关系约束)：
      [{"from": "a", "linkTypeId": "<归属>", "to": "b"}]
    condition 示例(跨别名最终条件)：
      "a.amount > b.credit_limit"
    命中后依次执行 action_ids；动作作用目标为 primary_alias 对应的对象实例。
    """
    __tablename__ = "sentinels"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    ontology_id: Mapped[str] = mapped_column(
        String, ForeignKey("ontology_projects.id", ondelete="CASCADE"), nullable=False, index=True)

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=True)

    # —— 监听范围(可跨对象) ——
    bindings: Mapped[list] = mapped_column(JSON, default=list)          # [{alias, objectTypeId, filter}]
    links: Mapped[list] = mapped_column(JSON, default=list)            # [{from, linkTypeId, to}]
    condition: Mapped[str] = mapped_column(Text, nullable=True)         # 跨别名表达式(求值用，前端编译)
    condition_rows: Mapped[list] = mapped_column(JSON, default=list)    # 结构化条件行(回显用) [{left,op,right,rightKind}]
    condition_logic: Mapped[str] = mapped_column(String(8), default="and")  # 行间逻辑 and|or
    primary_alias: Mapped[str] = mapped_column(String(50), nullable=True)  # 动作目标别名(默认首个绑定)

    # —— 命中后执行(可多个动作) ——
    action_ids: Mapped[list] = mapped_column(JSON, default=list)
    # 每个动作的安全参数绑定：{actionId: {parameterName: literal|bindingSpec}}。
    # bindingSpec 只支持 constant / match-property / target-id，不执行任意表达式。
    action_parameters: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")

    # —— 执行入口 ——
    on_change: Mapped[bool] = mapped_column(Boolean, default=True)      # 变化驱动
    on_schedule: Mapped[bool] = mapped_column(Boolean, default=False)   # 定期扫描
    scan_interval_seconds: Mapped[int] = mapped_column(Integer, default=300)
    last_scanned_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)

    # —— 触发语义(边沿触发,参考 Foundry Automate)——
    # on_enter        : 仅"进入集合"时触发(新满足条件)——默认,适合一次性动作
    # on_enter_leave  : 进入 + 离开 都触发(离开=条件消除,供收尾)
    # run_on_all      : 每轮对全部当前命中执行(电平/批量,少数场景)
    trigger_mode: Mapped[str] = mapped_column(String(16), default="on_enter")
    # 静默:仍评估并记录,但不执行动作(用于影子试跑 / 临时停触)
    muted: Mapped[bool] = mapped_column(Boolean, default=False)

    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(20), default="draft")    # draft | published

    # 血缘出处（Schema 也是事实）：如业务探索草稿生成的影子哨兵
    # {"kind": "business_exploration", "sessionId", "documentId", "draftId", "draftKey", "sourceRefs"}
    source: Mapped[dict] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class SentinelMatchState(Base):
    """哨兵命中状态 — 边沿触发的"上次匹配集"。

    每个哨兵当前命中的对象集合,以 match_key 逐条记录(命中的 primary 实例 id,
    跨对象时为匹配元组签名)。每次评估与当前命中集做差:
      进入 = 当前 − 上次  → 触发动作
      离开 = 上次 − 当前  → (可选)触发收尾
    与哨兵配置数据分离,作为高频读写的运行时状态独立存放。
    """
    __tablename__ = "sentinel_match_state"
    __table_args__ = (
        UniqueConstraint("sentinel_id", "match_key", name="uq_sentinel_match_state_key"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    ontology_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    sentinel_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    # 命中键:primary 实例 id(跨对象时为元组签名,如 "a=oid|b=mid")
    match_key: Mapped[str] = mapped_column(String(500), nullable=False)
    # 命中元组明细(回显/证据用)：{alias: instanceId}
    match_detail: Mapped[dict] = mapped_column(JSON, default=dict)
    # completed 才表示 on_enter 已被消费；processing/pending/failed 都是
    # 可恢复的执行 claim，仍会在后续评估中续跑而不是静默吸收边沿。
    runtime_status: Mapped[str] = mapped_column(
        String(24), default="processing_enter", server_default="completed")
    # run_on_all 每完成一轮递增；崩溃重试沿用当前 epoch，下一轮才生成新键。
    execution_epoch: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0")
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class SentinelFiring(Base):
    """哨兵触发日志。"""
    __tablename__ = "sentinel_firings"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    ontology_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    sentinel_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    sentinel_name: Mapped[str] = mapped_column(String(200), nullable=True)

    # trigger_source: manual | change | schedule
    trigger_source: Mapped[str] = mapped_column(String(20), nullable=False)
    # 当前命中的对象元组: [{alias: instanceId, ...}]
    matches: Mapped[list] = mapped_column(JSON, default=list)
    match_count: Mapped[int] = mapped_column(Integer, default=0)
    # 边沿:本次新进入 / 离开 的命中(键列表)
    entered: Mapped[list] = mapped_column(JSON, default=list)
    left: Mapped[list] = mapped_column(JSON, default=list)
    # 动作执行结果: [{actionId, targetInstanceId, edge, status, logId, effects}]
    action_results: Mapped[list] = mapped_column(JSON, default=list)

    # fired | pending | no_change | no_match | muted | error | skipped
    status: Mapped[str] = mapped_column(String(20), default="fired")
    error: Mapped[str] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=True)

    # Immutable ontology release that owned this evaluation.  The id is the
    # authoritative scope; version remains useful for display/compatibility.
    ontology_version: Mapped[str | None] = mapped_column(
        String(20), nullable=True, index=True)
    ontology_release_id: Mapped[str | None] = mapped_column(
        String, nullable=True, index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Notification(Base):
    """通知 — 动作 notification 副作用的真实落地点(内部收件箱，可查询)。"""
    __tablename__ = "sentinel_notifications"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    ontology_id: Mapped[str] = mapped_column(String, nullable=False, index=True)

    channel: Mapped[str] = mapped_column(String(20), default="internal")
    recipient: Mapped[str] = mapped_column(String(300), nullable=True, index=True)
    subject: Mapped[str] = mapped_column(String(300), nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=True)

    related_object_id: Mapped[str] = mapped_column(String, nullable=True, index=True)
    action_id: Mapped[str] = mapped_column(String, nullable=True)

    status: Mapped[str] = mapped_column(String(20), default="delivered")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
