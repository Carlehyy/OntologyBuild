"""迁移脚本单元测试 — 使用内存 SQLite 模拟 v1 数据库

测试覆盖范围：
  - MigrationStats.to_dict() 字段完整性
  - dry_run=True 时不写入任何数据
  - 实体/关系数量统计正确
  - v1 数据库不存在时抛出 FileNotFoundError
  - PostgreSQL 不可用时记录 error 而非崩溃
  - to_dict 包含 Neo4j 投影统计字段
"""
import json
import os
import sqlite3
import tempfile
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


# ── 测试数据库初始化 ─────────────────────────────────────────────────

def create_v1_db(db_path: str):
    """创建最小化 v1 SQLite 数据库用于测试

    使用 v1 原始字段命名：
      - entities.properties_json (TEXT)   （v1 旧字段名）
      - relations.source / target          （v1 旧字段名）
    """
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE users (
            id TEXT PRIMARY KEY,
            username TEXT,
            email TEXT,
            password_hash TEXT,
            role TEXT DEFAULT 'viewer',
            is_active INTEGER DEFAULT 1,
            created_at TEXT,
            updated_at TEXT
        );
        CREATE TABLE ontology_projects (
            id TEXT PRIMARY KEY,
            name TEXT,
            domain TEXT,
            description TEXT,
            version TEXT DEFAULT 'v0.1',
            status TEXT DEFAULT 'draft',
            created_by TEXT,
            created_at TEXT,
            updated_at TEXT
        );
        CREATE TABLE entities (
            id TEXT PRIMARY KEY,
            ontology_id TEXT,
            name_cn TEXT,
            name_en TEXT,
            type TEXT,
            description TEXT,
            confidence REAL DEFAULT 0.9,
            properties_json TEXT,
            version TEXT,
            created_at TEXT,
            updated_at TEXT
        );
        CREATE TABLE relations (
            id TEXT PRIMARY KEY,
            ontology_id TEXT,
            source TEXT,
            target TEXT,
            type TEXT,
            confidence REAL DEFAULT 0.8,
            properties TEXT,
            created_at TEXT
        );
        CREATE TABLE prompts (
            id TEXT PRIMARY KEY,
            name TEXT,
            domain TEXT,
            content TEXT,
            version TEXT DEFAULT 'v1.0',
            created_by TEXT,
            created_at TEXT,
            updated_at TEXT
        );
        CREATE TABLE model_configs (
            id TEXT PRIMARY KEY,
            name TEXT,
            provider TEXT,
            api_base TEXT,
            api_key_encrypted TEXT,
            models TEXT,
            created_by TEXT,
            created_at TEXT,
            updated_at TEXT
        );

        INSERT INTO users VALUES (
            'u-1', 'admin', 'admin@test.com', 'hash', 'admin', 1,
            datetime('now'), datetime('now')
        );
        INSERT INTO ontology_projects VALUES (
            'o-1', '供应链图谱', '供应链', '测试本体', 'v0.1', 'draft', 'u-1',
            datetime('now'), datetime('now')
        );
        INSERT INTO entities VALUES (
            'e-1', 'o-1', '华为', 'Huawei', 'Organization', '科技公司', 0.95,
            '{}', 'v0.1', datetime('now'), datetime('now')
        );
        INSERT INTO entities VALUES (
            'e-2', 'o-1', '苹果', 'Apple', 'Organization', '科技公司', 0.92,
            '{}', 'v0.1', datetime('now'), datetime('now')
        );
        INSERT INTO relations VALUES (
            'r-1', 'o-1', 'e-1', 'e-2', 'COMPETES', 0.8,
            '{"weight": 7}', datetime('now')
        );
        INSERT INTO prompts VALUES (
            'p-1', '供应链提示词', '供应链', '提取供应链实体', 'v1.0', 'u-1',
            datetime('now'), datetime('now')
        );
    """)
    conn.commit()
    conn.close()


class _RecordingQuery:
    """Small ORM-query double keyed by a model's ``id`` comparison."""

    def __init__(self, session, model):
        self._session = session
        self._model = model
        self._record_id = None

    def filter(self, *criteria):
        for criterion in criteria:
            if getattr(getattr(criterion, "left", None), "key", None) == "id":
                self._record_id = str(criterion.right.value)
        return self

    def first(self):
        return self._session.records.get(self._model, {}).get(self._record_id)


