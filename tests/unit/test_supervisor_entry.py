"""Unit tests — T009: supervisor entry/idempotency by state class.

Verifies:
- grounded → starts the pipeline (calls stage handlers)
- in-flight (triaging/enriching/responding) → resumes from that stage only
- awaiting_approval → no-op (waits for resume_incident, #10)
- terminal (resolved/escalated/failed) → no-op (idempotent re-delivery, SC-005)
- advance_status returning False (guard race) → clean exit, no exception
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


def _incident(status: IncidentStatus, severity: Severity = Severity.MEDIUM) -> Incident:
    return Incident(
        id=uuid.uuid4(),
        status=status,
        severity=severity,
        correlation_id="corr-entry",
        dedup_fingerprint="fp-entry",
        source="wazuh",
        raw_alert={},
    )


class FakeRepo:
    def __init__(self, incident: Incident, guard_fails: bool = False) -> None:
        self._incident = incident.model_copy(deep=True)
        self._guard_fails = guard_fails
        self.advances: list[dict] = []
        self.advance_calls: int = 0

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
        self.advance_calls += 1
        if self._guard_fails:
            return False
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


def _noop_stages() -> dict:
    """Stages that should never be called (for no-op tests)."""

    async def should_not_call(inc):
        raise AssertionError("Stage should not be called for terminal/parked incident")

    return {
        StageName.TRIAGE: should_not_call,
        StageName.ENRICHMENT: should_not_call,
        StageName.RESPONSE: should_not_call,
    }


# ---------------------------------------------------------------------------
# Terminal states → no-op (idempotent re-delivery, SC-005)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [IncidentStatus.RESOLVED, IncidentStatus.ESCALATED, IncidentStatus.FAILED])
async def test_terminal_incident_is_noop(status: IncidentStatus):
    incident = _incident(status)
    repo = FakeRepo(incident)
    sv = _make_supervisor(_noop_stages())
    await sv.run_incident(incident.id, repo)  # should not call any stage
    assert repo.advance_calls == 0


# ---------------------------------------------------------------------------
# Parked state → no-op
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_awaiting_approval_is_noop():
    incident = _incident(IncidentStatus.AWAITING_APPROVAL)
    repo = FakeRepo(incident)
    sv = _make_supervisor(_noop_stages())
    await sv.run_incident(incident.id, repo)
    assert repo.advance_calls == 0


# ---------------------------------------------------------------------------
# Grounded → starts pipeline
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_grounded_starts_pipeline():
    """Grounded incident is routed and at least one stage is called."""
    calls: list[str] = []

    async def triage(inc):
        calls.append("triage")
        return StageResult(stage=StageName.TRIAGE, outcome=StageOutcome.RESOLVED)

    incident = _incident(IncidentStatus.GROUNDED, severity=Severity.MEDIUM)
    repo = FakeRepo(incident)
    sv = _make_supervisor({StageName.TRIAGE: triage})
    await sv.run_incident(incident.id, repo)
    assert "triage" in calls


# ---------------------------------------------------------------------------
# In-flight states → resume from that stage only
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_triaging_resumes_from_triage_only():
    """An incident in triaging only calls the triage handler (not enrichment/response)."""
    calls: list[str] = []

    async def triage(inc):
        calls.append("triage")
        return StageResult(stage=StageName.TRIAGE, outcome=StageOutcome.RESOLVED)

    async def should_not_call(inc):
        calls.append("other")
        return StageResult(stage=StageName.ENRICHMENT, outcome=StageOutcome.ADVANCE)

    incident = _incident(IncidentStatus.TRIAGING)
    repo = FakeRepo(incident)
    sv = _make_supervisor({
        StageName.TRIAGE: triage,
        StageName.ENRICHMENT: should_not_call,
        StageName.RESPONSE: should_not_call,
    })
    await sv.run_incident(incident.id, repo)
    assert calls == ["triage"]


@pytest.mark.asyncio
async def test_enriching_resumes_from_enrichment_only():
    calls: list[str] = []

    async def enrichment(inc):
        calls.append("enrichment")
        return StageResult(stage=StageName.ENRICHMENT, outcome=StageOutcome.RESOLVED)

    async def triage(inc):
        calls.append("triage")
        return StageResult(stage=StageName.TRIAGE, outcome=StageOutcome.ADVANCE)

    incident = _incident(IncidentStatus.ENRICHING)
    repo = FakeRepo(incident)
    sv = _make_supervisor({StageName.TRIAGE: triage, StageName.ENRICHMENT: enrichment})
    await sv.run_incident(incident.id, repo)
    assert calls == ["enrichment"]


# ---------------------------------------------------------------------------
# Guard race (advance_status returns False) → clean exit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_guard_race_exits_cleanly():
    """If advance_status returns False (another worker moved the row), run_incident exits silently."""
    incident = _incident(IncidentStatus.GROUNDED)
    repo = FakeRepo(incident, guard_fails=True)

    async def triage(inc):
        raise AssertionError("Should not reach stage if guard fails on routing")

    sv = _make_supervisor({StageName.TRIAGE: triage})
    # Should not raise
    await sv.run_incident(incident.id, repo)
