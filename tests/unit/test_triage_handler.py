"""T014 — make_triage_handler with a fake LlmClient (US1)."""

from __future__ import annotations

import json
import uuid

import pytest

from backend.agents.triage import make_triage_handler
from backend.domain.incident import Incident, IncidentStatus, Severity
from backend.domain.llm import (
    LlmResponse,
    ProviderId,
    StopReason,
    TokenUsage,
)
from backend.domain.pipeline import StageName, StageOutcome
from backend.infra.config import TriageSettings


def _incident() -> Incident:
    return Incident(
        id=uuid.uuid4(),
        status=IncidentStatus.TRIAGING,
        severity=Severity.MEDIUM,
        correlation_id="corr-handler",
        dedup_fingerprint="fp-handler",
        source="wazuh",
        raw_alert={},
        evidence={
            "verdict": "suspicious",
            "severity": "medium",
            "normalized_event": {"rule_id": "550", "rule_description": "login failure"},
            "summary": "Multiple login failures detected",
            "retrieved_context": None,
        },
    )


def _response(verdict: str, confidence: float, extra: dict | None = None) -> LlmResponse:
    payload: dict = {
        "verdict": verdict,
        "confidence": confidence,
        "rationale": f"Evidence shows {verdict} pattern.",
        "cited_evidence": ["rule_description"],
    }
    if extra:
        payload.update(extra)
    return LlmResponse(
        content=json.dumps(payload),
        usage=TokenUsage(prompt_tokens=50, completion_tokens=30),
        model="test-model",
        provider=ProviderId.GEMINI,
        stop_reason=StopReason.END_TURN,
    )


class FakeLlm:
    def __init__(self, response: LlmResponse) -> None:
        self._response = response
        self.calls: list[dict] = []

    async def generate(self, request: object, *, correlation_id: str | None = None) -> LlmResponse:
        self.calls.append({"request": request, "correlation_id": correlation_id})
        return self._response


@pytest.mark.asyncio
async def test_real_verdict_advances():
    llm = FakeLlm(_response("real", 0.9))
    handler = make_triage_handler(llm, TriageSettings())
    result = await handler(_incident())

    assert result.stage == StageName.TRIAGE
    assert result.outcome == StageOutcome.ADVANCE
    assert result.tokens_consumed == 80  # 50 + 30
    assert result.evidence_patch is not None
    assert result.evidence_patch["triage"]["verdict"] == "real"
    assert len(result.evidence_patch["triage"]["cited_evidence"]) >= 1


@pytest.mark.asyncio
async def test_noise_verdict_resolves():
    llm = FakeLlm(_response("noise", 0.85))
    handler = make_triage_handler(llm, TriageSettings())
    result = await handler(_incident())

    assert result.outcome == StageOutcome.RESOLVED
    assert result.disposition == "auto_resolved_triage"
    assert result.evidence_patch["triage"]["verdict"] == "noise"


@pytest.mark.asyncio
async def test_exactly_one_llm_call_per_incident():
    llm = FakeLlm(_response("real", 0.9))
    handler = make_triage_handler(llm, TriageSettings())
    await handler(_incident())
    assert len(llm.calls) == 1


@pytest.mark.asyncio
async def test_correlation_id_forwarded():
    llm = FakeLlm(_response("real", 0.9))
    handler = make_triage_handler(llm, TriageSettings())
    inc = _incident()
    await handler(inc)
    assert llm.calls[0]["correlation_id"] == inc.correlation_id


@pytest.mark.asyncio
async def test_evidence_patch_contains_full_judgment():
    llm = FakeLlm(_response("real", 0.9))
    handler = make_triage_handler(llm, TriageSettings())
    result = await handler(_incident())

    triage = result.evidence_patch["triage"]  # type: ignore[index]
    assert "verdict" in triage
    assert "confidence" in triage
    assert "rationale" in triage
    assert "cited_evidence" in triage