class _RecordingSession:
    """Record merged ORM rows without requiring a second test database."""

    def __init__(self):
        self.records = {}
        self.commits = 0
        self.rollbacks = 0

    def query(self, model):
        return _RecordingQuery(self, model)

    def merge(self, instance):
        self.records.setdefault(type(instance), {})[str(instance.id)] = instance
        return instance

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def get_bind(self):
        return SimpleNamespace(dialect=SimpleNamespace(name="sqlite"))

    def close(self):
        pass


@pytest.fixture
def v1_db():
    """提供临时 v1 SQLite 数据库路径，测试结束后自动清理

    Windows 上 SQLite 文件在连接关闭前无法删除，使用 finalizer 兼容处理。
    """
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    create_v1_db(db_path)
    yield db_path
    # 忽略 Windows 上因文件锁导致的删除失败
    try:
        os.unlink(db_path)
    except PermissionError:
        pass


# ── 测试用例 ─────────────────────────────────────────────────────────

def test_migration_stats_structure():
    """MigrationStats.to_dict() 包含所有必要的顶层字段"""
    from scripts.migrations.migrate_v1_to_v2 import MigrationStats

    stats = MigrationStats(users=5, ontologies=2, entities=20)
    d = stats.to_dict()

    # 顶层结构
    assert "migrated" in d
    assert "errors" in d
    assert "warnings" in d

    # migrated 子字段
    assert d["migrated"]["users"] == 5
    assert d["migrated"]["ontologies"] == 2
    assert d["migrated"]["entities"] == 20
    assert d["migrated"]["relations"] == 0
    assert d["migrated"]["files"] == 0
    assert d["migrated"]["prompts"] == 0
    assert d["migrated"]["model_configs"] == 0


def test_migration_dry_run(v1_db):
    """dry_run=True 时不调用 session.merge 或 session.commit"""
    from scripts.migrations.migrate_v1_to_v2 import V1ToV2Migrator

    # 构造 Mock PostgreSQL Session
    mock_session = MagicMock()
    mock_session.execute.return_value = MagicMock()

    migrator = V1ToV2Migrator(v1_db_path=v1_db, pg_url="postgresql://x", dry_run=True)
    # 直接注入 mock session，跳过真实 PostgreSQL 连接
    migrator._v2_session = mock_session

    try:
        with patch.object(migrator, "_connect_v2", return_value=None):
            migrator._connect_v1()
            migrator._migrate_users()
            migrator._migrate_prompts()
            migrator._migrate_ontologies_and_entities()
    finally:
        # 确保 SQLite 连接被关闭（Windows 文件锁兼容）
        migrator._cleanup()

    # dry_run 模式下绝对不写入
    mock_session.merge.assert_not_called()
    mock_session.commit.assert_not_called()

    # 统计数量仍然正确
    assert migrator.stats.users == 1
    assert migrator.stats.ontologies == 1
    assert migrator.stats.entities == 2
    assert migrator.stats.relations == 1
    assert migrator.stats.prompts == 1


def test_migration_counts_entities(v1_db):
    """正确统计 v1 数据库中的实体和关系数量（dry_run 模式）"""
    from scripts.migrations.migrate_v1_to_v2 import V1ToV2Migrator

    migrator = V1ToV2Migrator(v1_db_path=v1_db, pg_url="postgresql://x", dry_run=True)
    try:
        migrator._connect_v1()
        migrator._migrate_ontologies_and_entities()
    finally:
        # 确保 SQLite 连接被关闭（Windows 文件锁兼容）
        migrator._cleanup()

    assert migrator.stats.entities == 2
    assert migrator.stats.relations == 1
    assert migrator.stats.ontologies == 1


def test_non_dry_run_persists_entities_relations_and_projecting_fence(v1_db):
    """The committed SQL truth includes relationships before projection."""
    from app.models.entity import Entity
    from app.models.ontology import OntologyProject
    from app.models.relation import Relation
    from scripts.migrations.migrate_v1_to_v2 import V1ToV2Migrator

    session = _RecordingSession()
    migrator = V1ToV2Migrator(v1_db, "postgresql://x")
    migrator._v2_session = session
    projection_calls = []

    def assert_committed_projection(ontology_id, **counts):
        project = session.records[OntologyProject][ontology_id]
        assert session.commits == 1
        assert project.projection_status == "projecting"
        projection_calls.append((ontology_id, counts))

    migrator._rebuild_projection = assert_committed_projection
    try:
        migrator._connect_v1()
        migrator._migrate_ontologies_and_entities()
    finally:
        migrator._cleanup()

    assert set(session.records[Entity]) == {"e-1", "e-2"}
    assert set(session.records[Relation]) == {"r-1"}
    relation = session.records[Relation]["r-1"]
    assert relation.source_entity == "e-1"
    assert relation.target_entity == "e-2"
    assert relation.properties == {"weight": 7}
    assert projection_calls == [
        ("o-1", {"entity_count": 2, "relation_count": 1})
    ]
    assert migrator.stats.ontologies == 1
    assert migrator.stats.entities == 2
    assert migrator.stats.relations == 1


