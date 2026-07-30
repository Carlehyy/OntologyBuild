"""
OntoPrompt API v2

架构：FastAPI + PostgreSQL + Neo4j + ChromaDB + MinIO + Celery/Redis
v2 新增：Pipelines 全链路（Connection→Dataset→Transform→Curated→Mapping）
v1 兼容：/api/v1/* 路由全部保留

启动：uvicorn app.main:app --host 0.0.0.0 --port 8000
"""
import hmac
import json

from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from sqlalchemy.orm import Session
from app.database import SessionLocal
from app.config import settings
from app.bootstrap import health as bootstrap_health
from app.bootstrap.lifecycle import application_lifespan
from app.bootstrap.seeding import seed_database
from app.routers import auth, users, ontologies, files, entities, logic, actions, extraction, graph, settings as settings_router, export, audit, mcp as mcp_router, domains
from app.model_configs.router import router as model_configs_router
from app.platform.router import router as overview_router
from app.settings.prompts.router import router as prompts_router
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
from app.data_channel.file_assets import router as pipeline_file_assets
from app.routers.v2 import test_data as test_data_v2
from app.data_channel.access import asset_lake_access_guard
from app.data_channel.datasets.sharing_router import (
    management_router as manual_sharing_router,
    public_router as public_manual_sharing_router,
)
from app.ontologies.access import legacy_ontology_write_guard

_seed_db = seed_database

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Compatibility wrapper: callers may still monkeypatch app.main._seed_db.
    async with application_lifespan(app, seed_database=_seed_db):
        yield

app = FastAPI(title="OntoPrompt API", version="0.1.0", lifespan=lifespan)

from app import mcp_server
mcp_server.bind_app(app)

@app.get("/health/live", tags=["health"])
def liveness():
    """Process liveness only; dependency readiness is exposed separately."""
    return bootstrap_health.liveness_payload()


# Compatibility aliases: old imports keep object identity, and patching
# ``app.main.urllib.request.urlopen`` still mutates the module used by the
# canonical probe implementation.
urllib = bootstrap_health.urllib
_probe_http_service = bootstrap_health.probe_http_service

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
        from app.settings.object_storage import mcp_server as minio_mcp
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
        if path == "/mcp/minio" or path.startswith("/mcp/minio/"):
            headers = dict(scope.get("headers") or [])
            auth = headers.get(b"authorization", b"").decode("latin-1")
            if not auth.startswith("Bearer "):
                await _send_json(send, 401, {"detail": "Missing MinIO MCP Bearer token"})
                return
            token = auth.removeprefix("Bearer ").strip()
            try:
                minio_mcp.validate_bearer_token(token)
            except HTTPException as exc:
                await _send_json(send, exc.status_code, {"detail": exc.detail})
                return
            minio_scope = dict(scope)
            minio_scope["path"] = "/"
            minio_scope["raw_path"] = b"/"
            await minio_mcp.handle_mcp(minio_scope, receive, send)
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
community_plugins_guard = menu_guard("community.plugins")
events_guard = menu_guard("events")
admin_guard = [Depends(require_admin)]

app.include_router(auth.router, prefix="/api/v1/auth", tags=["auth"])
app.include_router(users.router, prefix="/api/v1/users", tags=["users"])
app.include_router(
    overview_router,
    prefix="/api/v1/overview",
    tags=["overview"],
    dependencies=overview_guard,
)
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
app.include_router(prompts_router, prefix="/api/v1/prompts", tags=["prompts"])
app.include_router(domains.router, prefix="/api/v1/domains", tags=["domains"])
app.include_router(model_configs_router, prefix="/api/v1/models", tags=["models"], dependencies=models_guard)
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
# n8n invocation authority is separate from the system management MCP token;
# the endpoint also pins interface revisions and validates dynamic parameters.
app.include_router(api_hub_proxy.internal_router)
# Ordinary HTTP consumers use per-caller proxy keys and stable /proxy/<slug> URLs.
app.include_router(api_hub_http_proxy.public_router)
asset_lake_guard = [Depends(asset_lake_access_guard), *data_guard]
app.include_router(connections_v2.router, prefix="/api/v2/connections", tags=["v2-connections"], dependencies=asset_lake_guard)
app.include_router(datasets_v2.router, prefix="/api/v2/datasets", tags=["v2-datasets"], dependencies=asset_lake_guard)
app.include_router(manual_sharing_router, prefix="/api/v2/manual-dataset-sharing", tags=["manual-dataset-sharing"], dependencies=asset_lake_guard)
app.include_router(public_manual_sharing_router, prefix="/api/public/manual-datasets", tags=["public-manual-datasets"])
app.include_router(pipelines_v2.router, prefix="/api/v2/pipelines", tags=["v2-pipelines"], dependencies=pipeline_guard)
app.include_router(
    pipeline_file_assets.upload_router,
    prefix="/api/v2/file-transfer",
    tags=["v2-pipeline-file-transfer"],
)
app.include_router(
    pipeline_file_assets.asset_router,
    prefix="/api/v2/file-assets",
    tags=["v2-pipeline-file-assets"],
    dependencies=[*asset_lake_guard, *pipeline_guard],
)
app.include_router(
    pipeline_file_assets.public_router,
    prefix="/api/public/file-assets",
    tags=["public-pipeline-file-assets"],
)
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
from app.inbox import router as inbox_router
app.include_router(inbox_router.router, prefix="/api/v2/inbox", tags=["inbox"])
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
from app.ontologies.decision_simulation import router as decision_simulation_router
app.include_router(
    decision_simulation_router.router,
    prefix="/api/v2/formal/ontologies",
    tags=["ontology-decision-simulation"],
    dependencies=agent_guard,
)
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
# 开放社区复用超级助手的用户级 MCP 清单，但拥有独立菜单权限边界。
from app.community import router as community_router
app.include_router(
    community_router.router,
    prefix="/api/v2/community",
    tags=["community"],
    dependencies=community_plugins_guard,
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
    # Resolve the compatibility alias at call time so legacy monkeypatch targets
    # continue to control the readiness endpoint.
    return bootstrap_health.readiness_response(
        db,
        probe=_probe_http_service,
    )
