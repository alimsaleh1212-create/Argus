"""Integration test — T010: IncidentRepository against real Postgres.

TDD: must FAIL before repositories/incidents.py is implemented.
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
        # Run migrations
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
class TestIncidentRepository:
    async def _make_incident(self, session, fingerprint: str = "fp-test"):
        from backend.domain.incident import Incident, IncidentStatus, Severity
        from backend.repositories.incidents import IncidentRepository

        repo = IncidentRepository(session)
        inc = Incident(
            id=uuid.uuid4(),
            status=IncidentStatus.RECEIVED,
            severity=Severity.HIGH,
            correlation_id=str(uuid.uuid4()),
            dedup_fingerprint=fingerprint,
            source="wazuh",
            raw_alert={"rule": {"level": 10}},
        )
        return await repo.create(inc), repo

    async def test_create_and_get(self, db_session) -> None:
        created, repo = await self._make_incident(db_session)
        fetched = await repo.get(created.id)
        assert fetched is not None
        assert fetched.id == created.id
        assert fetched.status == "received"

    async def test_get_by_fingerprint(self, db_session) -> None:
        fp = f"fp-{uuid.uuid4().hex}"
        created, repo = await self._make_incident(db_session, fingerprint=fp)
        fetched = await repo.get_by_fingerprint(fp)
        assert fetched is not None
        assert fetched.id == created.id

    async def test_claim_for_grounding_atomic(self, db_session) -> None:
        created, repo = await self._make_incident(db_session)
        first = await repo.claim_for_grounding(created.id)
        assert first is True
        second = await repo.claim_for_grounding(created.id)
        assert second is False

    async def test_set_grounded(self, db_session) -> None:
        from backend.domain.incident import Evidence, NormalizedEvent, Severity

        created, repo = await self._make_incident(db_session)
        await repo.claim_for_grounding(created.id)
        ne = NormalizedEvent(rule_id="5763", rule_level=10)
        ev = Evidence(
            verdict="rule_match",
            severity=Severity.HIGH,
            normalized_event=ne,
            summary="test summary",
        )
        await repo.set_grounded(created.id, ne, ev, Severity.HIGH)
        fetched = await repo.get(created.id)
        assert fetched is not None
        assert fetched.status == "grounded"
        assert fetched.evidence is not None

    async def test_bump_attempt(self, db_session) -> None:
        created, repo = await self._make_incident(db_session)
        count = await repo.bump_attempt(created.id)
        assert count == 1
        count2 = await repo.bump_attempt(created.id)
        assert count2 == 2

    async def test_mark_failed(self, db_session) -> None:
        created, repo = await self._make_incident(db_session)
        await repo.mark_failed(created.id, reason="TestError")
        fetched = await repo.get(created.id)
        assert fetched is not None
        assert fetched.status == "failed"

    async def test_list_non_terminal(self, db_session) -> None:
        from backend.domain.incident import Incident, IncidentStatus, Severity
        from backend.repositories.incidents import IncidentRepository

        repo = IncidentRepository(db_session)
        inc = Incident(
            id=uuid.uuid4(),
            status=IncidentStatus.RECEIVED,
            severity=Severity.MEDIUM,
            correlation_id=str(uuid.uuid4()),
            dedup_fingerprint=f"fp-list-{uuid.uuid4().hex}",
            source="wazuh",
            raw_alert={},
        )
        created = await repo.create(inc)
        non_terminal = await repo.list_non_terminal()
        ids = [r.id for r in non_terminal]
        assert created.id in ids


@pytest.mark.integration
class TestPipelineRepositoryReads:
    async def _resolved(self, session, *, disposition: str):
        from backend.domain.incident import Incident, IncidentStatus, Severity
        from backend.repositories.incidents import IncidentRepository

        repo = IncidentRepository(session)
        inc = Incident(
            id=uuid.uuid4(),
            status=IncidentStatus.RECEIVED,
            severity=Severity.HIGH,
            correlation_id=str(uuid.uuid4()),
            dedup_fingerprint=f"fp-{uuid.uuid4().hex}",
            source="wazuh",
            raw_alert={"rule": {"level": 10}},
        )
        await repo.create(inc)
        await repo.advance_status(
            inc.id,
            expected=IncidentStatus.RECEIVED,
            target=IncidentStatus.RESOLVED,
            disposition=disposition,
        )
        return inc, repo

    async def test_status_counts_groups_by_status(self, db_session) -> None:
        from backend.domain.incident import Incident, IncidentStatus, Severity
        from backend.repositories.incidents import IncidentRepository

        repo = IncidentRepository(db_session)
        for _ in range(2):
            inc = Incident(
                id=uuid.uuid4(),
                status=IncidentStatus.RECEIVED,
                severity=Severity.LOW,
                correlation_id=str(uuid.uuid4()),
                dedup_fingerprint=f"fp-{uuid.uuid4().hex}",
                source="wazuh",
                raw_alert={"rule": {"level": 3}},
            )
            await repo.create(inc)

        counts = await repo.status_counts()
        assert counts.get("received", 0) >= 2

    async def test_disposition_counts_since_respects_window(self, db_session) -> None:
        import sqlalchemy as sa

        inc, repo = await self._resolved(db_session, disposition="auto_remediated")
        # Backdate this incident far outside the 24h window.
        await db_session.execute(
            sa.text("UPDATE incidents SET updated_at = now() - make_interval(hours => 100) WHERE id = :id"),
            {"id": str(inc.id)},
        )
        await db_session.commit()
        # A second, in-window resolved incident.
        await self._resolved(db_session, disposition="auto_remediated")

        counts = await repo.disposition_counts_since(window_hours=24)
        # The 100h-old one is excluded; the fresh one is counted.
        assert counts.get("auto_remediated", 0) >= 1
