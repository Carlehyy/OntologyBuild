"""
OntoPrompt API v2

架构：FastAPI + PostgreSQL + Neo4j + ChromaDB + MinIO + Celery/Redis
v2 新增：Pipelines 全链路（Connection→Dataset→Transform→Curated→Mapping）
v1 兼容：/api/v1/* 路由全部保留

启动：uvicorn app.main:app --host 0.0.0.0 --port 8000
"""
import json

from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from sqlalchemy import inspect, text
from sqlalchemy.orm import Session
from app.database import engine, Base, SessionLocal
from app.config import settings
from app.routers import auth, users, overview, ontologies, files, prompts, models, entities, logic, actions, extraction, graph, settings as settings_router, export, audit, mcp as mcp_router
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
from app.routers.v2 import test_data as test_data_v2

def _seed_db():
    from app.services.auth_service import seed_admin
    from app.models.rules_config import RulesConfig
    import uuid

    db = SessionLocal()
    try:
        # Import all models to ensure tables are created
        from app.models import user, ontology, file, prompt, model_config, entity, logic as logic_model, action, relation, extraction_task, rules_config, audit_task, mcp
        from app.models import user, ontology, file, prompt, model_config, entity, logic as logic_model, action, relation, extraction_task, rules_config, mcp
        from app.models.v2 import dataset as v2_dataset, pipeline as v2_pipeline, connection as v2_connection  # noqa: F401
        from app.models.v2.logic import OntologyLogicRule, OntologyStateMachine  # noqa: F401
        from app.models.v2.action import OntologyActionType, OntologyActionRun  # noqa: F401
        from app.models.v2.curated import CuratedDataset, CuratedReview, CuratedRowEdit  # noqa: F401
        from app.models.v2.sync_task import DataSyncTask, DataSyncHistory  # noqa: F401
        from app.data_channel.pipeline_tasks.models import PipelineTask  # noqa: F401
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
        # 能力注册中心 (技能包；P2 扩展 MCP server 注册)
        from app.capabilities.models import CapSkill  # noqa: F401
        from app.models.workflow_config import WorkflowConfig  # noqa: F401
        # 数据管家 (对话式 n8n 数据流水线：治理记录 + 会话)
        from app.data_channel.steward.models import (  # noqa: F401
            N8nPipeline, StewardConversation, StewardMessage,
        )
        # 事件登记 (智能助手↔数据通道之间的事件采集入口：主体/附件/审计/第三方密钥)
        from app.events.models import (  # noqa: F401
            RegisteredEvent, EventAttachment, EventAuditLog, EventIngestKey,
        )
        Base.metadata.create_all(bind=engine)

        # Lightweight column migrations — create_all skips existing tables
        with engine.connect() as conn:

            columns = {col["name"] for col in inspect(conn).get_columns("extraction_tasks")}
            if "validation_report" not in columns:
                conn.execute(text("ALTER TABLE extraction_tasks ADD COLUMN validation_report JSON"))
                conn.commit()
            entity_columns = {col["name"] for col in inspect(conn).get_columns("entities")}
            if "name_abbr" not in entity_columns:
                conn.execute(text("ALTER TABLE entities ADD COLUMN name_abbr VARCHAR(50)"))
                conn.commit()
            if "snomed_id" not in entity_columns:
                conn.execute(text("ALTER TABLE entities ADD COLUMN snomed_id VARCHAR(50)"))
                conn.commit()
            if "canonical_id" not in entity_columns:
                conn.execute(text("ALTER TABLE entities ADD COLUMN canonical_id VARCHAR(200)"))
                conn.commit()
            for stmt in [
                "ALTER TABLE model_configs ADD COLUMN config_type VARCHAR(30) DEFAULT 'llm'",
                "ALTER TABLE model_configs ADD COLUMN options JSON DEFAULT '{}'",
                "ALTER TABLE ontology_projects ADD COLUMN build_mode VARCHAR(30) DEFAULT 'simple_llm'",
                "ALTER TABLE v2_pipelines ADD COLUMN domain VARCHAR(100) DEFAULT '通用'",
                "ALTER TABLE v2_pipelines ADD COLUMN description TEXT DEFAULT ''",
                "ALTER TABLE v2_pipelines ADD COLUMN definition JSON",
                "ALTER TABLE v2_pipelines ADD COLUMN branch VARCHAR(50) DEFAULT 'main'",
                "ALTER TABLE v2_pipelines ADD COLUMN version INTEGER DEFAULT 1",
                "ALTER TABLE logic_rules ADD COLUMN enabled BOOLEAN DEFAULT 1",
                "ALTER TABLE logic_rules ADD COLUMN status VARCHAR(20) DEFAULT 'draft'",
                "ALTER TABLE actions ADD COLUMN enabled BOOLEAN DEFAULT 1",
                "ALTER TABLE actions ADD COLUMN status VARCHAR(20) DEFAULT 'draft'",
                "ALTER TABLE sentinels ADD COLUMN condition_rows JSON DEFAULT '[]'",
                "ALTER TABLE sentinels ADD COLUMN condition_logic VARCHAR(8) DEFAULT 'and'",
                "ALTER TABLE sentinels ADD COLUMN trigger_mode VARCHAR(16) DEFAULT 'on_enter'",
                "ALTER TABLE sentinels ADD COLUMN muted BOOLEAN DEFAULT 0",
                "ALTER TABLE ontology_versions ADD COLUMN snapshot_formal JSON",
                "ALTER TABLE fo_property_facts ADD COLUMN kind VARCHAR(16) DEFAULT 'property'",
                "ALTER TABLE fo_property_facts ADD COLUMN derived_from JSON",
                "ALTER TABLE fo_property_facts ADD COLUMN supersedes_id VARCHAR",
                "ALTER TABLE fo_property_facts ADD COLUMN caused_by VARCHAR",
                "ALTER TABLE fo_property_facts ADD COLUMN source VARCHAR(200) DEFAULT 'manual'",
                "ALTER TABLE fo_property_facts ADD COLUMN actor_id VARCHAR",
                # —— 图谱编辑器主链路（缺此列则 GET /full 必 500）——
                "ALTER TABLE fo_object_types ADD COLUMN interfaces JSON DEFAULT '[]'",
                # —— HITL 审批闸门 + 执行溯源 ——
                "ALTER TABLE fo_action_types ADD COLUMN requires_approval BOOLEAN DEFAULT 0",
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
            ]:
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

        # Seed 内置技能（幂等，只补缺失，不覆盖用户编辑）
        from app.capabilities.builtin import seed_builtin_skills
        seed_builtin_skills(db)

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
    # 哨兵引擎：注册 CDC(监听对象改动→变化驱动) + 启动定期扫描 worker
    try:
        from app.services.sentinel import register_cdc, start_scan_worker
        register_cdc(); start_scan_worker()
    except Exception as e:
        import logging; logging.getLogger(__name__).warning(f'Sentinel 启动失败: {e}')
    # 初始化 Neo4j 索引（后台执行，不阻塞启动）
    try:
        from app.services.v2.graph.index_setup import setup_indexes
        setup_indexes()
    except Exception:
        pass  # Neo4j 不可用时不影响启动
    # 启动数据同步任务调度器（后台线程）
    try:
        from app.services.v2.sync_scheduler import get_sync_scheduler
        get_sync_scheduler().start()
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"SyncScheduler 启动失败: {e}")
    from app import mcp_server as _mcp_server
    # session manager 每实例只能 run 一次；重复进入 lifespan（如测试）需重建
    async with _mcp_server.reset_session_manager().run():
        yield

