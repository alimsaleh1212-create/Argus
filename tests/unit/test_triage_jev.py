"""Triage with the optional Jev engine — jev / shadow / llm modes and LLM fallback (JD2–JD4)."""

from __future__ import annotations

import asyncio
import json
import uuid

import pytest

from backend.agents.triage import make_triage_handler
from backend.domain.decision import (
    ChoiceAnswer,
    DecisionError,
    DecisionErrorKind,
    DecisionResponse,
)
from backend.domain.incident import Incident, IncidentStatus, Severity
from backend.domain.llm import LlmResponse, ProviderId, StopReason, TokenUsage
from backend.domain.pipeline import StageOutcome, ToolError
from backend.domain.triage import TriageJudgment
from backend.infra.config import TriageSettings


def _incident() -> Incident:
    return Incident(
        id=uuid.uuid4(),
        status=IncidentStatus.TRIAGING,
        severity=Severity.MEDIUM,
        correlation_id="corr-jev",
        dedup_fingerprint="fp-jev",
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


class FakeLlm:
    def __init__(self, verdict: str = "real", confidence: float = 0.9) -> None:
        self._payload = {
            "verdict": verdict,
            "confidence": confidence,
            "rationale": f"LLM saw a {verdict} pattern.",
            "cited_evidence": ["rule_description"],
        }
        self.calls = 0

    async def generate(self, request: object, *, correlation_id: str | None = None):
        self.calls += 1
        return LlmResponse(
            content=json.dumps(self._payload),
            usage=TokenUsage(prompt_tokens=50, completion_tokens=30),
            model="llm-model",
            provider=ProviderId.GEMINI,
            stop_reason=StopReason.END_TURN,
        )


class FailingLlm:
    async def generate(self, request: object, *, correlation_id: str | None = None):
        raise RuntimeError("llm down")


class FakeJev:
    def __init__(
        self,
        choice: str = "noise",
        confidence: float = 0.95,
        error: Exception | None = None,
        delay: float = 0.0,
    ) -> None:
        self._choice = choice
        self._confidence = confidence
        self._error = error
        self._delay = delay
        self.requests: list = []

    async def decide(self, request, *, correlation_id: str) -> DecisionResponse:
        self.requests.append(request)
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._error is not None:
            raise self._error
        probs = {"real": 0.0, "noise": 0.0, "uncertain": 0.0}
        probs[self._choice] = self._confidence
        return DecisionResponse(
            model="jev-1.13.0",
            answers={
                "triage_verdict": ChoiceAnswer(
                    choice=self._choice, confidence=self._confidence, probabilities=probs
                )
            },
            input_tokens=200,
            output_tokens=3,
        )


# ── jev mode ─────────────────────────────────────────────────────────────────


async def test_jev_mode_decides_without_calling_llm():
    llm, jev = FakeLlm(), FakeJev(choice="noise", confidence=0.95)
    handler = make_triage_handler(llm, TriageSettings(engine="jev"), jev)

    result = await handler(_incident())

    assert llm.calls == 0
    assert result.outcome == StageOutcome.RESOLVED
    assert result.disposition == "auto_resolved_triage"
    assert result.llm_model == "jev-1.13.0"
    assert result.tokens_consumed == 203
    assert result.evidence_patch["triage_jev"]["mode"] == "jev"
    assert result.evidence_patch["triage_jev"]["verdict"] == "noise"


async def test_jev_mode_writes_a_valid_attribution_rationale():
    handler = make_triage_handler(FakeLlm(), TriageSettings(engine="jev"), FakeJev(choice="real"))

    result = await handler(_incident())

    judgment = TriageJudgment.model_validate(result.evidence_patch["triage"])
    assert "Jev (jev-1.13.0) classified the supplied evidence as real" in judgment.rationale
    assert "summary" in judgment.cited_evidence
    assert "retrieved_context" not in judgment.cited_evidence  # "none" is not evidence


async def test_jev_mode_sends_same_evidence_as_llm():
    jev = FakeJev()
    handler = make_triage_handler(FakeLlm(), TriageSettings(engine="jev"), jev)

    await handler(_incident())

    state = json.loads(jev.requests[0].state)
    assert state["summary"] == "Multiple login failures detected"
    assert set(jev.requests[0].questions["triage_verdict"].criteria) == {
        "real",
        "noise",
        "uncertain",
    }


async def test_jev_low_confidence_escalates_and_does_not_reask_llm():
    llm = FakeLlm()
    handler = make_triage_handler(llm, TriageSettings(engine="jev"), FakeJev(confidence=0.4))

    result = await handler(_incident())

    assert result.outcome == StageOutcome.ESCALATE
    assert llm.calls == 0


@pytest.mark.parametrize(
    "error",
    [DecisionError(DecisionErrorKind.TIMEOUT), DecisionError(DecisionErrorKind.AUTH), KeyError()],
)
async def test_jev_failure_falls_back_to_llm(error: Exception):
    llm = FakeLlm(verdict="real")
    handler = make_triage_handler(llm, TriageSettings(engine="jev"), FakeJev(error=error))

    result = await handler(_incident())

    assert llm.calls == 1
    assert result.outcome == StageOutcome.ADVANCE
    assert result.llm_model == "llm-model"
    assert result.evidence_patch["triage_jev"]["fallback"] == "llm"
    assert result.evidence_patch["triage_jev"]["mode"] == "jev"


async def test_jev_failure_and_llm_failure_fails_closed():
    handler = make_triage_handler(
        FailingLlm(),
        TriageSettings(engine="jev"),
        FakeJev(error=DecisionError(DecisionErrorKind.UNAVAILABLE)),
    )

    with pytest.raises(ToolError):
        await handler(_incident())


# ── shadow mode ──────────────────────────────────────────────────────────────


async def test_shadow_mode_llm_decides_and_records_jev():
    llm = FakeLlm(verdict="real", confidence=0.9)
    handler = make_triage_handler(llm, TriageSettings(engine="shadow"), FakeJev(choice="noise"))

    result = await handler(_incident())

    assert llm.calls == 1
    assert result.outcome == StageOutcome.ADVANCE  # the LLM's verdict, not Jev's
    assert result.evidence_patch["triage"]["verdict"] == "real"
    shadow = result.evidence_patch["triage_jev"]
    assert shadow["mode"] == "shadow"
    assert shadow["verdict"] == "noise"
    assert shadow["agrees_with_llm"] is False


async def test_shadow_mode_jev_error_never_affects_outcome():
    handler = make_triage_handler(
        FakeLlm(verdict="real"),
        TriageSettings(engine="shadow"),
        FakeJev(error=DecisionError(DecisionErrorKind.RATE_LIMITED)),
    )

    result = await handler(_incident())

    assert result.outcome == StageOutcome.ADVANCE
    assert result.evidence_patch["triage_jev"] == {"mode": "shadow", "error": "rate_limited"}


async def test_shadow_mode_runs_jev_concurrently():
    jev = FakeJev(choice="real", delay=0.05)
    handler = make_triage_handler(FakeLlm(), TriageSettings(engine="shadow"), jev)

    result = await handler(_incident())

    assert result.evidence_patch["triage_jev"]["agrees_with_llm"] is True


async def test_shadow_mode_llm_failure_still_raises():
    handler = make_triage_handler(FailingLlm(), TriageSettings(engine="shadow"), FakeJev())

    with pytest.raises(ToolError):
        await handler(_incident())


# ── inert unless a client is present ────────────────────────────────────────


async def test_engine_is_inert_without_decision_client():
    llm = FakeLlm()
    handler = make_triage_handler(llm, TriageSettings(engine="jev"), None)

    result = await handler(_incident())

    assert llm.calls == 1
    assert "triage_jev" not in result.evidence_patch


async def test_llm_engine_ignores_decision_client():
    llm, jev = FakeLlm(), FakeJev()
    handler = make_triage_handler(llm, TriageSettings(), jev)

    result = await handler(_incident())

    assert llm.calls == 1
    assert jev.requests == []
    assert "triage_jev" not in result.evidence_patch


def test_engine_rejects_unknown_value():
    with pytest.raises(ValueError):
        TriageSettings(engine="gpt")
