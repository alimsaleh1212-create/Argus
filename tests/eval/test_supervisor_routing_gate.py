"""Eval gate — T025: supervisor-routing gate (100% of fixtures reach expected disposition).

Provider-independent, deterministic, unit-tier. No DB, no LLM.
Per contracts/supervisor-routing-eval.md — threshold: 1.0 (all fixtures must pass).
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from backend.domain.incident import Incident, IncidentStatus, Severity
from backend.domain.pipeline import StageName, StageOutcome, StageResult, ToolError
from backend.infra.config import SupervisorSettings
from backend.infra.tracing import build_tracer
from backend.services.supervisor import Supervisor


# ---------------------------------------------------------------------------
# Fake repo
# ---------------------------------------------------------------------------


class FakeRepo:
    def __init__(self, incident: Incident) -> None:
        self._incident = incident.model_copy(deep=True)
        self.stage_calls: list[str] = []

    async def get(self, incident_id: uuid.UUID) -> Incident | None:
        if self._incident.id == incident_id:
            return self._incident
        return None

    async def advance_status(self, incident_id, *, expected, target, disposition=None, evidence_patch=None) -> bool:
        if self._incident.id != incident_id or self._incident.status != expected:
            return False
        self._incident = self._incident.model_copy(update={"status": target, "disposition": disposition})
        return True


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _make_incident(severity: str, flags: list[str] | None = None) -> Incident:
    evidence = {"flags": flags or [], "verdict": "test", "severity": severity,
                "normalized_event": {}, "summary": "eval"}
    return Incident(
        id=uuid.uuid4(),
        status=IncidentStatus.GROUNDED,
        severity=Severity(severity),
        correlation_id="corr-eval",
        dedup_fingerprint=f"fp-eval-{uuid.uuid4().hex}",
        source="wazuh",
        raw_alert={},
        evidence=evidence,
    )


def _make_supervisor(stages: dict, cfg: SupervisorSettings | None = None) -> Supervisor:
    return Supervisor(
        stages=stages,
        cfg=cfg or SupervisorSettings(),
        tracer=build_tracer(exporter=None),
    )


# ---------------------------------------------------------------------------
# Eval fixtures (from contracts/supervisor-routing-eval.md)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fixture_noise_low():
    """noise_low: severity=low → fast-path resolved, 0 stage calls."""
    calls: list[str] = []

    async def triage(inc):
        calls.append("triage")
        return StageResult(stage=StageName.TRIAGE, outcome=StageOutcome.RESOLVED)

    incident = _make_incident("low")
    repo = FakeRepo(incident)
    sv = _make_supervisor({StageName.TRIAGE: triage})
    await sv.run_incident(incident.id, repo)

    final = await repo.get(incident.id)
    assert final.status == IncidentStatus.RESOLVED, f"Expected resolved, got {final.status}"
    assert final.disposition == "auto_resolved_noise"
    assert calls == [], f"Expected 0 stage calls, got {calls}"


@pytest.mark.asyncio
async def test_fixture_critical_high():
    """critical_high: severity=critical → fast-path to responding (skip triage/enrichment)."""
    calls: list[str] = []

    async def triage(inc):
        calls.append("triage")
        return StageResult(stage=StageName.TRIAGE, outcome=StageOutcome.RESOLVED)

    async def response(inc):
        calls.append("response")
        return StageResult(stage=StageName.RESPONSE, outcome=StageOutcome.RESOLVED)

    incident = _make_incident("critical")
    repo = FakeRepo(incident)
    sv = _make_supervisor({StageName.TRIAGE: triage, StageName.RESPONSE: response})
    await sv.run_incident(incident.id, repo)

    final = await repo.get(incident.id)
    assert final.status == IncidentStatus.RESOLVED
    assert "triage" not in calls, f"Triage should not have been called for critical; calls={calls}"
    assert "response" in calls


@pytest.mark.asyncio
async def test_fixture_ambiguous_resolved_at_triage():
    """ambiguous_resolved_at_triage: medium, triage→RESOLVED → no enrichment."""
    calls: list[str] = []

    async def triage(inc):
        calls.append("triage")
        return StageResult(stage=StageName.TRIAGE, outcome=StageOutcome.RESOLVED)

    async def enrichment(inc):
        calls.append("enrichment")
        return StageResult(stage=StageName.ENRICHMENT, outcome=StageOutcome.ADVANCE)

    incident = _make_incident("medium")
    repo = FakeRepo(incident)
    sv = _make_supervisor({StageName.TRIAGE: triage, StageName.ENRICHMENT: enrichment})
    await sv.run_incident(incident.id, repo)

    final = await repo.get(incident.id)
    assert final.status == IncidentStatus.RESOLVED
    assert final.disposition == "auto_resolved_triage"
    assert "enrichment" not in calls, f"Enrichment should not have been called; calls={calls}"


@pytest.mark.asyncio
async def test_fixture_ambiguous_full_depth():
    """ambiguous_full_depth: high, triage→ADVANCE, enrichment→ADVANCE, response→RESOLVED."""
    calls: list[str] = []

    async def triage(inc):
        calls.append("triage")
        return StageResult(stage=StageName.TRIAGE, outcome=StageOutcome.ADVANCE)

    async def enrichment(inc):
        calls.append("enrichment")
        return StageResult(stage=StageName.ENRICHMENT, outcome=StageOutcome.ADVANCE)

    async def response(inc):
        calls.append("response")
        return StageResult(stage=StageName.RESPONSE, outcome=StageOutcome.RESOLVED)

    incident = _make_incident("high")
    repo = FakeRepo(incident)
    sv = _make_supervisor({StageName.TRIAGE: triage, StageName.ENRICHMENT: enrichment, StageName.RESPONSE: response})
    await sv.run_incident(incident.id, repo)

    final = await repo.get(incident.id)
    assert final.status == IncidentStatus.RESOLVED
    assert calls == ["triage", "enrichment", "response"], f"Expected full depth, got {calls}"


@pytest.mark.asyncio
async def test_fixture_destructive_parks():
    """destructive_parks: response returns NEEDS_APPROVAL → awaiting_approval."""
    async def response(inc):
        return StageResult(stage=StageName.RESPONSE, outcome=StageOutcome.NEEDS_APPROVAL)

    incident = _make_incident("critical")
    repo = FakeRepo(incident)
    sv = _make_supervisor({StageName.RESPONSE: response})
    await sv.run_incident(incident.id, repo)

    final = await repo.get(incident.id)
    assert final.status == IncidentStatus.AWAITING_APPROVAL
    assert final.disposition == "awaiting_approval_destructive"


@pytest.mark.asyncio
async def test_fixture_indeterminate_severity():
    """indeterminate_severity: severity_defaulted flag → triaging (never fast-pathed)."""
    calls: list[str] = []

    async def triage(inc):
        calls.append("triage")
        return StageResult(stage=StageName.TRIAGE, outcome=StageOutcome.RESOLVED)

    incident = _make_incident("low", flags=["severity_defaulted"])
    repo = FakeRepo(incident)
    sv = _make_supervisor({StageName.TRIAGE: triage})
    await sv.run_incident(incident.id, repo)

    assert "triage" in calls, "Expected triage to be called for indeterminate severity"
    final = await repo.get(incident.id)
    assert final.disposition != "auto_resolved_noise", "Should not fast-path with severity_defaulted"


@pytest.mark.asyncio
async def test_fixture_stage_error_escalates():
    """stage_error_escalates: triage raises non-retryable ToolError → escalated_stage_error."""
    async def error_triage(inc):
        raise ToolError(retryable=False, kind="inject_error")

    incident = _make_incident("medium")
    repo = FakeRepo(incident)
    sv = _make_supervisor({StageName.TRIAGE: error_triage})
    await sv.run_incident(incident.id, repo)

    final = await repo.get(incident.id)
    assert final.status == IncidentStatus.ESCALATED
    assert final.disposition == "escalated_stage_error"


@pytest.mark.asyncio
async def test_fixture_cap_breach_escalates():
    """cap_breach_escalates: stages loop ADVANCE past max_steps → escalated_step_cap."""
    async def triage(inc): return StageResult(stage=StageName.TRIAGE, outcome=StageOutcome.ADVANCE)
    async def enrichment(inc): return StageResult(stage=StageName.ENRICHMENT, outcome=StageOutcome.ADVANCE)
    async def response(inc): return StageResult(stage=StageName.RESPONSE, outcome=StageOutcome.ESCALATE)

    cfg = SupervisorSettings(max_steps=2)
    incident = _make_incident("medium")
    repo = FakeRepo(incident)
    sv = _make_supervisor(
        {StageName.TRIAGE: triage, StageName.ENRICHMENT: enrichment, StageName.RESPONSE: response},
        cfg=cfg,
    )
    await sv.run_incident(incident.id, repo)

    final = await repo.get(incident.id)
    assert final.status == IncidentStatus.ESCALATED
    assert final.disposition == "escalated_step_cap"
