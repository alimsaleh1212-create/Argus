"""CorpusProvider — lifespan singleton exposing CorpusRetriever for #9 consumption.

The provider wraps CorpusRepository behind a session-per-request adapter so
callers interact with the CorpusRetriever Protocol without managing sessions.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncGenerator
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.domain.corpus import ReferenceHit, ReferenceQuery
from backend.infra.logging import get_logger
from backend.repositories.corpus import CorpusRepository

logger = get_logger(__name__)


class _CorpusRetrieverSingleton:
    """Session-per-request CorpusRetriever backed by the shared session factory."""

    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = factory

    async def search_reference(self, query: ReferenceQuery, *, k: int) -> list[ReferenceHit]:
        try:
            async with self._factory() as session:
                repo = CorpusRepository(session)
                return await repo.search_reference(query, k=k)
        except Exception as exc:
            logger.warning("corpus_retriever_error", error=str(exc))
            return []


class CorpusProvider:
    """Lifespan singleton that builds a session-backed CorpusRetriever."""

    name = "corpus"

    @contextlib.asynccontextmanager
    async def build(self, settings: Any) -> AsyncGenerator[_CorpusRetrieverSingleton, None]:
        from sqlalchemy.ext.asyncio import create_async_engine

        dsn = settings.postgres.dsn.get_secret_value()
        engine = create_async_engine(dsn, pool_pre_ping=True, echo=False)
        factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
            engine, expire_on_commit=False
        )
        try:
            logger.info("corpus_provider_ready")
            yield _CorpusRetrieverSingleton(factory)
        finally:
            await engine.dispose()
            logger.info("corpus_provider_closed")


def get_corpus_retriever() -> _CorpusRetrieverSingleton:
    """Return the corpus retriever from the app container (for FastAPI Depends)."""
    from fastapi import Request

    from backend.infra.container import AppContainer

    def _inner(request: Request) -> _CorpusRetrieverSingleton:
        container: AppContainer = request.app.state.container
        return container.corpus

    return _inner  # type: ignore[return-value]
