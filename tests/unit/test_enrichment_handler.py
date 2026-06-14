"""Unit tests — make_enrichment_handler with fake LlmClient + fake retrievers (T012)."""

from __future__ import annotations

import json
import uuid
import uuid as _uuid
from datetime import UTC, datetime

import pytest

from backend.agents.enrichment import make_enrichment_handler
from backend.domain.corpus import (
    EntityRef,
    ReferenceCorpusEntry,
    ReferenceHit,
    ReferenceKind,
)
from backend.domain.incident import Incident, IncidentStatus, Severity
from backend.domain.llm import LlmResponse, ProviderId, StopReason, TokenUsage
from backend.domain.memory import EpisodeQuery, FactState, MemoryHit
from backend.domain.pipeline import StageName, StageOutcome
from backend.infra.config import EnrichmentSettings


def _incident() -> Incident:
    return Incident(
        id=uuid.uuid4(),
        status=IncidentStatus.ENRICHING,
        severity=Severity.HIGH,
        correlation_id="corr-enrichment-01",
        dedup_fingerprint="fp-enrich-01",
        source="wazuh",
        raw_alert={},
        evidence={
            "verdict": "suspicious",
            "severity": "high",
            "normalized_event": {
                "rule_id": "100001",
                "rule_description": "T1059 execution on host",
                "rule_groups": ["attack", "T1059"],
                "agent_name": "web-01",
                "agent_ip": "10.0.0.1",
                "fields": {"md5": "deadbeef"},
            },
            "summary": "Possible command execution detected on web-01",
            "triage": {"verdict": "real", "confidence": 0.9},
        },
    )


def _enrich_response(assessment: str = "confirmed", confidence: float = 0.85) -> LlmResponse:
    payload = {
        "assessment": assessment,
        "confidence": confidence,
        "correlation_summary": "Corpus maps T1059; prior incident on web-01 three days ago.",
        "external_findings": ["T1059 technique found in reference corpus"],
        "internal_findings": ["Prior incident on web-01 with same disposition"],
        "cited_evidence": ["corpus:T1059", "prior_incident:abc123"],
    }
    return LlmResponse(
        content=json.dumps(payload),
        usage=TokenUsage(prompt_tokens=100, completion_tokens=50),
        model="test-model",
        provider=ProviderId.GEMINI,
        stop_reason=StopReason.END_TURN,
    )


class FakeLlm:
    def __init__(self, response: LlmResponse) -> None:
        self._response = response
        self.calls: list = []

    async def generate(self, request, *, correlation_id=None):
        self.calls.append({"request": request, "correlation_id": correlation_id})
        return self._response


class FakeCorpus:
    def __init__(self, hits: list[ReferenceHit] | None = None):
        self._hits = hits or [
            ReferenceHit(
                entry=ReferenceCorpusEntry(
                    kind=ReferenceKind.TECHNIQUE,
                    key="T1059",
                    title="Command and Scripting Interpreter",
                    content="Adversaries may abuse command and script interpreters.",
                ),
                relevance=0.9,
                matched_on="technique",
            )
        ]

    async def search_reference(self, query, *, k: int) -> list:
        return self._hits


class FakeMemory:
    def __init__(self):
        self._hits = [
            MemoryHit(
                incident_id=_uuid.UUID("00000000-0000-0000-0000-000000000001"),
                summary="Previous T1059 on web-01",
                disposition="escalated",
                observed_at=datetime.now(UTC),
                relevance=0.85,
            )
        ]

    async def search_similar(self, query: EpisodeQuery, *, k: int) -> list:
        return self._hits

    async def query_fact(self, entity: EntityRef, fact_type: str, *, as_of=None) -> FactState:
        return FactState()

    async def write_episode(self, episode) -> None:
        pass

    async def write_fact(self, fact) -> None:
        pass


@pytest.mark.asyncio
async def test_confirmed_correlated_response_advances():
    llm = FakeLlm(_enrich_response("confirmed", 0.85))
    handler = make_enrichment_handler(llm, FakeCorpus(), FakeMemory(), None, EnrichmentSettings())
    result = await handler(_incident())

    assert result.stage == StageName.ENRICHMENT
    assert result.outcome == StageOutcome.ADVANCE
    assert result.tokens_consumed > 0


@pytest.mark.asyncio
async def test_evidence_patch_has_correlation_summary():
    llm = FakeLlm(_enrich_response("confirmed", 0.85))
    handler = make_enrichment_handler(llm, FakeCorpus(), FakeMemory(), None, EnrichmentSettings())
    result = await handler(_incident())

    patch = result.evidence_patch["enrichment"]
    assert patch["correlation_summary"]


@pytest.mark.asyncio
async def test_evidence_patch_has_external_and_internal_findings():
    llm = FakeLlm(_enrich_response("confirmed", 0.85))
    handler = make_enrichment_handler(llm, FakeCorpus(), FakeMemory(), None, EnrichmentSettings())
    result = await handler(_incident())

    patch = result.evidence_patch["enrichment"]
    assert len(patch["external_findings"]) >= 1
    assert len(patch["internal_findings"]) >= 1


@pytest.mark.asyncio
async def test_exactly_one_llm_call():
    llm = FakeLlm(_enrich_response("confirmed", 0.85))
    handler = make_enrichment_handler(llm, FakeCorpus(), FakeMemory(), None, EnrichmentSettings())
    await handler(_incident())
    assert len(llm.calls) == 1


@pytest.mark.asyncio
async def test_tokens_consumed_prompt_plus_completion():
    llm = FakeLlm(_enrich_response("confirmed", 0.85))
    handler = make_enrichment_handler(llm, FakeCorpus(), FakeMemory(), None, EnrichmentSettings())
    result = await handler(_incident())
    assert result.tokens_consumed == 150  # 100 + 50
