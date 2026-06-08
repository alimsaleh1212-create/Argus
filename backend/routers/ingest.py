"""Wazuh alert intake router — POST /ingest/wazuh.

Thin webhook: auth guard → size guard → validate → intake service → 202.
"""

from __future__ import annotations

import hmac
import json

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.dependencies import get_cache, get_db_session, get_obs, get_queue
from backend.domain.incident import IngestResult, WazuhAlert
from backend.infra.logging import get_logger
from backend.services import intake

router = APIRouter(prefix="/ingest", tags=["ingest"])
logger = get_logger(__name__)


def _get_webhook_token(request: Request) -> str:
    """Resolve the webhook shared-secret from the in-memory Vault cache.

    Vault resolves secret/ingest at startup; this is a synchronous cache read.
    Returns the 'token' field from the KV data.
    """
    vault = request.app.state.container.vault_client
    raw = vault.get_secret("secret/ingest")
    data = json.loads(raw)
    return data.get("token", "")


def _check_auth(request: Request, expected_token: str) -> None:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing token")
    provided = auth[len("Bearer "):]
    if not hmac.compare_digest(provided.encode(), expected_token.encode()):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")


@router.post("/wazuh", status_code=202)
async def ingest_wazuh(
    request: Request,
    db_session: AsyncSession = Depends(get_db_session),  # noqa: B008
    obs=Depends(get_obs),  # noqa: B008
    queue=Depends(get_queue),  # noqa: B008
    cache=Depends(get_cache),  # noqa: B008
) -> JSONResponse:
    """Accept a Wazuh push-webhook alert.

    auth guard → size guard → validate → intake.accept() → 202 Accepted.
    """
    settings = request.app.state.settings

    # Auth guard
    expected_token = _get_webhook_token(request)
    _check_auth(request, expected_token)

    # Size guard
    body = await request.body()
    if len(body) > settings.ingest.max_alert_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Alert too large",
        )

    # Parse + validate
    try:
        raw_data = json.loads(body)
        alert = WazuhAlert.model_validate(raw_data)
    except (ValidationError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    # Run intake
    try:
        result: IngestResult = await intake.accept(
            session=db_session,
            queue=queue,
            cache=cache,
            redactor=obs.redactor,
            settings=settings,
            alert=alert,
        )
    except Exception as exc:
        logger.warning("ingest_failed", error=type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Intake unavailable",
        ) from exc

    http_status = 200 if result.deduplicated else 202
    return JSONResponse(content=result.model_dump(mode="json"), status_code=http_status)
