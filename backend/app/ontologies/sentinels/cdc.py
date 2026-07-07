"""
变化捕获 (CDC) — 监听对象/链接实体改动，触发变化驱动评估

  - before_flush：收集本次刷写中变化的 ObjectInstance(新增/属性变更/删除)
    与 LinkInstance(新增/删除——关系拓扑变化对跨对象哨兵同样是"变化")。
  - after_commit：对每个(对象类型, 变化属性)触发 run_for_change；
    链接拓扑变化触发 run_for_link_change(后台线程，独立 Session)。

断环与去重：
  - in_sentinel_run   哨兵动作执行中 → 跳过即时再触发(动作回写→再触发死循环)。
  - suppress_dispatch 编辑器整体保存中 → 跳过(保存路径已同步调用 run_for_save，
    否则 CDC 后台线程与 run_for_save 并发评估同批哨兵，边沿差分竞态可能重复放炮)。
  遗漏条件由定期扫描兜底。
"""
from __future__ import annotations

import os
import threading
import uuid

from sqlalchemy import event, inspect
from sqlalchemy.orm import Session

from app.models.ontology_formal import ObjectInstance, LinkInstance
from app.services.sentinel.evaluator import in_sentinel_run

# 编辑器保存等"自己会同步评估哨兵"的路径在 session.info 里置此键，
# 抑制 CDC 的并行评估（session 随请求结束销毁，无需显式复位）
SUPPRESS_KEY = "_sentinel_suppress_dispatch"

AUTO_DISPATCH = os.getenv("SENTINEL_AUTO_DISPATCH", "1") not in ("0", "false", "False")
_KEY = "_sentinel_changes"        # session.info: {(ontology_id, object_type_id): set(changed_keys)}
_LINK_KEY = "_sentinel_link_changes"  # session.info: set(ontology_id)


def _record(session: Session, target: ObjectInstance, keys: list) -> None:
    bucket = session.info.setdefault(_KEY, {})
    k = (target.ontology_id, target.object_type_id)
    bucket.setdefault(k, set()).update(keys)


def _record_link(session: Session, target: LinkInstance) -> None:
    session.info.setdefault(_LINK_KEY, set()).add(target.ontology_id)


def _before_flush(session: Session, flush_context, instances) -> None:
    for obj in list(session.new):
        if isinstance(obj, ObjectInstance):
            if obj.id is None:
                obj.id = str(uuid.uuid4())
            _record(session, obj, list((obj.properties or {}).keys()))
        elif isinstance(obj, LinkInstance):
            _record_link(session, obj)
    for obj in list(session.deleted):
        # 删除同样是状态变化：哨兵条件可能因实例/链接消失而"离开"或"进入"
        if isinstance(obj, ObjectInstance):
            _record(session, obj, ["__deleted__"])
        elif isinstance(obj, LinkInstance):
            _record_link(session, obj)
    for obj in list(session.dirty):
        if not isinstance(obj, ObjectInstance):
            continue
        st = inspect(obj)
        ph, ch = st.attrs.properties.history, st.attrs.computed.history
        if not ph.has_changes() and not ch.has_changes():
            continue
        old = ph.deleted[0] if ph.deleted else (obj.properties or {})
        new = obj.properties or {}
        changed = sorted(set(old) | set(new))
        changed = [k for k in changed if (old or {}).get(k) != (new or {}).get(k)]
        _record(session, obj, changed or list(new.keys()))


def _after_commit(session: Session) -> None:
    changes = session.info.pop(_KEY, None)
    link_changes = session.info.pop(_LINK_KEY, None)
    if (not changes and not link_changes) or not AUTO_DISPATCH:
        return
    if in_sentinel_run.get():   # 断环：哨兵动作引发的写入不再即时级联
        return
    if session.info.get(SUPPRESS_KEY):  # 保存路径自带同步评估，避免双评估竞态
        return
    from app.services.sentinel.engine import run_for_change, run_for_link_change
    from app.database import SessionLocal

    payload = [(oid, otype, sorted(keys)) for (oid, otype), keys in (changes or {}).items()]
    link_payload = sorted(link_changes or set())

    def _run(items, link_items):
        for ontology_id, object_type_id, keys in items:
            db = SessionLocal()
            try:
                run_for_change(db, ontology_id, object_type_id, keys)
            except Exception:  # noqa: BLE001
                db.rollback()
            finally:
                db.close()
        for ontology_id in link_items:
            db = SessionLocal()
            try:
                run_for_link_change(db, ontology_id)
            except Exception:  # noqa: BLE001
                db.rollback()
            finally:
                db.close()

    threading.Thread(target=_run, args=(payload, link_payload), daemon=True).start()


_REGISTERED = False


def register_cdc() -> None:
    global _REGISTERED
    if _REGISTERED:
        return
    event.listen(Session, "before_flush", _before_flush)
    event.listen(Session, "after_commit", _after_commit)
    _REGISTERED = True
