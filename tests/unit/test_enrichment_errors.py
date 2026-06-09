"""Unit tests — LlmError → ToolError mapping and fail-closed behaviour (T023)."""

from __future__ import annotations

import json
import uuid

import pytest

from backend.agents.enrichment import make_enrichment_handler
from backend.domain.incident import Incident, IncidentStatus, Severity
from backend.domain.llm import LlmError, LlmErrorKind, LlmResponse, ProviderId, StopReason, TokenUsage
from backend.domain.pipeline import ToolError
from backend.infra.config import EnrichmentSettings


def _incident() -> Incident:
    return Incident(
        id=uuid.uuid4(),
        status=IncidentStatus.ENRICHING,
        severity=Severity.HIGH,
        correlation_id="corr-errors",
        dedup_fingerprint="fp-errors",
        source="wazuh",
        raw_alert={},
        evidence={
            "verdict": "suspicious",
            "severity": "high",
            "normalized_event": {"rule_id": "1", "rule_description": "alert", "rule_groups": [], "fields": {}},
            "summary": "error test incident",
        },
    )


class ErrorLlm:
    def __init__(self, exc: Exception):
        self._exc = exc

    async def generate(self, request, *, correlation_id=None):
        raise self._exc


class MalformedLlm:
    def __init__(self, content: str = "not json {{{"):
        self._content = content

    async def generate(self, request, *, correlation_id=None):
        return LlmResponse(
            content=self._content,
            usage=TokenUsage(prompt_tokens=10, completion_tokens=5),
            model="fake",
            provider=ProviderId.GEMINI,
            stop_reason=StopReason.END_TURN,
        )


@pytest.mark.asyncio
async def test_transient_llm_error_retryable():
    llm = ErrorLlm(LlmError(kind=LlmErrorKind.TRANSIENT, message="timeout"))
    handler = make_enrichment_handler(llm, None, None, None, EnrichmentSettings())
    with pytest.raises(ToolError) as exc_info:
        await handler(_incident())
    assert exc_info.value.retryable is True
    assert "transient" in exc_info.value.kind


@pytest.mark.asyncio
async def test_exhausted_llm_error_retryable():
    llm = ErrorLlm(LlmError(kind=LlmErrorKind.EXHAUSTED, message="rate limited"))
    handler = make_enrichment_handler(llm, None, None, None, EnrichmentSettings())
    with pytest.raises(ToolError) as exc_info:
        await handler(_incident())
    assert exc_info.value.retryable is True


@pytest.mark.asyncio
async def test_auth_llm_error_not_retryable():
    llm = ErrorLlm(LlmError(kind=LlmErrorKind.AUTH, message="bad key"))
    handler = make_enrichment_handler(llm, None, None, None, EnrichmentSettings())
    with pytest.raises(ToolError) as exc_info:
        await handler(_incident())
    assert exc_info.value.retryable is False


@pytest.mark.asyncio
async def test_unexpected_exception_not_retryable():
    llm = ErrorLlm(RuntimeError("unexpected"))
    handler = make_enrichment_handler(llm, None, None, None, EnrichmentSettings())
    with pytest.raises(ToolError) as exc_info:
        await handler(_incident())
    assert exc_info.value.retryable is False
    assert exc_info.value.kind == "llm_unexpected"


@pytest.mark.asyncio
async def test_malformed_json_raises_tool_error_not_retryable():
    handler = make_enrichment_handler(MalformedLlm("not json"), None, None, None, EnrichmentSettings())
    with pytest.raises(ToolError) as exc_info:
        await handler(_incident())
    assert exc_info.value.retryable is False
    assert exc_info.value.kind == "malformed_output"


@pytest.mark.asyncio
async def test_invalid_assessment_raises_malformed_output():
    """Out-of-vocabulary assessment → fail-closed ToolError, not advance/resolve."""
    bad_payload = json.dumps({
        "assessment": "maybe",  # not in enum
        "confidence": 0.9,
        "correlation_summary": "test",
        "cited_evidence": ["ev"],
    })
    handler = make_enrichment_handler(MalformedLlm(bad_payload), None, None, None, EnrichmentSettings())
    with pytest.raises(ToolError) as exc_info:
        await handler(_incident())
    assert exc_info.value.kind == "malformed_output"
    assert exc_info.value.retryable is False
