"""T025 — exactly one LLM call per incident; token accounting None-safe (FR-009, SC-006)."""

from __future__ import annotations

import json
import uuid

import pytest

from backend.agents.triage import make_triage_handler
from backend.domain.incident import Incident, IncidentStatus, Severity
from backend.domain.llm import LlmResponse, ProviderId, StopReason, TokenUsage
from backend.infra.config import TriageSettings


def _incident() -> Incident:
    return Incident(
        id=uuid.uuid4(),
        status=IncidentStatus.TRIAGING,
        severity=Severity.MEDIUM,
        correlation_id="corr-bound",
        dedup_fingerprint="fp-bound",
        source="wazuh",
        raw_alert={},
        evidence={"verdict": "suspicious", "severity": "medium", "normalized_event": {}, "summary": "t"},
    )


def _resp(prompt: int | None = 50, completion: int | None = 30) -> LlmResponse:
    payload = {
        "verdict": "real",
        "confidence": 0.9,
        "rationale": "ok",
        "cited_evidence": ["ev"],
    }
    return LlmResponse(
        content=json.dumps(payload),
        usage=TokenUsage(prompt_tokens=prompt, completion_tokens=completion),
        model="m",
        provider=ProviderId.GEMINI,
        stop_reason=StopReason.END_TURN,
    )


class CountingLlm:
    def __init__(self, response: LlmResponse) -> None:
        self._response = response
        self.call_count = 0

    async def generate(self, request: object, *, correlation_id: str | None = None) -> LlmResponse:
        self.call_count += 1
        return self._response


@pytest.mark.asyncio
async def test_exactly_one_generate_call():
    llm = CountingLlm(_resp())
    handler = make_triage_handler(llm, TriageSettings())
    await handler(_incident())
    assert llm.call_count == 1


@pytest.mark.asyncio
async def test_tokens_consumed_equals_prompt_plus_completion():
    llm = CountingLlm(_resp(prompt=40, completion=20))
    handler = make_triage_handler(llm, TriageSettings())
    result = await handler(_incident())
    assert result.tokens_consumed == 60


@pytest.mark.asyncio
async def test_tokens_consumed_none_safe_both_none():
    """Provider omits both token counts → 0 (never crash)."""
    llm = CountingLlm(_resp(prompt=None, completion=None))
    handler = make_triage_handler(llm, TriageSettings())
    result = await handler(_incident())
    assert result.tokens_consumed == 0


@pytest.mark.asyncio
async def test_tokens_consumed_none_safe_one_none():
    llm = CountingLlm(_resp(prompt=30, completion=None))
    handler = make_triage_handler(llm, TriageSettings())
    result = await handler(_incident())
    assert result.tokens_consumed == 30
