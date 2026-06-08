"""T010 — supervisor forwards StageResult.evidence_patch to advance_status (FR-010)."""

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
        correlation_id="corr-ep",
        dedup_fingerprint="fp-ep",
        source="wazuh",
        raw_alert={},
    )


class FakeRepo:
    def __init__(self, incident: Incident) -> None:
        self._incident = incident.model_copy(deep=True)
        self.advance_calls: list[dict] = []

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
        self.advance_calls.append(
            {"from": expected, "to": target, "disposition": disposition, "evidence_patch": evidence_patch}
        )
        self._incident = self._incident.model_copy(update={"status": target})
        return True


def _supervisor(stages: dict) -> Supervisor:
    return Supervisor(
        stages=stages,
        cfg=SupervisorSettings(),
        tracer=build_tracer(exporter=None),
    )


@pytest.mark.asyncio
async def test_supervisor_forwards_evidence_patch_on_advance():
    """A stage returning evidence_patch → supervisor passes it to advance_status."""
    patch = {"triage": {"verdict": "real", "confidence": 0.9}}
    incident = _incident()
    repo = FakeRepo(incident)

    async def fake_triage(_inc: Incident) -> StageResult:
        return StageResult(
            stage=StageName.TRIAGE,
            outcome=StageOutcome.ADVANCE,
            tokens_consumed=10,
            evidence_patch=patch,
        )

    # Enrichment and response stubs so the loop can terminate
    async def fake_enrichment(_inc: Incident) -> StageResult:
        return StageResult(stage=StageName.ENRICHMENT, outcome=StageOutcome.ADVANCE, tokens_consumed=0)

    async def fake_response(_inc: Incident) -> StageResult:
        return StageResult(stage=StageName.RESPONSE, outcome=StageOutcome.RESOLVED, tokens_consumed=0)

    sup = _supervisor({
        StageName.TRIAGE: fake_triage,
        StageName.ENRICHMENT: fake_enrichment,
        StageName.RESPONSE: fake_response,
    })
    await sup.run_incident(incident.id, repo)

    # The triage→enriching transition must have carried the patch
    triage_advance = next(
        c for c in repo.advance_calls if c["from"] == IncidentStatus.TRIAGING
    )
    assert triage_advance["evidence_patch"] == patch


@pytest.mark.asyncio
async def test_supervisor_passes_none_patch_when_stage_returns_none():
    """A stage returning no evidence_patch → advance_status receives evidence_patch=None."""
    incident = _incident()
    repo = FakeRepo(incident)

    async def fake_triage(_inc: Incident) -> StageResult:
        return StageResult(
            stage=StageName.TRIAGE,
            outcome=StageOutcome.ESCALATE,
            tokens_consumed=5,
            disposition="escalated_triage",
            evidence_patch=None,
        )

    sup = _supervisor({
        StageName.TRIAGE: fake_triage,
        StageName.ENRICHMENT: lambda _: None,  # type: ignore[arg-type]
        StageName.RESPONSE: lambda _: None,    # type: ignore[arg-type]
    })
    await sup.run_incident(incident.id, repo)

    triage_advance = next(
        c for c in repo.advance_calls if c["from"] == IncidentStatus.TRIAGING
    )
    assert triage_advance["evidence_patch"] is None