def test_relation_with_unresolved_endpoint_fails_before_commit(v1_db):
    """A corrupt legacy edge cannot silently point outside its ontology."""
    from scripts.migrations.migrate_v1_to_v2 import V1ToV2Migrator

    conn = sqlite3.connect(v1_db)
    conn.execute("UPDATE relations SET target = 'missing-entity' WHERE id = 'r-1'")
    conn.commit()
    conn.close()

    session = _RecordingSession()
    migrator = V1ToV2Migrator(v1_db, "postgresql://x")
    migrator._v2_session = session
    try:
        migrator._connect_v1()
        with pytest.raises(RuntimeError, match="无法解析的端点"):
            migrator._migrate_ontologies_and_entities()
    finally:
        migrator._cleanup()

    assert session.commits == 0
    assert session.rollbacks == 1
    assert migrator.stats.ontologies == 0


def test_projection_rebuild_uses_canonical_fail_closed_path():
    """Migration always invokes the real canonical rebuild, even in tests."""
    from scripts.migrations.migrate_v1_to_v2 import V1ToV2Migrator

    migrator = V1ToV2Migrator("unused.db", "postgresql://x")
    migrator._v2_session = MagicMock()

    with patch(
        "app.ontologies.projection_state.rebuild_after_commit"
    ) as rebuild:
        migrator._rebuild_projection(
            "o-1", entity_count=2, relation_count=1
        )

    rebuild.assert_called_once_with(
        migrator._v2_session,
        "o-1",
        run_in_test=True,
    )
    assert migrator.stats.neo4j_nodes == 2
    assert migrator.stats.neo4j_edges == 1


def test_projection_failure_is_reported_as_recoverable_not_success():
    """Committed SQL is reported while a failed projection stays explicit."""
    from scripts.migrations.migrate_v1_to_v2 import V1ToV2Migrator

    migrator = V1ToV2Migrator("unused.db", "postgresql://x")
    migrator._v2_session = MagicMock()

    with patch(
        "app.ontologies.projection_state.rebuild_after_commit",
        side_effect=RuntimeError("neo4j unavailable"),
    ):
        with pytest.raises(RuntimeError, match="项目保持非 ready"):
            migrator._rebuild_projection(
                "o-1", entity_count=2, relation_count=1
            )

    assert migrator.stats.neo4j_nodes == 0
    assert migrator.stats.neo4j_edges == 0


def test_migration_v1_db_not_found():
    """v1 数据库文件不存在时抛出 FileNotFoundError"""
    from scripts.migrations.migrate_v1_to_v2 import V1ToV2Migrator

    migrator = V1ToV2Migrator(
        v1_db_path="/nonexistent/path/that/does/not/exist.db",
        pg_url="x",
        dry_run=True,
    )
    with pytest.raises(FileNotFoundError):
        migrator._connect_v1()


def test_migration_reports_errors_on_bad_pg(v1_db):
    """PostgreSQL 连接串无效时，run() 记录 error 而不崩溃（返回统计）"""
    from scripts.migrations.migrate_v1_to_v2 import V1ToV2Migrator

    # 使用一个永远无法连接的假 URL
    migrator = V1ToV2Migrator(
        v1_db_path=v1_db,
        pg_url="postgresql://bad_user:bad_pass@127.0.0.1:19999/nonexistent_db",
    )
    stats = migrator.run()

    # 应有至少一条 error 记录
    assert len(stats.errors) > 0
    # run() 不应抛出异常，正常返回 MigrationStats
    assert stats is not None


def test_migration_stats_to_dict_complete():
    """to_dict exposes current relational and Neo4j migration counters."""
    from scripts.migrations.migrate_v1_to_v2 import MigrationStats

    stats = MigrationStats(neo4j_nodes=10, neo4j_edges=4)
    d = stats.to_dict()

    assert "neo4j_nodes" in d["migrated"]
    assert "neo4j_edges" in d["migrated"]
    assert d["migrated"]["neo4j_nodes"] == 10
    assert d["migrated"]["neo4j_edges"] == 4
