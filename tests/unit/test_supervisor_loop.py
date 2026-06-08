"""Unit tests — T008: supervisor run loop drives incident to terminal disposition.

With a fake repo and fake stage registry, asserts that a grounded incident:
- advances through the expected lifecycle states
- persists every transition (SC-001)
- ends in exactly one terminal disposition (never stuck in-flight)
"""

from __future__ import annotations

import uuid

import pytest

from backend.domain.incident import Incident, IncidentStatus, Severity
from backend.domain.pipeline import StageName, StageOutcome, StageResult
from backend.infra.config import SupervisorSettings
from backend.infra.tracing import build_tracer
from backend.services.supervisor import Supervisor


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TERMINAL = frozenset({IncidentStatus.RESOLVED, IncidentStatus.ESCALATED, IncidentStatus.FAILED})


def _incident(severity: Severity = Severity.MEDIUM) -> Incident:
    return Incident(
        id=uuid.uuid4(),
        status=IncidentStatus.GROUNDED,
        severity=severity,
        correlation_id="corr-loop",
        dedup_fingerprint="fp-loop",
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

    async def advance_status(
        self,
        incident_id: uuid.UUID,
        *,
        expected: IncidentStatus,
        target: IncidentStatus,
        disposition: str | None = None,
        evidence_patch: dict | None = None,
    ) -> bool:
        if self._incident.id != incident_id or self._incident.status != expected:
            return False
        self.advances.append({"from": expected, "to": target, "disposition": disposition})
        self._incident = self._incident.model_copy(update={"status": target, "disposition": disposition})
        return True


def _make_supervisor(stages: dict, cfg: SupervisorSettings | None = None) -> Supervisor:
    return Supervisor(
        stages=stages,
        cfg=cfg or SupervisorSettings(),
        tracer=build_tracer(exporter=None),
    )


# ---------------------------------------------------------------------------
# Full lifecycle walk
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_depth_ambiguous_incident_reaches_resolved():
    """grounded → triaging → enriching → responding → resolved (SC-001)."""
    call_order: list[str] = []

    async def triage(inc):
        call_order.append("triage")
        return StageResult(stage=StageName.TRIAGE, outcome=StageOutcome.ADVANCE)

    async def enrichment(inc):
        call_order.append("enrichment")
        return StageResult(stage=StageName.ENRICHMENT, outcome=StageOutcome.ADVANCE)

    async def response(inc):
        call_order.append("response")
        return StageResult(stage=StageName.RESPONSE, outcome=StageOutcome.RESOLVED, disposition="auto_remediated")

    incident = _incident()
    repo = FakeRepo(incident)
    sv = _make_supervisor({
        StageName.TRIAGE: triage,
        StageName.ENRICHMENT: enrichment,
        StageName.RESPONSE: response,
    })
    await sv.run_incident(incident.id, repo)

    final = await repo.get(incident.id)
    assert final.status == IncidentStatus.RESOLVED
    assert final.status in _TERMINAL
    assert call_order == ["triage", "enrichment", "response"]


@pytest.mark.asyncio
async def test_every_transition_is_persisted():
    """Every status change is recorded via advance_status (SC-001)."""
    async def triage(inc): return StageResult(stage=StageName.TRIAGE, outcome=StageOutcome.ADVANCE)
    async def enrichment(inc): return StageResult(stage=StageName.ENRICHMENT, outcome=StageOutcome.ADVANCE)
    async def response(inc): return StageResult(stage=StageName.RESPONSE, outcome=StageOutcome.RESOLVED)

    incident = _incident()
    repo = FakeRepo(incident)
    sv = _make_supervisor({
        StageName.TRIAGE: triage,
        StageName.ENRICHMENT: enrichment,
        StageName.RESPONSE: response,
    })
    await sv.run_incident(incident.id, repo)

    targets = [a["to"] for a in repo.advances]
    # grounded → triaging → enriching → responding → resolved
    assert IncidentStatus.TRIAGING in targets
    assert IncidentStatus.ENRICHING in targets
    assert IncidentStatus.RESPONDING in targets
    assert IncidentStatus.RESOLVED in targets


@pytest.mark.asyncio
async def test_incident_never_left_in_flight():
    """After run_incident completes, the incident is not in an in-flight state."""
    async def triage(inc): return StageResult(stage=StageName.TRIAGE, outcome=StageOutcome.ADVANCE)
    async def enrichment(inc): return StageResult(stage=StageName.ENRICHMENT, outcome=StageOutcome.ESCALATE)

    incident = _incident()
    repo = FakeRepo(incident)
    sv = _make_supervisor({StageName.TRIAGE: triage, StageName.ENRICHMENT: enrichment})
    await sv.run_incident(incident.id, repo)

    final = await repo.get(incident.id)
    in_flight = {IncidentStatus.TRIAGING, IncidentStatus.ENRICHING, IncidentStatus.RESPONDING}
    assert final.status not in in_flight
    assert final.status in _TERMINAL


@pytest.mark.asyncio
async def test_triage_escalate_lands_in_escalated():
    async def triage(inc): return StageResult(stage=StageName.TRIAGE, outcome=StageOutcome.ESCALATE)

    incident = _incident()
    repo = FakeRepo(incident)
    sv = _make_supervisor({StageName.TRIAGE: triage})
    await sv.run_incident(incident.id, repo)

    final = await repo.get(incident.id)
    assert final.status == IncidentStatus.ESCALATED
    assert final.disposition == "escalated_triage"


@pytest.mark.asyncio
async def test_response_needs_approval_parks():
    """Response returning NEEDS_APPROVAL parks the incident (non-terminal awaiting_approval)."""
    async def triage(inc): return StageResult(stage=StageName.TRIAGE, outcome=StageOutcome.ADVANCE)
    async def enrichment(inc): return StageResult(stage=StageName.ENRICHMENT, outcome=StageOutcome.ADVANCE)
    async def response(inc): return StageResult(stage=StageName.RESPONSE, outcome=StageOutcome.NEEDS_APPROVAL)

    incident = _incident()
    repo = FakeRepo(incident)
    sv = _make_supervisor({
        StageName.TRIAGE: triage,
        StageName.ENRICHMENT: enrichment,
        StageName.RESPONSE: response,
    })
    await sv.run_incident(incident.id, repo)

    final = await repo.get(incident.id)
    assert final.status == IncidentStatus.AWAITING_APPROVAL
    assert final.disposition == "awaiting_approval_destructive"
