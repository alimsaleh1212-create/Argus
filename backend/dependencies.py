"""FastAPI Depends() providers — read singletons from app.state.container.

Consumers obtain resources ONLY through these functions, never module globals
(FR-012). In tests, ``app.dependency_overrides[get_db_session] = fake``
substitutes a double without touching consumer code (FR-013).
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
