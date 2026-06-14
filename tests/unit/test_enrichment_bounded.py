"""Unit tests — exactly one LLM call, token reporting, concurrency, indicator cap (T024)."""

from __future__ import annotations

import json
import uuid

import pytest

from backend.agents.enrichment import make_enrichment_handler
from backend.domain.incident import Incident, IncidentStatus, Severity
from backend.domain.llm import LlmResponse, ProviderId, StopReason, TokenUsage
from backend.domain.memory import EpisodeQuery, FactState
from backend.infra.config import EnrichmentSettings


def _incident(extra_indicators: int = 0) -> Incident:
    fields: dict = {}
    # Populate indicator fields to test the cap
    for i in range(extra_indicators):
        fields[f"md5_{i}"] = f"hash{i:032d}"
    return Incident(
        id=uuid.uuid4(),
        status=IncidentStatus.ENRICHING,
        severity=Severity.MEDIUM,
        correlation_id="corr-bounded",
        dedup_fingerprint="fp-bounded",
        source="wazuh",
        raw_alert={},
        evidence={
            "verdict": "suspicious",
            "severity": "medium",
            "normalized_event": {
                "rule_id": "100",
                "rule_description": "Bounded test",
                "rule_groups": ["T1059"],
                "agent_ip": "10.0.0.1",
                "agent_name": "host-01",
                "fields": {
                    "md5": "aaa",
                    "sha1": "bbb",
                    "sha256": "ccc",
                    "hash": "ddd",
                    "domain": "evil.com",
                    "url": "http://evil.com",
                    **fields,
                },
            },
            "summary": "Test",
        },
    )


def _ok_response() -> LlmResponse:
    return LlmResponse(
        content=json.dumps(
            {
                "assessment": "confirmed",
                "confidence": 0.8,
                "correlation_summary": "Signals aligned.",
                "cited_evidence": ["rule_id=100"],
            }
        ),
        usage=TokenUsage(prompt_tokens=40, completion_tokens=20),
        model="fake",
        provider=ProviderId.GEMINI,
        stop_reason=StopReason.END_TURN,
    )


class CountingLlm:
    def __init__(self):
        self.call_count = 0

    async def generate(self, request, *, correlation_id=None):
        self.call_count += 1
        return _ok_response()


class CountingMemory:
    def __init__(self):
        self.query_fact_calls: list = []

    async def search_similar(self, query: EpisodeQuery, *, k: int) -> list:
        return []

    async def query_fact(self, entity, fact_type, *, as_of=None) -> FactState:
        self.query_fact_calls.append(entity)
        return FactState()

    async def write_episode(self, ep):
        pass

    async def write_fact(self, fact):
        pass


@pytest.mark.asyncio
async def test_exactly_one_generate_call_per_incident():
    llm = CountingLlm()
    handler = make_enrichment_handler(llm, None, None, None, EnrichmentSettings())
    await handler(_incident())
    assert llm.call_count == 1


@pytest.mark.asyncio
async def test_tokens_consumed_equals_prompt_plus_completion():
    llm = CountingLlm()
    handler = make_enrichment_handler(llm, None, None, None, EnrichmentSettings())
    result = await handler(_incident())
    assert result.tokens_consumed == 60  # 40 + 20


@pytest.mark.asyncio
async def test_tokens_consumed_none_safe_when_zero_counts():
    """If provider returns zero token counts, tokens_consumed is 0 (None-safe)."""

    class ZeroUsageLlm:
        async def generate(self, request, *, correlation_id=None):
            return LlmResponse(
                content=json.dumps(
                    {
                        "assessment": "confirmed",
                        "confidence": 0.8,
                        "correlation_summary": "ok",
                        "cited_evidence": ["ev"],
                    }
                ),
                usage=TokenUsage(prompt_tokens=0, completion_tokens=0),
                model="fake",
                provider=ProviderId.GEMINI,
                stop_reason=StopReason.END_TURN,
            )

    handler = make_enrichment_handler(ZeroUsageLlm(), None, None, None, EnrichmentSettings())
    result = await handler(_incident())
    assert result.tokens_consumed == 0


@pytest.mark.asyncio
async def test_indicator_calls_capped_at_max_indicators():
    """query_fact calls are bounded by max_indicators."""
    cfg = EnrichmentSettings(max_indicators=3)
    llm = CountingLlm()
    mem = CountingMemory()
    handler = make_enrichment_handler(llm, None, mem, None, cfg)
    await handler(_incident(extra_indicators=10))
    # Total entity count may exceed max_indicators but query_fact calls are bounded
    assert len(mem.query_fact_calls) <= cfg.max_indicators
