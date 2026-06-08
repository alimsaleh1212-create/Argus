"""Unit tests — T013: supervisor routing + adaptive depth (SC-003, FR-006/FR-007).

Covers:
- route_grounded: severity bands → correct routing sentinel
- Fast-path: low → resolved (0 stage calls), critical → responding
- Adaptive depth: triage RESOLVED skips enrichment; triage ADVANCE → enrichment
- severity_defaulted flag → always triaging (never fast-pathed)
"""

from __future__ import annotations

import uuid

import pytest

from backend.domain.incident import Incident, IncidentStatus, Severity
from backend.domain.pipeline import StageName, StageOutcome, StageResult
from backend.infra.config import SupervisorSettings
from backend.infra.tracing import build_tracer
from backend.services.supervisor import Supervisor, _ROUTE_AMBIGUOUS, _ROUTE_CRITICAL, _ROUTE_NOISE, route_grounded


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _incident(severity: Severity, flags: list[str] | None = None) -> Incident:
    evidence = {"flags": flags or [], "verdict": "test", "severity": severity.value,
                "normalized_event": {}, "summary": ""}
    return Incident(
        id=uuid.uuid4(),
        status=IncidentStatus.GROUNDED,
        severity=severity,
        correlation_id="corr-routing",
        dedup_fingerprint="fp-routing",
        source="wazuh",
        raw_alert={},
        evidence=evidence,
    )


class FakeRepo:
    def __init__(self, incident: Incident) -> None:
        self._incident = incident.model_copy(deep=True)
        self.advances: list[dict] = []

    async def get(self, incident_id: uuid.UUID) -> Incident | None:
        if self._incident.id == incident_id:
            return self._incident
        return None

    async def advance_status(self, incident_id, *, expected, target, disposition=None, evidence_patch=None) -> bool:
        if self._incident.id != incident_id or self._incident.status != expected:
            return False
        self.advances.append({"from": expected, "to": target, "disposition": disposition})
        self._incident = self._incident.model_copy(update={"status": target, "disposition": disposition})
        return True


def _make_supervisor(stages: dict, cfg: SupervisorSettings | None = None) -> Supervisor:
    return Supervisor(stages=stages, cfg=cfg or SupervisorSettings(), tracer=build_tracer(exporter=None))


# ---------------------------------------------------------------------------
# route_grounded: pure function tests
# ---------------------------------------------------------------------------


def test_route_low_severity_returns_noise():
    incident = _incident(Severity.LOW)
    assert route_grounded(incident, SupervisorSettings()) == _ROUTE_NOISE


def test_route_critical_severity_returns_critical():
    incident = _incident(Severity.CRITICAL)
    assert route_grounded(incident, SupervisorSettings()) == _ROUTE_CRITICAL


def test_route_medium_severity_returns_ambiguous():
    incident = _incident(Severity.MEDIUM)
    assert route_grounded(incident, SupervisorSettings()) == _ROUTE_AMBIGUOUS


def test_route_high_severity_returns_ambiguous():
    incident = _incident(Severity.HIGH)
    assert route_grounded(incident, SupervisorSettings()) == _ROUTE_AMBIGUOUS


def test_route_severity_defaulted_flag_forces_ambiguous():
    """severity_defaulted in evidence.flags → ambiguous, never fast-pathed (edge case)."""
    incident = _incident(Severity.LOW, flags=["severity_defaulted"])
    assert route_grounded(incident, SupervisorSettings()) == _ROUTE_AMBIGUOUS


def test_route_is_config_driven():
    """Routing thresholds from config, not hardcoded."""
    cfg = SupervisorSettings(
        fast_path_autoclose_severities=["low", "medium"],
        fast_path_critical_severities=["high", "critical"],
    )
    assert route_grounded(_incident(Severity.MEDIUM), cfg) == _ROUTE_NOISE
    assert route_grounded(_incident(Severity.HIGH), cfg) == _ROUTE_CRITICAL


