"""Unit tests — T007: supervisor transition-table legality.

Verifies that every enumerated (state, outcome) edge from data-model.md §4 is
accepted, and any pair NOT in the table results in escalated + disposition=escalated_illegal_transition.
"""

from __future__ import annotations

import uuid

import pytest

from backend.domain.incident import Incident, IncidentStatus, Severity
from backend.domain.pipeline import StageName, StageOutcome, StageResult, ToolError
from backend.infra.config import SupervisorSettings
from backend.infra.tracing import build_tracer
from backend.services.supervisor import TRANSITIONS, Supervisor, _ROUTE_AMBIGUOUS, _ROUTE_CRITICAL, _ROUTE_NOISE


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _incident(status: IncidentStatus = IncidentStatus.GROUNDED, severity: Severity = Severity.MEDIUM) -> Incident:
    return Incident(
        id=uuid.uuid4(),
        status=status,
        severity=severity,
        correlation_id="corr-transitions",
        dedup_fingerprint="fp-transitions",
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


async def _canned(stage: StageName, outcome: StageOutcome) -> StageResult:
    return StageResult(stage=stage, outcome=outcome)


# ---------------------------------------------------------------------------
# Transition table completeness
# ---------------------------------------------------------------------------


def test_routing_keys_present():
    """The three routing sentinels for the grounded state are all in TRANSITIONS."""
    assert (IncidentStatus.GROUNDED, _ROUTE_NOISE) in TRANSITIONS
    assert (IncidentStatus.GROUNDED, _ROUTE_CRITICAL) in TRANSITIONS
    assert (IncidentStatus.GROUNDED, _ROUTE_AMBIGUOUS) in TRANSITIONS


def test_stage_edges_present():
    """All enumerated stage-outcome edges from data-model.md §4 are present."""
    expected_edges = [
        (IncidentStatus.TRIAGING, StageOutcome.RESOLVED),
        (IncidentStatus.TRIAGING, StageOutcome.ADVANCE),
        (IncidentStatus.TRIAGING, StageOutcome.ESCALATE),
        (IncidentStatus.ENRICHING, StageOutcome.ADVANCE),
        (IncidentStatus.ENRICHING, StageOutcome.RESOLVED),
        (IncidentStatus.ENRICHING, StageOutcome.ESCALATE),
        (IncidentStatus.RESPONDING, StageOutcome.RESOLVED),
        (IncidentStatus.RESPONDING, StageOutcome.NEEDS_APPROVAL),
        (IncidentStatus.RESPONDING, StageOutcome.ESCALATE),
    ]
    for edge in expected_edges:
        assert edge in TRANSITIONS, f"Missing edge: {edge}"


# ---------------------------------------------------------------------------
# Illegal transition → escalated
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_illegal_outcome_from_triage_escalates():
    """Triage returning NEEDS_APPROVAL (illegal for triage) → escalated/illegal_transition."""
    incident = _incident()
    repo = FakeRepo(incident)

    async def bad_triage(inc: Incident) -> StageResult:
        return StageResult(stage=StageName.TRIAGE, outcome=StageOutcome.NEEDS_APPROVAL)

    sv = _make_supervisor({StageName.TRIAGE: bad_triage})
    await sv.run_incident(incident.id, repo)

    final = await repo.get(incident.id)
    assert final.status == IncidentStatus.ESCALATED
    assert final.disposition == "escalated_illegal_transition"


@pytest.mark.asyncio
async def test_illegal_outcome_from_enrichment_escalates():
    """Enrichment returning NEEDS_APPROVAL (illegal) → escalated/illegal_transition."""
    incident = _incident(status=IncidentStatus.ENRICHING)
    repo = FakeRepo(incident)

    async def bad_enrichment(inc: Incident) -> StageResult:
        return StageResult(stage=StageName.ENRICHMENT, outcome=StageOutcome.NEEDS_APPROVAL)

    sv = _make_supervisor({StageName.ENRICHMENT: bad_enrichment})
    await sv.run_incident(incident.id, repo)

    final = await repo.get(incident.id)
    assert final.status == IncidentStatus.ESCALATED
    assert final.disposition == "escalated_illegal_transition"


# ---------------------------------------------------------------------------
# Legal transitions complete without illegal-transition disposition
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_triage_advance_goes_to_enriching():
    async def triage(inc): return StageResult(stage=StageName.TRIAGE, outcome=StageOutcome.ADVANCE)
    async def enrichment(inc): return StageResult(stage=StageName.ENRICHMENT, outcome=StageOutcome.RESOLVED)

    incident = _incident()
    repo = FakeRepo(incident)
    sv = _make_supervisor({StageName.TRIAGE: triage, StageName.ENRICHMENT: enrichment})
    await sv.run_incident(incident.id, repo)

    # Verify the intermediate enriching transition happened
    statuses = [a["to"] for a in repo.advances]
    assert IncidentStatus.TRIAGING in statuses
    assert IncidentStatus.ENRICHING in statuses
    final = await repo.get(incident.id)
    assert final.status == IncidentStatus.RESOLVED
    assert final.disposition != "escalated_illegal_transition"


@pytest.mark.asyncio
async def test_triage_resolved_skips_enrichment():
    """Triage returning RESOLVED → resolved without calling enrichment."""
    calls: list[str] = []

    async def triage(inc):
        calls.append("triage")
        return StageResult(stage=StageName.TRIAGE, outcome=StageOutcome.RESOLVED)

    async def enrichment(inc):
        calls.append("enrichment")
        return StageResult(stage=StageName.ENRICHMENT, outcome=StageOutcome.ADVANCE)

    incident = _incident()
    repo = FakeRepo(incident)
    sv = _make_supervisor({StageName.TRIAGE: triage, StageName.ENRICHMENT: enrichment})
    await sv.run_incident(incident.id, repo)

    final = await repo.get(incident.id)
    assert final.status == IncidentStatus.RESOLVED
    assert "enrichment" not in calls
