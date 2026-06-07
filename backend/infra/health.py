"""Readiness probes for vault, postgres, and minio.

Each probe is redaction-safe: ``detail`` names the problem, never a secret
value. Per-dependency timeouts prevent a slow dep from blocking the others.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from backend.domain.health import DependencyStatus
from backend.infra.logging import get_logger

logger = get_logger(__name__)


async def check_vault(settings: Any) -> DependencyStatus:
    """Probe Vault by calling its /v1/sys/health endpoint."""
    timeout = getattr(settings.startup, "dependency_timeout_s", 5.0)
    addr = settings.vault.addr
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(f"{addr}/v1/sys/health")
        # Vault /v1/sys/health returns 200 (initialized+unsealed) or 503/429
        if resp.status_code in (200, 429, 472, 473):
            return DependencyStatus(name="vault", healthy=True)
        return DependencyStatus(
            name="vault",
            healthy=False,
            detail=f"Vault health returned HTTP {resp.status_code}",
        )
    except Exception as exc:
        return DependencyStatus(
            name="vault",
            healthy=False,
            detail=f"Vault unreachable: {type(exc).__name__}",
        )


async def check_postgres(settings: Any) -> DependencyStatus:
    """Probe Postgres by opening a short-lived async connection."""
    timeout = getattr(settings.startup, "dependency_timeout_s", 5.0)
    dsn = settings.postgres.dsn.get_secret_value()
    try:
        import asyncpg

        conn = await asyncio.wait_for(asyncpg.connect(dsn), timeout=timeout)
        await conn.close()
        return DependencyStatus(name="postgres", healthy=True)
    except TimeoutError:
        return DependencyStatus(
            name="postgres",
            healthy=False,
            detail="Postgres connection timed out",
        )
    except Exception as exc:
        return DependencyStatus(
            name="postgres",
            healthy=False,
            detail=f"Postgres unreachable: {type(exc).__name__}",
        )


async def check_minio(settings: Any) -> DependencyStatus:
    """Probe MinIO by issuing a HEAD to its health endpoint."""
    timeout = getattr(settings.startup, "dependency_timeout_s", 5.0)
    endpoint = settings.minio.endpoint_url
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(f"{endpoint}/minio/health/live")
        if resp.status_code == 200:
            return DependencyStatus(name="minio", healthy=True)
        return DependencyStatus(
            name="minio",
            healthy=False,
            detail=f"MinIO health returned HTTP {resp.status_code}",
        )
    except Exception as exc:
        return DependencyStatus(
            name="minio",
            healthy=False,
            detail=f"MinIO unreachable: {type(exc).__name__}",
        )
