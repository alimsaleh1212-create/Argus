"""T019 — Triage eval gate: macro-F1 on committed labeled alert set.

Runs triage handler against each labeled fixture and scores macro-F1 on real/noise
classification. Abstentions (uncertain) are counted separately and bounded.
To test against real LLM providers, use -m integration and supply real credentials.
Default (unit-tier): uses a fake LlmClient that returns a deterministic verdict.
"""

from __future__ import annotations

import json
import pathlib
import uuid
from typing import Any

import pytest

from backend.agents.triage import make_triage_handler
from backend.domain.incident import Incident, IncidentStatus, Severity
from backend.domain.llm import LlmResponse, ProviderId, StopReason, TokenUsage
from backend.infra.config import TriageSettings

FIXTURES_DIR = pathlib.Path(__file__).parent.parent / "fixtures" / "triage_labeled"

# Gate thresholds (mirror eval_thresholds.yaml)
MIN_MACRO_F1 = 0.75
MAX_ABSTENTION_RATE = 0.30


def _load_fixtures() -> list[dict[str, Any]]:
    fixtures = []
    for path in sorted(FIXTURES_DIR.glob("*.json")):
        with path.open() as f:
            fixtures.append(json.load(f))
    return fixtures


def _incident_from_fixture(fx: dict[str, Any]) -> Incident:
    return Incident(
        id=uuid.uuid4(),
        status=IncidentStatus.TRIAGING,
        severity=Severity.MEDIUM,
        correlation_id=fx.get("incident_id", "eval-fixture"),
        dedup_fingerprint=fx.get("incident_id", "eval-fixture"),
        source="eval",
        raw_alert={},
        evidence=fx["evidence"],
    )


def _macro_f1(
    tp_real: int, fp_real: int, fn_real: int, tp_noise: int, fp_noise: int, fn_noise: int
) -> float:
    def f1(tp: int, fp: int, fn: int) -> float:
        if tp + fp == 0 or tp + fn == 0:
            return 0.0
        precision = tp / (tp + fp)
        recall = tp / (tp + fn)
        if precision + recall == 0:
            return 0.0
        return 2 * precision * recall / (precision + recall)

    return (f1(tp_real, fp_real, fn_real) + f1(tp_noise, fp_noise, fn_noise)) / 2


class ScriptedLlm:
    """Returns a response whose verdict matches the gold label deterministically.

    This lets the gate test the scoring logic (F1 calculation, abstention bounds)
    without a real LLM call. Provider-level quality is tested in integration tier.
    """

    def __init__(self, verdict_map: dict[str, str]) -> None:
        self._map = verdict_map  # correlation_id → verdict

    async def generate(self, request: object, *, correlation_id: str | None = None) -> LlmResponse:
        verdict = self._map.get(correlation_id or "", "uncertain")
        payload = {
            "verdict": verdict,
            "confidence": 0.9 if verdict != "uncertain" else 0.4,
            "rationale": f"Scripted verdict: {verdict}",
            "cited_evidence": ["rule_description"],
        }
        return LlmResponse(
            content=json.dumps(payload),
            usage=TokenUsage(prompt_tokens=20, completion_tokens=10),
            model="scripted",
            provider=ProviderId.GEMINI,
            stop_reason=StopReason.END_TURN,
        )


@pytest.mark.asyncio
async def test_triage_gate_macro_f1_with_perfect_scripted_llm():
    """With a perfect (gold-matching) scripted LLM, macro-F1 should be 1.0."""
    fixtures = _load_fixtures()
    assert fixtures, "No labeled fixtures found — check tests/fixtures/triage_labeled/"

    verdict_map = {fx["incident_id"]: fx["gold_label"] for fx in fixtures}
    llm = ScriptedLlm(verdict_map)
    handler = make_triage_handler(llm, TriageSettings())

    tp_real = fp_real = fn_real = 0
    tp_noise = fp_noise = fn_noise = 0
    abstentions = 0

    for fx in fixtures:
        inc = _incident_from_fixture(fx)
        result = await handler(inc)

        # Derive predicted verdict from outcome
        if result.evidence_patch and "triage" in result.evidence_patch:
            predicted = result.evidence_patch["triage"]["verdict"]
        else:
            predicted = "uncertain"

        gold = fx["gold_label"]

        if predicted == "uncertain":
            abstentions += 1
            continue

        if gold == "real":
            if predicted == "real":
                tp_real += 1
            else:
                fn_real += 1
                fp_noise += 1
        elif gold == "noise":
            if predicted == "noise":
                tp_noise += 1
            else:
                fn_noise += 1
                fp_real += 1

    total = len(fixtures)
    abstention_rate = abstentions / total if total > 0 else 0.0
    macro_f1 = _macro_f1(tp_real, fp_real, fn_real, tp_noise, fp_noise, fn_noise)

    assert abstention_rate <= MAX_ABSTENTION_RATE, (
        f"Abstention rate {abstention_rate:.2%} exceeds max {MAX_ABSTENTION_RATE:.2%}"
    )
    assert macro_f1 >= MIN_MACRO_F1, (
        f"Macro-F1 {macro_f1:.3f} below threshold {MIN_MACRO_F1} "
        f"(tp_real={tp_real}, fp_real={fp_real}, fn_real={fn_real}, "
        f"tp_noise={tp_noise}, fp_noise={fp_noise}, fn_noise={fn_noise}, "
        f"abstentions={abstentions})"
    )


@pytest.mark.asyncio
async def test_triage_gate_abstention_bound():
    """A handler that abstains on everything must fail the max_abstention_rate gate."""
    fixtures = _load_fixtures()

    # All uncertain → abstention_rate = 1.0 → gate fails
    all_uncertain_map = {fx["incident_id"]: "uncertain" for fx in fixtures}
    llm = ScriptedLlm(all_uncertain_map)
    handler = make_triage_handler(llm, TriageSettings())

    abstentions = 0
    for fx in fixtures:
        inc = _incident_from_fixture(fx)
        result = await handler(inc)
        if (
            result.evidence_patch
            and result.evidence_patch.get("triage", {}).get("verdict") == "uncertain"
        ):
            abstentions += 1
        elif result.outcome.value == "escalate":
            abstentions += 1

    abstention_rate = abstentions / len(fixtures)
    assert abstention_rate > MAX_ABSTENTION_RATE, (
        "Expected abstention gate to fire for all-uncertain handler"
    )
