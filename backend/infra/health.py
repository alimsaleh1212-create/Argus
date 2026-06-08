"""Readiness probes for vault, postgres, minio, and llm.

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


async def check_llm(settings: Any) -> DependencyStatus:
    """Probe LLM providers — healthy iff ≥1 configured provider is reachable (LD5 / FR-019).

    Uses each driver's ping() method (cheap probe). Secret-free detail names
    which providers are down. Never crashes boot — only config/cred errors do.
    """
    from backend.infra.config import LlmSettings
    from backend.infra.llm_drivers import GeminiDriver, OllamaDriver

    timeout = getattr(settings.startup, "dependency_timeout_s", 5.0)
    llm_settings: LlmSettings = settings.llm

    # Build temporary probe drivers (no Vault — just connectivity checks)
    # Gemini ping: uses a placeholder key; auth errors are treated as unreachable
    try:
        gemini_driver = GeminiDriver(llm_settings, api_key="probe-placeholder")
    except Exception:
        gemini_driver = None

    try:
        ollama_driver = OllamaDriver(llm_settings)
    except Exception:
        ollama_driver = None

    results: list[tuple[str, bool]] = []

    async def _ping(name: str, driver: Any) -> None:
        if driver is None:
            results.append((name, False))
            return
        try:
            ok = await asyncio.wait_for(driver.ping(), timeout=timeout)
            results.append((name, bool(ok)))
        except Exception:
            results.append((name, False))

    await asyncio.gather(
        _ping("gemini", gemini_driver),
        _ping("ollama", ollama_driver),
    )

    any_reachable = any(ok for _, ok in results)
    down = [name for name, ok in results if not ok]

    if any_reachable:
        return DependencyStatus(name="llm", healthy=True)

    detail = f"No LLM provider reachable. Down: {down}"
    return DependencyStatus(name="llm", healthy=False, detail=detail)
