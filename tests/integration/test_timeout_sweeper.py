"""Integration tests — T027: timeout sweeper expires past-deadline pending approvals (US2)."""

from __future__ import annotations

import os
import subprocess
import uuid
from datetime import UTC, datetime, timedelta

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
            env=env, capture_output=True, text=True, check=True,
        )
        yield pg


@pytest_asyncio.fixture
async def db_setup(pg_container):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    engine = create_async_engine(pg_container.get_dsn(), echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest.mark.integration
class TestTimeoutSweeper:
    async def test_list_pending_expired_returns_overdue(self, db_setup) -> None:
        """list_pending_expired returns approvals whose deadline has passed."""
        from backend.domain.incident import Incident, IncidentStatus, Severity
        from backend.domain.response import ActionType, RemediationAction, RiskClass
        from backend.repositories.approvals import ApprovalRepository
        from backend.repositories.incidents import IncidentRepository

        incident_id = uuid.uuid4()
        async with db_setup() as session:
            inc_repo = IncidentRepository(session)
            inc = Incident(
                id=incident_id,
                status=IncidentStatus.AWAITING_APPROVAL,
                severity=Severity.CRITICAL,
                correlation_id=str(incident_id),
                dedup_fingerprint=f"fp-sweep-{incident_id.hex}",
                source="wazuh",
                raw_alert={},
            )
            await inc_repo.create(inc)

        async with db_setup() as session:
            app_repo = ApprovalRepository(session)
            action = RemediationAction(
                type=ActionType.BLOCK_IP, target="1.2.3.4",
                risk=RiskClass.APPROVAL_REQUIRED,
                idempotency_key=f"{incident_id}:p1:block_ip:1.2.3.4",
            )
            # Set deadline in the past
            past_deadline = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=60)
            await app_repo.create_pending(
                incident_id=incident_id, plan_id="p1",
                pending_actions=[action.model_dump(mode="json")],
                rationale="block IP", deadline_at=past_deadline,
            )

        # Sweeper queries for now
        now = datetime.now(UTC).replace(tzinfo=None)
        async with db_setup() as session:
            app_repo = ApprovalRepository(session)
            expired = await app_repo.list_pending_expired(now)
            assert any(r.incident_id == incident_id for r in expired)

    async def test_expire_incident_transitions_to_escalated(self, db_setup) -> None:
        """expire_incident drives AWAITING_APPROVAL → ESCALATED (approval_expired)."""
        from backend.domain.incident import Incident, IncidentStatus, Severity
        from backend.infra.config import SupervisorSettings
        from backend.infra.tracing import build_tracer
        from backend.repositories.audit import AuditRepository
        from backend.repositories.incidents import IncidentRepository
        from backend.services.supervisor import Supervisor

        incident_id = uuid.uuid4()
        async with db_setup() as session:
            inc_repo = IncidentRepository(session)
            inc = Incident(
                id=incident_id,
                status=IncidentStatus.AWAITING_APPROVAL,
                severity=Severity.CRITICAL,
                correlation_id=str(incident_id),
                dedup_fingerprint=f"fp-expire-{incident_id.hex}",
                source="wazuh",
                raw_alert={},
            )
            await inc_repo.create(inc)

        async with db_setup() as session:
            inc_repo = IncidentRepository(session)
            audit_repo = AuditRepository(session)
            sup = Supervisor(stages={}, cfg=SupervisorSettings(), tracer=build_tracer(exporter=None))

            expired = await sup.expire_incident(incident_id, inc_repo, audit_repo=audit_repo)
            assert expired is True

        async with db_setup() as session:
            inc_repo = IncidentRepository(session)
            fetched = await inc_repo.get(incident_id)
            assert fetched.status == IncidentStatus.ESCALATED
            assert fetched.disposition == "approval_expired"

    async def test_expired_approval_writes_audit_row(self, db_setup) -> None:
        """expire_incident writes an audit row with actor=timeout and outcome=not_executed."""
        from backend.domain.incident import Incident, IncidentStatus, Severity
        from backend.infra.config import SupervisorSettings
        from backend.infra.tracing import build_tracer
        from backend.repositories.audit import AuditRepository
        from backend.repositories.incidents import IncidentRepository
        from backend.services.supervisor import Supervisor

        incident_id = uuid.uuid4()
        async with db_setup() as session:
            inc_repo = IncidentRepository(session)
            inc = Incident(
                id=incident_id,
                status=IncidentStatus.AWAITING_APPROVAL,
                severity=Severity.CRITICAL,
                correlation_id=str(incident_id),
                dedup_fingerprint=f"fp-audit-expire-{incident_id.hex}",
                source="wazuh",
                raw_alert={},
            )
            await inc_repo.create(inc)

        async with db_setup() as session:
            inc_repo = IncidentRepository(session)
            audit_repo = AuditRepository(session)
            sup = Supervisor(stages={}, cfg=SupervisorSettings(), tracer=build_tracer(exporter=None))
            await sup.expire_incident(incident_id, inc_repo, audit_repo=audit_repo)

        async with db_setup() as session:
            audit_repo = AuditRepository(session)
            rows = await audit_repo.list_for_incident(incident_id)
            timeout_rows = [r for r in rows if r.actor == "timeout" and r.outcome == "not_executed"]
            assert len(timeout_rows) >= 1
