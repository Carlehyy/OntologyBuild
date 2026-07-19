"""
OntoPrompt API v2

架构：FastAPI + PostgreSQL + Neo4j + ChromaDB + MinIO + Celery/Redis
v2 新增：Pipelines 全链路（Connection→Dataset→Transform→Curated→Mapping）
v1 兼容：/api/v1/* 路由全部保留

启动：uvicorn app.main:app --host 0.0.0.0 --port 8000
"""
import hmac
import json
import urllib.request

from fastapi import FastAPI, Depends, HTTPException
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from sqlalchemy import inspect, text
from sqlalchemy.orm import Session
from app.database import engine, Base, SessionLocal
from app.config import settings
from app.routers import auth, users, overview, ontologies, files, prompts, models, entities, logic, actions, extraction, graph, settings as settings_router, export, audit, mcp as mcp_router, domains
from app.routers import formal as formal_router
from app.routers import sentinel as sentinel_router
from app.routers import collectors as collectors_router
from app.routers.v2 import connections as connections_v2
from app.routers.v2 import datasets as datasets_v2
from app.routers.v2 import pipelines as pipelines_v2
from app.routers.v2 import graph as graph_v2
from app.routers.v2 import search as search_v2
from app.routers.v2 import curated as curated_v2
from app.routers.v2 import mappings as mappings_v2
from app.routers.v2 import incremental as incremental_v2
from app.routers.v2 import logic_actions as logic_actions_v2
from app.routers.v2 import versions as versions_v2
from app.routers.v2 import attribute_schemas as attr_schemas_v2
from app.routers.v2 import inference as inference_v2
from app.routers.v2 import extraction as extraction_v2
from app.routers.v2 import sync_tasks as sync_tasks_v2
from app.data_channel.pipeline_tasks import router as pipeline_tasks_v2
from app.data_channel.steward import router as steward_v2
from app.data_channel.steward import browser_ws as steward_browser_ws
from app.routers.v2 import test_data as test_data_v2
from app.data_channel.access import asset_lake_access_guard
from app.data_channel.datasets.sharing_router import (
    management_router as manual_sharing_router,
    public_router as public_manual_sharing_router,
)
from app.ontologies.access import legacy_ontology_write_guard

