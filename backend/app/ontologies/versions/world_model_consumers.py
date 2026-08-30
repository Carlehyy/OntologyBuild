"""世界模型消费方影响分析：本体结构演进对已发布推演服务的破坏性提示。

推演服务在本体语义注册（applicable_object_types.object_type_ids）中声明的
对象类型一旦被新版本删除，服务将静默失效——agent 侧调用时会实时拦截并给出
原因，但维护者应在新版本发布确认前知情。本模块把该影响以"实时查询、不参与
impact 哈希"的方式附加到发布影响预览（get_draft_impact）：哈希只覆盖纯结构
diff，试跑与发布之间的 impact_hash 比对语义保持不变。
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.world_model.models import WorldModelService


def affected_services(db: Session, ontology_id: str, report: dict) -> list[dict]:
    """列出"声明的对象类型 ∩ 本次删除的类型"非空的在线/下线推演服务。"""
    deleted = set(
        ((report.get("resources") or {}).get("objectTypes") or {}).get("deleted") or []
    )
    if not deleted:
        return []
    # 语义注册是 JSON 列（无外键），服务量级小，Python 侧过滤即可
    out: list[dict] = []
    for svc in db.query(WorldModelService).all():
        binding = svc.applicable_object_types
        if not isinstance(binding, dict) or binding.get("ontology_id") != ontology_id:
            continue
        overlap = sorted(set(binding.get("object_type_ids") or []) & deleted)
        if overlap:
            out.append({
                "serviceId": svc.id,
                "name": svc.name,
                "status": svc.status,
                "missingObjectTypeIds": overlap,
            })
    out.sort(key=lambda item: item["name"])
    return out
