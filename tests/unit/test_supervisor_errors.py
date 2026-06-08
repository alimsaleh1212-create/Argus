"""Unit tests — T022: supervisor retry/degradation under stage failures (SC-004).

Verifies:
- Retryable ToolError: retried ≤ max_stage_retries, then escalated on exhaustion
- Non-retryable ToolError: escalated immediately (no retry)
- Unexpected exception: escalated immediately, worker survives
- Worker (run_incident) never propagates an exception out of its body
"""

from __future__ import annotations

import uuid

import pytest

from backend.domain.incident import Incident, IncidentStatus, Severity
from backend.domain.pipeline import StageName, StageOutcome, StageResult, ToolError
from backend.infra.config import SupervisorSettings
from backend.infra.tracing import build_tracer
from backend.services.supervisor import Supervisor


def _incident(severity: Severity = Severity.MEDIUM) -> Incident:
    return Incident(
        id=uuid.uuid4(),
        status=IncidentStatus.GROUNDED,
        severity=severity,
        correlation_id="corr-errors",
        dedup_fingerprint="fp-errors",
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


def _make_supervisor(stages: dict, cfg: SupervisorSettings | None = None) -> Supervisor:
    return Supervisor(
        stages=stages,
        cfg=cfg or SupervisorSettings(),
        tracer=build_tracer(exporter=None),
    )


# ---------------------------------------------------------------------------
# Retryable ToolError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retryable_error_is_retried():
    """A retryable ToolError causes the stage to be retried up to max_stage_retries."""
    attempts = 0

    async def flaky_triage(inc):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise ToolError(retryable=True, kind="timeout", detail="transient")
        return StageResult(stage=StageName.TRIAGE, outcome=StageOutcome.RESOLVED)

    cfg = SupervisorSettings(max_stage_retries=2)
    incident = _incident()
    repo = FakeRepo(incident)
    sv = _make_supervisor({StageName.TRIAGE: flaky_triage}, cfg=cfg)
    await sv.run_incident(incident.id, repo)

    # 3 attempts: 1 initial + 2 retries
    assert attempts == 3
    final = await repo.get(incident.id)
    assert final.status == IncidentStatus.RESOLVED


@pytest.mark.asyncio
async def test_retryable_error_exhaustion_escalates():
    """When all retries are exhausted, the incident is escalated."""
    attempts = 0

    async def always_fails(inc):
        nonlocal attempts
        attempts += 1
        raise ToolError(retryable=True, kind="timeout", detail="always fails")

    cfg = SupervisorSettings(max_stage_retries=2)
    incident = _incident()
    repo = FakeRepo(incident)
    sv = _make_supervisor({StageName.TRIAGE: always_fails}, cfg=cfg)
    await sv.run_incident(incident.id, repo)

    # max_stage_retries=2 → 3 total attempts (1+2)
    assert attempts == 3
    final = await repo.get(incident.id)
    assert final.status == IncidentStatus.ESCALATED
    assert final.disposition == "escalated_stage_error"


# ---------------------------------------------------------------------------
# Non-retryable ToolError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_non_retryable_error_escalates_immediately():
    """A non-retryable ToolError escalates without any retry."""
    attempts = 0

    async def bad_triage(inc):
        nonlocal attempts
        attempts += 1
        raise ToolError(retryable=False, kind="bad_playbook", detail="permanent")

    cfg = SupervisorSettings(max_stage_retries=3)
    incident = _incident()
    repo = FakeRepo(incident)
    sv = _make_supervisor({StageName.TRIAGE: bad_triage}, cfg=cfg)
    await sv.run_incident(incident.id, repo)

    assert attempts == 1  # no retry
    final = await repo.get(incident.id)
    assert final.status == IncidentStatus.ESCALATED
    assert final.disposition == "escalated_stage_error"


# ---------------------------------------------------------------------------
# Unexpected exception (not ToolError)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unexpected_exception_escalates_and_does_not_propagate():
    """An unexpected exception from a stage escalates the incident and never escapes run_incident."""
    async def broken_triage(inc):
        raise RuntimeError("unexpected crash")

    incident = _incident()
    repo = FakeRepo(incident)
    sv = _make_supervisor({StageName.TRIAGE: broken_triage})

    # Must NOT raise
    await sv.run_incident(incident.id, repo)

    final = await repo.get(incident.id)
    assert final.status == IncidentStatus.ESCALATED
    assert final.disposition == "escalated_stage_error"


@pytest.mark.asyncio
async def test_worker_survives_multiple_failures():
    """Successive run_incident calls for different incidents — a failure in one doesn't affect others."""

    async def bad_triage(inc):
        raise ToolError(retryable=False, kind="crash")

    async def good_triage(inc):
        return StageResult(stage=StageName.TRIAGE, outcome=StageOutcome.RESOLVED)

    sv = _make_supervisor({StageName.TRIAGE: bad_triage})
    sv_good = _make_supervisor({StageName.TRIAGE: good_triage})

    for _ in range(3):
        bad_inc = _incident()
        repo = FakeRepo(bad_inc)
        await sv.run_incident(bad_inc.id, repo)
        final = await repo.get(bad_inc.id)
        assert final.status == IncidentStatus.ESCALATED

    good_inc = _incident()
    repo2 = FakeRepo(good_inc)
    await sv_good.run_incident(good_inc.id, repo2)
    final2 = await repo2.get(good_inc.id)
    assert final2.status == IncidentStatus.RESOLVED