def _seed_db():
    from app.services.auth_service import seed_admin
    from app.models.rules_config import RulesConfig
    import uuid

    db = SessionLocal()
    try:
        # Import all models to ensure tables are created
        from app.models import user, ontology, file, prompt, model_config, entity, logic as logic_model, action, relation, extraction_task, rules_config, audit_task, mcp, domain
        from app.models import user, ontology, file, prompt, model_config, entity, logic as logic_model, action, relation, extraction_task, rules_config, mcp, domain
        from app.models.v2 import dataset as v2_dataset, pipeline as v2_pipeline, connection as v2_connection  # noqa: F401
        from app.models.v2.logic import OntologyLogicRule, OntologyStateMachine  # noqa: F401
        from app.models.v2.action import OntologyActionType, OntologyActionRun  # noqa: F401
        from app.models.v2.curated import CuratedDataset, CuratedReview, CuratedRowEdit  # noqa: F401
        from app.models.v2.sync_task import DataSyncTask, DataSyncHistory  # noqa: F401
        from app.data_channel.pipeline_tasks.models import PipelineTask  # noqa: F401
        from app.data_channel.datasets.sharing_models import (  # noqa: F401
            ManualDatasetChange, ManualDatasetShare,
        )
        from app.models.v2.mapping import OntologyMapping, OntologyLinkMapping  # noqa: F401
        from app.models.ontology_version import OntologyVersion, OntologyChangeLog  # noqa: F401
        from app.models.attribute_schema import AttributeSchema, VocabularyEntry  # noqa: F401
        from app.models.inference import ShadowRun, InferenceRun, InferenceResult, ActionFiring, AuditLog  # noqa: F401
        # 正规本体模型 (Palantir 风格) — 平台核心
        from app.models.ontology_formal import (  # noqa: F401
            ObjectType, LinkType, ActionType, OntologyFunction,
            ObjectInstance, LinkInstance, ActionExecutionLog,
        )
        # 哨兵引擎模型 (反应式运行时)
        from app.models.sentinel import Sentinel, SentinelFiring, Notification, SentinelMatchState  # noqa: F401
        # 本体智能体 (授权边界内的 agent 运行时)
        from app.ontologies.agent_runtime.models import AgentProfile, AgentConversation, AgentMessage  # noqa: F401
        # 业务探索 (对话式业务建模：画布/文档/本体草稿/会话附件)
        from app.exploration.models import (  # noqa: F401
            ExplorationSession, ExplorationMessage, ExplorationDocument, ExplorationDraft,
            ExplorationAttachment,
        )
        from app.models.workflow_config import WorkflowConfig  # noqa: F401
        # 数据管家 (对话式 n8n 数据流水线：治理记录 + 会话)
        from app.data_channel.steward.models import (  # noqa: F401
            N8nPipeline, StewardConversation, StewardMessage,
        )
        # 事件登记 (智能助手↔数据通道之间的事件采集入口：主体/附件/审计/第三方密钥)
        from app.events.models import (  # noqa: F401
            RegisteredEvent, EventAttachment, EventAuditLog, EventIngestKey,
        )
        # 超级助手（独立会话、目录型 Skill、MCP 客户端配置）
        from app.super_assistant.models import (  # noqa: F401
            SuperAssistantConversation, SuperAssistantMessage,
            SuperAssistantToolRun, SuperAssistantSkill, SuperAssistantMcpServer,
        )
        # 生产 schema 只认 Alembic。create_all 会把漏跑迁移伪装成“可启动”，
        # 却没有回填、外键与唯一约束；开发/测试仍保留零配置建库便利。
        if settings.environment != "production":
            Base.metadata.create_all(bind=engine)

        # Lightweight column migrations — create_all skips existing tables
        with engine.connect() as conn:
            if settings.environment != "production":
                columns = {
                    col["name"]
                    for col in inspect(conn).get_columns("extraction_tasks")
                }
                if "validation_report" not in columns:
                    conn.execute(text(
                        "ALTER TABLE extraction_tasks "
                        "ADD COLUMN validation_report JSON"))
                    conn.commit()
                entity_columns = {
                    col["name"] for col in inspect(conn).get_columns("entities")
                }
                if "name_abbr" not in entity_columns:
                    conn.execute(text(
                        "ALTER TABLE entities ADD COLUMN name_abbr VARCHAR(50)"))
                    conn.commit()
                if "snomed_id" not in entity_columns:
                    conn.execute(text(
                        "ALTER TABLE entities ADD COLUMN snomed_id VARCHAR(50)"))
                    conn.commit()
                if "canonical_id" not in entity_columns:
                    conn.execute(text(
                        "ALTER TABLE entities ADD COLUMN canonical_id VARCHAR(200)"))
                    conn.commit()
            # 这些历史兼容 DDL 只服务未迁移的开发库；生产部署已在启动前执行
            # ``alembic upgrade head``，不得再由应用逐条 ALTER 并吞掉失败。
            for stmt in ([] if settings.environment == "production" else [
                "ALTER TABLE model_configs ADD COLUMN config_type VARCHAR(30) DEFAULT 'llm'",
                "ALTER TABLE model_configs ADD COLUMN options JSON DEFAULT '{}'",
                "ALTER TABLE model_configs ADD COLUMN enabled BOOLEAN DEFAULT TRUE",
                "ALTER TABLE model_configs ADD COLUMN is_default BOOLEAN DEFAULT FALSE",
                "ALTER TABLE ontology_projects ADD COLUMN build_mode VARCHAR(30) DEFAULT 'simple_llm'",
                "ALTER TABLE ontology_projects ADD COLUMN icon VARCHAR(50)",
                "ALTER TABLE ontology_projects ADD COLUMN current_release_id VARCHAR",
                "ALTER TABLE v2_pipelines ADD COLUMN domain VARCHAR(100) DEFAULT '通用'",
                "ALTER TABLE v2_pipelines ADD COLUMN description TEXT DEFAULT ''",
                "ALTER TABLE v2_pipelines ADD COLUMN definition JSON",
                "ALTER TABLE v2_pipelines ADD COLUMN branch VARCHAR(50) DEFAULT 'main'",
                "ALTER TABLE v2_pipelines ADD COLUMN version INTEGER DEFAULT 1",
                "ALTER TABLE logic_rules ADD COLUMN enabled BOOLEAN DEFAULT TRUE",
                "ALTER TABLE logic_rules ADD COLUMN status VARCHAR(20) DEFAULT 'draft'",
                "ALTER TABLE actions ADD COLUMN enabled BOOLEAN DEFAULT TRUE",
                "ALTER TABLE actions ADD COLUMN status VARCHAR(20) DEFAULT 'draft'",
                "ALTER TABLE sentinels ADD COLUMN condition_rows JSON DEFAULT '[]'",
                "ALTER TABLE sentinels ADD COLUMN condition_logic VARCHAR(8) DEFAULT 'and'",
                "ALTER TABLE sentinels ADD COLUMN trigger_mode VARCHAR(16) DEFAULT 'on_enter'",
                "ALTER TABLE sentinels ADD COLUMN muted BOOLEAN DEFAULT FALSE",
                "ALTER TABLE ontology_versions ADD COLUMN snapshot_formal JSON",
                "ALTER TABLE ontology_versions ADD COLUMN parent_version_id VARCHAR",
                "ALTER TABLE ontology_versions ADD COLUMN base_release_id VARCHAR",
                "ALTER TABLE ontology_versions ADD COLUMN promoted_from_id VARCHAR",
                "ALTER TABLE ontology_versions ADD COLUMN node_kind VARCHAR(20) DEFAULT 'release'",
                "ALTER TABLE ontology_versions ADD COLUMN lifecycle_status VARCHAR(20) DEFAULT 'released'",
                "ALTER TABLE ontology_versions ADD COLUMN revision INTEGER DEFAULT 0",
                "ALTER TABLE ontology_versions ADD COLUMN snapshot_hash VARCHAR(64)",
                "ALTER TABLE ontology_versions ADD COLUMN published_at DATETIME",
                "ALTER TABLE fo_property_facts ADD COLUMN kind VARCHAR(16) DEFAULT 'property'",
                "ALTER TABLE fo_property_facts ADD COLUMN derived_from JSON",
                "ALTER TABLE fo_property_facts ADD COLUMN supersedes_id VARCHAR",
                "ALTER TABLE fo_property_facts ADD COLUMN caused_by VARCHAR",
                "ALTER TABLE fo_property_facts ADD COLUMN source VARCHAR(200) DEFAULT 'manual'",
                "ALTER TABLE fo_property_facts ADD COLUMN actor_id VARCHAR",
                # —— 图谱编辑器主链路（缺此列则 GET /full 必 500）——
                "ALTER TABLE fo_object_types ADD COLUMN interfaces JSON DEFAULT '[]'",
                # —— HITL 审批闸门 + 执行溯源 ——
                "ALTER TABLE fo_action_types ADD COLUMN requires_approval BOOLEAN DEFAULT FALSE",
                "ALTER TABLE fo_action_logs ADD COLUMN actor_id VARCHAR",
                "ALTER TABLE fo_action_logs ADD COLUMN decided_by VARCHAR",
                "ALTER TABLE fo_action_logs ADD COLUMN decided_at DATETIME",
                "ALTER TABLE fo_action_logs ADD COLUMN decision_reason TEXT",
                "ALTER TABLE fo_action_logs ADD COLUMN related_log_id VARCHAR",
                # —— 事实层 CBox 补全 + 确定性排序 ——
                "ALTER TABLE fo_property_facts ADD COLUMN confidence FLOAT",
                "ALTER TABLE fo_property_facts ADD COLUMN valid_at DATETIME",
                "ALTER TABLE fo_property_facts ADD COLUMN seq INTEGER DEFAULT 0",
                # —— 同步任务状态归一（引擎曾写大写，与枚举/守卫的小写互不相认）——
                "UPDATE v2_data_sync_tasks SET status = lower(status) WHERE status != lower(status)",
                "UPDATE v2_data_sync_histories SET status = lower(status) WHERE status != lower(status)",
                # —— 映射绑定已有对象实体（model-first：先建模、再灌数据）——
                "ALTER TABLE v2_ontology_mappings ADD COLUMN target_object_type_id VARCHAR",
                # —— 流水线调度任务血缘：run 由哪条任务触发 ——
                "ALTER TABLE v2_pipeline_runs ADD COLUMN task_id VARCHAR",
                # —— 事件登记：留存明文密钥以便面板反复复制 ——
                "ALTER TABLE event_ingest_keys ADD COLUMN secret_plain VARCHAR(120)",
                # —— 未发布 n8n 执行预览的列样本 → 发布时固化为影子流水线期望列契约 ——
                "ALTER TABLE v2_n8n_pipelines ADD COLUMN last_test_result JSON",
                # —— 流水线启用开关：停用后任务池/链式触发不执行 ——
                "ALTER TABLE v2_pipelines ADD COLUMN enabled BOOLEAN DEFAULT TRUE",
                # —— 本体元素血缘出处（业务探索草稿落地时写入，Schema 也是事实）——
                "ALTER TABLE fo_object_types ADD COLUMN source JSON",
                "ALTER TABLE fo_link_types ADD COLUMN source JSON",
                "ALTER TABLE fo_action_types ADD COLUMN source JSON",
                "ALTER TABLE fo_functions ADD COLUMN source JSON",
                "ALTER TABLE sentinels ADD COLUMN source JSON",
                # —— 胖关系（LPG 边属性）：关系映射支持连接表 + 边属性字段映射 ——
                "ALTER TABLE v2_ontology_link_mappings ADD COLUMN link_type_id VARCHAR",
                "ALTER TABLE v2_ontology_link_mappings ADD COLUMN edge_dataset_id VARCHAR",
                "ALTER TABLE v2_ontology_link_mappings ADD COLUMN field_mapping JSON DEFAULT '{}'",
                # —— 回填历史 NULL latest_version_id（create_version 旧 bug：flush 前取 id）——
                "UPDATE v2_datasets SET latest_version_id = ("
                " SELECT v.id FROM v2_dataset_versions v WHERE v.dataset_id = v2_datasets.id"
                " ORDER BY v.version_no DESC LIMIT 1)"
                " WHERE latest_version_id IS NULL AND EXISTS ("
                " SELECT 1 FROM v2_dataset_versions v2 WHERE v2.dataset_id = v2_datasets.id)",
            ]):
                try:
                    conn.execute(text(stmt))
                    conn.commit()
                except Exception as e:
                    conn.rollback()  # 清掉 aborted 事务，避免下一个 stmt 全炸
                    msg = str(e).lower()
                    if "duplicate column" in msg or "already exists" in msg:
                        pass
                    else:
                        import logging
                        logging.getLogger(__name__).error(
                            "schema 迁移失败(将导致该表读写 500): %s -> %s", stmt, e)

        seed_admin(db)

        # 对象存储删除采用 transactional outbox：上次停机/存储故障遗留的任务在
        # 启动时重试。空队列不会初始化 MinIO 客户端，不增加健康检查压力。
        from app.data_channel.datasets.service import drain_storage_deletion_outbox
        drain_storage_deletion_outbox(
            db, strict_schema=settings.environment == "production")

        # 回填：存量 n8n 治理记录补建影子流水线行（创建即在列表可见的新规则）
        # 幂等——已有 pipeline_id 的记录 ensure 只做同步，不重复建行
        try:
            from app.data_channel.steward.service import ensure_shadow_pipeline
            from app.data_channel.steward.models import N8nPipeline as _N8nRec, STATUS_ARCHIVED as _ARCH
            from app.models.v2.pipeline import Pipeline as _Pipeline
            orphans = db.query(_N8nRec).filter(
                _N8nRec.status != _ARCH, _N8nRec.pipeline_id.is_(None)).all()
            for _rec in orphans:
                ensure_shadow_pipeline(db, _rec)
            owner_repairs = 0
            managed = db.query(_N8nRec).filter(
                _N8nRec.status != _ARCH,
                _N8nRec.pipeline_id.is_not(None),
                _N8nRec.created_by.is_not(None),
            ).all()
            for _rec in managed:
                shadow = db.query(_Pipeline).filter(
                    _Pipeline.id == _rec.pipeline_id).first()
                if shadow is not None and not shadow.created_by:
                    shadow.created_by = _rec.created_by
                    owner_repairs += 1
            if orphans or owner_repairs:
                db.commit()
        except Exception:  # noqa: BLE001 — 回填失败不阻塞启动
            db.rollback()
            import logging
            logging.getLogger(__name__).warning("n8n 影子流水线回填失败", exc_info=True)

        # 重启时清理遗留的 running 任务 — daemon 线程被杀后 task 会永久卡在 85%
        from app.models.extraction_task import ExtractionTask
        stale = db.query(ExtractionTask).filter(ExtractionTask.status == "running").all()
        for t in stale:
            t.status = "failed"
            t.error  = "服务重启，任务中断。请重新触发提取。"
        if stale:
            db.commit()

        # Seed confidence rules
        if db.query(RulesConfig).count() == 0:
            rules = [
                ("confidence_entity_min", "0.5", "实体最低置信度", "Entity min confidence"),
                ("confidence_logic_min", "0.6", "逻辑规则最低置信度", "Logic rule min confidence"),
                ("confidence_action_min", "0.6", "动作最低置信度", "Action min confidence"),
                ("confidence_relation_min", "0.5", "关系最低置信度", "Relation min confidence"),
                ("confidence_high_threshold", "0.9", "高置信度阈值", "High confidence threshold"),
                ("confidence_medium_threshold", "0.7", "中置信度阈值", "Medium confidence threshold"),
                ("confidence_low_threshold", "0.5", "低置信度阈值", "Low confidence threshold"),
                ("confidence_display_dashed_below", "0.7", "低于此值显示虚线边", "Show dashed edge below threshold"),
            ]
            for key, val, label_cn, label_en in rules:
                db.add(RulesConfig(id=str(uuid.uuid4()), rule_key=key, rule_value=val,
                                   rule_label_cn=label_cn, rule_label_en=label_en))
            db.commit()

        # Seed / update builtin prompts (upsert by name)
        from app.models.prompt import Prompt
        from app.models.user import User
        from app.routers.prompts import BUILTIN_PROMPTS
        admin = db.query(User).filter(User.role == "admin").first()
        if admin:
            for p in BUILTIN_PROMPTS:
                existing = db.query(Prompt).filter(Prompt.name == p["name"]).first()
                if existing:
                    existing.content = p["content"]
                    existing.domain = p["domain"]
                else:
                    db.add(Prompt(id=str(uuid.uuid4()), name=p["name"], domain=p["domain"],
                                  content=p["content"], version="v1.0", created_by=admin.id))
            db.commit()
    finally:
        db.close()

