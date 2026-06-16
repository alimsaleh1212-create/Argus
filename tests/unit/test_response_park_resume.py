"""Unit tests — T018: park branch + Pass-B resume (US2)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from backend.domain.incident import Incident, IncidentStatus, Severity
from backend.domain.pipeline import StageOutcome
from backend.domain.response import ActionType, RiskClass


def _responding_incident(severity: str = "critical") -> Incident:
    ne = {"severity": severity, "rule_groups": ["attack"]}
    return Incident(
        id=uuid.uuid4(),
        status=IncidentStatus.RESPONDING,
        severity=Severity(severity),
        correlation_id="corr-park",
        dedup_fingerprint="fp-park",
        source="wazuh",
        raw_alert={},
        evidence={"severity": severity, "normalized_event": ne},
    )


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


def _catalog_auto_only():
    from backend.agents.response import PlaybookEntry

    return [
        PlaybookEntry(
            id="watchlist",
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

    async def append(
        self, *, incident_id, actor, action, target=None, outcome, idempotency_key=None
    ):
        self.rows.append(
            {
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
        return self.rows


class _FakeApprovalRepo:
    def __init__(self, approved_record=None) -> None:
        self._approved = approved_record
        self._records = {}
        self._next_id = 1

    async def get_approved_pending_for(self, incident_id):
        return self._approved

    async def create_pending(
        self, *, incident_id, plan_id, pending_actions, rationale, deadline_at
    ):
        rid = self._next_id
        self._next_id += 1
        self._records[rid] = {
            "id": rid,
            "status": "pending",
            "pending_actions": pending_actions,
            "plan_id": plan_id,
            "rationale": rationale,
        }
        return rid

    async def resolve(self, approval_id, *, to, decided_by):
        if approval_id in self._records and self._records[approval_id]["status"] == "pending":
            self._records[approval_id]["status"] = to.value
            return True
        return False


# ---------------------------------------------------------------------------
# Pass A: park branch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_park_branch_returns_needs_approval():
    """Destructive-action plan → NEEDS_APPROVAL, approval record created, no destructive execution."""
    import contextlib

    from backend.agents.response import make_response_handler
    from backend.infra.config import ResponseSettings
    from backend.infra.executors import build_mock_executors

    audit_repo = _FakeAuditRepo()
    approval_repo = _FakeApprovalRepo()
    incident = _responding_incident()
    catalog = _catalog_destructive()
    cfg = ResponseSettings()
    executors = build_mock_executors()

    @contextlib.asynccontextmanager
    async def _session_factory():
        yield None

    with (
        patch("backend.agents.response.handler.ApprovalRepository", return_value=approval_repo),
        patch("backend.agents.response.handler.AuditRepository", return_value=audit_repo),
    ):
        handler = make_response_handler(
            llm=None,
            session_factory=_session_factory,
            executors=executors,
            cfg=cfg,
            catalog=catalog,
        )
        result = await handler(incident)

    assert result.outcome == StageOutcome.NEEDS_APPROVAL
    # Approval record written
    assert len(approval_repo._records) == 1
    # No isolate_host executed
    assert not any(r["action"] == "isolate_host" for r in audit_repo.rows)


@pytest.mark.asyncio
async def test_park_branch_auto_actions_still_execute():
    """Co-proposed auto actions execute before parking (T019 spec)."""
    import contextlib

    from backend.agents.response import make_response_handler
    from backend.infra.config import ResponseSettings
    from backend.infra.executors import build_mock_executors

    audit_repo = _FakeAuditRepo()
    approval_repo = _FakeApprovalRepo()
    incident = _responding_incident()
    catalog = _catalog_destructive()  # has isolate_host + open_ticket
    cfg = ResponseSettings()
    executors = build_mock_executors()

    @contextlib.asynccontextmanager
    async def _session_factory():
        yield None

    with (
        patch("backend.agents.response.handler.ApprovalRepository", return_value=approval_repo),
        patch("backend.agents.response.handler.AuditRepository", return_value=audit_repo),
    ):
        handler = make_response_handler(
            llm=None,
            session_factory=_session_factory,
            executors=executors,
            cfg=cfg,
            catalog=catalog,
        )
        await handler(incident)

    # open_ticket (auto) should have been executed
    auto_rows = [r for r in audit_repo.rows if r["action"] == "open_ticket"]
    assert len(auto_rows) == 1
    assert auto_rows[0]["outcome"] == "applied"


# ---------------------------------------------------------------------------
# Pass B: resume execution (no LLM)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pass_b_executes_approved_plan():
    """Approved pending record → Pass B executes it, returns RESOLVED/remediated, no LLM call."""
    import contextlib

    from backend.agents.response import make_response_handler
    from backend.infra.config import ResponseSettings
    from backend.infra.executors import build_mock_executors
    from backend.repositories.approvals import ApprovalRecord

    incident = _responding_incident()
    # Build an approved record with the destructive action
    from backend.domain.response import RemediationAction, RiskClass

    approved_action = RemediationAction(
        type=ActionType.ISOLATE_HOST,
        target="web-srv-01",
        risk=RiskClass.APPROVAL_REQUIRED,
        idempotency_key=f"{incident.id}:plan1:isolate_host:web-srv-01",
    )
    approved_record = ApprovalRecord(
        id=1,
        incident_id=incident.id,
        plan_id="plan1",
        pending_actions=[approved_action.model_dump(mode="json")],
        rationale="approved by human",
        status="approved",
        deadline_at=datetime.now(UTC).replace(tzinfo=None) + timedelta(seconds=1800),
        decided_by="admin",
        decided_at=datetime.now(UTC).replace(tzinfo=None),
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    audit_repo = _FakeAuditRepo()
    approval_repo = _FakeApprovalRepo(approved_record=approved_record)

    @contextlib.asynccontextmanager
    async def _session_factory():
        yield None

    catalog = _catalog_destructive()
    cfg = ResponseSettings()
    executors = build_mock_executors()

    with (
        patch("backend.agents.response.handler.ApprovalRepository", return_value=approval_repo),
        patch("backend.agents.response.handler.AuditRepository", return_value=audit_repo),
    ):
        handler = make_response_handler(
            llm=None,
            session_factory=_session_factory,
            executors=executors,
            cfg=cfg,
            catalog=catalog,
        )
        result = await handler(incident)

    assert result.outcome == StageOutcome.RESOLVED
    assert result.disposition == "remediated"
    assert result.tokens_consumed == 0  # no LLM on Pass B
    executed = [r for r in audit_repo.rows if r["action"] == "isolate_host"]
    assert len(executed) == 1
    assert executed[0]["actor"] == "admin"
    assert executed[0]["outcome"] == "applied"


@pytest.mark.asyncio
async def test_pass_b_no_llm_call():
    """Pass B never calls the LLM."""
    import contextlib

    from backend.agents.response import make_response_handler
    from backend.domain.response import RemediationAction
    from backend.infra.config import ResponseSettings
    from backend.infra.executors import build_mock_executors
    from backend.repositories.approvals import ApprovalRecord

    incident = _responding_incident()
    approved_action = RemediationAction(
        type=ActionType.ISOLATE_HOST,
        target="host",
        risk=RiskClass.APPROVAL_REQUIRED,
        idempotency_key=f"{incident.id}:plan1:isolate_host:host",
    )
    approved_record = ApprovalRecord(
        id=1,
        incident_id=incident.id,
        plan_id="plan1",
        pending_actions=[approved_action.model_dump(mode="json")],
        rationale="ok",
        status="approved",
        deadline_at=datetime.now(UTC).replace(tzinfo=None) + timedelta(seconds=60),
        decided_by="admin",
        decided_at=datetime.now(UTC).replace(tzinfo=None),
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    class _TrackingLlm:
        call_count = 0

        async def generate(self, req, *, correlation_id=None):
            self.__class__.call_count += 1
            raise AssertionError("LLM should not be called on Pass B")

    @contextlib.asynccontextmanager
    async def _session_factory():
        yield None

    llm = _TrackingLlm()
    audit_repo = _FakeAuditRepo()
    approval_repo = _FakeApprovalRepo(approved_record=approved_record)
    catalog = _catalog_destructive()
    cfg = ResponseSettings()
    executors = build_mock_executors()

    with (
        patch("backend.agents.response.handler.ApprovalRepository", return_value=approval_repo),
        patch("backend.agents.response.handler.AuditRepository", return_value=audit_repo),
    ):
        handler = make_response_handler(
            llm=llm, session_factory=_session_factory, executors=executors, cfg=cfg, catalog=catalog
        )
        await handler(incident)

    assert _TrackingLlm.call_count == 0
