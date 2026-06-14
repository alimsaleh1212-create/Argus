"""Integration test — T014: supervisor advance_status against real Postgres.

Tests guarded transitions, disposition persistence, and resume from in-flight state.
Uses testcontainers (skips gracefully if not installed).
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


async def _create_incident(session, status="received", severity="medium"):
    from backend.domain.incident import Incident, IncidentStatus, Severity
    from backend.repositories.incidents import IncidentRepository

    repo = IncidentRepository(session)
    inc = Incident(
        id=uuid.uuid4(),
        status=IncidentStatus(status),
        severity=Severity(severity),
        correlation_id=str(uuid.uuid4()),
        dedup_fingerprint=f"fp-{uuid.uuid4().hex}",
        source="wazuh",
        raw_alert={"rule": {"level": 8}},
    )
    return await repo.create(inc), repo


@pytest.mark.integration
class TestSupervisorPostgres:
    async def test_advance_status_guarded_transition(self, db_session) -> None:
        """advance_status returns True only when status matches the expected value."""
        from backend.domain.incident import IncidentStatus

        created, repo = await _create_incident(db_session)
        # Claim for grounding first
        await repo.claim_for_grounding(created.id)

        # Now try to advance grounding → grounded
        result = await repo.advance_status(
            created.id,
            expected=IncidentStatus.GROUNDING,
            target=IncidentStatus.GROUNDED,
        )
        assert result is True

        fetched = await repo.get(created.id)
        assert fetched.status == IncidentStatus.GROUNDED

    async def test_advance_status_guard_rejects_wrong_expected(self, db_session) -> None:
        """advance_status returns False when the guard doesn't match (idempotency)."""
        from backend.domain.incident import IncidentStatus

        created, repo = await _create_incident(db_session)
        # Incident is RECEIVED; try to advance as if it were GROUNDED → should fail
        result = await repo.advance_status(
            created.id,
            expected=IncidentStatus.GROUNDED,
            target=IncidentStatus.TRIAGING,
        )
        assert result is False

        # Status unchanged
        fetched = await repo.get(created.id)
        assert fetched.status == IncidentStatus.RECEIVED

    async def test_disposition_persisted_on_terminal_transition(self, db_session) -> None:
        """Disposition is written to the DB on terminal transitions."""
        from backend.domain.incident import IncidentStatus

        created, repo = await _create_incident(db_session)
        await repo.claim_for_grounding(created.id)

        # Simulate fast-path: grounding → grounded → resolved with disposition
        await repo.advance_status(
            created.id,
            expected=IncidentStatus.GROUNDING,
            target=IncidentStatus.GROUNDED,
        )
        await repo.advance_status(
            created.id,
            expected=IncidentStatus.GROUNDED,
            target=IncidentStatus.RESOLVED,
            disposition="auto_resolved_noise",
        )

        fetched = await repo.get(created.id)
        assert fetched.status == IncidentStatus.RESOLVED
        assert fetched.disposition == "auto_resolved_noise"

    async def test_resume_from_in_flight_state(self, db_session) -> None:
        """An incident already in-flight resumes: advance_status works from that state."""
        from backend.domain.incident import IncidentStatus

        created, repo = await _create_incident(db_session)
        await repo.claim_for_grounding(created.id)

        # Place the incident in TRIAGING (simulating a crash between transitions)
        await repo.advance_status(
            created.id, expected=IncidentStatus.GROUNDING, target=IncidentStatus.GROUNDED
        )
        await repo.advance_status(
            created.id, expected=IncidentStatus.GROUNDED, target=IncidentStatus.TRIAGING
        )

        fetched = await repo.get(created.id)
        assert fetched.status == IncidentStatus.TRIAGING

        # Resume: advance from TRIAGING to ENRICHING
        result = await repo.advance_status(
            created.id,
            expected=IncidentStatus.TRIAGING,
            target=IncidentStatus.ENRICHING,
        )
        assert result is True

        fetched = await repo.get(created.id)
        assert fetched.status == IncidentStatus.ENRICHING

    async def test_terminal_state_is_guarded_against_double_processing(self, db_session) -> None:
        """Attempting to advance a terminal incident returns False (idempotent re-delivery)."""
        from backend.domain.incident import IncidentStatus

        created, repo = await _create_incident(db_session)
        await repo.claim_for_grounding(created.id)
        await repo.advance_status(
            created.id, expected=IncidentStatus.GROUNDING, target=IncidentStatus.GROUNDED
        )
        await repo.advance_status(
            created.id,
            expected=IncidentStatus.GROUNDED,
            target=IncidentStatus.RESOLVED,
            disposition="auto_resolved_noise",
        )

        # Try to advance again — should fail because status is RESOLVED, not GROUNDED
        result = await repo.advance_status(
            created.id,
            expected=IncidentStatus.GROUNDED,
            target=IncidentStatus.TRIAGING,
        )
        assert result is False

        # Status unchanged
        fetched = await repo.get(created.id)
        assert fetched.status == IncidentStatus.RESOLVED