@asynccontextmanager
async def lifespan(app: FastAPI):
    _seed_db()
    # API-Hub keeps its original isolated SQLite store and refresh scheduler.
    # It is initialized inside the host application's lifecycle so there is
    # still only one backend process to operate.
    from app.api_hub import db as api_hub_db, scheduler as api_hub_scheduler
    api_hub_db.init_db()
    api_hub_scheduler.start()
    # 哨兵引擎：注册 CDC(监听对象改动→变化驱动) + 启动定期扫描 worker
    try:
        from app.services.sentinel import register_cdc, start_scan_worker
        register_cdc()
        sentinel_started = start_scan_worker()
        if settings.environment == "production" and not sentinel_started:
            raise RuntimeError("Sentinel scan worker is disabled or failed to start")
    except Exception as e:
        if settings.environment == "production":
            raise RuntimeError("Sentinel engine failed to initialize") from e
        import logging; logging.getLogger(__name__).warning(f'Sentinel 启动失败: {e}')
    # 初始化 Neo4j 索引；开发环境可降级，生产环境必须完成后才就绪。
    try:
        from app.services.v2.graph.index_setup import setup_indexes
        index_result = setup_indexes()
        if settings.environment == "production" and (
            index_result.get("status") != "done"
            or any(item.get("status") != "ok"
                   for item in index_result.get("results", []))
        ):
            raise RuntimeError(f"Neo4j index setup incomplete: {index_result}")
    except Exception as e:
        if settings.environment == "production":
            raise RuntimeError("Neo4j indexes failed to initialize") from e
    # 启动数据同步任务调度器（后台线程）
    try:
        from app.services.v2.sync_scheduler import get_sync_scheduler
        scheduler = get_sync_scheduler()
        scheduler.start()
        if settings.environment == "production" and not scheduler.healthy:
            raise RuntimeError(
                f"Data scheduler is not healthy: {scheduler.last_error or 'not running'}")
    except Exception as e:
        if settings.environment == "production":
            raise RuntimeError("Data scheduler failed to initialize") from e
        import logging
        logging.getLogger(__name__).warning(f"SyncScheduler 启动失败: {e}")
    from app import mcp_server as _mcp_server
    from app.api_hub import mcp_server as api_hub_mcp
    # session manager 每实例只能 run 一次；重复进入 lifespan（如测试）需重建
    api_hub_public, api_hub_system = api_hub_mcp.reset_session_managers()
    try:
        async with (
            _mcp_server.reset_session_manager().run(),
            api_hub_public.run(),
            api_hub_system.run(),
        ):
            yield
    finally:
        from app.data_channel.steward.browser_runtime import browser_manager
        browser_manager.close_all()
        try:
            from app.services.v2.sync_scheduler import get_sync_scheduler
            get_sync_scheduler().shutdown()
        except Exception:  # noqa: BLE001
            pass
        api_hub_scheduler.shutdown()

