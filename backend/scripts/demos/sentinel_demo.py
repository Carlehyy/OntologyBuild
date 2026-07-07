"""
哨兵引擎端到端自检 —— 跨对象条件 + 多动作 + 三入口

场景：订单(A) 金额 > 其所属商家(B) 的信用额度 → 触发哨兵 →
执行两个动作:① 通知商家 ② 给订单打高风险标记。

验证:
  ① 跨对象条件(A.amount > B.credit_limit，经「归属」链接关联)
  ② 命中后执行多个动作
  ③ 三种入口都能驱动评估(手动 / 变化驱动 / 定期扫描)

    cd backend && python -m scripts.demos.sentinel_demo
"""
import os
import sys
import time
import uuid

_DB = "/tmp/sentinel_demo.db"
if os.path.exists(_DB):
    os.remove(_DB)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB}"
os.environ["SENTINEL_AUTO_DISPATCH"] = "0"   # 确定性：演示里显式调用变化驱动
os.environ["SENTINEL_SCAN_ENABLED"] = "0"    # 不起后台线程，手动调 run_scheduled

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import Base, engine, SessionLocal  # noqa: E402
from app.models.user import User  # noqa: E402,F401
from app.models.ontology import OntologyProject  # noqa: E402
from app.models.ontology_formal import (  # noqa: E402
    ObjectType, LinkType, ActionType, ObjectInstance, LinkInstance,
)
from app.models.sentinel import Sentinel, Notification  # noqa: E402
from app.services.sentinel import register_cdc, run_manual, run_for_change, run_scheduled  # noqa: E402

uid = lambda: str(uuid.uuid4())


def setup(db):
    onto = uid()
    db.add(OntologyProject(id=onto, name="电商本体", domain="ecommerce", created_by="demo"))
    ot_order = ObjectType(id=uid(), ontology_id=onto, name="Order", display_name="订单")
    ot_merchant = ObjectType(id=uid(), ontology_id=onto, name="Merchant", display_name="商家")
    db.add_all([ot_order, ot_merchant])
    lt_belongs = LinkType(id=uid(), ontology_id=onto, name="belongsTo", display_name="归属",
                          source_object_type_id=ot_order.id, target_object_type_id=ot_merchant.id,
                          cardinality="many-to-one")
    db.add(lt_belongs)
    # 动作1：通知商家(沿归属链接解析邮箱)
    act_notify = ActionType(
        id=uid(), ontology_id=onto, name="notifyMerchant", display_name="通知商家",
        object_type_id=ot_order.id,
        rules=[{"id": "r1", "type": "notification", "enabled": True, "order": 1, "config": {
            "channel": "email", "recipientSource": "link", "linkTypeId": lt_belongs.id,
            "recipientProperty": "email", "subject": "大额订单风控提醒",
            "messageTemplate": "订单 {object.order_no} 金额 {object.amount} 超出信用额度，请核实。"}}])
    # 动作2：给订单打高风险标记(更新属性)
    act_flag = ActionType(
        id=uid(), ontology_id=onto, name="flagRisk", display_name="标记高风险",
        object_type_id=ot_order.id,
        rules=[{"id": "r1", "type": "update_property", "enabled": True, "order": 1, "config": {
            "targetProperty": "risk_flag", "valueSource": "constant", "value": "\"HIGH\""}}])
    db.add_all([act_notify, act_flag])
    # 哨兵：跨对象条件 + 两个动作
    sentinel = Sentinel(
        id=uid(), ontology_id=onto, name="bigOrderRisk", display_name="大额订单超信用额度",
        bindings=[
            {"alias": "a", "objectTypeId": ot_order.id, "filter": "a.status == 'submitted'"},
            {"alias": "b", "objectTypeId": ot_merchant.id, "filter": None},
        ],
        links=[{"from": "a", "linkTypeId": lt_belongs.id, "to": "b"}],
        condition="a.amount > b.credit_limit",     # ← 跨对象条件
        primary_alias="a",
        action_ids=[act_notify.id, act_flag.id],   # ← 多个动作
        on_change=True, on_schedule=True, scan_interval_seconds=5,
        enabled=True, status="published",
    )
    db.add(sentinel)
    db.commit()
    return onto, ot_order, ot_merchant, lt_belongs


