"""Process liveness and dependency-readiness implementation."""
from __future__ import annotations

import tempfile
import urllib.request
from collections.abc import Callable

from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.shared.http_transport import open_with_loopback_bypass


def liveness_payload() -> dict[str, str]:
    return {"status": "ok"}


def probe_http_service(url: str, *, timeout: float = 3.0) -> None:
    """Probe an internal HTTP service without leaving a pooled socket behind.

    Readiness runs repeatedly in production.  Constructing a new Chroma
    ``HttpClient`` for every probe leaked its underlying HTTP connection into
    CLOSE_WAIT until the backend exhausted its 1024 file descriptors.  A
    short-lived stdlib request with ``Connection: close`` keeps this path
    bounded and makes ownership of the socket explicit.
    """
    request = urllib.request.Request(url, headers={"Connection": "close"})
    with open_with_loopback_bypass(request, timeout=timeout) as response:
        if response.status >= 400:
            raise RuntimeError(f"health probe returned HTTP {response.status}")
        # Consume a bounded response so the connection can close cleanly.
        response.read(4096)


def readiness_response(
    db: Session,
    *,
    probe: Callable[[str], None],
) -> JSONResponse:
    checks = {
        "status": "ok",
        "db": "unknown",
        "redis": "unknown",
        "neo4j": "unknown",
        "minio": "unknown",
        "object_storage": "unknown",
        "chroma": "unknown",
        "browser": "unknown",
        "sentinel_scheduler": "unknown",
        "sentinel_cdc": "unknown",
        "data_scheduler": "unknown",
        "ontology_projection": "unknown",
    }

    # PostgreSQL check
    try:
        db.execute(text("SELECT 1"))
        checks["db"] = "ok"
    except Exception:
        checks["db"] = "error"

    # Redis/Celery broker check. A TCP accept is not readiness: authenticate
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

    # MinIO check. Prefer the administrator-managed endpoint when enabled.
    # The unauthenticated liveness endpoint is sufficient here:
    # credential correctness is exercised by real storage operations, while
    # readiness must not create a new unclosed urllib3 pool every few seconds.
    try:
        from app.settings.object_storage.models import MinioConfig

        managed = None
        if not settings.require_external_dependencies:
            managed = db.query(MinioConfig).filter(
                MinioConfig.id == "default",
                MinioConfig.enabled.is_(True),
                MinioConfig.connected.is_(True),
            ).first()
        minio_endpoint = (
            managed.endpoint if managed else settings.minio_endpoint
        ).rstrip("/")
        if "://" not in minio_endpoint:
            scheme = (
                "https"
                if (managed.secure if managed else settings.minio_use_ssl)
                else "http"
            )
            minio_endpoint = f"{scheme}://{minio_endpoint}"
        probe(f"{minio_endpoint}/minio/health/live")
        checks["minio"] = "ok"
        checks["object_storage"] = "minio"
    except Exception:
        checks["minio"] = "unavailable"
        if settings.storage_local_fallback:
            try:
                from app.shared.storage import StorageService

                local_base = StorageService._configured_local_base()
                local_base.mkdir(parents=True, exist_ok=True)
                # os.access is unreliable for root; a same-directory temp file
                # verifies that the mounted fallback is actually writable.
                with tempfile.NamedTemporaryFile(dir=local_base):
                    pass
                checks["object_storage"] = "local"
            except Exception:
                checks["object_storage"] = "unavailable"
        else:
            checks["object_storage"] = "unavailable"

    # ChromaDB check. Do not instantiate chromadb.HttpClient here: Chroma
    # 0.5.x does not expose deterministic client shutdown and repeated health
    # probes accumulate CLOSE_WAIT sockets.
    try:
        probe(
            f"http://{settings.chroma_host}:"
            f"{settings.chroma_port}/api/v1/heartbeat"
        )
        checks["chroma"] = "ok"
    except Exception:
        checks["chroma"] = "unavailable"

    # Data-steward Chromium/CDP readiness. A running container is insufficient:
    # the image may be alive while its internal CDP bridge is misconfigured.
    try:
        from app.data_channel.steward.browser_runtime import probe_browser_cdp

        checks["browser"] = (
            "ok" if probe_browser_cdp()["reachable"] else "unavailable"
        )
    except Exception:
        checks["browser"] = "unavailable"

    try:
        from app.ontologies.sentinels.scan_worker import scan_worker_status

        sentinel_status = scan_worker_status()
        checks["sentinel_scheduler"] = (
            "ok"
            if sentinel_status["alive"] and not sentinel_status["last_error"]
            else "unavailable"
        )
    except Exception:
        checks["sentinel_scheduler"] = "unavailable"

    try:
        from app.ontologies.sentinels.cdc import cdc_dispatch_status

        cdc_status = cdc_dispatch_status(
            session_factory=sessionmaker(
                bind=db.get_bind(),
                expire_on_commit=False,
            )
        )
        checks["sentinel_cdc"] = (
            "ok" if cdc_status["healthy"] else "unavailable"
        )
        checks["sentinel_cdc_detail"] = {
            "quiescent": cdc_status["quiescent"],
            "worker_alive": cdc_status["worker_alive"],
            "queued": cdc_status["queued"],
            "durable": cdc_status["durable"],
            "error_count": len(cdc_status["last_errors"]),
            "error_code": (
                "dispatch_error"
                if cdc_status["last_error"] or cdc_status["last_errors"]
                else None
            ),
        }
    except Exception:
        checks["sentinel_cdc"] = "unavailable"
        checks["sentinel_cdc_detail"] = {
            "quiescent": False,
            "error_code": "status_unavailable",
        }

    try:
        from app.data_channel.sync_tasks.scheduler import get_sync_scheduler

        scheduler = get_sync_scheduler()
        checks["data_scheduler"] = (
            "ok" if scheduler.healthy else "unavailable"
        )
    except Exception:
        checks["data_scheduler"] = "unavailable"

    try:
        from app.models.ontology import OntologyProject
        from app.models.v2.mapping import OntologyMapping

        published_ids = [
            item[0]
            for item in db.query(OntologyProject.id)
            .filter(OntologyProject.status == "published")
            .all()
        ]
        unhealthy = 0
        if published_ids:
            unhealthy = (
                db.query(OntologyMapping)
                .filter(
                    OntologyMapping.ontology_id.in_(published_ids),
                    OntologyMapping.status != "applied",
                )
                .count()
            )
        checks["ontology_projection"] = (
            "ok" if unhealthy == 0 else "unavailable"
        )
    except Exception:
        checks["ontology_projection"] = "unavailable"

    service_keys = (
        "db",
        "redis",
        "neo4j",
        "minio",
        "chroma",
        "browser",
        "sentinel_scheduler",
        "data_scheduler",
        "sentinel_cdc",
        "ontology_projection",
    )
    unavailable = [name for name in service_keys if checks[name] != "ok"]
    # MinIO is optional when the configured durable fallback is writable.
    # Keep ``minio=unavailable`` for observability without making deployment
    # readiness depend on an intentionally absent service.
    if checks["object_storage"] == "local":
        unavailable = [name for name in unavailable if name != "minio"]
    strict = settings.environment == "production"
    checks["status"] = (
        "ok" if not unavailable else ("error" if strict else "degraded")
    )
    checks["unavailable"] = unavailable
    return JSONResponse(
        status_code=503 if strict and unavailable else 200,
        content=checks,
    )
