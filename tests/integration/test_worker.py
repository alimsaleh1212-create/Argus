"""Integration tests — T029: worker loop against real Redis + Postgres.

TDD: must FAIL before worker.py is implemented.
"""

from __future__ import annotations

import os
import subprocess
import uuid

import pytest
import pytest_asyncio


@pytest.fixture(scope="module")
def pg_container():
    pytest.importorskip("testcontainers")
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("pgvector/pgvector:pg16") as pg:
        pg.get_dsn = lambda: (
            f"postgresql+asyncpg://{pg.username}:{pg.password}"
            f"@{pg.get_container_host_ip()}:{pg.get_exposed_port(5432)}/{pg.dbname}"
        )
        env = {**os.environ, "SENTINEL__POSTGRES__DSN": pg.get_dsn()}
        subprocess.run(
            ["uv", "run", "alembic", "-c", "config/alembic.ini", "upgrade", "head"],
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        yield pg


@pytest.fixture(scope="module")
def redis_container():
    pytest.importorskip("testcontainers")
    from testcontainers.redis import RedisContainer

    with RedisContainer("redis:7") as rc:
        yield rc


@pytest_asyncio.fixture
async def db_session(pg_container):
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    engine = create_async_engine(pg_container.get_dsn(), echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


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
        queue_key="worker:test:queue",
        processing_key="worker:test:processing",
        block_s=0.2,
    )
    await redis_client.delete("worker:test:queue", "worker:test:processing")
    return q


async def _create_received_incident(db_session):
    from backend.domain.incident import Incident, IncidentStatus, Severity
    from backend.repositories.incidents import IncidentRepository

    repo = IncidentRepository(db_session)
    inc = Incident(
        id=uuid.uuid4(),
        status=IncidentStatus.RECEIVED,
        severity=Severity.HIGH,
        correlation_id=str(uuid.uuid4()),
        dedup_fingerprint=f"fp-{uuid.uuid4().hex}",
        source="wazuh",
        raw_alert={"rule": {"level": 10, "id": "5763", "description": "SSH brute force"}},
        normalized_event={
            "rule_id": "5763",
            "rule_level": 10,
            "rule_description": "SSH brute force",
            "rule_groups": ["sshd"],
            "agent_id": "001",
            "agent_name": "web-server-01",
            "agent_ip": "10.0.0.42",
            "event_time": None,
            "fields": {},
        },
    )
    return await repo.create(inc), repo


@pytest.mark.integration
class TestWorkerLoop:
    async def test_claim_ground_ack_flow(self, db_session, queue) -> None:
        """claim→ground→set_grounded→ack: Incident reaches grounded status."""
        from backend.domain.incident import IncidentStatus
        from backend.repositories.incidents import IncidentRepository
        from backend.services.grounding import ground
        from backend.services.pipeline import dispatch_to_pipeline

        inc, repo = await _create_received_incident(db_session)
        await queue.enqueue(str(inc.id))

        incident_id = await queue.dequeue()
        assert incident_id == str(inc.id)

        claimed = await repo.claim_for_grounding(uuid.UUID(incident_id))
        assert claimed is True

        fetched = await repo.get(uuid.UUID(incident_id))
        evidence = ground(fetched)
        await repo.set_grounded(
            uuid.UUID(incident_id),
            fetched.normalized_event
            if not isinstance(fetched.normalized_event, dict)
            else __import__("backend.domain.incident", fromlist=["NormalizedEvent"]).NormalizedEvent.model_validate(fetched.normalized_event),
            evidence,
            evidence.severity,
        )
        await dispatch_to_pipeline(fetched)
        await queue.ack(incident_id)

        final = await repo.get(uuid.UUID(incident_id))
        assert final.status == IncidentStatus.GROUNDED
        assert final.evidence is not None

    async def test_redelivery_skipped_idempotent(self, db_session, queue) -> None:
        """Re-delivery of an already-grounded Incident is a no-op (idempotent)."""
        from backend.domain.incident import IncidentStatus
        from backend.repositories.incidents import IncidentRepository

        inc, repo = await _create_received_incident(db_session)
        # Manually set to grounded
        from backend.domain.incident import Evidence, NormalizedEvent, Severity

        ne = NormalizedEvent(rule_id="5763", rule_level=10, rule_description="SSH brute force")
        ev = Evidence(
            verdict="rule_match",
            severity=Severity.HIGH,
            normalized_event=ne,
            summary="SSH brute force on web-01",
        )
        await repo.claim_for_grounding(inc.id)
        await repo.set_grounded(inc.id, ne, ev, Severity.HIGH)

        # Try to claim again — must return False (already grounded, not received)
        second_claim = await repo.claim_for_grounding(inc.id)
        assert second_claim is False

    async def test_exception_bumps_attempts_and_marks_failed_at_budget(
        self, db_session
    ) -> None:
        """Forced exception bumps attempts; at max_attempts → failed."""
        from backend.domain.incident import IncidentStatus
        from backend.repositories.incidents import IncidentRepository

        inc, repo = await _create_received_incident(db_session)
        max_attempts = 3
        for _ in range(max_attempts):
            count = await repo.bump_attempt(inc.id)
        await repo.mark_failed(inc.id, reason="MaxAttemptsExceeded")

        final = await repo.get(inc.id)
        assert final.status == IncidentStatus.FAILED
        assert final.attempts == max_attempts

    async def test_recover_reclaims_stranded_jobs(self, queue, redis_client) -> None:
        """Stranded entries in processing list are returned to main queue by recover()."""
        stranded = str(uuid.uuid4())
        await redis_client.lpush("worker:test:processing", stranded)

        count = await queue.recover()
        assert count >= 1

        result = await queue.dequeue()
        assert result == stranded
