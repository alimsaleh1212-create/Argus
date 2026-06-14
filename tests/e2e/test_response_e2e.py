"""e2e tests — T017 (US1) + T028 (US2): response stage full-depth pipeline.

T017: confirmed auto-only incident driven through worker → RESPONDING → resolved/auto_remediated,
      audit rows written, LLM faked at the driver boundary.

T028: destructive incident → awaiting_approval;
      approve → remediated; reject → rejected_by_human; timeout → approval_expired.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest


def _make_auto_incident():
    from backend.domain.incident import Incident, IncidentStatus, Severity

    return Incident(
        id=uuid.uuid4(),
        status=IncidentStatus.RESPONDING,
        severity=Severity.MEDIUM,
        correlation_id="corr-e2e-auto",
        dedup_fingerprint=f"fp-e2e-auto-{uuid.uuid4().hex}",
        source="wazuh",
        raw_alert={},
        evidence={
            "severity": "medium",
            "normalized_event": {"severity": "medium", "rule_groups": []},
        },
    )


def _make_destructive_incident():
    from backend.domain.incident import Incident, IncidentStatus, Severity

    return Incident(
        id=uuid.uuid4(),
        status=IncidentStatus.RESPONDING,
        severity=Severity.CRITICAL,
        correlation_id="corr-e2e-destruct",
        dedup_fingerprint=f"fp-e2e-destruct-{uuid.uuid4().hex}",
        source="wazuh",
        raw_alert={},
        evidence={
            "severity": "critical",
            "normalized_event": {"severity": "critical", "rule_groups": ["attack"]},
        },
    )


def _catalog_auto_only():
    from backend.agents.response import PlaybookEntry

    return [
        PlaybookEntry(
            id="watchlist_and_ticket",
            description="low-medium",
            criteria={"severity": ["low", "medium"]},
            actions=[{"type": "add_to_watchlist"}, {"type": "open_ticket"}],
        )
    ]


def _catalog_destructive():
    from backend.agents.response import PlaybookEntry

    return [
        PlaybookEntry(
            id="isolate_and_ticket",
            description="critical attack",
            criteria={"severity": ["critical"], "rule_groups": ["attack"]},
            actions=[{"type": "isolate_host"}, {"type": "open_ticket"}],
        )
    ]


class _FakeRepo:
    def __init__(self, incident) -> None:
        self._incident = incident.model_copy(deep=True)
        self.advances = []

    async def get(self, incident_id):
        if self._incident.id == incident_id:
            return self._incident
        return None

    async def advance_status(
        self, incident_id, *, expected, target, disposition=None, evidence_patch=None
    ):
        if self._incident.id != incident_id or self._incident.status != expected:
            return False
        self.advances.append(
            {"from": expected.value, "to": target.value, "disposition": disposition}
        )
        self._incident = self._incident.model_copy(
            update={"status": target, "disposition": disposition}
        )
        return True


class _FakeAuditRepo:
    def __init__(self) -> None:
        self.rows = []
        self._applied = set()

    async def is_applied(self, key: str) -> bool:
        return key in self._applied

    async def append(
        self, *, incident_id, actor, action, target=None, outcome, idempotency_key=None
    ):
        self.rows.append(
            {
                "incident_id": str(incident_id),
                "actor": actor,
                "action": action,
                "outcome": outcome,
                "idempotency_key": idempotency_key,
            }
        )
        if outcome == "applied" and idempotency_key:
            if idempotency_key in self._applied:
                return False
            self._applied.add(idempotency_key)
        return True

    async def list_for_incident(self, incident_id):
        return [r for r in self.rows if r["incident_id"] == str(incident_id)]


class _FakeApprovalRepo:
    def __init__(self) -> None:
        self._records = {}
        self._next_id = 1

    async def get_approved_pending_for(self, incident_id):
        for r in self._records.values():
            if r.incident_id == incident_id and r.status == "approved":
                return r
        return None

    async def create_pending(
        self, *, incident_id, plan_id, pending_actions, rationale, deadline_at
    ):
        rid = self._next_id
        self._next_id += 1
        from backend.repositories.approvals import ApprovalRecord

        rec = ApprovalRecord(
            id=rid,
            incident_id=incident_id,
            plan_id=plan_id,
            pending_actions=pending_actions,
            rationale=rationale,
            status="pending",
            deadline_at=deadline_at,
            decided_by=None,
            decided_at=None,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        self._records[rid] = rec
        return rid

    async def resolve(self, approval_id, *, to, decided_by):
        if approval_id not in self._records:
            return False
        rec = self._records[approval_id]
        if rec.status != "pending":
            return False
        from dataclasses import replace

        self._records[approval_id] = replace(
            rec,
            status=to.value,
            decided_by=decided_by,
            decided_at=datetime.now(UTC).replace(tzinfo=None),
        )
        return True

    async def get(self, approval_id):
        return self._records.get(approval_id)

    async def list_pending_expired(self, now):
        return [r for r in self._records.values() if r.status == "pending" and r.deadline_at < now]


def _make_session_factory(audit_repo, approval_repo):
    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

    class _FakeFactory:
        def __call__(self):
            return _FakeSession()

    import contextlib

    @contextlib.asynccontextmanager
    async def _factory():
        yield _FakeSession()

    # Patch ApprovalRepository and AuditRepository construction inside the handler
    return approval_repo, audit_repo


@pytest.mark.asyncio
async def test_auto_path_resolves_auto_remediated():
    """Auto-only incident → resolved/auto_remediated with audit rows."""
    from unittest.mock import patch

    from backend.agents.response import make_response_handler
    from backend.domain.pipeline import StageOutcome
    from backend.infra.config import ResponseSettings
    from backend.infra.executors import build_mock_executors

    incident = _make_auto_incident()
    catalog = _catalog_auto_only()
    cfg = ResponseSettings()
    executors = build_mock_executors()
    audit_repo = _FakeAuditRepo()
    approval_repo = _FakeApprovalRepo()

    import contextlib

    @contextlib.asynccontextmanager
    async def _session_factory():
        yield None  # session not used directly; repos are patched

    with (
        patch("backend.agents.response.ApprovalRepository") as MockApproval,
        patch("backend.agents.response.AuditRepository") as MockAudit,
    ):
        MockApproval.return_value = approval_repo
        MockAudit.return_value = audit_repo

        handler = make_response_handler(
            llm=None,  # deterministic — no LLM needed
            session_factory=_session_factory,
            executors=executors,
            cfg=cfg,
            catalog=catalog,
        )
        result = await handler(incident)

    assert result.outcome == StageOutcome.RESOLVED
    assert result.disposition == "auto_remediated"
    assert result.tokens_consumed == 0
    applied = [r for r in audit_repo.rows if r["outcome"] == "applied"]
    assert len(applied) >= 1


@pytest.mark.asyncio
async def test_destructive_incident_parks():
    """Destructive-only incident → NEEDS_APPROVAL, nothing executed."""
    from unittest.mock import patch

    from backend.agents.response import make_response_handler
    from backend.domain.pipeline import StageOutcome
    from backend.infra.config import ResponseSettings
    from backend.infra.executors import build_mock_executors

    incident = _make_destructive_incident()
    catalog = _catalog_destructive()
    cfg = ResponseSettings()
    executors = build_mock_executors()
    audit_repo = _FakeAuditRepo()
    approval_repo = _FakeApprovalRepo()

    import contextlib

    @contextlib.asynccontextmanager
    async def _session_factory():
        yield None

    with (
        patch("backend.agents.response.ApprovalRepository") as MockApproval,
        patch("backend.agents.response.AuditRepository") as MockAudit,
    ):
        MockApproval.return_value = approval_repo
        MockAudit.return_value = audit_repo

        handler = make_response_handler(
            llm=None,
            session_factory=_session_factory,
            executors=executors,
            cfg=cfg,
            catalog=catalog,
        )
        result = await handler(incident)

    assert result.outcome == StageOutcome.NEEDS_APPROVAL
    # No destructive (isolate_host) actions executed — only auto (open_ticket) may have run
    destructive_rows = [r for r in audit_repo.rows if r["action"] == "isolate_host"]
    assert destructive_rows == []
    # Approval record was created for the destructive action
    assert len(approval_repo._records) == 1


@pytest.mark.asyncio
async def test_approve_resumes_to_remediated():
    """Approve → Pass B executes the approved plan → resolved/remediated."""
    from unittest.mock import patch

    from backend.agents.response import make_response_handler
    from backend.domain.pipeline import StageOutcome
    from backend.infra.config import ResponseSettings
    from backend.infra.executors import build_mock_executors

    incident = _make_destructive_incident()
    catalog = _catalog_destructive()
    cfg = ResponseSettings()
    executors = build_mock_executors()
    audit_repo = _FakeAuditRepo()
    approval_repo = _FakeApprovalRepo()

    import contextlib

    @contextlib.asynccontextmanager
    async def _session_factory():
        yield None

    # First pass: park
    with (
        patch("backend.agents.response.ApprovalRepository") as MockApproval,
        patch("backend.agents.response.AuditRepository") as MockAudit,
    ):
        MockApproval.return_value = approval_repo
        MockAudit.return_value = audit_repo

        handler = make_response_handler(
            llm=None,
            session_factory=_session_factory,
            executors=executors,
            cfg=cfg,
            catalog=catalog,
        )
        await handler(incident)

    # Simulate approval: mark the approval record as approved with pending_actions populated
    approval_id = 1
    rec = approval_repo._records[approval_id]
    from dataclasses import replace

    approval_repo._records[approval_id] = replace(
        rec,
        status="approved",
        decided_by="admin",
        decided_at=datetime.now(UTC).replace(tzinfo=None),
    )

    # Second pass (Pass B): execute the approved plan
    with (
        patch("backend.agents.response.ApprovalRepository") as MockApproval,
        patch("backend.agents.response.AuditRepository") as MockAudit,
    ):
        MockApproval.return_value = approval_repo
        MockAudit.return_value = audit_repo

        result = await handler(incident)

    assert result.outcome == StageOutcome.RESOLVED
    assert result.disposition == "remediated"
    applied = [r for r in audit_repo.rows if r["outcome"] == "applied"]
    assert len(applied) >= 1


@pytest.mark.asyncio
async def test_reject_writes_audit_not_executed():
    """Reject → audit row with not_executed, nothing else runs."""

    from backend.infra.config import SupervisorSettings
    from backend.infra.tracing import build_tracer
    from backend.services.supervisor import Supervisor

    incident = _make_destructive_incident()
    audit_repo = _FakeAuditRepo()

    repo = _FakeRepo(incident)
    # Move incident to AWAITING_APPROVAL
    from backend.domain.incident import IncidentStatus

    repo._incident = repo._incident.model_copy(update={"status": IncidentStatus.AWAITING_APPROVAL})

    sup = Supervisor(stages={}, cfg=SupervisorSettings(), tracer=build_tracer(exporter=None))
    disp = await sup.resume_incident(
        incident.id, "reject", repo, audit_repo=audit_repo, actor="admin"
    )

    assert disp == "rejected_by_human"
    assert repo._incident.status == IncidentStatus.RESOLVED
    assert any(r["action"] == "approval_rejected" for r in audit_repo.rows)


@pytest.mark.asyncio
async def test_timeout_expires_to_approval_expired():
    """Timeout sweeper fires → ESCALATED/approval_expired, nothing executed."""
    from backend.domain.incident import IncidentStatus
    from backend.infra.config import SupervisorSettings
    from backend.infra.tracing import build_tracer
    from backend.services.supervisor import Supervisor

    incident = _make_destructive_incident()
    repo = _FakeRepo(incident)
    repo._incident = repo._incident.model_copy(update={"status": IncidentStatus.AWAITING_APPROVAL})

    audit_repo = _FakeAuditRepo()
    sup = Supervisor(stages={}, cfg=SupervisorSettings(), tracer=build_tracer(exporter=None))
    expired = await sup.expire_incident(incident.id, repo, audit_repo=audit_repo)

    assert expired is True
    assert repo._incident.status == IncidentStatus.ESCALATED
    assert repo._incident.disposition == "approval_expired"
    assert any(r["action"] == "approval_expired" for r in audit_repo.rows)
