"""
哨兵边沿触发端到端自检 —— 验证"进入即触发、持续为真不重复触发、离开可收尾"

参考 Foundry Automate:命中集与上次做差,仅对"进入"触发动作。

    cd backend && python -m scripts.demos.sentinel_edge_demo
"""
import os, sys, uuid
_DB = "/tmp/sentinel_edge_demo.db"
if os.path.exists(_DB): os.remove(_DB)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB}"
os.environ["SENTINEL_AUTO_DISPATCH"] = "0"
os.environ["SENTINEL_SCAN_ENABLED"] = "0"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import Base, engine, SessionLocal  # noqa: E402
from app.models.user import User  # noqa: E402,F401
from app.models.ontology import OntologyProject  # noqa: E402
from app.models.ontology_formal import ObjectType, ActionType, ObjectInstance  # noqa: E402
from app.models.sentinel import Sentinel, Notification, SentinelMatchState  # noqa: E402
from app.services.sentinel import register_cdc, run_manual  # noqa: E402

uid = lambda: str(uuid.uuid4())


def main() -> int:
    Base.metadata.create_all(bind=engine); register_cdc()
    db = SessionLocal(); onto = uid()
    db.add(OntologyProject(id=onto, name="电商", domain="ec", created_by="demo"))
    ot = ObjectType(id=uid(), ontology_id=onto, name="Order", display_name="订单")
    db.add(ot)
    act = ActionType(id=uid(), ontology_id=onto, name="notify", display_name="通知",
                     object_type_id=ot.id,
                     rules=[{"id": "r1", "type": "notification", "enabled": True, "order": 1,
                             "config": {"channel": "internal", "recipientSource": "constant",
                                        "recipient": "ops", "subject": "大额订单",
                                        "messageTemplate": "订单 {object.order_no} 金额超阈值"}}])
    db.add(act)
    sentinel = Sentinel(id=uid(), ontology_id=onto, name="big", display_name="大额订单",
                        bindings=[{"alias": "a", "objectTypeId": ot.id, "filter": None}],
                        condition="a.amount >= 1000", primary_alias="a",
                        action_ids=[act.id], on_change=True, on_schedule=True,
                        scan_interval_seconds=5, trigger_mode="on_enter",
                        enabled=True, status="published")
    db.add(sentinel)
    o1 = ObjectInstance(id=uid(), ontology_id=onto, object_type_id=ot.id,
                        properties={"order_no": "SO-1", "amount": 1999}, source="manual")
    db.add(o1); db.commit()

    def notif_count():
        return db.query(Notification).filter(Notification.ontology_id == onto).count()

    ok = True
    # —— 第1次:订单已满足 → 进入 → 触发1次 ——
    r1 = run_manual(db, onto)
    c1 = notif_count()
    print(f"  第1次评估: fired={r1['fired']} no_change={r1['no_change']} 通知数={c1}")
    if not (r1["fired"] == 1 and c1 == 1): print("  ❌ 首次应触发1次"); ok = False

    # —— 第2、3次:状态没变(仍满足)→ 不应重复触发 ——
    run_manual(db, onto); r3 = run_manual(db, onto)
    c3 = notif_count()
    print(f"  第2、3次评估(状态未变): no_change → 通知数仍={c3}")
    if not (r3["no_change"] == 1 and r3["fired"] == 0 and c3 == 1):
        print("  ❌ 持续为真不应重复触发"); ok = False

    # —— 新增一个满足的订单 → 只对新进入的触发 ——
    o2 = ObjectInstance(id=uid(), ontology_id=onto, object_type_id=ot.id,
                        properties={"order_no": "SO-2", "amount": 3000}, source="manual")
    db.add(o2); db.commit()
    r4 = run_manual(db, onto); c4 = notif_count()
    print(f"  新增满足订单后: fired={r4['fired']} entered={r4['firings'][0]['entered'] and len(r4['firings'][0]['entered'])} 通知数={c4}")
    if not (r4["fired"] == 1 and c4 == 2): print("  ❌ 应只对新进入订单触发1次"); ok = False

    # —— 把 o1 降到阈值以下 → 离开(on_enter 模式不触发收尾,但应从命中集移除) ——
    o1.properties = {"order_no": "SO-1", "amount": 500}; db.add(o1); db.commit()
    r5 = run_manual(db, onto); c5 = notif_count()
    states = db.query(SentinelMatchState).filter(SentinelMatchState.sentinel_id == sentinel.id).count()
    print(f"  o1 降到阈值下: left 生效, 当前命中集={states}, 通知数仍={c5}")
    if not (c5 == 2 and states == 1): print("  ❌ 离开应从命中集移除且不新增通知"); ok = False

    # —— o1 再次升回阈值上 → 重新进入 → 再触发 ——
    o1.properties = {"order_no": "SO-1", "amount": 2500}; db.add(o1); db.commit()
    r6 = run_manual(db, onto); c6 = notif_count()
    print(f"  o1 升回阈值上: 重新进入 → fired={r6['fired']} 通知数={c6}")
    if not (r6["fired"] == 1 and c6 == 3): print("  ❌ 重新进入应再次触发"); ok = False

    print("\n" + ("✅ 边沿触发通过：进入即触发、持续为真不重复触发、新进入单独触发、离开移除命中、重新进入再触发。"
                  if ok else "⚠️ 存在失败，见上。"))
    db.close()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