app = FastAPI(title="OntoPrompt API", version="0.1.0", lifespan=lifespan)

from app import mcp_server
mcp_server.bind_app(app)

@app.get("/api/health", tags=["health"])
def health_check():
    return {"status": "ok"}

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 允许所有来源（演示环境）
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

app.include_router(auth.router, prefix="/api/v1/auth", tags=["auth"])
app.include_router(users.router, prefix="/api/v1/users", tags=["users"])
app.include_router(overview.router, prefix="/api/v1/overview", tags=["overview"])
app.include_router(ontologies.router, prefix="/api/v1/ontologies", tags=["ontologies"])
app.include_router(files.router, prefix="/api/v1/ontologies/{ontology_id}/files", tags=["files"])
app.include_router(entities.router, prefix="/api/v1/ontologies/{ontology_id}/entities", tags=["entities"])
app.include_router(logic.router, prefix="/api/v1/ontologies/{ontology_id}/logic", tags=["logic"])
app.include_router(actions.router, prefix="/api/v1/ontologies/{ontology_id}/actions", tags=["actions"])
app.include_router(extraction.router, prefix="/api/v1/ontologies/{ontology_id}/execute", tags=["extraction"])
app.include_router(graph.router, prefix="/api/v1/ontologies/{ontology_id}/graph", tags=["graph"])
app.include_router(export.router, prefix="/api/v1/ontologies/{ontology_id}/export", tags=["export"])
app.include_router(audit.router, prefix="/api/v1/ontologies/{ontology_id}/audit", tags=["audit"])
app.include_router(prompts.router, prefix="/api/v1/prompts", tags=["prompts"])
app.include_router(models.router, prefix="/api/v1/models", tags=["models"])
app.include_router(settings_router.router, prefix="/api/v1/settings", tags=["settings"])
app.include_router(mcp_router.router, prefix="/api/v1/mcp", tags=["mcp"])
app.include_router(connections_v2.router, prefix="/api/v2/connections", tags=["v2-connections"])
app.include_router(datasets_v2.router, prefix="/api/v2/datasets", tags=["v2-datasets"])
app.include_router(pipelines_v2.router, prefix="/api/v2/pipelines", tags=["v2-pipelines"])
app.include_router(graph_v2.router, prefix="/api/v2/ontologies", tags=["v2-graph"])
app.include_router(search_v2.router, prefix="/api/v2/ontologies", tags=["v2-search"])
app.include_router(curated_v2.router, prefix="/api/v2/curated", tags=["v2-curated"])
app.include_router(mappings_v2.router, prefix="/api/v2/ontologies", tags=["v2-mappings"])
app.include_router(incremental_v2.router, prefix="/api/v2/incremental", tags=["v2-incremental"])
app.include_router(logic_actions_v2.router, prefix="/api/v2/ontologies", tags=["v2-logic-actions"])
app.include_router(versions_v2.router, prefix="/api/v2/ontologies", tags=["v2-versions"])
app.include_router(attr_schemas_v2.router, prefix="/api/v2/ontologies", tags=["v2-attributes"])
app.include_router(inference_v2.router, prefix="/api/v2/ontologies", tags=["v2-inference"])
app.include_router(extraction_v2.router, prefix="/api/v2/ontologies", tags=["v2-extraction"])
app.include_router(sync_tasks_v2.router, prefix="/api/v2/sync-tasks", tags=["v2-sync-tasks"])
app.include_router(pipeline_tasks_v2.router, prefix="/api/v2/pipeline-tasks", tags=["v2-pipeline-tasks"])
app.include_router(steward_v2.router, prefix="/api/v2/steward", tags=["v2-steward"])
app.include_router(test_data_v2.router, prefix="/api/v2/test-data", tags=["v2-test-data"])

