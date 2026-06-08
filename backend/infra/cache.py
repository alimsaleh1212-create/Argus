"""CacheProvider — Redis connection-pool singleton + dedup helpers.

redis.asyncio is imported ONLY here and in queue.py (no-bypass rule).
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncGenerator
from typing import Any

import redis.asyncio as aioredis

from backend.infra.logging import get_logger

logger = get_logger(__name__)


class CacheProvider:
    """Lifespan singleton that builds one redis.asyncio connection pool."""

    name = "cache"

    @contextlib.asynccontextmanager
    async def build(self, settings: Any) -> AsyncGenerator[aioredis.Redis, None]:
        redis_url = settings.redis.url
        logger.info("cache_connecting", url=redis_url)
        client: aioredis.Redis = aioredis.from_url(
            redis_url,
            decode_responses=True,
        )
        try:
            await client.ping()
            logger.info("cache_ready")
            yield client
        finally:
            await client.aclose()
            logger.info("cache_closed")


async def claim_fingerprint(
    redis: aioredis.Redis,
    fingerprint: str,
    incident_id: str,
    prefix: str,
    window_s: int,
) -> bool:
    """SET dedup:<fp> <incident_id> NX EX window_s.

    Returns True if the key was set (first sighting), False on a duplicate.
    """
    key = f"{prefix}{fingerprint}"
    result = await redis.set(key, incident_id, nx=True, ex=window_s)
    return result is True


async def lookup_fingerprint(
    redis: aioredis.Redis,
    fingerprint: str,
    prefix: str,
) -> str | None:
    """GET dedup:<fp> — returns the incident_id or None if expired/absent."""
    key = f"{prefix}{fingerprint}"
    return await redis.get(key)
