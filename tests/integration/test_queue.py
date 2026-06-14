"""Integration test — T012: RedisTaskQueue against real Redis.

TDD: must FAIL before infra/queue.py is implemented.
"""

from __future__ import annotations

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


@pytest_asyncio.fixture
async def queue(redis_client):
    from backend.infra.queue import RedisTaskQueue

    q = RedisTaskQueue(
        redis=redis_client,
        queue_key="test:queue",
        processing_key="test:processing",
    )
    # clean up before each test
    await redis_client.delete("test:queue", "test:processing")
    return q


@pytest.mark.integration
class TestRedisTaskQueue:
    async def test_enqueue_dequeue(self, queue) -> None:
        incident_id = "test-incident-001"
        await queue.enqueue(incident_id)
        result = await queue.dequeue()
        assert result == incident_id

    async def test_ack_removes_from_processing(self, queue, redis_client) -> None:
        incident_id = "test-incident-002"
        await queue.enqueue(incident_id)
        dequeued = await queue.dequeue()
        assert dequeued == incident_id

        processing = await redis_client.lrange("test:processing", 0, -1)
        assert incident_id in processing

        await queue.ack(incident_id)
        processing_after = await redis_client.lrange("test:processing", 0, -1)
        assert incident_id not in processing_after

    async def test_recover_drains_processing_to_main(self, queue, redis_client) -> None:
        # Simulate a stranded item in processing (no ack)
        stranded_id = "test-incident-stranded"
        await redis_client.lpush("test:processing", stranded_id)

        count = await queue.recover()
        assert count == 1

        # Should now be in the main queue
        result = await queue.dequeue()
        assert result == stranded_id

    async def test_dequeue_returns_none_when_empty(self, queue) -> None:
        # With a block timeout of 0.1s, an empty queue should return None quickly
        import redis.asyncio as aioredis

        from backend.infra.queue import RedisTaskQueue

        host = queue._redis.connection_pool.connection_kwargs.get("host", "localhost")
        port = queue._redis.connection_pool.connection_kwargs.get("port", 6379)
        client = aioredis.Redis(host=host, port=port, decode_responses=True)
        fast_q = RedisTaskQueue(
            redis=client,
            queue_key="test:queue:empty",
            processing_key="test:processing:empty",
            block_s=0.1,
        )
        await client.delete("test:queue:empty", "test:processing:empty")
        result = await fast_q.dequeue()
        assert result is None
        await client.aclose()
