"""Alembic environment — async (asyncpg) online migrations.

Imports the model metadata so `alembic revision --autogenerate` sees every table.
As models are added per milestone (M2/M3+), import them in apm.db.models so they
register on Base.metadata.
"""

from __future__ import annotations

import asyncio

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from apm.db.base import Base

# Import side-effect: registers all ORM models on Base.metadata.
try:  # models package appears at M2; tolerate its absence in M0.
    import apm.db.models  # noqa: F401
except ModuleNotFoundError:
    pass

config = context.config
target_metadata = Base.metadata


def _run_sync_migrations(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def _run_async() -> None:
    url = config.get_main_option("sqlalchemy.url")
    engine = create_async_engine(url, pool_pre_ping=True)
    async with engine.connect() as connection:
        await connection.run_sync(_run_sync_migrations)
    await engine.dispose()


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    asyncio.run(_run_async())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
