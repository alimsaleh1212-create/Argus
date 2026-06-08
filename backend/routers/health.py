"""Health and readiness API router.

GET /health  — liveness (always 200, zero dep I/O)
GET /ready   — readiness (200 all-healthy / 503 any-unhealthy)
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse

from backend.domain.health import DependencyStatus, Liveness, ReadinessReport
from backend.infra.health import check_llm, check_minio, check_postgres, check_redis, check_vault
from backend.infra.logging import get_logger

logger = get_logger(__name__)
router = APIRouter(tags=["health"])


@router.get("/health", response_model=Liveness)
async def health() -> Liveness:
    """Liveness probe — process is up. Never touches external services."""
    return Liveness()


async def run_readiness_probes(settings: Any) -> list[DependencyStatus]:
    """Run all dependency probes concurrently and return their statuses."""
    results = await asyncio.gather(
        check_vault(settings),
        check_postgres(settings),
        check_minio(settings),
        check_llm(settings),
        check_redis(settings),
    )
    return list(results)


@router.get(
    "/ready",
    response_model=ReadinessReport,
    responses={503: {"model": ReadinessReport}},
)
async def readiness(request: Request) -> JSONResponse:
    """Readiness probe — 200 when all deps healthy, 503 otherwise."""
    settings = request.app.state.settings
    dep_statuses = await run_readiness_probes(settings)
    ready = all(d.healthy for d in dep_statuses)
    report = ReadinessReport(ready=ready, dependencies=dep_statuses)
    http_status = status.HTTP_200_OK if ready else status.HTTP_503_SERVICE_UNAVAILABLE
    return JSONResponse(content=report.model_dump(), status_code=http_status)
