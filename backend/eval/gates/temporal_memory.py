"""Temporal-memory eval gate (SPEC-memory #6 + SPEC-memory-feedback-loop #16).

Validates invalidate-not-delete semantics over the committed scenario fixtures
in tests/fixtures/memory_temporal/scenarios.json.
"""

from __future__ import annotations

from backend.domain.eval import GateKind, GateResult, GateSpec
from backend.eval.gates import GATE_REGISTRY


async def run_temporal_memory(spec: GateSpec, provider: str | None = None) -> GateResult:
    """Run temporal-validity scenarios — 100% pass required."""
    try:
        from tests.eval.test_temporal_gate import _run_temporal_scenarios

        passed, total = await _run_temporal_scenarios()
    except Exception as e:
        return GateResult(
            gate=spec.name,
            kind=spec.kind,
            provider=provider,
            score=0.0,
            threshold=spec.threshold,
            passed=None,
            blocking=spec.kind == GateKind.required,
            evidence=f"temporal store unavailable: {e}",
        )

    pass_rate = passed / total if total else 0.0
    threshold_rate = spec.threshold.get("pass_rate", 1.0)
    return GateResult(
        gate=spec.name,
        kind=spec.kind,
        provider=provider,
        score=pass_rate,
        threshold=spec.threshold,
        passed=pass_rate >= threshold_rate,
        blocking=spec.kind == GateKind.required,
        evidence=f"{passed}/{total} temporal scenarios passed",
    )


GATE_REGISTRY["temporal_memory"] = run_temporal_memory
