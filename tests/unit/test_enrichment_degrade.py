"""Unit tests — best-effort retrieval degradation (T022)."""

from __future__ import annotations

import json
import uuid

import pytest

from backend.agents.enrichment import make_enrichment_handler
from backend.domain.incident import Incident, IncidentStatus, Severity
from backend.domain.llm import LlmResponse, ProviderId, StopReason, TokenUsage
from backend.domain.memory import EpisodeQuery, FactState
from backend.infra.config import EnrichmentSettings


def _incident() -> Incident:
    return Incident(
        id=uuid.uuid4(),
        status=IncidentStatus.ENRICHING,
        severity=Severity.MEDIUM,
        correlation_id="corr-degrade",
        dedup_fingerprint="fp-degrade",
        source="wazuh",
        raw_alert={},
        evidence={
            "verdict": "suspicious",
            "severity": "medium",
            "normalized_event": {
                "rule_id": "100",
                "rule_description": "Test event",
                "rule_groups": [],
                "agent_name": "host-01",
                "agent_ip": "10.0.0.1",
                "fields": {},
            },
            "summary": "Test incident",
        },
    )


def _ok_response() -> LlmResponse:
    return LlmResponse(
        content=json.dumps(
            {
                "assessment": "confirmed",
                "confidence": 0.8,
                "correlation_summary": "Context partially available, signals suggest real threat.",
                "external_findings": [],
                "internal_findings": [],
                "cited_evidence": ["rule_id=100"],
            }
        ),
        usage=TokenUsage(prompt_tokens=30, completion_tokens=20),
        model="fake",
        provider=ProviderId.GEMINI,
        stop_reason=StopReason.END_TURN,
    )


class FakeLlm:
    def __init__(self, response):
        self._response = response

    async def generate(self, request, *, correlation_id=None):
        return self._response


@pytest.mark.asyncio
async def test_no_memory_no_intel_still_returns_report():
    """memory=None + intel=None + empty corpus → handler completes (best-effort)."""
    llm = FakeLlm(_ok_response())
    handler = make_enrichment_handler(llm, None, None, None, EnrichmentSettings())
    result = await handler(_incident())
    assert result.evidence_patch is not None
    assert "enrichment" in result.evidence_patch


@pytest.mark.asyncio
async def test_raising_corpus_swallowed_to_empty():
    """A corpus that raises → that source is empty; stage still completes."""

    class RaisingCorpus:
        async def search_reference(self, query, *, k: int):
            raise ConnectionError("postgres down")

    llm = FakeLlm(_ok_response())
    handler = make_enrichment_handler(llm, RaisingCorpus(), None, None, EnrichmentSettings())
    result = await handler(_incident())
    assert "enrichment" in result.evidence_patch


@pytest.mark.asyncio
async def test_raising_memory_swallowed_to_empty():
    """A memory store that raises → that source is empty; stage still completes."""

    class RaisingMemory:
        async def search_similar(self, query: EpisodeQuery, *, k: int):
            raise ConnectionError("neo4j down")

        async def query_fact(self, entity, fact_type, *, as_of=None) -> FactState:
            raise ConnectionError("neo4j down")

        async def write_episode(self, ep):
            pass

        async def write_fact(self, fact):
            pass

    llm = FakeLlm(_ok_response())
    handler = make_enrichment_handler(llm, None, RaisingMemory(), None, EnrichmentSettings())
    result = await handler(_incident())
    assert "enrichment" in result.evidence_patch


@pytest.mark.asyncio
async def test_both_retrieval_sources_raising_still_completes():
    """Both corpus and memory raise → empty context; stage still makes the call and completes."""

    class RaisingCorpus:
        async def search_reference(self, query, *, k: int):
            raise RuntimeError("corpus error")

    class RaisingMemory:
        async def search_similar(self, query, *, k):
            raise RuntimeError("memory error")

        async def query_fact(self, entity, fact_type, *, as_of=None):
            raise RuntimeError("memory error")

        async def write_episode(self, ep):
            pass

        async def write_fact(self, fact):
            pass

    llm = FakeLlm(_ok_response())
    handler = make_enrichment_handler(
        llm, RaisingCorpus(), RaisingMemory(), None, EnrichmentSettings()
    )
    result = await handler(_incident())
    assert "enrichment" in result.evidence_patch
