"""Migration contract for retiring legacy document-to-ontology extraction."""

import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text


RETIRED_TABLES = {
    "rules_config",
    "extraction_tasks",
    "prompts",
    "uploaded_files",
    "mcp_interface_configs",
}


def _alembic_config(backend: Path, db_path: Path) -> Config:
    cfg = Config(str(backend / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend / "alembic"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return cfg


def test_retirement_drops_only_legacy_tables_and_downgrade_is_schema_only(
    tmp_path,
    monkeypatch,
):
    backend = Path(__file__).resolve().parents[2]
    db_path = tmp_path / "retired-extraction.db"
    application_logger = logging.getLogger(
        "app.data_channel.datasets.router",
    )
    monkeypatch.setattr(application_logger, "disabled", False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    cfg = _alembic_config(backend, db_path)

    command.upgrade(cfg, "0054_fact_lineage_indexes")
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO rules_config "
            "(id,rule_key,rule_value,rule_label_cn,rule_label_en,editable,"
            "created_at,updated_at) VALUES "
            "('rule-1','legacy','value','旧规则','Legacy',1,"
            "CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
        ))
        connection.execute(text(
            "INSERT INTO prompts "
            "(id,name,domain,content,version,created_by,created_at,updated_at) "
            "VALUES ('prompt-1','旧模板','supply-chain','legacy','v1.0',"
            "'missing-user',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
        ))
        connection.execute(text(
            "INSERT INTO uploaded_files "
            "(id,ontology_id,filename,file_path,file_size,mime_type,converted_md,"
            "created_at) VALUES ('file-1','missing-ontology','legacy.pdf',"
            "'/legacy/legacy.pdf',1,'application/pdf','legacy',CURRENT_TIMESTAMP)"
        ))
        connection.execute(text(
            "INSERT INTO extraction_tasks "
            "(id,ontology_id,prompt_id,model_id,status,parameters,progress,error,"
            "validation_report,created_at,updated_at) VALUES "
            "('task-1','missing-ontology','prompt-1',NULL,'completed','{}','{}',"
            "NULL,'{}',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
        ))
        connection.execute(text(
            "INSERT INTO mcp_interface_configs "
            "(id,operation_id,method,path,enabled,display_name,description,"
            "created_by,updated_by,created_at,updated_at) VALUES "
            "('mcp-1','legacy_operation','GET','/legacy',1,'Legacy',NULL,NULL,"
            "NULL,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
        ))
    engine.dispose()

    command.upgrade(cfg, "head")
    assert application_logger.disabled is False
    engine = create_engine(f"sqlite:///{db_path}")
    tables = set(inspect(engine).get_table_names())
    assert RETIRED_TABLES.isdisjoint(tables)
    assert "minio_config" not in tables
    assert {
        "minio_operation_audits",
        "super_assistant_mcp_servers",
        "v2_datasets",
        "v2_ontology_mappings",
    } <= tables
    engine.dispose()

    command.downgrade(cfg, "0054_fact_lineage_indexes")
    engine = create_engine(f"sqlite:///{db_path}")
    inspector = inspect(engine)
    assert RETIRED_TABLES <= set(inspector.get_table_names())
    assert {column["name"] for column in inspector.get_columns(
        "extraction_tasks",
    )} == {
        "id", "ontology_id", "prompt_id", "model_id", "status",
        "parameters", "progress", "error", "validation_report",
        "created_at", "updated_at",
    }
    assert {
        foreign_key["referred_table"]
        for foreign_key in inspector.get_foreign_keys("extraction_tasks")
    } == {"ontology_projects", "prompts", "model_configs"}
    assert {
        index["name"]
        for index in inspector.get_indexes("mcp_interface_configs")
    } == {"ix_mcp_interface_configs_operation_id"}
    with engine.connect() as connection:
        for table_name in RETIRED_TABLES:
            assert connection.execute(text(
                f'SELECT COUNT(*) FROM "{table_name}"'
            )).scalar_one() == 0
    engine.dispose()

    # Re-upgrade proves the schema-only downgrade remains operational.
    command.upgrade(cfg, "head")
    engine = create_engine(f"sqlite:///{db_path}")
    assert RETIRED_TABLES.isdisjoint(inspect(engine).get_table_names())
    engine.dispose()
