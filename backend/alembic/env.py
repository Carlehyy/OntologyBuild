from logging.config import fileConfig

from sqlalchemy import engine_from_config
from sqlalchemy import pool

from alembic import context

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Use the same Settings resolution as the application, but only to replace the
# repository's committed placeholder URL.  An explicit sqlalchemy.url supplied
# by an embedding caller (notably migration tests) is always authoritative:
# Settings also reads local dotenv files (backend/.env), so checking whether
# DATABASE_URL was "configured" cannot distinguish a developer's real dev
# database from the caller's intent and would silently migrate that dev
# database instead of the caller's target.
from app.config import settings  # noqa: E402

configured_url = config.get_main_option("sqlalchemy.url")
if configured_url.startswith("driver://"):
    # Alembic's ConfigParser treats percent characters as interpolation.
    config.set_main_option(
        "sqlalchemy.url",
        settings.database_url.replace("%", "%%"),
    )

# Import the complete model registry so autogenerate and fresh-database upgrades
# operate on the same metadata as the application.
from app.database import Base  # noqa: E402
from app.model_registry import import_all_models  # noqa: E402

import_all_models()

target_metadata = Base.metadata

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
