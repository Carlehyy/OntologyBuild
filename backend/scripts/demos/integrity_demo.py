"""
引用完整性清理自检 —— 验证删除被引用实体后,悬空引用被正确清理。

  python -m scripts.integrity_demo
"""
import os, sys, uuid
_DB = "/tmp/integrity_demo.db"
if os.path.exists(_DB): os.remove(_DB)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB}"
os.environ["SENTINEL_AUTO_DISPATCH"] = "0"; os.environ["SENTINEL_SCAN_ENABLED"] = "0"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import Base, engine, SessionLocal  # noqa: E402
from app.models.user import User  # noqa: E402,F401
from app.models.ontology import OntologyProject  # noqa: E402
from app.models.ontology_formal import ObjectType, LinkType, ActionType, OntologyFunction  # noqa: E402
from app.models.sentinel import Sentinel  # noqa: E402
from app.routers.formal import _scrub_dangling_references  # noqa: E402

uid = lambda: str(uuid.uuid4())


def main() -> int:
    Base.metadata.create_all(bind=engine)
    db = SessionLocal(); onto = uid()
    db.add(OntologyProject(id=onto, name="t", domain="d", created_by="u"))

    # 存活的对象类型 A;待删除的对象类型 B、关系 L、函数 F
    a = ObjectType(id=uid(), ontology_id=onto, name="A", display_name="A")
    db.add(a); db.commit()
    DEL_OT, DEL_LT, DEL_FN = "ot_del", "lt_del", "fn_del"

    # 动作:rules 同时引用 待删关系(create_link) + 待删类型(create_object)
    #        + 待删函数(update_property.functionId);validation 也指向待删函数
    act = ActionType(id=uid(), ontology_id=onto, name="act", display_name="动作",
                     object_type_id=a.id, validation_function_id=DEL_FN,
                     rules=[
                       {"id": "r1", "type": "create_link", "config": {"type": "create_link", "linkTypeId": DEL_LT}},
                       {"id": "r2", "type": "create_object", "config": {"type": "create_object", "targetObjectTypeId": DEL_OT, "propertyMappings": [{"targetProperty": "x", "functionId": DEL_FN}]}},
                       {"id": "r3", "type": "update_property", "config": {"type": "update_property", "targetProperty": "y", "functionId": DEL_FN}},
                     ])
    db.add(act)

    # 哨兵:绑定 存活A + 待删B;动作含 存活act + 不存在act;关系含 待删L
    sen = Sentinel(id=uid(), ontology_id=onto, name="s", display_name="哨兵",
                   bindings=[{"alias": "a", "objectTypeId": a.id}, {"alias": "b", "objectTypeId": DEL_OT}],
                   links=[{"from": "a", "linkTypeId": DEL_LT, "to": "b"}],
                   primary_alias="b", action_ids=[act.id, "act_ghost"])
    db.add(sen); db.commit()

    # 此时库里并没有 DEL_OT/DEL_LT/DEL_FN/act_ghost(模拟它们已被删除) → 跑清理
    counts = _scrub_dangling_references(db, onto)
    db.commit()
    print("  清理计数:", counts)

    ok = True
    act = db.query(ActionType).filter(ActionType.id == act.id).first()
    cfgs = {r["id"]: r["config"] for r in act.rules}
    checks = [
        ("create_link.linkTypeId 已清空", cfgs["r1"].get("linkTypeId") == ""),
        ("create_object.targetObjectTypeId 已清空", cfgs["r2"].get("targetObjectTypeId") == ""),
        ("propertyMappings.functionId 已清空", cfgs["r2"]["propertyMappings"][0].get("functionId") == ""),
        ("update_property.functionId 已清空", cfgs["r3"].get("functionId") == ""),
        ("update_property.targetProperty 保留(非id引用不动)", cfgs["r3"].get("targetProperty") == "y"),
        ("validation_function_id 已置空", act.validation_function_id is None),
    ]
    sen = db.query(Sentinel).filter(Sentinel.id == sen.id).first()
    checks += [
        ("哨兵 action_ids 剔除幽灵动作", sen.action_ids == [act.id]),
        ("哨兵 bindings 剔除已删对象类型", [b["objectTypeId"] for b in sen.bindings] == [a.id]),
        ("哨兵 primary_alias 重置为存活绑定", sen.primary_alias == "a"),
        ("哨兵 links 剔除已删关系", sen.links == []),
    ]
    for name, passed in checks:
        print(f"  {'✓' if passed else '❌'} {name}")
        ok = ok and passed

    print("\n" + ("✅ 引用完整性清理全部通过。" if ok else "⚠️ 存在失败,见上。"))
    db.close()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
