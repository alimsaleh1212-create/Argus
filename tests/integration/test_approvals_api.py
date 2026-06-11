"""Integration tests — T026: approvals API approve + reject against real Postgres session (US2)."""

from __future__ import annotations

import json
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


async def _create_parked_incident(factory):
    """Create an incident in AWAITING_APPROVAL state with a pending approval."""
    import uuid
    from datetime import UTC, datetime, timedelta

    from backend.domain.incident import Incident, IncidentStatus, Severity
    from backend.domain.response import ActionType, RemediationAction, RiskClass
    from backend.repositories.approvals import ApprovalRepository
    from backend.repositories.incidents import IncidentRepository

    incident_id = uuid.uuid4()
    async with factory() as session:
        inc_repo = IncidentRepository(session)
        inc = Incident(
            id=incident_id,
            status=IncidentStatus.AWAITING_APPROVAL,
            severity=Severity.CRITICAL,
            correlation_id=str(incident_id),
            dedup_fingerprint=f"fp-{incident_id.hex}",
            source="wazuh",
            raw_alert={},
        )
        await inc_repo.create(inc)

    async with factory() as session:
        app_repo = ApprovalRepository(session)
        action = RemediationAction(
            type=ActionType.ISOLATE_HOST,
            target="srv-01",
            risk=RiskClass.APPROVAL_REQUIRED,
            idempotency_key=f"{incident_id}:plan1:isolate_host:srv-01",
        )
        deadline = datetime.now(UTC).replace(tzinfo=None) + timedelta(seconds=1800)
        approval_id = await app_repo.create_pending(
            incident_id=incident_id,
            plan_id="plan1",
            pending_actions=[action.model_dump(mode="json")],
            rationale="critical host compromise",
            deadline_at=deadline,
        )

    return incident_id, approval_id


