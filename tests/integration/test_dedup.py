"""Integration tests — T036: dedup helpers against real Redis.

TDD: must FAIL before infra/cache.py claim_fingerprint/lookup_fingerprint exist.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio


@pytest.fixture(scope="module")
def redis_container():
    pytest.importorskip("testcontainers")
    from testcontainers.redis import RedisContainer

    with RedisContainer("redis:7") as rc:
        yield rc


@pytest_asyncio.fixture
async def redis_client(redis_container):
    import redis.asyncio as aioredis

    host = redis_container.get_container_host_ip()
    port = redis_container.get_exposed_port(6379)
    client = aioredis.Redis(host=host, port=int(port), decode_responses=True)
    yield client
    await client.aclose()


@pytest.mark.integration
class TestDedupHelpers:
    async def test_claim_returns_true_first_then_false_within_ttl(self, redis_client) -> None:
        from backend.infra.cache import claim_fingerprint

        fp = f"test-fp-{uuid.uuid4().hex}"
        incident_id = str(uuid.uuid4())

        first = await claim_fingerprint(redis_client, fp, incident_id, "dedup:", 300)
        assert first is True

        second = await claim_fingerprint(redis_client, fp, incident_id, "dedup:", 300)
        assert second is False

    async def test_lookup_returns_incident_id_after_claim(self, redis_client) -> None:
        from backend.infra.cache import claim_fingerprint, lookup_fingerprint

        fp = f"test-fp-{uuid.uuid4().hex}"
        incident_id = str(uuid.uuid4())

        await claim_fingerprint(redis_client, fp, incident_id, "dedup:", 300)
        result = await lookup_fingerprint(redis_client, fp, "dedup:")
        assert result == incident_id

    async def test_key_expires_after_window(self, redis_client) -> None:
        """Key expires after dedup_window_s (tested with TTL=1s)."""
        import asyncio

        from backend.infra.cache import claim_fingerprint, lookup_fingerprint

        fp = f"test-fp-expire-{uuid.uuid4().hex}"
        incident_id = str(uuid.uuid4())

        await claim_fingerprint(redis_client, fp, incident_id, "dedup:", 1)
        await asyncio.sleep(1.1)

        result = await lookup_fingerprint(redis_client, fp, "dedup:")
        assert result is None
