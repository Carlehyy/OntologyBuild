"""
Configuration management for the Ontology-Graph-AI Framework.
All settings are loaded from environment variables with sensible defaults.
"""

import os
from pathlib import Path
from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # App
    app_name: str = Field(default="OntologyGraph AI Framework", alias="APP_NAME")
    app_version: str = Field(default="0.1.0", alias="APP_VERSION")
    debug: bool = Field(default=False, alias="DEBUG")
    secret_key: str = Field(default="change-me-in-production", alias="SECRET_KEY")

    # CORS
    cors_origins: str = Field(default="http://localhost:5173,http://localhost:3000", alias="CORS_ORIGINS")

    # Database (SQLite for development, PostgreSQL for production)
    database_url: str = Field(default="sqlite:///./data/ontology_graph.db", alias="DATABASE_URL")

    # Graph Database (Kùzu - embedded property graph)
    graph_db_path: str = Field(default="./data/graph", alias="GRAPH_DB_PATH")

    # LLM Settings
    llm_provider: str = Field(default="openai", alias="LLM_PROVIDER")  # "openai", "ollama", "none"
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    openai_model: str = Field(default="gpt-4o-mini", alias="OPENAI_MODEL")
    ollama_base_url: str = Field(default="http://localhost:11434", alias="OLLAMA_BASE_URL")
    ollama_model: str = Field(default="llama3.2", alias="OLLAMA_MODEL")

    # Extraction Settings
    extraction_max_tokens: int = Field(default=4000, alias="EXTRACTION_MAX_TOKENS")
    extraction_temperature: float = Field(default=0.1, alias="EXTRACTION_TEMPERATURE")

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        populate_by_name = True
        extra = "ignore"


def get_settings() -> Settings:
    """Get application settings singleton."""
    return Settings()


# Ensure data directories exist
def ensure_directories():
    """Create necessary data directories."""
    settings = get_settings()
    data_dir = Path("./data")
    data_dir.mkdir(parents=True, exist_ok=True)
    # Note: Don't create graph_dir - Kùzu needs to create it itself
    uploads_dir = Path("./data/uploads")
    uploads_dir.mkdir(parents=True, exist_ok=True)
