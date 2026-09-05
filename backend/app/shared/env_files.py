"""Stable environment-file locations for local OntologyBuild development.

The paths are derived from this module rather than the process working
directory.  This keeps ``uvicorn``, Alembic, NATS executor and the embedded API Hub
consistent whether they are launched from the repository root or ``backend``.
"""

from pathlib import Path

from dotenv import load_dotenv


BACKEND_DIR = Path(__file__).resolve().parents[2]
PROJECT_DIR = BACKEND_DIR.parent
LEGACY_BACKEND_ENV_FILE = BACKEND_DIR / ".env"
LOCAL_CONFIG_ENV_FILE = PROJECT_DIR / "config" / "generated" / "local" / ".env"


def load_backend_dotenv() -> None:
    """Populate missing process variables from local development env files.

    ``python-dotenv`` does not distinguish values loaded by an earlier file
    from variables inherited from the operating system.  Loading the central
    file first therefore gives it precedence over the legacy ``backend/.env``
    while ``override=False`` keeps real process variables authoritative.
    """

    load_dotenv(
        LOCAL_CONFIG_ENV_FILE,
        encoding="utf-8",
        override=False,
    )
    load_dotenv(
        LEGACY_BACKEND_ENV_FILE,
        encoding="utf-8",
        override=False,
    )
