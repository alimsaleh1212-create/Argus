"""Integration test — Task A2: IncidentRepository.acknowledge against real Postgres.

TDD: must FAIL before repositories/incidents.py implements `acknowledge`.
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
        # Run migrations (applies 0007 — acknowledged_at/acknowledged_by columns)
        env = {**os.environ, "ARGUS__POSTGRES__DSN": pg.get_dsn()}
        subprocess.run(
            ["uv", "run", "alembic", "-c", "config/alembic.ini", "upgrade", "head"],
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        yield pg


@pytest_asyncio.fixture
async def db_session(pg_container):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(pg_container.get_dsn(), echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest.mark.integration
class TestIncidentAcknowledge:
    async def _make_escalated_incident(self, session, fingerprint: str | None = None):
        from backend.domain.incident import Incident, IncidentStatus, Severity
        from backend.repositories.incidents import IncidentRepository

        repo = IncidentRepository(session)
        inc = Incident(
            id=uuid.uuid4(),
            status=IncidentStatus.RECEIVED,
            severity=Severity.HIGH,
            correlation_id=str(uuid.uuid4()),
            dedup_fingerprint=fingerprint or f"fp-ack-{uuid.uuid4().hex}",
            source="wazuh",
            raw_alert={"rule": {"level": 12}},
        )
        created = await repo.create(inc)
        ok = await repo.advance_status(
            created.id,
            expected=IncidentStatus.RECEIVED,
            target=IncidentStatus.ESCALATED,
            disposition="escalated_triage",
        )
        assert ok is True
        return created, repo

    async def test_acknowledge_sets_columns_only_when_escalated(self, db_session) -> None:
        created, repo = await self._make_escalated_incident(db_session)

        ok = await repo.acknowledge(created.id, actor="alice")
        assert ok is True

        again = await repo.acknowledge(created.id, actor="bob")
        assert again is False  # idempotent guard — already acknowledged

        reloaded = await repo.get(created.id)
        assert reloaded is not None
        assert reloaded.acknowledged_by == "alice"
        assert reloaded.acknowledged_at is not None

    async def test_acknowledge_no_op_when_not_escalated(self, db_session) -> None:
        from backend.domain.incident import Incident, IncidentStatus, Severity
        from backend.repositories.incidents import IncidentRepository

        repo = IncidentRepository(db_session)
        inc = Incident(
            id=uuid.uuid4(),
            status=IncidentStatus.RECEIVED,
            severity=Severity.LOW,
            correlation_id=str(uuid.uuid4()),
            dedup_fingerprint=f"fp-ack-noop-{uuid.uuid4().hex}",
            source="wazuh",
            raw_alert={"rule": {"level": 3}},
        )
        created = await repo.create(inc)

        ok = await repo.acknowledge(created.id, actor="alice")
        assert ok is False

        reloaded = await repo.get(created.id)
        assert reloaded is not None
        assert reloaded.acknowledged_by is None
        assert reloaded.acknowledged_at is None
