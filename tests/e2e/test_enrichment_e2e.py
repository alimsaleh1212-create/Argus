"""Enrichment e2e tests — full-depth incidents through triage→enrichment with faked LLM.

Covers:
- T014/US1: real→enriching→responding; evidence.enrichment persisted with correlation_summary + findings
- T020/US2: (a) exonerating→resolved/auto_resolved_enrichment (no response stage); (b) conflicting→escalated
- T026/US3: transient error→escalated; malformed response→escalated; memory outage→still completes
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import pytest

from backend.agents.enrichment import make_enrichment_handler
from backend.domain.corpus import (
    ReferenceCorpusEntry,
    ReferenceHit,
    ReferenceKind,
)
from backend.domain.incident import Incident, IncidentStatus, Severity
from backend.domain.llm import (
    LlmError,
    LlmErrorKind,
    LlmResponse,
    ProviderId,
    StopReason,
    TokenUsage,
)
from backend.domain.memory import EpisodeQuery, FactState, MemoryHit
from backend.domain.pipeline import StageName, StageOutcome, StageResult
from backend.infra.config import EnrichmentSettings, TriageSettings

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _incident(severity: Severity = Severity.HIGH) -> Incident:
    return Incident(
        id=uuid.uuid4(),
        status=IncidentStatus.GROUNDED,
        severity=severity,
        correlation_id=f"corr-enr-e2e-{uuid.uuid4()}",
        dedup_fingerprint=f"fp-enr-{uuid.uuid4()}",
        source="wazuh",
        raw_alert={},
        evidence={
            "verdict": "suspicious",
            "severity": severity.value,
            "normalized_event": {
                "rule_id": "100001",
                "rule_description": "T1059 execution detected",
                "rule_groups": ["attack", "T1059"],
                "agent_name": "web-01",
                "agent_ip": "10.0.0.1",
                "fields": {"md5": "deadbeef"},
            },
            "summary": "Suspicious command execution on web-01",
            "flags": [],
        },
    )


def _triage_response(verdict: str = "real", confidence: float = 0.9) -> LlmResponse:
    return LlmResponse(
        content=json.dumps(
            {
                "verdict": verdict,
                "confidence": confidence,
                "rationale": f"Looks {verdict}.",
                "cited_evidence": ["rule_description"],
            }
        ),
        usage=TokenUsage(prompt_tokens=20, completion_tokens=10),
        model="fake",
        provider=ProviderId.GEMINI,
        stop_reason=StopReason.END_TURN,
    )


def _enrich_response(
    assessment: str = "confirmed",
    confidence: float = 0.85,
    external: list | None = None,
    internal: list | None = None,
) -> LlmResponse:
    return LlmResponse(
        content=json.dumps(
            {
                "assessment": assessment,
                "confidence": confidence,
                "correlation_summary": f"Cross-correlation shows {assessment} verdict.",
                "external_findings": external or ["T1059 in corpus"],
                "internal_findings": internal or ["Prior incident on web-01"],
                "cited_evidence": ["corpus:T1059", "prior:abc"],
            }
        ),
        usage=TokenUsage(prompt_tokens=50, completion_tokens=30),
        model="fake",
        provider=ProviderId.GEMINI,
        stop_reason=StopReason.END_TURN,
    )


class SequencedLlm:
    """LLM that returns responses from a queue (first for triage, then for enrichment)."""

    def __init__(self, responses: list) -> None:
        self._queue = list(responses)
        self.call_count = 0

    async def generate(self, request, *, correlation_id=None):
        self.call_count += 1
        if not self._queue:
            raise RuntimeError("No more fake responses")
        resp = self._queue.pop(0)
        if isinstance(resp, Exception):
            raise resp
        return resp


class FakeCorpus:
    async def search_reference(self, query, *, k: int) -> list:
        return [
            ReferenceHit(
                entry=ReferenceCorpusEntry(
                    kind=ReferenceKind.TECHNIQUE,
                    key="T1059",
                    title="Command and Scripting Interpreter",
                    content="Adversaries abuse interpreters.",
                ),
                relevance=0.9,
                matched_on="technique",
            )
        ]


class FakeMemory:
    def __init__(self, raise_on_search: bool = False) -> None:
        self._raise = raise_on_search

    async def search_similar(self, query: EpisodeQuery, *, k: int) -> list:
        if self._raise:
            raise ConnectionError("neo4j unavailable")
        return [
            MemoryHit(
                incident_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
                summary="Prior T1059 on web-01",
                disposition="escalated",
                observed_at=datetime.now(UTC),
                relevance=0.82,
            )
        ]

    async def query_fact(self, entity, fact_type: str, *, as_of=None) -> FactState:
        if self._raise:
            raise ConnectionError("neo4j unavailable")
        return FactState()

    async def write_episode(self, episode) -> None:
        pass

    async def write_fact(self, fact) -> None:
        pass


class FakeRepo:
    """Minimal fake incident repository supporting triage+enrichment transitions."""

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
        self.advances.append(
            {
                "from": expected,
                "to": target,
                "disposition": disposition,
                "evidence_patch": evidence_patch,
            }
        )
        updated_evidence = dict(self._incident.evidence or {})
        if evidence_patch:
            updated_evidence.update(evidence_patch)
        self._incident = self._incident.model_copy(
            update={
                "status": target,
                "disposition": disposition,
                "evidence": updated_evidence,
            }
        )
        return True


def _make_supervisor(
    triage_llm,
    enrich_llm,
    corpus=None,
    memory=None,
    intel=None,
    enrich_cfg: EnrichmentSettings | None = None,
    max_stage_retries: int = 1,
):
    from backend.agents.response import run_response
    from backend.agents.triage import make_triage_handler
    from backend.infra.config import SupervisorSettings
    from backend.infra.tracing import build_tracer
    from backend.services.supervisor import Supervisor

    triage_handler = make_triage_handler(triage_llm, TriageSettings())
    enrichment_handler = make_enrichment_handler(
        enrich_llm, corpus, memory, intel, enrich_cfg or EnrichmentSettings()
    )

    return Supervisor(
        stages={
            StageName.TRIAGE: triage_handler,
            StageName.ENRICHMENT: enrichment_handler,
            StageName.RESPONSE: run_response,
        },
        cfg=SupervisorSettings(max_stage_retries=max_stage_retries),
        tracer=build_tracer(exporter=None),
    )


# ---------------------------------------------------------------------------
# T014 — US1: full-depth incident advances to responding with evidence.enrichment persisted
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_confirmed_enrichment_advances_to_responding():
    """Real+confirmed → triage→enriching→responding; evidence.enrichment persisted (US1)."""
    llm = SequencedLlm(
        [
            _triage_response("real", 0.9),
            _enrich_response("confirmed", 0.85),
        ]
    )
    incident = _incident()
    repo = FakeRepo(incident)

    sup = _make_supervisor(llm, llm, corpus=FakeCorpus(), memory=FakeMemory())
    await sup.run_incident(incident.id, repo)

    # Response stub runs after enrichment ADVANCE and resolves the incident
    assert repo._incident.status == IncidentStatus.RESOLVED
    assert "enrichment" in repo._incident.evidence


@pytest.mark.asyncio
async def test_enrichment_evidence_has_correlation_summary():
    llm = SequencedLlm(
        [
            _triage_response("real", 0.9),
            _enrich_response("confirmed", 0.85),
        ]
    )
    incident = _incident()
    repo = FakeRepo(incident)
    sup = _make_supervisor(llm, llm, corpus=FakeCorpus(), memory=FakeMemory())
    await sup.run_incident(incident.id, repo)

    enr = repo._incident.evidence["enrichment"]
    assert enr["correlation_summary"]
    assert len(enr["external_findings"]) >= 1
    assert len(enr["internal_findings"]) >= 1


@pytest.mark.asyncio
async def test_response_stage_reached_after_enrichment():
    """Confirmed enrichment → RESPONDING status confirms response stage entry."""
    llm = SequencedLlm(
        [
            _triage_response("real", 0.9),
            _enrich_response("confirmed", 0.85),
        ]
    )
    incident = _incident()
    repo = FakeRepo(incident)
    sup = _make_supervisor(llm, llm, corpus=FakeCorpus(), memory=FakeMemory())
    await sup.run_incident(incident.id, repo)

    enrichment_advance = next(
        (c for c in repo.advances if c["from"] == IncidentStatus.ENRICHING), None
    )
    assert enrichment_advance is not None
    assert enrichment_advance["to"] == IncidentStatus.RESPONDING


# ---------------------------------------------------------------------------
# T020 — US2: exonerating→resolved; conflicting→escalated
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_benign_high_confidence_resolves_no_response_stage():
    """Benign enrichment (conf≥resolve_min) → resolved; response stage does NOT run (US2a)."""
    response_calls: list = []

    llm = SequencedLlm(
        [
            _triage_response("real", 0.9),
            _enrich_response("benign", 0.85),
        ]
    )
    incident = _incident()
    repo = FakeRepo(incident)

    from backend.agents.triage import make_triage_handler
    from backend.infra.config import SupervisorSettings
    from backend.infra.tracing import build_tracer
    from backend.services.supervisor import Supervisor

    triage_h = make_triage_handler(llm, TriageSettings())
    enrich_h = make_enrichment_handler(llm, FakeCorpus(), FakeMemory(), None, EnrichmentSettings())

    async def _tracking_response(inc: Incident) -> StageResult:
        response_calls.append(inc.id)
        return StageResult(
            stage=StageName.RESPONSE, outcome=StageOutcome.RESOLVED, tokens_consumed=0
        )

    sup = Supervisor(
        stages={
            StageName.TRIAGE: triage_h,
            StageName.ENRICHMENT: enrich_h,
            StageName.RESPONSE: _tracking_response,
        },
        cfg=SupervisorSettings(),
        tracer=build_tracer(exporter=None),
    )
    await sup.run_incident(incident.id, repo)

    assert repo._incident.status == IncidentStatus.RESOLVED
    assert repo._incident.disposition == "auto_resolved_enrichment"
    assert response_calls == [], "Response stage must NOT run after benign resolution"


@pytest.mark.asyncio
async def test_inconclusive_enrichment_escalates():
    """Inconclusive (conflicting evidence) → escalated/escalated_enrichment (US2b)."""
    llm = SequencedLlm(
        [
            _triage_response("real", 0.9),
            _enrich_response("inconclusive", 0.5),
        ]
    )
    incident = _incident()
    repo = FakeRepo(incident)
    sup = _make_supervisor(llm, llm, corpus=FakeCorpus(), memory=FakeMemory())
    await sup.run_incident(incident.id, repo)

    assert repo._incident.status == IncidentStatus.ESCALATED
    assert repo._incident.disposition == "escalated_enrichment"
    assert "enrichment" in repo._incident.evidence


@pytest.mark.asyncio
async def test_escalated_enrichment_records_evidence():
    """Escalated enrichment still persists evidence.enrichment rationale (US2b)."""
    llm = SequencedLlm(
        [
            _triage_response("real", 0.9),
            _enrich_response("inconclusive", 0.45),
        ]
    )
    incident = _incident()
    repo = FakeRepo(incident)
    sup = _make_supervisor(llm, llm, corpus=FakeCorpus(), memory=FakeMemory())
    await sup.run_incident(incident.id, repo)

    enrich_advance = next((c for c in repo.advances if c["from"] == IncidentStatus.ENRICHING), None)
    assert enrich_advance is not None
    assert enrich_advance["evidence_patch"]["enrichment"]["correlation_summary"]


# ---------------------------------------------------------------------------
# T026 — US3: failure injection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_transient_enrichment_error_escalates_after_retries():
    """Transient LLM error on enrichment → supervisor retries → escalated (US3)."""
    triage_llm = SequencedLlm([_triage_response("real", 0.9)])
    enrich_llm = SequencedLlm(
        [
            LlmError(kind=LlmErrorKind.TRANSIENT, message="timeout"),
            LlmError(kind=LlmErrorKind.TRANSIENT, message="timeout"),
        ]
    )
    incident = _incident()
    repo = FakeRepo(incident)
    sup = _make_supervisor(
        triage_llm, enrich_llm, corpus=FakeCorpus(), memory=FakeMemory(), max_stage_retries=1
    )
    await sup.run_incident(incident.id, repo)

    assert repo._incident.status == IncidentStatus.ESCALATED


@pytest.mark.asyncio
async def test_malformed_enrichment_response_escalates():
    """Malformed JSON from enrichment LLM → escalated; never auto-resolved (US3)."""
    triage_llm = SequencedLlm([_triage_response("real", 0.9)])

    class MalformedLlm:
        async def generate(self, request, *, correlation_id=None):
            return LlmResponse(
                content="not json {{{",
                usage=TokenUsage(prompt_tokens=10, completion_tokens=5),
                model="fake",
                provider=ProviderId.GEMINI,
                stop_reason=StopReason.END_TURN,
            )

    incident = _incident()
    repo = FakeRepo(incident)
    enrich_h = make_enrichment_handler(
        MalformedLlm(), FakeCorpus(), FakeMemory(), None, EnrichmentSettings()
    )

    from backend.agents.response import run_response
    from backend.agents.triage import make_triage_handler
    from backend.infra.config import SupervisorSettings
    from backend.infra.tracing import build_tracer
    from backend.services.supervisor import Supervisor

    sup = Supervisor(
        stages={
            StageName.TRIAGE: make_triage_handler(triage_llm, TriageSettings()),
            StageName.ENRICHMENT: enrich_h,
            StageName.RESPONSE: run_response,
        },
        cfg=SupervisorSettings(),
        tracer=build_tracer(exporter=None),
    )
    await sup.run_incident(incident.id, repo)

    assert repo._incident.status == IncidentStatus.ESCALATED
    assert repo._incident.status not in (IncidentStatus.RESOLVED, IncidentStatus.RESPONDING)


@pytest.mark.asyncio
async def test_memory_outage_enrichment_still_completes():
    """Memory unavailable mid-run → enrichment degrades to corpus-only; still advances (US3)."""
    llm = SequencedLlm(
        [
            _triage_response("real", 0.9),
            _enrich_response("confirmed", 0.85, internal=[]),
        ]
    )
    incident = _incident()
    repo = FakeRepo(incident)
    sup = _make_supervisor(
        llm,
        llm,
        corpus=FakeCorpus(),
        memory=FakeMemory(raise_on_search=True),  # memory raises on search
    )
    await sup.run_incident(incident.id, repo)

    # Stage should still complete (escalate or advance) — never fail-crash
    assert repo._incident.status in (
        IncidentStatus.RESPONDING,
        IncidentStatus.ESCALATED,
        IncidentStatus.RESOLVED,
    )


@pytest.mark.asyncio
async def test_second_incident_processed_after_first_fails():
    """Worker keeps consuming: a failed enrichment on inc1 doesn't prevent inc2 (US3)."""
    triage_llm1 = SequencedLlm([_triage_response("real", 0.9)])
    enrich_llm1 = SequencedLlm(
        [
            LlmError(kind=LlmErrorKind.TRANSIENT, message="oops"),
            LlmError(kind=LlmErrorKind.TRANSIENT, message="oops"),
        ]
    )
    triage_llm2 = SequencedLlm([_triage_response("real", 0.9)])
    enrich_llm2 = SequencedLlm([_enrich_response("confirmed", 0.85)])

    inc1, inc2 = _incident(), _incident()
    repo1, repo2 = FakeRepo(inc1), FakeRepo(inc2)

    sup1 = _make_supervisor(
        triage_llm1, enrich_llm1, corpus=FakeCorpus(), memory=FakeMemory(), max_stage_retries=1
    )
    sup2 = _make_supervisor(triage_llm2, enrich_llm2, corpus=FakeCorpus(), memory=FakeMemory())

    await sup1.run_incident(inc1.id, repo1)
    await sup2.run_incident(inc2.id, repo2)

    assert repo1._incident.status == IncidentStatus.ESCALATED
    # inc2 goes triage→enriching→responding→resolved (response stub resolves it)
    assert repo2._incident.status == IncidentStatus.RESOLVED
