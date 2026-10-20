"""Ambiguous playbook tail with the optional Jev engine (JD2/JD3)."""

from __future__ import annotations

import json
import uuid

import pytest

from backend.agents.response import PlaybookEntry, select_playbook
from backend.domain.decision import (
    ChoiceAnswer,
    DecisionError,
    DecisionErrorKind,
    DecisionResponse,
)
from backend.domain.incident import Incident, IncidentStatus, Severity
from backend.domain.llm import LlmResponse, ProviderId, StopReason, TokenUsage
from backend.domain.pipeline import ToolError
from backend.infra.config import ResponseSettings


def _incident() -> Incident:
    return Incident(
        id=uuid.uuid4(),
        status=IncidentStatus.RESPONDING,
        severity=Severity.MEDIUM,
        correlation_id="corr-select-jev",
        dedup_fingerprint="fp-select-jev",
        source="wazuh",
        raw_alert={},
        evidence={"severity": "medium", "normalized_event": {"severity": "medium"}},
    )


def _catalog_multi() -> list[PlaybookEntry]:
    return [
        PlaybookEntry(
            id="pb_a",
            description="medium only",
            criteria={"severity": ["medium"]},
            actions=[{"type": "add_to_watchlist"}],
        ),
        PlaybookEntry(
            id="pb_b",
            description="medium or high",
            criteria={"severity": ["medium", "high"]},
            actions=[{"type": "open_ticket"}],
        ),
    ]


class FakeJev:
    def __init__(
        self, choice: str = "pb_b", confidence: float = 0.85, error: Exception | None = None
    ) -> None:
        self._choice = choice
        self._confidence = confidence
        self._error = error
        self.requests: list = []

    async def decide(self, request, *, correlation_id: str) -> DecisionResponse:
        self.requests.append(request)
        if self._error is not None:
            raise self._error
        return DecisionResponse(
            model="jev-1.13.0",
            answers={"playbook": ChoiceAnswer(choice=self._choice, confidence=self._confidence)},
            input_tokens=90,
            output_tokens=2,
        )


class FakeLlm:
    def __init__(self) -> None:
        self.calls = 0

    async def generate(self, request, *, correlation_id=None):
        self.calls += 1
        return LlmResponse(
            content=json.dumps({"playbook_id": "pb_a", "confidence": 0.9, "rationale": "llm"}),
            usage=TokenUsage(prompt_tokens=10, completion_tokens=5),
            model="llm-model",
            provider=ProviderId.GEMINI,
            stop_reason=StopReason.END_TURN,
        )


def _cfg(engine: str = "jev") -> ResponseSettings:
    return ResponseSettings(selection_engine=engine)


async def test_jev_selects_among_candidates_without_llm():
    llm, jev = FakeLlm(), FakeJev(choice="pb_b")

    sel = await select_playbook(_incident(), _catalog_multi(), llm, _cfg(), decision=jev)

    assert llm.calls == 0
    assert sel.plan.selected_by == "jev"
    assert sel.plan.playbook_id == "pb_b"
    assert sel.llm_model == "jev-1.13.0"
    assert sel.tokens_consumed == 92
    assert set(jev.requests[0].questions["playbook"].criteria) == {"pb_a", "pb_b"}


async def test_jev_low_confidence_escalates_without_llm():
    llm = FakeLlm()

    with pytest.raises(ToolError) as exc_info:
        await select_playbook(
            _incident(), _catalog_multi(), llm, _cfg(), decision=FakeJev(confidence=0.3)
        )

    assert exc_info.value.kind == "low_confidence_selection"
    assert llm.calls == 0


async def test_jev_failure_falls_back_to_llm():
    llm = FakeLlm()
    jev = FakeJev(error=DecisionError(DecisionErrorKind.TIMEOUT))

    sel = await select_playbook(_incident(), _catalog_multi(), llm, _cfg(), decision=jev)

    assert llm.calls == 1
    assert sel.plan.selected_by == "llm"
    assert sel.plan.playbook_id == "pb_a"


async def test_jev_failure_without_llm_fails_closed():
    jev = FakeJev(error=DecisionError(DecisionErrorKind.UNAVAILABLE))

    with pytest.raises(ToolError) as exc_info:
        await select_playbook(_incident(), _catalog_multi(), None, _cfg(), decision=jev)

    assert exc_info.value.kind == "jev_selection_failed"


async def test_llm_engine_ignores_jev():
    llm, jev = FakeLlm(), FakeJev()

    sel = await select_playbook(_incident(), _catalog_multi(), llm, _cfg("llm"), decision=jev)

    assert sel.plan.selected_by == "llm"
    assert jev.requests == []


async def test_deterministic_match_never_calls_jev():
    jev = FakeJev()
    catalog = _catalog_multi()[:1]

    sel = await select_playbook(_incident(), catalog, None, _cfg(), decision=jev)

    assert sel.plan.selected_by == "deterministic"
    assert jev.requests == []
