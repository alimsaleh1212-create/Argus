"""T016/T022/T027 — Triage e2e: ambiguous incidents through supervisor→triage with faked LLM.

Covers:
- T016/US1: real→enriching, noise→resolved, evidence.triage persisted, no further stage after noise
- T022/US2: uncertain/low-confidence → escalated (escalated_triage)
- T027/US3: transient error → retry→escalated; malformed output → escalated; worker keeps running
"""

from __future__ import annotations

import json
import uuid

import pytest

from backend.domain.incident import Incident, IncidentStatus, Severity
from backend.domain.llm import LlmError, LlmErrorKind, LlmResponse, ProviderId, StopReason, TokenUsage
from backend.domain.pipeline import StageName, StageOutcome, StageResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ambiguous_incident(severity: Severity = Severity.MEDIUM) -> Incident:
    return Incident(
        id=uuid.uuid4(),
        status=IncidentStatus.GROUNDED,
        severity=severity,
        correlation_id="e2e-triage",
        dedup_fingerprint=f"fp-e2e-{uuid.uuid4()}",
        source="wazuh",
        raw_alert={},
        evidence={
            "verdict": "suspicious",
            "severity": severity.value,
            "normalized_event": {"rule_id": "5763", "rule_description": "SSH brute force"},
            "summary": "Multiple failed SSH logins followed by success.",
            "flags": [],
        },
    )


def _llm_response(verdict: str, confidence: float) -> LlmResponse:
    return LlmResponse(
        content=json.dumps({
            "verdict": verdict,
            "confidence": confidence,
            "rationale": f"Evidence indicates {verdict}.",
            "cited_evidence": ["rule_description"],
        }),
        usage=TokenUsage(prompt_tokens=30, completion_tokens=20),
        model="fake",
        provider=ProviderId.GEMINI,
        stop_reason=StopReason.END_TURN,
    )


class FakeLlm:
    def __init__(self, responses: list[LlmResponse | Exception]) -> None:
        self._responses = iter(responses)
        self.call_count = 0

    async def generate(self, request: object, *, correlation_id: str | None = None) -> LlmResponse:
        self.call_count += 1
        resp = next(self._responses)
        if isinstance(resp, Exception):
            raise resp
        return resp


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
        self.advances.append({
            "from": expected,
            "to": target,
            "disposition": disposition,
            "evidence_patch": evidence_patch,
        })
        self._incident = self._incident.model_copy(update={"status": target, "disposition": disposition})
        return True


def _make_supervisor(triage_handler, max_stage_retries: int = 1):
    from backend.agents.enrichment import run_enrichment
    from backend.agents.response import run_response
    from backend.infra.config import SupervisorSettings
    from backend.infra.tracing import build_tracer
    from backend.services.supervisor import Supervisor

    return Supervisor(
        stages={
            StageName.TRIAGE: triage_handler,
            StageName.ENRICHMENT: run_enrichment,
            StageName.RESPONSE: run_response,
        },
        cfg=SupervisorSettings(max_stage_retries=max_stage_retries),
        tracer=build_tracer(exporter=None),
    )


# ---------------------------------------------------------------------------
# T016 — US1: real→enriching, noise→resolved
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_real_incident_routes_to_enriching():
    """Real verdict → triage advances to enriching; evidence.triage persisted (US1)."""
    from backend.agents.triage import make_triage_handler
    from backend.infra.config import TriageSettings

    llm = FakeLlm([_llm_response("real", 0.9)])
    handler = make_triage_handler(llm, TriageSettings())

    incident = _ambiguous_incident()
    repo = FakeRepo(incident)

    # Use escalating enrichment stub so pipeline stops at enriching→escalated
    # (we only care about the triage→enriching transition, not the full run)
    async def _halt_enrichment(inc: Incident) -> StageResult:
        return StageResult(stage=StageName.ENRICHMENT, outcome=StageOutcome.ESCALATE, tokens_consumed=0)

    from backend.agents.response import run_response
    from backend.infra.config import SupervisorSettings
    from backend.infra.tracing import build_tracer
    from backend.services.supervisor import Supervisor

    sup = Supervisor(
        stages={
            StageName.TRIAGE: handler,
            StageName.ENRICHMENT: _halt_enrichment,
            StageName.RESPONSE: run_response,
        },
        cfg=SupervisorSettings(),
        tracer=build_tracer(exporter=None),
    )
    await sup.run_incident(incident.id, repo)

    # Triage must have advanced to enriching (the triage→enriching transition happened)
    triage_advance = next(
        (c for c in repo.advances if c["from"] == IncidentStatus.TRIAGING), None
    )
    assert triage_advance is not None, "Triage→enriching transition not found"
    assert triage_advance["to"] == IncidentStatus.ENRICHING
    assert triage_advance["evidence_patch"] is not None
    assert triage_advance["evidence_patch"]["triage"]["verdict"] == "real"


@pytest.mark.asyncio
async def test_noise_incident_resolves_with_no_further_stages():
    """Noise → resolved (auto_resolved_triage); enrichment and response never run (FR-014)."""
    from backend.agents.triage import make_triage_handler
    from backend.infra.config import TriageSettings

    enrichment_calls = []

    async def _tracking_enrichment(inc: Incident) -> StageResult:
        enrichment_calls.append(inc.id)
        return StageResult(stage=StageName.ENRICHMENT, outcome=StageOutcome.ADVANCE, tokens_consumed=0)

    llm = FakeLlm([_llm_response("noise", 0.85)])
    handler = make_triage_handler(llm, TriageSettings())

    incident = _ambiguous_incident()
    repo = FakeRepo(incident)

    from backend.agents.response import run_response
    from backend.infra.config import SupervisorSettings
    from backend.infra.tracing import build_tracer
    from backend.services.supervisor import Supervisor

    sup = Supervisor(
        stages={
            StageName.TRIAGE: handler,
            StageName.ENRICHMENT: _tracking_enrichment,
            StageName.RESPONSE: run_response,
        },
        cfg=SupervisorSettings(),
        tracer=build_tracer(exporter=None),
    )
    await sup.run_incident(incident.id, repo)

    assert repo._incident.status == IncidentStatus.RESOLVED
    assert repo._incident.disposition == "auto_resolved_triage"
    assert enrichment_calls == [], "Enrichment must NOT run after triage resolves (adaptive depth)"


