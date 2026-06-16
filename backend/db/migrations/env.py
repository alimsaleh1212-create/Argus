"""Alembic async migration environment.

Reads the DSN from ARGUS__POSTGRES__DSN (via Settings) so the same config
source is used in all environments. Uses asyncpg driver via SQLAlchemy async.
"""

from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# Alembic Config object from alembic.ini
config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Override the sqlalchemy.url with the value from environment (FR-015).
dsn = os.environ.get("ARGUS__POSTGRES__DSN", "")
if not dsn:
    # Fall back to loading from Settings (resolves .env as well)
    from backend.infra.config import load_settings

    s = load_settings()
    dsn = s.postgres.dsn.get_secret_value()

config.set_main_option("sqlalchemy.url", dsn)

# target_metadata can be set to a SQLAlchemy MetaData for autogenerate support.
# Domain models add their metadata here when they define tables.
target_metadata = None


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (no live connection)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations against a live async engine."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