# ---------------------------------------------------------------------------
# Fast-path: zero stage calls (SC-003)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_low_severity_resolves_with_zero_stage_calls():
    """Obvious noise → resolved with NO stage invocations (SC-003)."""
    calls: list[str] = []

    async def should_not_call(inc):
        calls.append("called")
        return StageResult(stage=StageName.TRIAGE, outcome=StageOutcome.RESOLVED)

    incident = _incident(Severity.LOW)
    repo = FakeRepo(incident)
    sv = _make_supervisor({StageName.TRIAGE: should_not_call})
    await sv.run_incident(incident.id, repo)

    final = await repo.get(incident.id)
    assert final.status == IncidentStatus.RESOLVED
    assert final.disposition == "auto_resolved_noise"
    assert calls == []  # zero stage calls


@pytest.mark.asyncio
async def test_critical_severity_goes_straight_to_responding():
    """Obvious critical → responding (skips triage/enrichment)."""
    calls: list[str] = []

    async def triage(inc):
        calls.append("triage")
        return StageResult(stage=StageName.TRIAGE, outcome=StageOutcome.RESOLVED)

    async def response(inc):
        calls.append("response")
        return StageResult(stage=StageName.RESPONSE, outcome=StageOutcome.RESOLVED)

    incident = _incident(Severity.CRITICAL)
    repo = FakeRepo(incident)
    sv = _make_supervisor({StageName.TRIAGE: triage, StageName.RESPONSE: response})
    await sv.run_incident(incident.id, repo)

    final = await repo.get(incident.id)
    assert final.status == IncidentStatus.RESOLVED
    assert "triage" not in calls
    assert "response" in calls


# ---------------------------------------------------------------------------
# Adaptive depth: enrichment only runs if triage ADVANCEs (FR-006)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_triage_resolved_skips_enrichment_and_response():
    """Triage returning RESOLVED → enrichment and response NOT called (adaptive depth)."""
    calls: list[str] = []

    async def triage(inc):
        calls.append("triage")
        return StageResult(stage=StageName.TRIAGE, outcome=StageOutcome.RESOLVED)

    async def enrichment(inc):
        calls.append("enrichment")
        return StageResult(stage=StageName.ENRICHMENT, outcome=StageOutcome.ADVANCE)

    incident = _incident(Severity.MEDIUM)
    repo = FakeRepo(incident)
    sv = _make_supervisor({StageName.TRIAGE: triage, StageName.ENRICHMENT: enrichment})
    await sv.run_incident(incident.id, repo)

    assert calls == ["triage"]


@pytest.mark.asyncio
async def test_triage_advance_triggers_enrichment():
    """Triage ADVANCE → enrichment is called (adaptive depth)."""
    calls: list[str] = []

    async def triage(inc):
        calls.append("triage")
        return StageResult(stage=StageName.TRIAGE, outcome=StageOutcome.ADVANCE)

    async def enrichment(inc):
        calls.append("enrichment")
        return StageResult(stage=StageName.ENRICHMENT, outcome=StageOutcome.RESOLVED)

    incident = _incident(Severity.MEDIUM)
    repo = FakeRepo(incident)
    sv = _make_supervisor({StageName.TRIAGE: triage, StageName.ENRICHMENT: enrichment})
    await sv.run_incident(incident.id, repo)

    assert calls == ["triage", "enrichment"]


@pytest.mark.asyncio
async def test_severity_defaulted_routes_to_triage():
    """An incident with severity_defaulted is never fast-pathed — goes to triage."""
    calls: list[str] = []

    async def triage(inc):
        calls.append("triage")
        return StageResult(stage=StageName.TRIAGE, outcome=StageOutcome.RESOLVED)

    incident = _incident(Severity.LOW, flags=["severity_defaulted"])
    repo = FakeRepo(incident)
    sv = _make_supervisor({StageName.TRIAGE: triage})
    await sv.run_incident(incident.id, repo)

    assert "triage" in calls
    final = await repo.get(incident.id)
    assert final.disposition != "auto_resolved_noise"