# ---------------------------------------------------------------------------
# T022 — US2: uncertain / low-confidence → escalated
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_uncertain_verdict_escalates():
    from backend.agents.triage import make_triage_handler
    from backend.infra.config import TriageSettings

    llm = FakeLlm([_llm_response("uncertain", 0.4)])
    handler = make_triage_handler(llm, TriageSettings())

    incident = _ambiguous_incident()
    repo = FakeRepo(incident)
    sup = _make_supervisor(handler)
    await sup.run_incident(incident.id, repo)

    assert repo._incident.status == IncidentStatus.ESCALATED
    assert repo._incident.disposition == "escalated_triage"
    # evidence_patch should still record the judgment
    triage_advance = next(
        (c for c in repo.advances if c["from"] == IncidentStatus.TRIAGING), None
    )
    assert triage_advance is not None
    assert triage_advance["evidence_patch"]["triage"]["verdict"] == "uncertain"


@pytest.mark.asyncio
async def test_low_confidence_escalates():
    from backend.agents.triage import make_triage_handler
    from backend.infra.config import TriageSettings

    llm = FakeLlm([_llm_response("real", 0.3)])  # below advance_min=0.6
    handler = make_triage_handler(llm, TriageSettings())

    incident = _ambiguous_incident()
    repo = FakeRepo(incident)
    sup = _make_supervisor(handler)
    await sup.run_incident(incident.id, repo)

    assert repo._incident.status == IncidentStatus.ESCALATED
    assert repo._incident.disposition == "escalated_triage"


# ---------------------------------------------------------------------------
# T027 — US3: failure injection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_transient_error_retries_then_escalates():
    """Transient LLM error → supervisor retries → escalated after max_stage_retries."""
    from backend.agents.triage import make_triage_handler
    from backend.infra.config import TriageSettings

    # Always transient — never succeeds
    llm = FakeLlm([
        LlmError(kind=LlmErrorKind.TRANSIENT, message="timeout"),
        LlmError(kind=LlmErrorKind.TRANSIENT, message="timeout"),
    ])
    handler = make_triage_handler(llm, TriageSettings())

    incident = _ambiguous_incident()
    repo = FakeRepo(incident)
    sup = _make_supervisor(handler, max_stage_retries=1)
    await sup.run_incident(incident.id, repo)

    # Must escalate (not crash), and NOT be in enriching/resolved
    assert repo._incident.status == IncidentStatus.ESCALATED
    assert repo._incident.status != IncidentStatus.ENRICHING
    assert repo._incident.status != IncidentStatus.RESOLVED


@pytest.mark.asyncio
async def test_malformed_response_escalates_not_resolves():
    """Malformed JSON from LLM → escalated; never auto-resolved or advanced."""
    from backend.agents.triage import make_triage_handler
    from backend.infra.config import TriageSettings

    class MalformedLlm:
        async def generate(self, request: object, *, correlation_id: str | None = None) -> LlmResponse:
            return LlmResponse(
                content="not valid json {{{",
                usage=TokenUsage(prompt_tokens=5, completion_tokens=5),
                model="fake",
                provider=ProviderId.GEMINI,
                stop_reason=StopReason.END_TURN,
            )

    handler = make_triage_handler(MalformedLlm(), TriageSettings())
    incident = _ambiguous_incident()
    repo = FakeRepo(incident)
    sup = _make_supervisor(handler)
    await sup.run_incident(incident.id, repo)

    assert repo._incident.status == IncidentStatus.ESCALATED
    assert repo._incident.status not in (IncidentStatus.ENRICHING, IncidentStatus.RESOLVED)


@pytest.mark.asyncio
async def test_second_incident_processed_after_first_fails():
    """Worker-level: a failed triage on incident 1 doesn't prevent incident 2 from succeeding."""
    from backend.agents.triage import make_triage_handler
    from backend.infra.config import TriageSettings

    # First call: malformed; second call: real
    class SequencedLlm:
        def __init__(self) -> None:
            self._calls = 0

        async def generate(self, request: object, *, correlation_id: str | None = None) -> LlmResponse:
            self._calls += 1
            if self._calls == 1:
                return LlmResponse(
                    content="not json",
                    usage=TokenUsage(prompt_tokens=5, completion_tokens=5),
                    model="fake", provider=ProviderId.GEMINI, stop_reason=StopReason.END_TURN,
                )
            return _llm_response("real", 0.9)

    handler = make_triage_handler(SequencedLlm(), TriageSettings())

    inc1 = _ambiguous_incident()
    inc2 = _ambiguous_incident()
    repo1, repo2 = FakeRepo(inc1), FakeRepo(inc2)
    sup = _make_supervisor(handler)

    await sup.run_incident(inc1.id, repo1)
    await sup.run_incident(inc2.id, repo2)

    assert repo1._incident.status == IncidentStatus.ESCALATED
    # Inc2 must have passed through triage with a real verdict (ADVANCE)
    triage_advance2 = next(
        (c for c in repo2.advances if c["from"] == IncidentStatus.TRIAGING), None
    )
    assert triage_advance2 is not None, "Inc2 must pass through triage"
    assert triage_advance2["to"] == IncidentStatus.ENRICHING
