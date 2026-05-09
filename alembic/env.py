"""Alembic environment configuration.

Enables ``render_as_batch`` when the configured database is SQLite so
migrations that ``ALTER TABLE`` (add columns, add FKs, change types) keep
working. On Postgres / MySQL the flag is a no-op and regular DDL is used.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool
from sqlalchemy.engine import Connection

from server.database import Base
from server.models import (  # noqa: F401  (registered with metadata on import)
    Approval,
    AuditLog,
    Draft,
    FBAccount,
    Post,
    Target,
    User,
)

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _render_as_batch(connection: Connection | None) -> bool:
    """Return True when the backend benefits from Alembic batch mode.

    SQLite cannot execute most ``ALTER TABLE`` operations in-place, so
    Alembic emulates them by copying to a temp table. We want that
    behavior for SQLite and leave other backends to use native DDL.
    """
    if connection is None:
        url = config.get_main_option("sqlalchemy.url", "")
        return url.startswith("sqlite")
    return connection.dialect.name == "sqlite"


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=_render_as_batch(None),
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=_render_as_batch(connection),
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
