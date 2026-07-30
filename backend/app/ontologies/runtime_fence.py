"""Cross-runtime ontology mutation fence.

The lock serializes Mapping rebuilds, Action execution, Sentinel evaluation,
and release transitions without making those business modules depend on one
another.  The public wrapper also honors an explicitly monkeypatched historical
``mapping_service._ontology_build_lock`` while that module is loaded.
"""
from __future__ import annotations

from contextlib import contextmanager
import logging
import sys
import threading

from sqlalchemy import text
from sqlalchemy.orm import Session


logger = logging.getLogger("app.ontologies.mappings.mapping_service")

_BUILD_LOCKS_GUARD = threading.Lock()
_LOCAL_BUILD_LOCKS: dict[str, threading.RLock] = {}
_BUILD_LOCK_OWNERS = threading.local()


def _local_build_lock(ontology_id: str) -> threading.RLock:
    with _BUILD_LOCKS_GUARD:
        return _LOCAL_BUILD_LOCKS.setdefault(ontology_id, threading.RLock())


@contextmanager
def _canonical_ontology_build_lock(db: Session, ontology_id: str):
    """Serialize a complete ontology rebuild across threads and processes.

    The project-row lock protects the relational phase, but ``build_all``
    intentionally commits that transaction before rebuilding Neo4j and Chroma.
    Without a wider lock another build can then delete a Chroma collection
    while the first build is writing it.  PostgreSQL session advisory locks
    survive those commits, so a dedicated physical connection owns the lock
    until every derived projection and Sentinel barrier has finished.
    """
    ontology_id = str(ontology_id)
    owned_depths = getattr(_BUILD_LOCK_OWNERS, "depths", None)
    if owned_depths is None:
        owned_depths = {}
        _BUILD_LOCK_OWNERS.depths = owned_depths
    if owned_depths.get(ontology_id, 0):
        owned_depths[ontology_id] += 1
        try:
            yield
        finally:
            owned_depths[ontology_id] -= 1
        return

    local_lock = _local_build_lock(ontology_id)
    local_lock.acquire()
    advisory_connection = None
    advisory_acquired = False
    lock_key = f"ontology-mapping-build:{ontology_id}"
    try:
        bind = db.get_bind()
        if bind is not None and bind.dialect.name == "postgresql":
            # Never acquire a session-scoped advisory lock through the business
            # Session: its commits may return that connection to the pool while
            # the lock is still held.
            engine = bind if hasattr(bind, "connect") else bind.engine
            advisory_connection = engine.connect()
            advisory_connection.execute(
                text("SELECT pg_advisory_lock(hashtextextended(:key, 0))"),
                {"key": lock_key},
            )
            advisory_acquired = True
            advisory_connection.commit()
        owned_depths[ontology_id] = 1
        yield
    finally:
        owned_depths.pop(ontology_id, None)
        if advisory_acquired and advisory_connection is not None:
            try:
                released = advisory_connection.execute(
                    text("SELECT pg_advisory_unlock(hashtextextended(:key, 0))"),
                    {"key": lock_key},
                ).scalar()
                advisory_connection.commit()
                if released is False:
                    logger.error(
                        "本体 Mapping advisory lock 未被当前连接持有: %s",
                        ontology_id,
                    )
            except Exception:
                # Returning a physical connection which still owns a
                # session-level lock to the pool can deadlock all future
                # builds.  Invalidate it so the driver closes the connection
                # and PostgreSQL releases every session lock.
                logger.warning(
                    "释放本体 Mapping advisory lock 失败: %s",
                    ontology_id, exc_info=True)
                try:
                    advisory_connection.invalidate()
                except Exception:
                    logger.warning(
                        "废弃 Mapping advisory lock 连接失败: %s",
                        ontology_id, exc_info=True)
        if advisory_connection is not None:
            try:
                advisory_connection.close()
            except Exception:
                logger.warning(
                    "关闭 Mapping advisory lock 连接失败: %s",
                    ontology_id, exc_info=True)
        local_lock.release()


@contextmanager
def _ontology_build_lock(db: Session, ontology_id: str):
    """Acquire the canonical fence while preserving the historical patch seam.

    Older tests and integrations patched ``mapping_service`` because that was
    the original owner.  Runtime consumers now import this neutral port.  When
    the compatibility attribute has deliberately been replaced, delegate to it
    rather than silently bypassing the caller's lock provider.
    """
    compatibility_module = sys.modules.get(
        "app.ontologies.mappings.mapping_service"
    )
    compatibility_lock = getattr(
        compatibility_module,
        "_ontology_build_lock",
        _ontology_build_lock,
    )
    if compatibility_lock is not _ontology_build_lock:
        with compatibility_lock(db, ontology_id):
            yield
        return
    with _canonical_ontology_build_lock(db, ontology_id):
        yield