# 正规本体模型 (Palantir 风格) — 平台核心建模 API
app.include_router(formal_router.router, prefix="/api/v2/formal/ontologies", tags=["formal-ontology"])
# 本体智能体 — 授权边界内的 LLM agent（对象/链接/事实/动作是它的全部世界）
from app.ontologies.agent_runtime import router as agent_runtime_router
app.include_router(agent_runtime_router.router, prefix="/api/v2/formal/ontologies", tags=["ontology-agent"])
# 业务探索 — 对话式业务建模：六类模型画布 → 需求文档 → 本体草稿（人审落地）
from app.exploration import router as exploration_router
app.include_router(exploration_router.router, prefix="/api/v2/exploration", tags=["exploration"])
# 能力注册中心 — 平台级技能包管理（P2 扩展 MCP）
from app.capabilities import router as capabilities_router
app.include_router(capabilities_router.router, prefix="/api/v2/capabilities", tags=["capabilities"])
app.include_router(sentinel_router.router, prefix="/api/v1/ontologies/{ontology_id}/sentinels", tags=["sentinel"])
# 数据采集器 — AI HOT 等真实数据源接入
app.include_router(collectors_router.router, prefix="/api/v2/collectors", tags=["collectors"])
# 事件登记 — 智能助手↔数据通道之间的事件采集入口（平台录入 + 第三方 API Key 上传）
from app.events import router as events_router
app.include_router(events_router.router, prefix="/api/v2/events", tags=["events"])
app.include_router(events_router.ingest_router, prefix="/api/v2/ingest", tags=["events-ingest"])

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@app.get("/api/health")
@app.get("/health")
def health(db: Session = Depends(get_db)):
    checks = {
        "status": "ok",
        "db": "unknown",
        "neo4j": "unknown",
        "minio": "unknown",
        "chroma": "unknown",
    }

    # PostgreSQL check
    try:
        db.execute(text("SELECT 1"))
        checks["db"] = "ok"
    except Exception:
        checks["db"] = "error"

    # Neo4j check
    try:
        from neo4j import GraphDatabase
        driver = GraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_user, settings.neo4j_password),
        )
        driver.verify_connectivity()
        driver.close()
        checks["neo4j"] = "ok"
    except Exception:
        checks["neo4j"] = "unavailable"

    # MinIO check
    try:
        from minio import Minio
        client = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_use_ssl,
        )
        client.list_buckets()
        checks["minio"] = "ok"
    except Exception:
        checks["minio"] = "unavailable"

    # ChromaDB check
    try:
        import chromadb
        client = chromadb.HttpClient(
            host=settings.chroma_host,
            port=settings.chroma_port,
        )
        client.heartbeat()
        checks["chroma"] = "ok"
    except Exception:
        checks["chroma"] = "unavailable"

    return checks
