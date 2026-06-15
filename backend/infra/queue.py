"""RedisTaskQueue — reliable Redis-list queue (at-least-once delivery).

redis.asyncio is imported ONLY here and in cache.py (no-bypass rule).
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncGenerator
from typing import Any, Protocol, runtime_checkable

import redis.asyncio as aioredis
from redis.exceptions import TimeoutError as RedisTimeoutError

from backend.infra.logging import get_logger

logger = get_logger(__name__)


@runtime_checkable
class TaskQueue(Protocol):
    """Minimal enqueue/dequeue contract for alert dispatch."""

    async def enqueue(self, incident_id: str) -> str: ...
    async def dequeue(self) -> str | None: ...
    async def ack(self, incident_id: str) -> None: ...
    async def recover(self) -> int: ...


class RedisTaskQueue:
    """Reliable Redis-list queue (BLMOVE main→processing, LREM ack, drain on recover)."""

    def __init__(
        self,
        redis: aioredis.Redis,
        queue_key: str,
        processing_key: str,
        block_s: float = 5.0,
    ) -> None:
        self._redis = redis
        self._queue_key = queue_key
        self._processing_key = processing_key
        self._block_s = block_s

    async def enqueue(self, incident_id: str) -> str:
        await self._redis.lpush(self._queue_key, incident_id)
        return incident_id

    async def dequeue(self) -> str | None:
        try:
            result = await self._redis.blmove(
                self._queue_key,
                self._processing_key,
                timeout=self._block_s,
                src="RIGHT",
                dest="LEFT",
            )
        except RedisTimeoutError:
            # redis-py >= 8 raises on a blocking-pop timeout instead of
            # returning None. An idle queue is not an error — signal "nothing
            # available" so the worker loop polls again (preserves str | None).
            return None
        return result  # type: ignore[return-value]

    async def ack(self, incident_id: str) -> None:
        await self._redis.lrem(self._processing_key, 1, incident_id)

    async def recover(self) -> int:
        """Drain all stranded processing entries back to the main queue."""
        count = 0
        while True:
            item = await self._redis.rpoplpush(self._processing_key, self._queue_key)
            if item is None:
                break
            count += 1
        return count


class QueueProvider:
    """Lifespan singleton that builds the RedisTaskQueue (reuses the cache pool)."""

    name = "queue"

    @contextlib.asynccontextmanager
    async def build(self, settings: Any) -> AsyncGenerator[RedisTaskQueue, None]:
        # Reuse the cache pool already built by CacheProvider
        redis_client: aioredis.Redis = settings._container.cache  # type: ignore[attr-defined]
        queue_key = settings.redis.queue_key
        processing_key = settings.redis.processing_key
        block_s = settings.redis.dequeue_block_s
        logger.info("queue_ready", queue_key=queue_key)
        yield RedisTaskQueue(
            redis=redis_client,
            queue_key=queue_key,
            processing_key=processing_key,
            block_s=block_s,
        )