def seed_data(db, onto, ot_order, ot_merchant, lt_belongs, amount, status="submitted"):
    m = ObjectInstance(id=uid(), ontology_id=onto, object_type_id=ot_merchant.id,
                       properties={"name": "优选数码", "email": "shop@yx.com", "credit_limit": 1000},
                       source="manual")
    o = ObjectInstance(id=uid(), ontology_id=onto, object_type_id=ot_order.id,
                       properties={"order_no": "SO-001", "amount": amount, "status": status},
                       source="manual")
    db.add_all([m, o])
    db.add(LinkInstance(id=uid(), ontology_id=onto, link_type_id=lt_belongs.id,
                        source_object_id=o.id, target_object_id=m.id))
    db.commit()
    return o, m


def report(db, onto, title):
    notes = db.query(Notification).filter(Notification.ontology_id == onto).all()
    orders = db.query(ObjectInstance).filter(
        ObjectInstance.ontology_id == onto,
        ObjectInstance.object_type_id == ot_order_id).all()
    flagged = [o for o in orders if (o.properties or {}).get("risk_flag") == "HIGH"]
    print(f"  [{title}] 通知数={len(notes)} 高风险标记订单数={len(flagged)}")
    return notes, flagged


def main() -> int:
    Base.metadata.create_all(bind=engine)
    register_cdc()
    global ot_order_id
    ok = True

    # ===== 入口①：手动触发 =====
    db = SessionLocal()
    onto, ot_order, ot_merchant, lt_belongs = setup(db)
    ot_order_id = ot_order.id
    seed_data(db, onto, ot_order, ot_merchant, lt_belongs, amount=1999)  # 1999 > 1000 → 命中
    res = run_manual(db, onto)
    notes, flagged = report(db, onto, "手动触发")
    if not (res["fired"] == 1 and len(notes) == 1 and len(flagged) == 1
            and notes[0].recipient == "shop@yx.com"):
        print("  ❌ 手动触发未达预期", res); ok = False
    db.close()

    # ===== 入口②：变化驱动(挑出引用该对象类型的哨兵) =====
    db = SessionLocal()
    onto2, ot_order2, ot_merchant2, lt2 = setup(db)
    o2, m2 = seed_data(db, onto2, ot_order2, ot_merchant2, lt2, amount=1999)
    # 模拟某属性变化触发评估(变化驱动按对象类型挑哨兵)
    res2 = run_for_change(db, onto2, ot_order2.id, ["amount"])
    notes2 = db.query(Notification).filter(Notification.ontology_id == onto2).all()
    print(f"  [变化驱动] fired={res2['fired']} 通知数={len(notes2)}")
    if not (res2["fired"] == 1 and len(notes2) == 1):
        print("  ❌ 变化驱动未达预期", res2); ok = False
    db.close()

    # ===== 入口③：定期扫描(到达间隔) =====
    db = SessionLocal()
    onto3, ot_order3, ot_merchant3, lt3 = setup(db)
    seed_data(db, onto3, ot_order3, ot_merchant3, lt3, amount=1999)
    res3 = run_scheduled(db)  # last_scanned_at 为空 → 立即到期
    notes3 = db.query(Notification).filter(Notification.ontology_id == onto3).all()
    fired3 = sum(1 for f in res3["firings"] if f["status"] == "fired")
    print(f"  [定期扫描] fired={fired3} 通知数={len(notes3)}")
    if not (fired3 >= 1 and len(notes3) == 1):  # run_scheduled 跨本体全局，onto3 自身命中 1 条即可
        print("  ❌ 定期扫描未达预期", res3); ok = False
    db.close()

    # ===== 反例:金额未超额 → 不应触发 =====
    db = SessionLocal()
    onto4, ot_order4, ot_merchant4, lt4 = setup(db)
    seed_data(db, onto4, ot_order4, ot_merchant4, lt4, amount=500)  # 500 < 1000 → 不命中
    res4 = run_manual(db, onto4)
    notes4 = db.query(Notification).filter(Notification.ontology_id == onto4).all()
    print(f"  [反例:未超额] fired={res4['fired']} 通知数={len(notes4)}")
    if not (res4["fired"] == 0 and len(notes4) == 0):
        print("  ❌ 反例未达预期(不应触发)", res4); ok = False
    db.close()

    print("\n" + ("✅ 全部通过：跨对象条件(订单金额 > 商家信用额度) + 多动作(通知+标记) + 三入口(手动/变化/扫描)均生效；未超额订单被正确忽略。"
                  if ok else "⚠️ 存在失败，见上。"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
