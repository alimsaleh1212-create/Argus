"""Unit tests — T023: supervisor park (awaiting_approval) and resume edges (FR-013).

Verifies:
- NEEDS_APPROVAL from response → awaiting_approval (parked, loop stops)
- resume_incident(approve) → responding
- resume_incident(reject) → resolved (rejected_by_human)
"""

from __future__ import annotations

import uuid

import pytest

from backend.domain.incident import Incident, IncidentStatus, Severity
from backend.domain.pipeline import StageName, StageOutcome, StageResult
from backend.infra.config import SupervisorSettings
from backend.infra.tracing import build_tracer
from backend.services.supervisor import Supervisor


def _incident(status: IncidentStatus = IncidentStatus.GROUNDED) -> Incident:
    return Incident(
        id=uuid.uuid4(),
        status=status,
        severity=Severity.CRITICAL,  # straight to response
        correlation_id="corr-approval",
        dedup_fingerprint="fp-approval",
        source="wazuh",
        raw_alert={},
    )


class FakeRepo:
    def __init__(self, incident: Incident) -> None:
        self._incident = incident.model_copy(deep=True)
        self.advances: list[dict] = []

    async def get(self, incident_id: uuid.UUID) -> Incident | None:
        if self._incident.id == incident_id:
            return self._incident
        return None

    async def advance_status(self, incident_id, *, expected, target, disposition=None) -> bool:
        if self._incident.id != incident_id or self._incident.status != expected:
            return False
        self.advances.append({"from": expected, "to": target, "disposition": disposition})
        self._incident = self._incident.model_copy(update={"status": target, "disposition": disposition})
        return True


def _make_supervisor(stages: dict) -> Supervisor:
    return Supervisor(stages=stages, cfg=SupervisorSettings(), tracer=build_tracer(exporter=None))


# ---------------------------------------------------------------------------
# Park transition
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_needs_approval_parks_incident():
    """Response returning NEEDS_APPROVAL → incident parks in awaiting_approval, loop stops."""
    after_park_calls = 0

    async def response(inc):
        return StageResult(stage=StageName.RESPONSE, outcome=StageOutcome.NEEDS_APPROVAL)

    incident = _incident()  # critical → goes straight to response
    repo = FakeRepo(incident)
    sv = _make_supervisor({StageName.RESPONSE: response})
    await sv.run_incident(incident.id, repo)

    final = await repo.get(incident.id)
    assert final.status == IncidentStatus.AWAITING_APPROVAL
    assert final.disposition == "awaiting_approval_destructive"


@pytest.mark.asyncio
async def test_loop_stops_after_park():
    """After parking, run_incident does not call any more stages."""
    post_park_calls = 0

    async def response(inc):
        return StageResult(stage=StageName.RESPONSE, outcome=StageOutcome.NEEDS_APPROVAL)

    incident = _incident()
    repo = FakeRepo(incident)
    sv = _make_supervisor({StageName.RESPONSE: response})
    await sv.run_incident(incident.id, repo)

    # Re-deliver the parked incident — should be a no-op
    call_count_before = len(repo.advances)
    await sv.run_incident(incident.id, repo)
    assert len(repo.advances) == call_count_before  # no new advances


# ---------------------------------------------------------------------------
# Resume edges
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resume_approve_transitions_to_responding():
    """resume_incident(approve) transitions awaiting_approval → responding."""
    incident = _incident(status=IncidentStatus.AWAITING_APPROVAL)
    repo = FakeRepo(incident)
    sv = _make_supervisor({})

    await sv.resume_incident(incident.id, "approve", repo)

    final = await repo.get(incident.id)
    assert final.status == IncidentStatus.RESPONDING


@pytest.mark.asyncio
async def test_resume_reject_transitions_to_resolved():
    """resume_incident(reject) transitions awaiting_approval → resolved (rejected_by_human)."""
    incident = _incident(status=IncidentStatus.AWAITING_APPROVAL)
    repo = FakeRepo(incident)
    sv = _make_supervisor({})

    await sv.resume_incident(incident.id, "reject", repo)

    final = await repo.get(incident.id)
    assert final.status == IncidentStatus.RESOLVED
    assert final.disposition == "rejected_by_human"


@pytest.mark.asyncio
async def test_resume_unknown_decision_is_noop():
    """An unknown decision string does not crash or mutate state."""
    incident = _incident(status=IncidentStatus.AWAITING_APPROVAL)
    repo = FakeRepo(incident)
    sv = _make_supervisor({})

    await sv.resume_incident(incident.id, "unknown_decision", repo)

    final = await repo.get(incident.id)
    assert final.status == IncidentStatus.AWAITING_APPROVAL  # unchanged
