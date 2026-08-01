"""
正规本体模型 API (Formal Ontology) — /api/v2/formal/ontologies/{ontology_id}/...

平台核心：本体建模 + 数据采集落地。
所有响应统一包裹 {"data": ...}，与前端 apiClient 解包逻辑一致。
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from typing import Optional

from app.deps import get_db, get_current_user, require_admin
from app.ontologies.access import ontology_access_guard
from app.models.ontology_formal import (
    ObjectType, LinkType, ActionType, OntologyFunction,
    ObjectInstance, LinkInstance, ActionExecutionLog,
)
from app.ontologies.runtime_fence import _ontology_build_lock
from app.ontologies.formal_modeling import (
    action_workflow_service,
    dashboard_queries,
    instance_service,
    schema_authoring_service,
)
from app.ontologies.formal_modeling.instance_service import (
    _instance_browser_release,
    _instance_summary,
    _mapping_matches_link_type,
    _mapping_matches_object_type,
    _mapping_value,
    _release_catalog_item,
    _release_dataset_associations,
)
from app.ontologies.formal_modeling.schema_authoring_service import (
    FIELDS_ACTION,
    FIELDS_FUNCTION,
    FIELDS_INSTANCE,
    FIELDS_LINK_INSTANCE,
    FIELDS_LINK_TYPE,
    FIELDS_OBJECT_TYPE,
    _crud,
    _dedup_properties,
    _reject_direct_runtime_data_write,
    _require_schema_draft,
    _revision_of,
    _runtime_state,
    _scrub_dangling_references,
    _scrub_orphan_data,
    _upsert_items,
    validate_model,
)
from app.ontologies.formal_modeling.runtime_support import (
    _approval_instance_label,
    _current_release_view,
    _fact_to_dict,
    _naive_utc,
    _ok,
    _orm_view,
    _raise_validation_failed,
    _release_fact_query,
    _require_ontology,
)
from app.schemas import ontology_formal as S
from app.ontologies.formal_modeling.action_engine import execute_action
from app.ontologies.formal_modeling.function_engine import (
    compute_object_set_aggregates,
    test_function,
)

router = APIRouter(dependencies=[Depends(ontology_access_guard)])
logger = schema_authoring_service.logger


# ============================================================
#  Full Ontology — schema authoring adapters
# ============================================================
@router.get("/{ontology_id}/full")
def get_full_ontology(ontology_id: str, db: Session = Depends(get_db), _=Depends(get_current_user)):
    return schema_authoring_service.get_full_ontology(
        ontology_id,
        db,
        require_ontology_fn=_require_ontology,
        revision_of_fn=_revision_of,
        ok_fn=_ok,
    )


@router.put("/{ontology_id}/full")
def save_full_ontology(ontology_id: str, body: S.SaveFullOntologyRequest,
                       db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    """用前端图谱编辑页的当前状态整体替换该本体下的建模 + 实例数据。

    采用 upsert + 清理：保留 body 中带 id 的记录（按 id upsert），
    删除库中存在但 body 未提供的记录。执行日志不在此处理。
    """
    return schema_authoring_service.save_full_ontology(
        ontology_id,
        body,
        db,
        current_user,
        require_ontology_fn=_require_ontology,
        require_schema_draft_fn=_require_schema_draft,
        revision_of_fn=_revision_of,
        upsert_items_fn=_upsert_items,
        dedup_properties_fn=_dedup_properties,
        scrub_dangling_references_fn=_scrub_dangling_references,
        raise_validation_failed_fn=_raise_validation_failed,
        validate_model_fn=validate_model,
        get_full_ontology_fn=get_full_ontology,
    )


@router.patch("/{ontology_id}/full")
def patch_full_ontology(ontology_id: str, body: S.PatchOntologyRequest,
                        db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    """只应用自上次保存以来的变更/删除，机制与 PUT /full 完全等价：
    并发检测 → 合并视图强制校验 → 应用 delta → 属性事实 + 派生重算 →
    孤儿清理 → 哨兵评估。负载 O(变更) 而非 O(全模型)。
    """
    return schema_authoring_service.patch_full_ontology(
        ontology_id,
        body,
        db,
        current_user,
        require_ontology_fn=_require_ontology,
        require_schema_draft_fn=_require_schema_draft,
        revision_of_fn=_revision_of,
        upsert_items_fn=_upsert_items,
        dedup_properties_fn=_dedup_properties,
        scrub_orphan_data_fn=_scrub_orphan_data,
        scrub_dangling_references_fn=_scrub_dangling_references,
        raise_validation_failed_fn=_raise_validation_failed,
        validate_model_fn=validate_model,
        ok_fn=_ok,
    )


def _schema_authoring_compatibility_helpers() -> dict:
    """Resolve router-era monkeypatch targets at request time."""
    return {
        "_require_ontology": _require_ontology,
        "_require_schema_draft": _require_schema_draft,
        "_runtime_state": _runtime_state,
        "_orm_view": _orm_view,
        "_raise_validation_failed": _raise_validation_failed,
        "_ok": _ok,
        "validate_model": validate_model,
    }


router.include_router(_crud(
    ObjectType,
    S.ObjectTypeCreate,
    S.ObjectTypeUpdate,
    S.ObjectTypeOut,
    "object-types",
    compatibility_helpers=_schema_authoring_compatibility_helpers,
))
router.include_router(_crud(
    LinkType,
    S.LinkTypeCreate,
    S.LinkTypeUpdate,
    S.LinkTypeOut,
    "link-types",
    compatibility_helpers=_schema_authoring_compatibility_helpers,
))
router.include_router(_crud(
    ActionType,
    S.ActionTypeCreate,
    S.ActionTypeUpdate,
    S.ActionTypeOut,
    "actions",
    compatibility_helpers=_schema_authoring_compatibility_helpers,
))
router.include_router(_crud(
    OntologyFunction,
    S.FunctionCreate,
    S.FunctionUpdate,
    S.FunctionOut,
    "functions",
    compatibility_helpers=_schema_authoring_compatibility_helpers,
))


# ============================================================
#  Object Instances (数据采集落地点 — 自定义以支持 external_id 去重)
# ============================================================
@router.get("/{ontology_id}/instances")
def list_instances(ontology_id: str, object_type_id: Optional[str] = None,
                   expected_release_id: Optional[str] = None,
                   db: Session = Depends(get_db), _=Depends(get_current_user)):
    return instance_service.list_instances(
        ontology_id,
        object_type_id,
        expected_release_id,
        db,
    )


@router.get("/{ontology_id}/instance-browser/catalog")
def instance_browser_catalog(
        ontology_id: str,
        db: Session = Depends(get_db),
        _=Depends(get_current_user)):
    """Published schema tree plus counts from its current runtime projection."""
    return instance_service.instance_browser_catalog(ontology_id, db)


@router.post("/{ontology_id}/instance-browser/adopt-legacy")
def instance_browser_adopt_legacy(
        ontology_id: str,
        body: S.AdoptLegacyProjectionRequest,
        db: Session = Depends(get_db),
        current_user=Depends(require_admin)):
    """Explicit admin-only repair; never weakens release-scoped reads."""
    return instance_service.instance_browser_adopt_legacy(
        ontology_id,
        body,
        db,
        current_user,
    )


@router.get("/{ontology_id}/instance-browser/objects")
def instance_browser_objects(
        ontology_id: str,
        object_type_id: str = Query(..., min_length=1),
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
        keyword: Optional[str] = Query(None, max_length=200),
        db: Session = Depends(get_db),
        _=Depends(get_current_user)):
    return instance_service.instance_browser_objects(
        ontology_id,
        object_type_id,
        page,
        page_size,
        keyword,
        db,
    )


@router.get("/{ontology_id}/instance-browser/links")
def instance_browser_links(
        ontology_id: str,
        link_type_id: str = Query(..., min_length=1),
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
        keyword: Optional[str] = Query(None, max_length=200),
        db: Session = Depends(get_db),
        _=Depends(get_current_user)):
    return instance_service.instance_browser_links(
        ontology_id,
        link_type_id,
        page,
        page_size,
        keyword,
        db,
    )


@router.post("/{ontology_id}/instances", status_code=201)
def create_instance(ontology_id: str, body: S.ObjectInstanceCreate,
                    db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    return instance_service.create_instance(
        ontology_id,
        body,
        db,
        current_user,
        reject_runtime_write_fn=_reject_direct_runtime_data_write,
    )


@router.put("/{ontology_id}/instances/{instance_id}")
def update_instance(ontology_id: str, instance_id: str, body: S.ObjectInstanceUpdate,
                    db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    return instance_service.update_instance(
        ontology_id,
        instance_id,
        body,
        db,
        current_user,
        reject_runtime_write_fn=_reject_direct_runtime_data_write,
    )


@router.get("/{ontology_id}/instances/{instance_id}/facts")
def list_instance_facts(ontology_id: str, instance_id: str,
                        property_name: Optional[str] = None, limit: int = 200,
                        db: Session = Depends(get_db), _=Depends(get_current_user)):
    """实例的属性级变更历史（Fact 溯源层），按时间倒序。"""
    return instance_service.list_instance_facts(
        ontology_id,
        instance_id,
        property_name,
        limit,
        db,
    )


@router.get("/{ontology_id}/instances/{instance_id}/as-of")
def instance_as_of(ontology_id: str, instance_id: str, t: str,
                   db: Session = Depends(get_db), _=Depends(get_current_user)):
    """时态回放：时刻 T 的实例投影 = recorded_at ≤ T 且未被 T 前事实 supersede 的
    每个属性最新事实。含存在性（墓碑事实之后视为已删除）。t 为 ISO 时间串。"""
    return instance_service.instance_as_of(
        ontology_id,
        instance_id,
        t,
        db,
    )


@router.delete("/{ontology_id}/instances/{instance_id}", status_code=204)
def delete_instance(ontology_id: str, instance_id: str,
                    db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    return instance_service.delete_instance(
        ontology_id,
        instance_id,
        db,
        current_user,
        reject_runtime_write_fn=_reject_direct_runtime_data_write,
    )


# ============================================================
#  Link Instances
# ============================================================
@router.get("/{ontology_id}/link-instances")
def list_link_instances(
        ontology_id: str, expected_release_id: Optional[str] = None,
        db: Session = Depends(get_db), _=Depends(get_current_user)):
    return instance_service.list_link_instances(
        ontology_id,
        expected_release_id,
        db,
    )


@router.post("/{ontology_id}/link-instances", status_code=201)
def create_link_instance(ontology_id: str, body: S.LinkInstanceCreate,
                         db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    return instance_service.create_link_instance(
        ontology_id,
        body,
        db,
        current_user,
        reject_runtime_write_fn=_reject_direct_runtime_data_write,
    )


@router.delete("/{ontology_id}/link-instances/{link_id}", status_code=204)
def delete_link_instance(ontology_id: str, link_id: str,
                         db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    return instance_service.delete_link_instance(
        ontology_id,
        link_id,
        db,
        current_user,
        reject_runtime_write_fn=_reject_direct_runtime_data_write,
    )


# ============================================================
#  Runtime: Run Action / Test Function / HITL 审批
# ============================================================
@router.post("/{ontology_id}/run-action")
def run_action(ontology_id: str, body: S.RunActionRequest,
               db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    # Projection locking remains an adapter-level transaction boundary.
    with _ontology_build_lock(db, ontology_id):
        return action_workflow_service.run_action_locked(
            ontology_id,
            body,
            db,
            current_user,
            execute_action_fn=execute_action,
        )


@router.get("/{ontology_id}/pending-actions")
def list_pending_actions(ontology_id: str, release_id: Optional[str] = None,
                         current_release_only: bool = False,
                         db: Session = Depends(get_db), _=Depends(get_current_user)):
    """待审批或待恢复动作。

    ``executing`` 表示人的批准事实已经耐久化，但进程可能在技术执行/关联日志
    之间退出；它必须继续出现在治理队列中，才能用稳定幂等键安全恢复。
    """
    return action_workflow_service.list_pending_actions(
        ontology_id,
        release_id,
        current_release_only,
        db,
    )


@router.post("/{ontology_id}/action-logs/{log_id}/decide")
def decide_pending_action(ontology_id: str, log_id: str, body: S.DecisionRequest,
                          db: Session = Depends(get_db),
                          current_user=Depends(require_admin)):
    """HITL 决策：批准 → 写决策事实并真正执行（执行产生的事实 caused_by=决策事实）；
    拒绝 → 同样写决策事实（拒绝也要溯源，供将来分析"哪些建议被人否决了"）。"""
    # Approval is a real side effect and must not race the transient
    # ``projecting`` interval of a concurrent automatic/manual full rebuild.
    # Rejection has no business side effect and remains immediately available.
    if (body.decision or "").lower() == "approved":
        with _ontology_build_lock(db, ontology_id):
            return _decide_pending_action_locked(
                ontology_id,
                log_id,
                body,
                db,
                current_user,
            )
    return _decide_pending_action_locked(
        ontology_id,
        log_id,
        body,
        db,
        current_user,
    )


def _decide_pending_action_locked(
        ontology_id: str, log_id: str, body: S.DecisionRequest,
        db: Session, current_user):
    return action_workflow_service.decide_pending_action_locked(
        ontology_id,
        log_id,
        body,
        db,
        current_user,
        execute_action_fn=execute_action,
    )


@router.post("/{ontology_id}/test-function")
def run_test_function(ontology_id: str, body: S.TestFunctionRequest,
                      db: Session = Depends(get_db), _=Depends(get_current_user)):
    _require_ontology(db, ontology_id)
    result = test_function(db, ontology_id, body)
    return _ok(result)


@router.get("/{ontology_id}/object-types/{object_type_id}/aggregates")
def object_set_aggregates(ontology_id: str, object_type_id: str,
                          db: Session = Depends(get_db), _=Depends(get_current_user)):
    """某对象类型的集合指标 —— 跑该类型下所有 object_set 函数并返回结果。"""
    _require_ontology(db, ontology_id)
    return _ok(compute_object_set_aggregates(db, ontology_id, object_type_id))


# ============================================================
#  本体驾驶舱（Overview）— 详情页一屏看懂：模型 / 数据 / 运行 / 事实流 / 健康
# ============================================================
@router.get("/{ontology_id}/overview")
def ontology_overview(ontology_id: str, db: Session = Depends(get_db), _=Depends(get_current_user)):
    """Latest-release dashboard backed by the immutable release snapshot.

    Schema/configuration counts come from ``current_release_id`` rather than
    mutable runtime tables.  Runtime projections and telemetry are then
    constrained to identifiers/version lineage owned by that release.
    """
    return dashboard_queries.ontology_overview(ontology_id, db)


@router.get("/{ontology_id}/facts/recent")
def recent_facts(ontology_id: str, limit: int = 30, kind: Optional[str] = None,
                 release_id: Optional[str] = None,
                 current_release_only: bool = False,
                 db: Session = Depends(get_db), _=Depends(get_current_user)):
    """本体级事实流（跨实例，时间倒序），带可读主体标签——治理页的时间线。"""
    return dashboard_queries.recent_facts(
        ontology_id,
        limit,
        kind,
        release_id,
        current_release_only,
        db,
    )


# ============================================================
#  自治等级（Autonomy）— 哨兵×动作的人工批准率统计与晋升建议
# ============================================================
AUTONOMY_PROMOTE_MIN = dashboard_queries.AUTONOMY_PROMOTE_MIN
AUTONOMY_PROMOTE_RATE = dashboard_queries.AUTONOMY_PROMOTE_RATE
AUTONOMY_DEMOTE_FAILRATE = dashboard_queries.AUTONOMY_DEMOTE_FAILRATE


@router.get("/{ontology_id}/autonomy")
def autonomy_stats(ontology_id: str, release_id: Optional[str] = None,
                   db: Session = Depends(get_db), _=Depends(get_current_user)):
    """按动作统计 HITL 决策历史，给出自治等级与晋升/降级建议。

    等级模型（自治是挣来的，不是配出来的）：
      L0 影子   绑定的哨兵全部静默——只观察记录，不产生副作用
      L1 人审   requires_approval=True，每次真实执行等人拍板
      L2 自动   requires_approval=False，命中即执行
    晋升依据 = 近期人工批准率（人几乎总是点"批准"，说明规则已可信）。
    """
    return dashboard_queries.autonomy_stats(ontology_id, release_id, db)


# ============================================================
#  Execution Logs
# ============================================================
@router.get("/{ontology_id}/logs")
def list_logs(ontology_id: str, db: Session = Depends(get_db), _=Depends(get_current_user)):
    return dashboard_queries.list_logs(ontology_id, db)
