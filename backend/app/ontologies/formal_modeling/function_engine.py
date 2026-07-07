"""
后端函数引擎 (Function Engine)

执行本体函数 (OntologyFunction)。安全策略：
  - language='expression' → 用 safe_eval 在白名单 AST 沙箱里求值
  - language='typescript'  → 后端不执行（标记为客户端执行），返回提示
四类：object / object_set / action_validation / query
"""
from __future__ import annotations
import time
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.models.ontology_formal import OntologyFunction, ObjectInstance
from app.services.formal.safe_eval import safe_eval, SafeEvalError


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _build_scope(fn: OntologyFunction, db: Session, ontology_id: str,
                 obj_props: Optional[dict], params: dict) -> dict[str, Any]:
    scope: dict[str, Any] = {
        "object": obj_props or {},
        "params": params or {},
    }
    # object_set / query 注入对象集合
    if fn.function_type in ("object_set", "query", "action_validation"):
        q = db.query(ObjectInstance).filter(ObjectInstance.ontology_id == ontology_id)
        if fn.target_object_type_id:
            q = q.filter(ObjectInstance.object_type_id == fn.target_object_type_id)
        scope["objects"] = [i.properties or {} for i in q.all()]
    else:
        scope["objects"] = []
    return scope


def execute_function(fn: OntologyFunction, db: Session, ontology_id: str,
                     obj_props: Optional[dict] = None,
                     params: Optional[dict] = None) -> dict[str, Any]:
    """执行单个函数，返回 {success, result, error, durationMs, timestamp}（camelCase）"""
    start = time.time()
    ts = _now_iso()
    params = params or {}

    if not fn.enabled:
        return {"success": False, "error": f'函数 "{fn.display_name}" 已禁用',
                "durationMs": 0, "timestamp": ts}

    if fn.language == "typescript":
        # 后端不执行 TS，交给前端 functionEngine；这里给出明确契约
        return {"success": False,
                "error": "TypeScript 函数在前端执行（请用图谱编辑页的函数测试器）；后端仅支持 expression 语言。",
                "durationMs": 0, "timestamp": ts, "clientSide": True}

    try:
        scope = _build_scope(fn, db, ontology_id, obj_props, params)
        result = safe_eval(fn.body, scope)

        if fn.function_type == "action_validation":
            # 规范化为 ValidationResult
            if isinstance(result, bool):
                result = {"valid": result, "errors": [] if result else ["校验失败"]}
            elif isinstance(result, dict) and "valid" in result:
                result.setdefault("errors", [])
            else:
                result = {"valid": True, "errors": []}

        return {"success": True, "result": result, "data": result,
                "durationMs": int((time.time() - start) * 1000), "timestamp": ts}
    except SafeEvalError as e:
        return {"success": False, "error": str(e),
                "durationMs": int((time.time() - start) * 1000), "timestamp": ts}
    except Exception as e:  # noqa: BLE001
        return {"success": False, "error": str(e),
                "durationMs": int((time.time() - start) * 1000), "timestamp": ts}


def compute_derived_properties(object_type, instance_props: dict, db: Session,
                               ontology_id: str) -> dict[str, Any]:
    """计算某对象实例的所有 computed 属性"""
    computed: dict[str, Any] = {}
    for prop in (object_type.properties or []):
        if prop.get("source") == "computed" and prop.get("functionId"):
            fn = db.query(OntologyFunction).filter(
                OntologyFunction.id == prop["functionId"],
                OntologyFunction.ontology_id == ontology_id,
            ).first()
            if fn:
                r = execute_function(fn, db, ontology_id, obj_props=instance_props)
                computed[prop["name"]] = r.get("result") if r.get("success") else f"#ERROR: {r.get('error')}"
    return computed


def test_function(db: Session, ontology_id: str, body) -> dict[str, Any]:
    """API 入口：测试一个函数 (body 是 TestFunctionRequest)"""
    fn = db.query(OntologyFunction).filter(
        OntologyFunction.id == body.function_id,
        OntologyFunction.ontology_id == ontology_id,
    ).first()
    if not fn:
        return {"success": False, "error": "函数不存在", "durationMs": 0, "timestamp": _now_iso()}

    obj_props = body.object_props
    if obj_props is None and body.object_instance_id:
        inst = db.query(ObjectInstance).filter(
            ObjectInstance.id == body.object_instance_id,
            ObjectInstance.ontology_id == ontology_id,
        ).first()
        obj_props = inst.properties if inst else None

    return execute_function(fn, db, ontology_id, obj_props=obj_props, params=body.params)
