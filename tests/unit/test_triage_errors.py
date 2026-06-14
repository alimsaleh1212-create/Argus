"""T024 — error map: LlmError → ToolError, fail-closed (FR-007, SC-005)."""

from __future__ import annotations

import json
import uuid

import pytest

from backend.agents.triage import make_triage_handler
from backend.domain.incident import Incident, IncidentStatus, Severity
from backend.domain.llm import (
    LlmError,
    LlmErrorKind,
    LlmResponse,
    ProviderId,
    StopReason,
    TokenUsage,
)
from backend.domain.pipeline import StageOutcome, ToolError
from backend.infra.config import TriageSettings


def _incident() -> Incident:
    return Incident(
        id=uuid.uuid4(),
        status=IncidentStatus.TRIAGING,
        severity=Severity.MEDIUM,
        correlation_id="corr-err",
        dedup_fingerprint="fp-err",
        source="wazuh",
        raw_alert={},
        evidence={
            "verdict": "suspicious",
            "severity": "medium",
            "normalized_event": {},
            "summary": "test",
        },
    )


def _good_response() -> LlmResponse:
    return LlmResponse(
        content=json.dumps(
            {
                "verdict": "real",
                "confidence": 0.9,
                "rationale": "Looks real.",
                "cited_evidence": ["rule_description"],
            }
        ),
        usage=TokenUsage(prompt_tokens=10, completion_tokens=10),
        model="m",
        provider=ProviderId.GEMINI,
        stop_reason=StopReason.END_TURN,
    )


class ErrorLlm:
    def __init__(self, kind: LlmErrorKind) -> None:
        self._kind = kind

    async def generate(self, request: object, *, correlation_id: str | None = None) -> LlmResponse:
        raise LlmError(kind=self._kind, message="test error")


class MalformedLlm:
    def __init__(self, content: str) -> None:
        self._content = content

    async def generate(self, request: object, *, correlation_id: str | None = None) -> LlmResponse:
        return LlmResponse(
            content=self._content,
            usage=TokenUsage(prompt_tokens=5, completion_tokens=5),
            model="m",
            provider=ProviderId.GEMINI,
            stop_reason=StopReason.END_TURN,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", [LlmErrorKind.TRANSIENT, LlmErrorKind.EXHAUSTED])
async def test_transient_errors_raise_retryable_tool_error(kind: LlmErrorKind):
    handler = make_triage_handler(ErrorLlm(kind), TriageSettings())
    with pytest.raises(ToolError) as exc_info:
        await handler(_incident())
    assert exc_info.value.retryable is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind",
    [
        LlmErrorKind.AUTH,
        LlmErrorKind.INVALID_REQUEST,
        LlmErrorKind.CONTENT_REFUSAL,
        LlmErrorKind.CONTRACT_UNSATISFIED,
    ],
)
async def test_permanent_errors_raise_non_retryable_tool_error(kind: LlmErrorKind):
    handler = make_triage_handler(ErrorLlm(kind), TriageSettings())
    with pytest.raises(ToolError) as exc_info:
        await handler(_incident())
    assert exc_info.value.retryable is False


@pytest.mark.asyncio
async def test_malformed_json_raises_non_retryable_malformed_output():
    handler = make_triage_handler(MalformedLlm("not json at all"), TriageSettings())
    with pytest.raises(ToolError) as exc_info:
        await handler(_incident())
    assert exc_info.value.retryable is False
    assert exc_info.value.kind == "malformed_output"


@pytest.mark.asyncio
async def test_oov_verdict_raises_malformed_output():
    """An out-of-vocabulary verdict fails the second validation layer → malformed_output."""
    content = json.dumps(
        {
            "verdict": "banana",
            "confidence": 0.9,
            "rationale": "test",
            "cited_evidence": ["ev"],
        }
    )
    handler = make_triage_handler(MalformedLlm(content), TriageSettings())
    with pytest.raises(ToolError) as exc_info:
        await handler(_incident())
    assert exc_info.value.kind == "malformed_output"


@pytest.mark.asyncio
async def test_malformed_output_never_resolves_or_advances():
    """Fail-closed: a bad response never produces ADVANCE or RESOLVED."""
    handler = make_triage_handler(MalformedLlm("{}"), TriageSettings())
    with pytest.raises(ToolError):
        result = await handler(_incident())
        # Should not reach here; if somehow it does, check it's not a good outcome
        assert result.outcome not in (StageOutcome.ADVANCE, StageOutcome.RESOLVED)
