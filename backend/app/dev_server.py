"""Configuration-aware local Uvicorn entry point.

Run from the ``backend`` directory with::

    uv run python -m app.dev_server

Production containers intentionally keep using their existing explicit
Uvicorn command and never enter this module.
"""

from pathlib import Path

import uvicorn
from alembic import command
from alembic.config import Config

from app.config import settings


def upgrade_local_schema() -> None:
    """Apply committed Alembic history before a source-development startup."""
    backend_root = Path(__file__).resolve().parents[1]
    config = Config(str(backend_root / "alembic.ini"))
    config.set_main_option("script_location", str(backend_root / "alembic"))
    command.upgrade(config, "head")


def main() -> None:
    if settings.environment.strip().lower() == "production":
        raise RuntimeError(
            "app.dev_server 仅用于本地开发；生产环境请继续使用部署中的 Uvicorn 命令"
        )
    upgrade_local_schema()
    uvicorn.run(
        "app.main:app",
        host=settings.local_backend_host,
        port=settings.local_backend_port,
        reload=True,
    )


if __name__ == "__main__":
    main()
