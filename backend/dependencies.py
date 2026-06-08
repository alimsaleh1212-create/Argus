"""FastAPI Depends() providers — read singletons from app.state.container.

Consumers obtain resources ONLY through these functions, never module globals
(FR-012). In tests, ``app.dependency_overrides[get_obs] = fake``
substitutes a double without touching consumer code (FR-020).
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from backend.infra.blob import BlobClient
from backend.infra.db import DbEngine
from backend.infra.vault import VaultClient


async def get_db_session(request: Request) -> AsyncIterator[AsyncSession]:
    db: DbEngine = request.app.state.container.db_engine
    async with db.session_factory() as session:
        yield session


async def get_blob_client(request: Request) -> BlobClient:
    return request.app.state.container.blob_client


async def get_vault_client(request: Request) -> VaultClient:
    return request.app.state.container.vault_client


async def get_obs(request: Request):
    """Return the unified Observability bundle (FR-018, FR-020)."""

    return request.app.state.container.observability


async def get_redactor_dep(request: Request):
    """Return the Redactor for injection into endpoints that redact directly."""
    obs = await get_obs(request)
    return obs.redactor


async def get_tracer(request: Request):
    """Return the Tracer for injection into endpoints that open spans directly."""
    obs = await get_obs(request)
    return obs.tracer


async def get_llm(request: Request):
    """Return the process-singleton LLM adapter (FR-014).

    Consumers depend on this via FastAPI Depends(get_llm); they never construct
    a vendor client and never import a vendor SDK (FR-001, SC-001).
    Substitutable in tests: app.dependency_overrides[get_llm] = lambda: FakeLlm().
    """
    return request.app.state.container.llm
