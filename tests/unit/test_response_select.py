"""Unit tests — T009: deterministic select_playbook + ambiguous → one LLM call (US1)."""

from __future__ import annotations

import json
import uuid

import pytest

from backend.agents.response import PlaybookEntry, select_playbook
from backend.domain.incident import Incident, IncidentStatus, Severity
from backend.domain.pipeline import ToolError
from backend.infra.config import ResponseSettings


def _incident(severity: str = "medium", rule_groups: list[str] | None = None) -> Incident:
    ne = {"severity": severity, "rule_groups": rule_groups or []}
    return Incident(
        id=uuid.uuid4(),
        status=IncidentStatus.RESPONDING,
        severity=Severity(severity)
        if severity in ("low", "medium", "high", "critical")
        else Severity.MEDIUM,
        correlation_id="corr-select",
        dedup_fingerprint="fp-select",
        source="wazuh",
        raw_alert={},
        evidence={"severity": severity, "normalized_event": ne},
    )


def _catalog_single() -> list[PlaybookEntry]:
    return [
        PlaybookEntry(
            id="watchlist_and_ticket",
            description="low-medium threat",
            criteria={"severity": ["low", "medium"]},
            actions=[{"type": "add_to_watchlist"}, {"type": "open_ticket"}],
        )
    ]


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


def _catalog_no_match() -> list[PlaybookEntry]:
    return [
        PlaybookEntry(
            id="critical_only",
            description="critical only",
            criteria={"severity": ["critical"]},
            actions=[{"type": "isolate_host"}],
        )
    ]


# ---------------------------------------------------------------------------
# Deterministic path (no LLM)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_select_deterministic_single_match():
    inc = _incident(severity="medium")
    cfg = ResponseSettings()
    selection = await select_playbook(inc, _catalog_single(), llm=None, cfg=cfg)
    plan = selection.plan
    tokens = selection.tokens_consumed
    assert plan.selected_by == "deterministic"
    assert plan.playbook_id == "watchlist_and_ticket"
    assert tokens == 0
    assert len(plan.actions) == 2


@pytest.mark.asyncio
async def test_select_deterministic_zero_tokens():
    inc = _incident(severity="low")
    cfg = ResponseSettings()
    sel = await select_playbook(inc, _catalog_single(), llm=None, cfg=cfg)
    assert sel.tokens_consumed == 0
    assert sel.tokens_in is None
    assert sel.tokens_out is None


@pytest.mark.asyncio
async def test_select_no_match_raises():
    inc = _incident(severity="medium")
    cfg = ResponseSettings()
    with pytest.raises(ToolError) as exc_info:
        await select_playbook(inc, _catalog_no_match(), llm=None, cfg=cfg)
    assert exc_info.value.retryable is False


@pytest.mark.asyncio
async def test_select_empty_catalog_raises():
    inc = _incident(severity="medium")
    cfg = ResponseSettings()
    with pytest.raises(ToolError) as exc_info:
        await select_playbook(inc, [], llm=None, cfg=cfg)
    assert "empty_catalog" in exc_info.value.kind


# ---------------------------------------------------------------------------
# Ambiguous tail → one LLM call
# ---------------------------------------------------------------------------


class _FakeLlm:
    def __init__(self, payload: dict, fail: bool = False) -> None:
        self._payload = payload
        self._fail = fail
        self.call_count = 0

    async def generate(self, request, *, correlation_id=None):
        self.call_count += 1
        if self._fail:
            raise RuntimeError("llm_down")
        from backend.domain.llm import LlmResponse, ProviderId, StopReason, TokenUsage

        return LlmResponse(
            content=json.dumps(self._payload),
            usage=TokenUsage(prompt_tokens=10, completion_tokens=5),
            model="test",
            provider=ProviderId.GEMINI,
            stop_reason=StopReason.END_TURN,
        )


@pytest.mark.asyncio
async def test_select_ambiguous_uses_llm():
    """Multiple matching playbooks → exactly one LLM call."""
    inc = _incident(severity="medium")
    cfg = ResponseSettings()
    llm = _FakeLlm({"playbook_id": "pb_a", "confidence": 0.9, "rationale": "pb_a fits"})
    selection = await select_playbook(inc, _catalog_multi(), llm=llm, cfg=cfg)
    plan = selection.plan
    tokens = selection.tokens_consumed
    assert llm.call_count == 1
    assert plan.playbook_id == "pb_a"
    assert plan.selected_by == "llm"
    assert tokens > 0
    assert selection.tokens_in == 10
    assert selection.tokens_out == 5
    assert selection.llm_model == "test"


@pytest.mark.asyncio
async def test_select_low_confidence_escalates():
    inc = _incident(severity="medium")
    cfg = ResponseSettings(select_min_confidence=0.7)
    llm = _FakeLlm({"playbook_id": "pb_a", "confidence": 0.5, "rationale": "uncertain"})
    with pytest.raises(ToolError) as exc_info:
        await select_playbook(inc, _catalog_multi(), llm=llm, cfg=cfg)
    assert "low_confidence" in exc_info.value.kind


@pytest.mark.asyncio
async def test_select_unknown_playbook_id_escalates():
    inc = _incident(severity="medium")
    cfg = ResponseSettings()
    llm = _FakeLlm({"playbook_id": "nonexistent", "confidence": 0.95, "rationale": "..."})
    with pytest.raises(ToolError) as exc_info:
        await select_playbook(inc, _catalog_multi(), llm=llm, cfg=cfg)
    assert "unknown_playbook" in exc_info.value.kind


@pytest.mark.asyncio
async def test_select_llm_error_raises_tool_error():
    from backend.domain.llm import LlmError, LlmErrorKind

    inc = _incident(severity="medium")
    cfg = ResponseSettings()

    class _LlmDown:
        async def generate(self, req, *, correlation_id=None):
            raise LlmError(kind=LlmErrorKind.TRANSIENT, message="timeout")

    with pytest.raises(ToolError) as exc_info:
        await select_playbook(inc, _catalog_multi(), llm=_LlmDown(), cfg=cfg)
    assert exc_info.value.retryable is True
