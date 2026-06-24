"""
Ontology-Graph-AI Framework - FastAPI Application Entry Point

This is the main entry point for the backend API.
All routes are mounted under their respective prefixes.

Key design decisions:
1. Configuration is data, not code (ontology/rules/mappings in DB)
2. Engine is domain-agnostic (only understands abstract concepts)
3. Human-in-the-loop at every step (feedback drives evolution)
4. LLM is optional - system works with deterministic fallback
"""

import os
import sys

# Add the backend directory to the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from app.config import get_settings, ensure_directories
from app.database import init_db
from app.routers import ontology, rules, mapping, extraction, graph, inference, feedback, admin


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    # Startup
    ensure_directories()
    init_db()
    print(f"✅ {settings.app_name} v{settings.app_version} started")
    print(f"   LLM Provider: {settings.llm_provider}")
    print(f"   Database: {settings.database_url}")
    print(f"   Graph DB: {settings.graph_db_path}")
    yield
    # Shutdown
    print("👋 Application shutting down")


settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    description="""
    Ontology-Graph-AI Self-Evolving Framework API.

    A domain-agnostic framework for document → ontology → graph → AI inference pipelines
    with human-in-the-loop feedback driving continuous self-improvement.

    Stage 1 (Current): Execution + Feedback Recording
    - Document upload and LLM-based extraction
    - Ontology/schema management
    - Rule management
    - Graph browsing and querying
    - Human review workflow
    - Structured feedback recording

    Key Principles:
    - Configuration is data, not code
    - Engine is domain-agnostic
    - LLM is optional (deterministic fallback always works)
    - Every user interaction is a feedback signal
    """,
    version=settings.app_version,
    lifespan=lifespan,
)

# CORS - configured for development
origins = settings.cors_origins.split(",") if settings.cors_origins else ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ──────────────────────────────────────────────
# Route Registration
# ──────────────────────────────────────────────

app.include_router(ontology.router, prefix="/api/v1")
app.include_router(rules.router, prefix="/api/v1")
app.include_router(mapping.router, prefix="/api/v1")
app.include_router(extraction.router, prefix="/api/v1")
app.include_router(graph.router, prefix="/api/v1")
app.include_router(inference.router, prefix="/api/v1")
app.include_router(feedback.router, prefix="/api/v1")
app.include_router(admin.router, prefix="/api/v1")


# ──────────────────────────────────────────────
# Root Endpoint
# ──────────────────────────────────────────────

@app.get("/")
def root():
    """Root endpoint - API info."""
    return {
        "name": settings.app_name,
        "version": settings.app_version,
        "description": "Ontology-Graph-AI Self-Evolving Framework",
        "stage": "Stage 1: Execution + Feedback Recording",
        "docs": "/docs",
    }


@app.get("/api/v1")
def api_info():
    """API info endpoint."""
    return {
        "version": "v1",
        "endpoints": {
            "ontology": "/api/v1/ontology",
            "rules": "/api/v1/rules",
            "mappings": "/api/v1/mappings",
            "extraction": "/api/v1/extraction",
            "graph": "/api/v1/graph",
            "inference": "/api/v1/inference",
            "feedback": "/api/v1/feedback",
            "admin": "/api/v1/admin",
        },
    }


# ──────────────────────────────────────────────
# Seed Data (Development)
# ──────────────────────────────────────────────

