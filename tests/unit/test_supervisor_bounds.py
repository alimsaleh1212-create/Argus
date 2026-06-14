"""Unit tests — T021: supervisor step/token cap enforcement (SC-002).

Verifies the hard caps → escalated, never an unbounded loop.
"""

from __future__ import annotations

import uuid

import pytest

from backend.domain.incident import Incident, IncidentStatus, Severity
from backend.domain.pipeline import StageName, StageOutcome, StageResult
from backend.infra.config import SupervisorSettings
from backend.infra.tracing import build_tracer
from backend.services.supervisor import Supervisor


def _incident(severity: Severity = Severity.MEDIUM) -> Incident:
    return Incident(
        id=uuid.uuid4(),
        status=IncidentStatus.GROUNDED,
        severity=severity,
        correlation_id="corr-bounds",
        dedup_fingerprint="fp-bounds",
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
        self, incident_id, *, expected, target, disposition=None, evidence_patch=None
    ) -> bool:
        if self._incident.id != incident_id or self._incident.status != expected:
            return False
        self.advances.append({"from": expected, "to": target, "disposition": disposition})
        self._incident = self._incident.model_copy(
            update={"status": target, "disposition": disposition}
        )
        return True


# ---------------------------------------------------------------------------
# Step cap
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_step_cap_escalates():
    """When stages keep returning ADVANCE past max_steps, the incident is escalated."""
    call_count = 0

    async def always_advance(inc):
        nonlocal call_count
        call_count += 1
        # Return ADVANCE from triage and enrichment in a cycle
        if inc.status == IncidentStatus.TRIAGING or True:
            return StageResult(stage=StageName.TRIAGE, outcome=StageOutcome.ADVANCE)

    # Cap at 2 steps so the loop hits it quickly
    cfg = SupervisorSettings(max_steps=2, max_tokens=40_000, max_stage_retries=0)

    async def triage(inc):
        return StageResult(stage=StageName.TRIAGE, outcome=StageOutcome.ADVANCE)

    async def enrichment(inc):
        return StageResult(stage=StageName.ENRICHMENT, outcome=StageOutcome.ADVANCE)

    async def response(inc):
        return StageResult(
            stage=StageName.RESPONSE, outcome=StageOutcome.ADVANCE
        )  # illegal but tests cap

    incident = _incident()
    repo = FakeRepo(incident)
    sv = Supervisor(
        stages={
            StageName.TRIAGE: triage,
            StageName.ENRICHMENT: enrichment,
            StageName.RESPONSE: response,
        },
        cfg=cfg,
        tracer=build_tracer(exporter=None),
    )
    await sv.run_incident(incident.id, repo)

    final = await repo.get(incident.id)
    assert final.status == IncidentStatus.ESCALATED
    assert final.disposition == "escalated_step_cap"


@pytest.mark.asyncio
async def test_step_cap_never_loops_unboundedly():
    """The loop terminates; it never runs forever past max_steps."""
    invocations = 0

    async def triage(inc):
        nonlocal invocations
        invocations += 1
        return StageResult(stage=StageName.TRIAGE, outcome=StageOutcome.ADVANCE)

    async def enrichment(inc):
        nonlocal invocations
        invocations += 1
        return StageResult(stage=StageName.ENRICHMENT, outcome=StageOutcome.ADVANCE)

    async def response(inc):
        nonlocal invocations
        invocations += 1
        return StageResult(stage=StageName.RESPONSE, outcome=StageOutcome.ESCALATE)

    cfg = SupervisorSettings(max_steps=3, max_tokens=40_000)
    incident = _incident()
    repo = FakeRepo(incident)
    sv = Supervisor(
        stages={
            StageName.TRIAGE: triage,
            StageName.ENRICHMENT: enrichment,
            StageName.RESPONSE: response,
        },
        cfg=cfg,
        tracer=build_tracer(exporter=None),
    )
    await sv.run_incident(incident.id, repo)

    # invocations bounded by cap + normal depth
    assert invocations <= cfg.max_steps + 2


# ---------------------------------------------------------------------------
# Token cap
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_token_cap_escalates():
    """When accumulated tokens exceed max_tokens, the incident is escalated."""

    async def triage(inc):
        return StageResult(
            stage=StageName.TRIAGE, outcome=StageOutcome.ADVANCE, tokens_consumed=500
        )

    # Cap at 100 tokens → triage's 500 tokens push us over
    cfg = SupervisorSettings(max_steps=8, max_tokens=100)
    incident = _incident()
    repo = FakeRepo(incident)
    sv = Supervisor(
        stages={StageName.TRIAGE: triage},
        cfg=cfg,
        tracer=build_tracer(exporter=None),
    )
    await sv.run_incident(incident.id, repo)

    final = await repo.get(incident.id)
    assert final.status == IncidentStatus.ESCALATED
    assert final.disposition == "escalated_token_cap"


@pytest.mark.asyncio
async def test_token_cap_zero_stages_allowed_below_cap():
    """If the first stage stays under the token cap, the incident proceeds normally."""

    async def triage(inc):
        return StageResult(
            stage=StageName.TRIAGE, outcome=StageOutcome.RESOLVED, tokens_consumed=50
        )

    cfg = SupervisorSettings(max_steps=8, max_tokens=1000)
    incident = _incident()
    repo = FakeRepo(incident)
    sv = Supervisor(stages={StageName.TRIAGE: triage}, cfg=cfg, tracer=build_tracer(exporter=None))
    await sv.run_incident(incident.id, repo)

    final = await repo.get(incident.id)
    assert final.status == IncidentStatus.RESOLVED
    assert final.disposition != "escalated_token_cap"
