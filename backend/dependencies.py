"""FastAPI Depends() providers — read singletons from app.state.container.

Also includes dashboard-specific providers: get_auth_service, get_current_operator,
get_trace_repo (added by #12).

Consumers obtain resources ONLY through these functions, never module globals
(FR-012). In tests, ``app.dependency_overrides[get_obs] = fake``
substitutes a double without touching consumer code (FR-020).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

from fastapi import Depends, HTTPException, Query, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from backend.domain.dashboard import OperatorSession
from backend.infra.auth import AuthError
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


async def get_cache(request: Request):
    """Return the Redis client singleton."""
    return request.app.state.container.cache


async def get_queue(request: Request):
    """Return the RedisTaskQueue singleton."""
    return request.app.state.container.queue


async def get_supervisor(request: Request):
    """Return the Supervisor singleton (for #12 dashboard and tests)."""
    return request.app.state.container.supervisor


async def get_incident_repo(request: Request):
    """Return an IncidentRepository bound to the current request's DB session."""
    from backend.repositories.incidents import IncidentRepository

    db: Any = request.app.state.container.db_engine
    async with db.session_factory() as session:
        yield IncidentRepository(session)


async def get_approval_repo(request: Request):
    """Return an ApprovalRepository bound to the current request's DB session."""
    from backend.repositories.approvals import ApprovalRepository

    db: Any = request.app.state.container.db_engine
    async with db.session_factory() as session:
        yield ApprovalRepository(session)


async def get_audit_repo(request: Request):
    """Return an AuditRepository bound to the current request's DB session."""
    from backend.repositories.audit import AuditRepository

    db: Any = request.app.state.container.db_engine
    async with db.session_factory() as session:
        yield AuditRepository(session)


# ── Dashboard providers (#12) ──────────────────────────────────────────────────

_bearer_scheme = HTTPBearer(auto_error=False)


async def get_auth_service(request: Request):
    """Return the AuthService singleton (lazy-init from Vault creds)."""
    return request.app.state.container.auth_service


async def get_current_operator(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    token_param: str | None = Query(default=None, alias="token"),
    auth_svc=Depends(get_auth_service),
):
    """Validate a bearer token (REST), or a ``?token=`` query param on the SSE
    stream route only (EventSource cannot set Authorization headers).

    Returns OperatorSession on success; raises HTTP 401 on any failure.
    """
    raw_token: str | None = None
    if credentials is not None:
        raw_token = credentials.credentials
    elif token_param is not None and request.url.path.endswith("/stream"):
        # Query-param tokens land in proxy/access logs, so only the SSE route —
        # which has no header alternative — is allowed to use one.
        raw_token = token_param

    if not raw_token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    try:
        payload = auth_svc.verify_token(raw_token)
    except AuthError:
        raise HTTPException(status_code=401, detail="Not authenticated") from None

    return OperatorSession(
        subject=payload["sub"],
        role=payload["role"],
        expires_at=datetime.fromtimestamp(payload["exp"], tz=UTC),
    )


async def get_trace_repo(request: Request):
    """Return the TraceRepository singleton."""
    return request.app.state.container.trace_repo
