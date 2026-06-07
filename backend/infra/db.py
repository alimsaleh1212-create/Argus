"""Async SQLAlchemy engine + session factory and DbEngineProvider."""

from __future__ import annotations

import contextlib
from collections.abc import AsyncGenerator
from typing import Any

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from backend.infra.logging import get_logger

logger = get_logger(__name__)


class DbEngine:
    """Holds the shared async engine and session factory."""

    def __init__(
        self, engine: AsyncEngine, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        self.engine = engine
        self.session_factory = session_factory

    async def dispose(self) -> None:
        await self.engine.dispose()


class DbEngineProvider:
    name = "db_engine"

    @contextlib.asynccontextmanager
    async def build(self, settings: Any) -> AsyncGenerator[DbEngine, None]:
        dsn = settings.postgres.dsn.get_secret_value()
        engine = create_async_engine(dsn, pool_pre_ping=True, echo=False)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)

        db = DbEngine(engine=engine, session_factory=session_factory)
        logger.info("db_engine_ready")

        try:
            yield db
        finally:
            await db.dispose()
            logger.info("db_engine_disposed")


def register_db_provider() -> None:
    from backend.infra.container import register_provider

    register_provider(DbEngineProvider())
