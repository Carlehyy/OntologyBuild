"""Configuration-aware local Uvicorn entry point.

Run from the ``backend`` directory with::

    uv run python -m app.dev_server

Production containers intentionally keep using their existing explicit
Uvicorn command and never enter this module.
"""

import uvicorn

from app.config import settings


def main() -> None:
    if settings.environment.strip().lower() == "production":
        raise RuntimeError(
            "app.dev_server 仅用于本地开发；生产环境请继续使用部署中的 Uvicorn 命令"
        )
    uvicorn.run(
        "app.main:app",
        host=settings.local_backend_host,
        port=settings.local_backend_port,
        reload=True,
    )


if __name__ == "__main__":
    main()
