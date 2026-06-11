"""Unit tests — T030: idempotency — duplicate execute / duplicate resume → exactly one audit row (US3)."""

from __future__ import annotations

import contextlib
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from backend.domain.incident import Incident, IncidentStatus, Severity
from backend.domain.pipeline import StageOutcome
from backend.domain.response import ActionType, RiskClass


def _incident() -> Incident:
    ne = {"severity": "medium", "rule_groups": []}
    return Incident(
        id=uuid.uuid4(),
        status=IncidentStatus.RESPONDING,
        severity=Severity.MEDIUM,
        correlation_id="corr-idem",
        dedup_fingerprint="fp-idem",
        source="wazuh",
        raw_alert={},
        evidence={"severity": "medium", "normalized_event": ne},
    )


def _catalog():
    from backend.agents.response import PlaybookEntry
    return [
        PlaybookEntry(
            id="watchlist_only",
            description="medium",
            criteria={"severity": ["medium"]},
            actions=[{"type": "add_to_watchlist"}],
        )
    ]


class _FakeAuditRepo:
    def __init__(self) -> None:
        self.rows = []
        self._applied = set()

    async def is_applied(self, key: str) -> bool:
        return key in self._applied

    async def append(self, *, incident_id, actor, action, target=None, outcome, idempotency_key=None):
        if outcome == "applied" and idempotency_key:
            if idempotency_key in self._applied:
                return False
            self._applied.add(idempotency_key)
        self.rows.append({
            "actor": actor, "action": action, "outcome": outcome,
            "idempotency_key": idempotency_key,
        })
        return True


class _FakeApprovalRepo:
    async def get_approved_pending_for(self, incident_id):
        return None

    async def create_pending(self, *, incident_id, plan_id, pending_actions, rationale, deadline_at):
        return 1


@contextlib.asynccontextmanager
async def _session_factory():
    yield None


@pytest.mark.asyncio
async def test_duplicate_execute_writes_one_audit_row():
    """Executing the same action twice writes only one applied audit row."""
    from backend.agents.response import make_response_handler
    from backend.infra.config import ResponseSettings
    from backend.infra.executors import build_mock_executors

    audit_repo = _FakeAuditRepo()
    approval_repo = _FakeApprovalRepo()
    incident = _incident()
    catalog = _catalog()
    cfg = ResponseSettings()
    executors = build_mock_executors()

    with (
        patch("backend.agents.response.ApprovalRepository", return_value=approval_repo),
        patch("backend.agents.response.AuditRepository", return_value=audit_repo),
    ):
        handler = make_response_handler(
            llm=None, session_factory=_session_factory, executors=executors, cfg=cfg, catalog=catalog
        )
        # First call
        result1 = await handler(incident)
        # Second call (duplicate — same incident, same idempotency key)
        result2 = await handler(incident)

    assert result1.outcome == StageOutcome.RESOLVED
    assert result2.outcome == StageOutcome.RESOLVED

    # Only one applied row (second call was idempotently skipped)
    applied_rows = [r for r in audit_repo.rows if r["outcome"] == "applied"]
    assert len(applied_rows) == 1


@pytest.mark.asyncio
async def test_idempotent_skip_returns_applied_synthetic_result():
    """When an action is already applied (pre-check), a synthetic APPLIED result is returned."""
    from backend.agents.response import _execute_with_audit
    from backend.domain.response import ActionStatus, ActionType, RemediationAction, RiskClass
    from backend.infra.executors import build_mock_executors

    incident_id = uuid.uuid4()
    idem_key = f"{incident_id}:plan1:add_to_watchlist:host"
    action = RemediationAction(
        type=ActionType.ADD_TO_WATCHLIST,
        target="host",
        risk=RiskClass.AUTO,
        idempotency_key=idem_key,
    )

    # Pre-populate as already applied
    audit_repo = _FakeAuditRepo()
    audit_repo._applied.add(idem_key)

    executors = build_mock_executors()

    result = await _execute_with_audit(
        action=action,
        incident_id=incident_id,
        actor="agent",
        audit_repo=audit_repo,
        executors=executors,
    )

    assert result.status == ActionStatus.APPLIED
    assert result.detail == "idempotent_skip"
    # No new audit row written (was skipped before executing)
    assert audit_repo.rows == []


@pytest.mark.asyncio
async def test_duplicate_resume_guard():
    """Second approve on an already-resolved incident is blocked by advance_status guard."""
    from backend.domain.incident import IncidentStatus
    from backend.infra.config import SupervisorSettings
    from backend.infra.tracing import build_tracer
    from backend.services.supervisor import Supervisor

    incident = Incident(
        id=uuid.uuid4(),
        status=IncidentStatus.RESOLVED,  # already resolved
        severity=Severity.CRITICAL,
        correlation_id="corr-dup-resume",
        dedup_fingerprint="fp-dup",
        source="wazuh",
        raw_alert={},
        disposition="remediated",
    )

    class _FakeRepo:
        def __init__(self, inc):
            self._inc = inc
            self.advance_calls = 0

        async def get(self, iid):
            return self._inc if self._inc.id == iid else None

        async def advance_status(self, iid, *, expected, target, disposition=None, evidence_patch=None):
            self.advance_calls += 1
            if self._inc.id != iid or self._inc.status != expected:
                return False
            return True

    repo = _FakeRepo(incident)
    audit_repo = _FakeAuditRepo()

    sup = Supervisor(stages={}, cfg=SupervisorSettings(), tracer=build_tracer(exporter=None))
    # incident is RESOLVED (not AWAITING_APPROVAL) → advance_status guard fails
    disp = await sup.resume_incident(
        incident.id, "approve", repo, audit_repo=audit_repo, actor="admin"
    )
    # Guard lost → returns current disposition
    assert disp == "remediated"
    # advance_status was called but returned False (guard lost)
    assert repo.advance_calls == 1