@pytest.mark.integration
class TestApprovalsRepositoryApproveReject:
    async def test_approve_transitions_record(self, db_setup) -> None:
        """resolve(approve) transitions approval from pending → approved."""
        from backend.domain.response import ApprovalStatus
        from backend.repositories.approvals import ApprovalRepository

        incident_id, approval_id = await _create_parked_incident(db_setup)

        async with db_setup() as session:
            repo = ApprovalRepository(session)
            resolved = await repo.resolve(approval_id, to=ApprovalStatus.APPROVED, decided_by="admin")
            assert resolved is True

        async with db_setup() as session:
            repo = ApprovalRepository(session)
            record = await repo.get(approval_id)
            assert record is not None
            assert record.status == "approved"
            assert record.decided_by == "admin"

    async def test_reject_transitions_record(self, db_setup) -> None:
        """resolve(reject) transitions approval from pending → rejected."""
        from backend.domain.response import ApprovalStatus
        from backend.repositories.approvals import ApprovalRepository

        incident_id, approval_id = await _create_parked_incident(db_setup)

        async with db_setup() as session:
            repo = ApprovalRepository(session)
            resolved = await repo.resolve(approval_id, to=ApprovalStatus.REJECTED, decided_by="admin")
            assert resolved is True

        async with db_setup() as session:
            repo = ApprovalRepository(session)
            record = await repo.get(approval_id)
            assert record.status == "rejected"

    async def test_second_decision_is_noop(self, db_setup) -> None:
        """First decision wins — duplicate approve/reject is a guarded no-op."""
        from backend.domain.response import ApprovalStatus
        from backend.repositories.approvals import ApprovalRepository

        incident_id, approval_id = await _create_parked_incident(db_setup)

        async with db_setup() as session:
            repo = ApprovalRepository(session)
            first = await repo.resolve(approval_id, to=ApprovalStatus.APPROVED, decided_by="admin")
            assert first is True

        async with db_setup() as session:
            repo = ApprovalRepository(session)
            second = await repo.resolve(approval_id, to=ApprovalStatus.REJECTED, decided_by="admin")
            assert second is False  # guard blocked the second decision

    async def test_get_approved_pending_for(self, db_setup) -> None:
        """get_approved_pending_for returns the approved record for an incident."""
        from backend.domain.response import ApprovalStatus
        from backend.repositories.approvals import ApprovalRepository

        incident_id, approval_id = await _create_parked_incident(db_setup)

        async with db_setup() as session:
            repo = ApprovalRepository(session)
            # Before approval: should be None (pending is not approved)
            before = await repo.get_approved_pending_for(incident_id)
            assert before is None

        async with db_setup() as session:
            repo = ApprovalRepository(session)
            await repo.resolve(approval_id, to=ApprovalStatus.APPROVED, decided_by="admin")

        async with db_setup() as session:
            repo = ApprovalRepository(session)
            after = await repo.get_approved_pending_for(incident_id)
            assert after is not None
            assert after.status == "approved"

    async def test_audit_log_write_and_read(self, db_setup) -> None:
        """AuditRepository.append writes rows; list_for_incident reads them."""
        from backend.domain.incident import Incident, IncidentStatus, Severity
        from backend.repositories.audit import AuditRepository
        from backend.repositories.incidents import IncidentRepository

        incident_id = uuid.uuid4()
        async with db_setup() as session:
            inc_repo = IncidentRepository(session)
            await inc_repo.create(Incident(
                id=incident_id, status=IncidentStatus.RESPONDING, severity=Severity.MEDIUM,
                correlation_id=str(incident_id), dedup_fingerprint=f"fp-audit-{incident_id.hex}",
                source="wazuh", raw_alert={},
            ))

        async with db_setup() as session:
            audit = AuditRepository(session)
            result = await audit.append(
                incident_id=incident_id,
                actor="response_agent",
                action="add_to_watchlist",
                target="10.0.0.1",
                outcome="applied",
                idempotency_key=f"{incident_id}:plan1:add_to_watchlist:10.0.0.1",
            )
            assert result is True

        async with db_setup() as session:
            audit = AuditRepository(session)
            rows = await audit.list_for_incident(incident_id)
            assert len(rows) == 1
            assert rows[0].actor == "response_agent"
            assert rows[0].outcome == "applied"

    async def test_audit_idempotency_key_blocks_duplicate(self, db_setup) -> None:
        """Duplicate applied row with same idempotency_key returns False."""
        from backend.domain.incident import Incident, IncidentStatus, Severity
        from backend.repositories.audit import AuditRepository
        from backend.repositories.incidents import IncidentRepository

        incident_id = uuid.uuid4()
        key = f"{incident_id}:plan1:add_to_watchlist:host"

        async with db_setup() as session:
            inc_repo = IncidentRepository(session)
            await inc_repo.create(Incident(
                id=incident_id, status=IncidentStatus.RESPONDING, severity=Severity.MEDIUM,
                correlation_id=str(incident_id), dedup_fingerprint=f"fp-idem-{incident_id.hex}",
                source="wazuh", raw_alert={},
            ))

        async with db_setup() as session:
            audit = AuditRepository(session)
            first = await audit.append(
                incident_id=incident_id, actor="agent", action="add_to_watchlist",
                target="host", outcome="applied", idempotency_key=key,
            )
            assert first is True

        async with db_setup() as session:
            audit = AuditRepository(session)
            second = await audit.append(
                incident_id=incident_id, actor="agent", action="add_to_watchlist",
                target="host", outcome="applied", idempotency_key=key,
            )
            assert second is False  # conflict blocked

    async def test_is_applied_check(self, db_setup) -> None:
        """is_applied returns True after an applied row is written."""
        from backend.domain.incident import Incident, IncidentStatus, Severity
        from backend.repositories.audit import AuditRepository
        from backend.repositories.incidents import IncidentRepository

        incident_id = uuid.uuid4()
        key = f"{incident_id}:planX:open_ticket:incident"

        async with db_setup() as session:
            inc_repo = IncidentRepository(session)
            await inc_repo.create(Incident(
                id=incident_id, status=IncidentStatus.RESPONDING, severity=Severity.MEDIUM,
                correlation_id=str(incident_id), dedup_fingerprint=f"fp-applied-{incident_id.hex}",
                source="wazuh", raw_alert={},
            ))

        async with db_setup() as session:
            audit = AuditRepository(session)
            assert await audit.is_applied(key) is False

        async with db_setup() as session:
            audit = AuditRepository(session)
            await audit.append(
                incident_id=incident_id, actor="agent", action="open_ticket",
                target="incident", outcome="applied", idempotency_key=key,
            )

        async with db_setup() as session:
            audit = AuditRepository(session)
            assert await audit.is_applied(key) is True