@app.post("/api/v1/seed")
def seed_data():
    """
    Seed the database with sample data for development/testing.
    Creates a sample 'ITSM' domain with realistic configuration.
    """
    from app.database import SessionLocal
    from app.models import (
        Domain, ObjectType, PropertyType, RelationType, Rule,
        Entity, Relation, User, AuditLog,
    )
    from app.services.graph_service import get_graph_service

    db = SessionLocal()

    try:
        # Check if already seeded
        existing = db.query(Domain).filter(Domain.name == "IT Service Management").first()
        if existing:
            return {"message": "Database already seeded", "domain_id": existing.id}

        # Create domain
        domain = Domain(
            name="IT Service Management",
            description="IT service management domain with incidents, changes, and assets",
        )
        db.add(domain)
        db.flush()

        # Object Types
        ot_incident = ObjectType(
            domain_id=domain.id,
            name="Incident",
            description="An unplanned interruption to an IT service",
            color="#ef4444",
            icon="alert-triangle",
        )
        ot_change = ObjectType(
            domain_id=domain.id,
            name="Change",
            description="Addition, modification, or removal of anything that could affect IT services",
            color="#f59e0b",
            icon="git-branch",
        )
        ot_ci = ObjectType(
            domain_id=domain.id,
            name="ConfigurationItem",
            description="Any component that needs to be managed to deliver an IT service",
            color="#3b82f6",
            icon="server",
        )
        ot_person = ObjectType(
            domain_id=domain.id,
            name="Person",
            description="An individual involved in IT service management",
            color="#10b981",
            icon="user",
        )
        db.add_all([ot_incident, ot_change, ot_ci, ot_person])
        db.flush()

        # Properties for Incident
        db.add_all([
            PropertyType(object_type_id=ot_incident.id, name="priority", data_type="enum",
                        enum_values=["P1-Critical", "P2-High", "P3-Medium", "P4-Low"], is_required=True),
            PropertyType(object_type_id=ot_incident.id, name="status", data_type="enum",
                        enum_values=["New", "In Progress", "Resolved", "Closed"], is_required=True),
            PropertyType(object_type_id=ot_incident.id, name="category", data_type="string"),
            PropertyType(object_type_id=ot_incident.id, name="description", data_type="text"),
        ])

        # Properties for Change
        db.add_all([
            PropertyType(object_type_id=ot_change.id, name="change_type", data_type="enum",
                        enum_values=["Standard", "Normal", "Emergency"], is_required=True),
            PropertyType(object_type_id=ot_change.id, name="risk_level", data_type="enum",
                        enum_values=["Low", "Medium", "High"], is_required=True),
            PropertyType(object_type_id=ot_change.id, name="status", data_type="enum",
                        enum_values=["Draft", "Submitted", "Approved", "Implemented", "Closed"]),
            PropertyType(object_type_id=ot_change.id, name="description", data_type="text"),
        ])

        # Properties for CI
        db.add_all([
            PropertyType(object_type_id=ot_ci.id, name="ci_type", data_type="string", is_required=True),
            PropertyType(object_type_id=ot_ci.id, name="owner", data_type="string"),
            PropertyType(object_type_id=ot_ci.id, name="location", data_type="string"),
            PropertyType(object_type_id=ot_ci.id, name="status", data_type="enum",
                        enum_values=["Active", "Retired", "Maintenance"]),
        ])

        # Properties for Person
        db.add_all([
            PropertyType(object_type_id=ot_person.id, name="role", data_type="string"),
            PropertyType(object_type_id=ot_person.id, name="department", data_type="string"),
            PropertyType(object_type_id=ot_person.id, name="email", data_type="string"),
        ])

        # Relation Types
        rt_affects = RelationType(
            domain_id=domain.id,
            name="affects",
            description="An incident affects a configuration item",
            source_type_id=ot_incident.id,
            target_type_id=ot_ci.id,
            is_directed=True,
            cardinality="many_to_many",
        )
        rt_impacts = RelationType(
            domain_id=domain.id,
            name="impacts",
            description="A change impacts a configuration item",
            source_type_id=ot_change.id,
            target_type_id=ot_ci.id,
            is_directed=True,
            cardinality="many_to_many",
        )
        rt_assignee = RelationType(
            domain_id=domain.id,
            name="assigned_to",
            description="An incident or change is assigned to a person",
            source_type_id=ot_incident.id,
            target_type_id=ot_person.id,
            is_directed=True,
            cardinality="many_to_one",
        )
        rt_related = RelationType(
            domain_id=domain.id,
            name="related_to",
            description="An incident is related to a change",
            source_type_id=ot_incident.id,
            target_type_id=ot_change.id,
            is_directed=False,
            cardinality="many_to_many",
        )
        db.add_all([rt_affects, rt_impacts, rt_assignee, rt_related])
        db.flush()

        # Sample Rules
        db.add_all([
            Rule(
                domain_id=domain.id,
                name="P1 Incident Alert",
                description="Alert when a P1 critical incident is created",
                condition={"pattern": "entity_match", "parameters": {"entity_type": "Incident", "prop_priority": "P1-Critical"}},
                action_type="emit_alert",
                action_config={"message_template": "P1 Critical Incident: {{entity.name}} requires immediate attention", "severity": "critical"},
                priority=100,
                is_active=True,
                is_draft=False,
            ),
            Rule(
                domain_id=domain.id,
                name="High Risk Change Approval",
                description="Require approval for high-risk changes",
                condition={"pattern": "entity_match", "parameters": {"entity_type": "Change", "prop_risk_level": "High"}},
                action_type="require_approval",
                action_config={"message_template": "High-risk change requires CAB approval: {{entity.name}}", "severity": "high"},
                priority=90,
                is_active=True,
                is_draft=False,
            ),
            Rule(
                domain_id=domain.id,
                name="Unassigned Incident",
                description="Flag incidents without an assignee for more than 1 hour",
                condition={"pattern": "entity_match", "parameters": {"entity_type": "Incident", "prop_status": "New"}},
                action_type="emit_alert",
                action_config={"message_template": "Incident {{entity.name}} has been unassigned for too long", "severity": "medium"},
                priority=50,
                is_active=True,
                is_draft=False,
            ),
        ])

        # Sample Entities
        entities = [
            Entity(domain_id=domain.id, object_type_id=ot_incident.id, name="INC-2024-001",
                   properties={"priority": "P1-Critical", "status": "In Progress", "category": "Network", "description": "Core switch failure in DC1"},
                   confidence=1.0, is_verified=True),
            Entity(domain_id=domain.id, object_type_id=ot_incident.id, name="INC-2024-002",
                   properties={"priority": "P3-Medium", "status": "New", "category": "Application", "description": "Email service slow response"},
                   confidence=1.0, is_verified=True),
            Entity(domain_id=domain.id, object_type_id=ot_change.id, name="CHG-2024-015",
                   properties={"change_type": "Normal", "risk_level": "High", "status": "Submitted", "description": "Firewall rule upgrade"},
                   confidence=1.0, is_verified=True),
            Entity(domain_id=domain.id, object_type_id=ot_ci.id, name="SW-DC1-Core-01",
                   properties={"ci_type": "Network Switch", "owner": "Network Team", "location": "DC1", "status": "Active"},
                   confidence=1.0, is_verified=True),
            Entity(domain_id=domain.id, object_type_id=ot_ci.id, name="SRV-APP-Email-01",
                   properties={"ci_type": "Server", "owner": "App Team", "location": "DC2", "status": "Active"},
                   confidence=1.0, is_verified=True),
            Entity(domain_id=domain.id, object_type_id=ot_person.id, name="Alice Zhang",
                   properties={"role": "Network Engineer", "department": "Infrastructure", "email": "alice@company.com"},
                   confidence=1.0, is_verified=True),
            Entity(domain_id=domain.id, object_type_id=ot_person.id, name="Bob Chen",
                   properties={"role": "System Administrator", "department": "Operations", "email": "bob@company.com"},
                   confidence=1.0, is_verified=True),
        ]
        db.add_all(entities)
        db.flush()

        # Sync entities to graph
        graph_svc = get_graph_service()
        ot_map = {ot.id: ot for ot in [ot_incident, ot_change, ot_ci, ot_person]}
        for entity in entities:
            ot = ot_map.get(entity.object_type_id)
            graph_svc.sync_entity(
                domain_id=domain.id,
                entity_id=entity.id,
                object_type_id=entity.object_type_id,
                object_type_name=ot.name if ot else "Unknown",
                name=entity.name,
                properties=entity.properties or {},
                confidence=entity.confidence,
                is_verified=entity.is_verified,
            )

        # Sample Relations
        relations = [
            Relation(domain_id=domain.id, relation_type_id=rt_affects.id,
                     source_id=entities[0].id, target_id=entities[3].id, confidence=1.0, is_verified=True),
            Relation(domain_id=domain.id, relation_type_id=rt_affects.id,
                     source_id=entities[1].id, target_id=entities[4].id, confidence=1.0, is_verified=True),
            Relation(domain_id=domain.id, relation_type_id=rt_impacts.id,
                     source_id=entities[2].id, target_id=entities[3].id, confidence=1.0, is_verified=True),
            Relation(domain_id=domain.id, relation_type_id=rt_assignee.id,
                     source_id=entities[0].id, target_id=entities[5].id, confidence=1.0, is_verified=True),
            Relation(domain_id=domain.id, relation_type_id=rt_assignee.id,
                     source_id=entities[1].id, target_id=entities[6].id, confidence=1.0, is_verified=True),
        ]
        db.add_all(relations)
        db.flush()

        # Sync relations to graph
        rt_map = {rt.id: rt for rt in [rt_affects, rt_impacts, rt_assignee, rt_related]}
        for relation in relations:
            rt = rt_map.get(relation.relation_type_id)
            graph_svc.sync_relation(
                domain_id=domain.id,
                relation_id=relation.id,
                relation_type_id=relation.relation_type_id,
                relation_name=rt.name if rt else "relates_to",
                source_id=relation.source_id,
                target_id=relation.target_id,
                confidence=relation.confidence,
                is_verified=relation.is_verified,
            )

        # Sample Users
        db.add_all([
            User(username="admin", email="admin@company.com", display_name="System Admin",
                 role="admin", password_hash=None),
            User(username="expert", email="expert@company.com", display_name="Domain Expert",
                 role="domain_expert", password_hash=None),
            User(username="reviewer", email="reviewer@company.com", display_name="Review User",
                 role="reviewer", password_hash=None),
        ])

        db.commit()

        return {
            "message": "Database seeded successfully",
            "domain_id": domain.id,
            "domain_name": domain.name,
            "object_types": 4,
            "relation_types": 4,
            "rules": 3,
            "entities": len(entities),
            "relations": len(relations),
        }

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Seed failed: {str(e)}")
    finally:
        db.close()


# ──────────────────────────────────────────────
# Run directly (development)
# ──────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info",
    )