app = FastAPI(title="OntoPrompt API", version="0.1.0", lifespan=lifespan)

from app import mcp_server
mcp_server.bind_app(app)

@app.get("/health/live", tags=["health"])
def liveness():
    """Process liveness only; dependency readiness is exposed separately."""
    return {"status": "ok"}


def _probe_http_service(url: str, *, timeout: float = 3.0) -> None:
    """Probe an internal HTTP service without leaving a pooled socket behind.

    Readiness runs repeatedly in production.  Constructing a new Chroma
    ``HttpClient`` for every probe leaked its underlying HTTP connection into
    CLOSE_WAIT until the backend exhausted its 1024 file descriptors.  A
    short-lived stdlib request with ``Connection: close`` keeps this path
    bounded and makes ownership of the socket explicit.
    """
    request = urllib.request.Request(url, headers={"Connection": "close"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        if response.status >= 400:
            raise RuntimeError(f"health probe returned HTTP {response.status}")
        # Consume a bounded response so the connection can close cleanly.
        response.read(4096)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        origin.strip()
        for origin in settings.cors_allowed_origins.split(",")
        if origin.strip()
    ],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


async def _send_json(send, status: int, obj: dict):
    body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    await send({
        "type": "http.response.start",
        "status": status,
        "headers": [(b"content-type", b"application/json; charset=utf-8")],
    })
    await send({"type": "http.response.body", "body": body})


class McpMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        from app.api_hub import config as api_hub_config
        from app.api_hub import mcp_server as api_hub_mcp
        if path == api_hub_config.SYSTEM_MCP_PATH or path.startswith(api_hub_config.SYSTEM_MCP_PATH + "/"):
            if not api_hub_config.SYSTEM_MCP_TOKEN:
                await _send_json(send, 503, {"error": "system MCP is disabled"})
                return
            headers = dict(scope.get("headers") or [])
            auth = headers.get(b"authorization", b"").decode("latin-1")
            if not hmac.compare_digest(
                auth, f"Bearer {api_hub_config.SYSTEM_MCP_TOKEN}"
            ):
                await _send_json(send, 401, {"error": "unauthorized"})
                return
            hub_scope = dict(scope)
            hub_scope["path"] = "/"
            hub_scope["raw_path"] = b"/"
            await api_hub_mcp.handle_mcp_system(hub_scope, receive, send)
            return
        if path == api_hub_config.MCP_PATH or path.startswith(api_hub_config.MCP_PATH + "/"):
            if not api_hub_config.MCP_TOKEN:
                await _send_json(send, 503, {"error": "API-Hub MCP is disabled"})
                return
            headers = dict(scope.get("headers") or [])
            auth = headers.get(b"authorization", b"").decode("latin-1")
            if not hmac.compare_digest(auth, f"Bearer {api_hub_config.MCP_TOKEN}"):
                await _send_json(send, 401, {"error": "unauthorized"})
                return
            hub_scope = dict(scope)
            hub_scope["path"] = "/"
            hub_scope["raw_path"] = b"/"
            await api_hub_mcp.handle_mcp(hub_scope, receive, send)
            return
        if path == "/mcp" or path.startswith("/mcp/"):
            headers = dict(scope.get("headers") or [])
            auth = headers.get(b"authorization", b"").decode("latin-1")
            if not auth.startswith("Bearer "):
                await _send_json(send, 401, {"detail": "Missing Bearer token"})
                return
            token = auth.removeprefix("Bearer ").strip()
            try:
                mcp_server.validate_bearer_token(token)
            except HTTPException as exc:
                await _send_json(send, exc.status_code, {"detail": exc.detail})
                return
            scope = dict(scope)
            scope["path"] = "/"
            scope["raw_path"] = b"/"
            await mcp_server.handle_mcp(scope, receive, send, bearer_token=token)
            return
        await self.app(scope, receive, send)


app.add_middleware(McpMiddleware)

from app.deps import require_admin, require_menu_permission


def menu_guard(menu_key: str, *, read_menu_keys: tuple[str, ...] = ()):
    return [Depends(require_menu_permission(menu_key, read_menu_keys=read_menu_keys))]


overview_guard = menu_guard("overview")
ontology_guard = menu_guard(
    "ontologies",
    read_menu_keys=("agent", "explore", "events"),
)
models_guard = menu_guard(
    "models",
    read_menu_keys=(
        "super_assistant",
        "explore",
        "ontologies",
        "agent",
        "data.pipelines",
    ),
)
data_guard = menu_guard("data")
pipeline_guard = menu_guard(
    "data.pipelines",
    read_menu_keys=("data.structured", "data.sync_tasks"),
)
agent_guard = menu_guard("agent")
explore_guard = menu_guard("explore")
assistant_guard = menu_guard("super_assistant")
events_guard = menu_guard("events")
admin_guard = [Depends(require_admin)]

app.include_router(auth.router, prefix="/api/v1/auth", tags=["auth"])
app.include_router(users.router, prefix="/api/v1/users", tags=["users"])
app.include_router(overview.router, prefix="/api/v1/overview", tags=["overview"], dependencies=overview_guard)
app.include_router(ontologies.router, prefix="/api/v1/ontologies", tags=["ontologies"], dependencies=ontology_guard)
legacy_write_guard = [Depends(legacy_ontology_write_guard)]
legacy_ontology_guard = [*legacy_write_guard, *ontology_guard]
app.include_router(files.router, prefix="/api/v1/ontologies/{ontology_id}/files", tags=["files"], dependencies=legacy_ontology_guard)
app.include_router(entities.router, prefix="/api/v1/ontologies/{ontology_id}/entities", tags=["entities"], dependencies=legacy_ontology_guard)
app.include_router(logic.router, prefix="/api/v1/ontologies/{ontology_id}/logic", tags=["logic"], dependencies=legacy_ontology_guard)
app.include_router(actions.router, prefix="/api/v1/ontologies/{ontology_id}/actions", tags=["actions"], dependencies=legacy_ontology_guard)
app.include_router(extraction.router, prefix="/api/v1/ontologies/{ontology_id}/execute", tags=["extraction"], dependencies=legacy_ontology_guard)
app.include_router(graph.router, prefix="/api/v1/ontologies/{ontology_id}/graph", tags=["graph"], dependencies=ontology_guard)
app.include_router(export.router, prefix="/api/v1/ontologies/{ontology_id}/export", tags=["export"], dependencies=ontology_guard)
app.include_router(audit.router, prefix="/api/v1/ontologies/{ontology_id}/audit", tags=["audit"], dependencies=ontology_guard)
app.include_router(prompts.router, prefix="/api/v1/prompts", tags=["prompts"])
app.include_router(domains.router, prefix="/api/v1/domains", tags=["domains"])
app.include_router(models.router, prefix="/api/v1/models", tags=["models"], dependencies=models_guard)
app.include_router(settings_router.router, prefix="/api/v1/settings", tags=["settings"], dependencies=admin_guard)
app.include_router(mcp_router.router, prefix="/api/v1/mcp", tags=["mcp"])

# 接口代理（API-Hub）管理面统一沿用平台 JWT；对 Agent 的 MCP 端点在
# /api-hub/mcp 与 /api-hub/mcp/system，继续使用各自独立 token。
from app.api_hub.routers import backup as api_hub_backup
from app.api_hub.routers import credential as api_hub_credential
from app.api_hub.routers import interfaces as api_hub_interfaces
from app.api_hub.routers import mcp as api_hub_mcp_router
from app.api_hub.routers import proxy as api_hub_proxy
from app.api_hub.routers import http_proxy as api_hub_http_proxy
api_hub_interfaces_guard = menu_guard("api_hub.interfaces")
api_hub_history_guard = menu_guard("api_hub.history")
api_hub_authorization_guard = menu_guard("api_hub.authorization")
app.include_router(api_hub_credential.router, prefix="/api/api-hub", dependencies=api_hub_authorization_guard)
app.include_router(api_hub_interfaces.router, prefix="/api/api-hub", dependencies=api_hub_interfaces_guard)
app.include_router(api_hub_interfaces.runs_router, prefix="/api/api-hub", dependencies=api_hub_history_guard)
app.include_router(api_hub_backup.router, prefix="/api/api-hub", dependencies=api_hub_authorization_guard)
app.include_router(api_hub_mcp_router.router, prefix="/api/api-hub", dependencies=api_hub_interfaces_guard)
app.include_router(
    api_hub_http_proxy.admin_router,
    prefix="/api/api-hub",
    dependencies=api_hub_interfaces_guard,
)
# n8n service-to-service calls use API_HUB_SYSTEM_MCP_TOKEN and only reach
# interfaces explicitly added to the open list.
app.include_router(api_hub_proxy.router)
# Ordinary HTTP consumers use per-caller proxy keys and stable /proxy/<slug> URLs.
app.include_router(api_hub_http_proxy.public_router)
asset_lake_guard = [Depends(asset_lake_access_guard), *data_guard]
app.include_router(connections_v2.router, prefix="/api/v2/connections", tags=["v2-connections"], dependencies=asset_lake_guard)
app.include_router(datasets_v2.router, prefix="/api/v2/datasets", tags=["v2-datasets"], dependencies=asset_lake_guard)
app.include_router(manual_sharing_router, prefix="/api/v2/manual-dataset-sharing", tags=["manual-dataset-sharing"], dependencies=asset_lake_guard)
app.include_router(public_manual_sharing_router, prefix="/api/public/manual-datasets", tags=["public-manual-datasets"])
app.include_router(pipelines_v2.router, prefix="/api/v2/pipelines", tags=["v2-pipelines"], dependencies=pipeline_guard)
app.include_router(graph_v2.router, prefix="/api/v2/ontologies", tags=["v2-graph"], dependencies=ontology_guard)
app.include_router(search_v2.router, prefix="/api/v2/ontologies", tags=["v2-search"], dependencies=ontology_guard)
app.include_router(curated_v2.router, prefix="/api/v2/curated", tags=["v2-curated"], dependencies=asset_lake_guard)
app.include_router(mappings_v2.router, prefix="/api/v2/ontologies", tags=["v2-mappings"], dependencies=ontology_guard)
app.include_router(incremental_v2.router, prefix="/api/v2/incremental", tags=["v2-incremental"], dependencies=asset_lake_guard)
app.include_router(logic_actions_v2.router, prefix="/api/v2/ontologies", tags=["v2-logic-actions"], dependencies=legacy_ontology_guard)
app.include_router(versions_v2.router, prefix="/api/v2/ontologies", tags=["v2-versions"], dependencies=ontology_guard)
app.include_router(attr_schemas_v2.router, prefix="/api/v2/ontologies", tags=["v2-attributes"], dependencies=ontology_guard)
app.include_router(inference_v2.router, prefix="/api/v2/ontologies", tags=["v2-inference"], dependencies=ontology_guard)
app.include_router(extraction_v2.router, prefix="/api/v2/ontologies", tags=["v2-extraction"], dependencies=legacy_ontology_guard)
app.include_router(sync_tasks_v2.router, prefix="/api/v2/sync-tasks", tags=["v2-sync-tasks"], dependencies=asset_lake_guard)
app.include_router(pipeline_tasks_v2.router, prefix="/api/v2/pipeline-tasks", tags=["v2-pipeline-tasks"], dependencies=asset_lake_guard)
app.include_router(steward_v2.router, prefix="/api/v2/steward", tags=["v2-steward"], dependencies=[*asset_lake_guard, *pipeline_guard])
# WebSocket uses a one-time, user-bound ticket issued by the authenticated
# steward router.  It intentionally sits outside HTTPBearer dependencies because
# browsers cannot attach that header to a native WebSocket handshake.
app.include_router(steward_browser_ws.router, prefix="/api/v2/steward", tags=["v2-steward-browser"])
app.include_router(test_data_v2.router, prefix="/api/v2/test-data", tags=["v2-test-data"], dependencies=asset_lake_guard)

# 正规本体模型 (Palantir 风格) — 平台核心建模 API
app.include_router(formal_router.router, prefix="/api/v2/formal/ontologies", tags=["formal-ontology"], dependencies=ontology_guard)
# 本体智能体 — 授权边界内的 LLM agent（对象/链接/事实/动作是它的全部世界）
from app.ontologies.agent_runtime import router as agent_runtime_router
app.include_router(agent_runtime_router.router, prefix="/api/v2/formal/ontologies", tags=["ontology-agent"], dependencies=agent_guard)
# 业务探索 — 对话式业务建模：六类模型画布 → 需求文档 → 本体草稿（人审落地）
from app.exploration import router as exploration_router
app.include_router(exploration_router.router, prefix="/api/v2/exploration", tags=["exploration"], dependencies=explore_guard)
# 超级助手 — 与本体、业务探索完全解耦的通用 Agent 运行时
from app.super_assistant import router as super_assistant_router
app.include_router(
    super_assistant_router.router,
    prefix="/api/v2/super-assistant",
    tags=["super-assistant"],
    dependencies=assistant_guard,
)
app.include_router(sentinel_router.router, prefix="/api/v1/ontologies/{ontology_id}/sentinels", tags=["sentinel"], dependencies=ontology_guard)
# 数据采集器 — AI HOT 等真实数据源接入
app.include_router(collectors_router.router, prefix="/api/v2/collectors", tags=["collectors"])
# 事件登记 — 智能助手↔数据通道之间的事件采集入口（平台录入 + 第三方 API Key 上传）
from app.events import router as events_router
app.include_router(events_router.router, prefix="/api/v2/events", tags=["events"], dependencies=events_guard)
app.include_router(events_router.ingest_router, prefix="/api/v2/ingest", tags=["events-ingest"])

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@app.get("/api/health", tags=["health"])
@app.get("/health", tags=["health"])
@app.get("/health/ready", tags=["health"])
def health(db: Session = Depends(get_db)):
    checks = {
        "status": "ok",
        "db": "unknown",
        "redis": "unknown",
        "neo4j": "unknown",
        "minio": "unknown",
        "chroma": "unknown",
        "browser": "unknown",
        "sentinel_scheduler": "unknown",
        "data_scheduler": "unknown",
        "ontology_projection": "unknown",
    }

    # PostgreSQL check
    try:
        db.execute(text("SELECT 1"))
        checks["db"] = "ok"
    except Exception:
        checks["db"] = "error"

    # Redis/Celery broker check.  A TCP accept is not readiness: authenticate
    # and execute PING so a misconfigured or loading Redis fails closed.
    try:
        import redis
        redis.Redis.from_url(
            settings.redis_url,
            socket_connect_timeout=1.5,
            socket_timeout=1.5,
        ).ping()
        checks["redis"] = "ok"
    except Exception:
        checks["redis"] = "unavailable"

    # Neo4j check
    driver = None
    try:
        from neo4j import GraphDatabase
        driver = GraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_user, settings.neo4j_password),
        )
        driver.verify_connectivity()
        checks["neo4j"] = "ok"
    except Exception:
        checks["neo4j"] = "unavailable"
    finally:
        if driver is not None:
            try:
                driver.close()
            except Exception:
                pass

    # MinIO check.  The unauthenticated liveness endpoint is sufficient here:
    # credential correctness is exercised by real storage operations, while
    # readiness must not create a new unclosed urllib3 pool every few seconds.
    try:
        minio_endpoint = settings.minio_endpoint.rstrip("/")
        if "://" not in minio_endpoint:
            scheme = "https" if settings.minio_use_ssl else "http"
            minio_endpoint = f"{scheme}://{minio_endpoint}"
        _probe_http_service(f"{minio_endpoint}/minio/health/live")
        checks["minio"] = "ok"
    except Exception:
        checks["minio"] = "unavailable"

    # ChromaDB check.  Do not instantiate chromadb.HttpClient here: Chroma
    # 0.5.x does not expose deterministic client shutdown and repeated health
    # probes accumulate CLOSE_WAIT sockets.
    try:
        _probe_http_service(
            f"http://{settings.chroma_host}:{settings.chroma_port}/api/v1/heartbeat")
        checks["chroma"] = "ok"
    except Exception:
        checks["chroma"] = "unavailable"

    # Data-steward Chromium/CDP readiness.  A running container is insufficient:
    # the image may be alive while its internal CDP bridge is misconfigured.
    try:
        from app.data_channel.steward.browser_runtime import probe_browser_cdp
        checks["browser"] = "ok" if probe_browser_cdp()["reachable"] else "unavailable"
    except Exception:
        checks["browser"] = "unavailable"

    try:
        from app.ontologies.sentinels.scan_worker import scan_worker_status
        sentinel_status = scan_worker_status()
        checks["sentinel_scheduler"] = (
            "ok" if sentinel_status["alive"] and not sentinel_status["last_error"]
            else "unavailable")
    except Exception:
        checks["sentinel_scheduler"] = "unavailable"

    try:
        from app.data_channel.sync_tasks.scheduler import get_sync_scheduler
        scheduler = get_sync_scheduler()
        checks["data_scheduler"] = "ok" if scheduler.healthy else "unavailable"
    except Exception:
        checks["data_scheduler"] = "unavailable"

    try:
        from app.models.ontology import OntologyProject
        from app.models.v2.mapping import OntologyMapping
        published_ids = [item[0] for item in db.query(OntologyProject.id).filter(
            OntologyProject.status == "published").all()]
        unhealthy = 0
        if published_ids:
            unhealthy = db.query(OntologyMapping).filter(
                OntologyMapping.ontology_id.in_(published_ids),
                OntologyMapping.status != "applied",
            ).count()
        checks["ontology_projection"] = "ok" if unhealthy == 0 else "unavailable"
    except Exception:
        checks["ontology_projection"] = "unavailable"

    service_keys = (
        "db", "redis", "neo4j", "minio", "chroma", "browser",
        "sentinel_scheduler", "data_scheduler",
        "ontology_projection",
    )
    unavailable = [name for name in service_keys if checks[name] != "ok"]
    strict = settings.environment == "production"
    checks["status"] = "ok" if not unavailable else ("error" if strict else "degraded")
    checks["unavailable"] = unavailable
    return JSONResponse(status_code=503 if strict and unavailable else 200, content=checks)
