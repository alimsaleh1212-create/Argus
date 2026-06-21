"""Verification-accuracy eval gate (SPEC-remediation-verification #15).

Deterministic, provider-independent: drives labeled post-remediation fixture states through
decide_verdict and scores classification accuracy + false_verified_rate.
"""

from __future__ import annotations

import json
from pathlib import Path

from backend.domain.eval import GateKind, GateResult, GateSpec
from backend.domain.response import (
    ActionType,
    IndicatorRecheck,
    ProbeResult,
    ProbeState,
    VerificationSignals,
    VerificationVerdict,
    decide_verdict,
)
from backend.eval.gates import GATE_REGISTRY

_FIXTURE_DIR = Path("tests/fixtures/verification")


def _load_signals(signals_raw: list[dict]) -> list[VerificationSignals]:
    """Deserialise fixture signals list into VerificationSignals objects."""
    result: list[VerificationSignals] = []
    for s in signals_raw:
        probe_raw = s["probe"]
        probe = ProbeResult(
            type=ActionType(probe_raw["type"]),
            target=probe_raw["target"],
            state=ProbeState(probe_raw["state"]),
            detail=probe_raw.get("detail", ""),
        )
        recheck: IndicatorRecheck | None = None
        if s.get("recheck"):
            r = s["recheck"]
            recheck = IndicatorRecheck(
                target=r["target"],
                intel_verdict=r["intel_verdict"],
                fact_value=r.get("fact_value"),
                fact_is_current=r.get("fact_is_current", False),
            )
        result.append(VerificationSignals(probe=probe, recheck=recheck))
    return result


class _DefaultCfg:
    verify_regressed_verdicts = ["malicious", "suspicious"]
    verify_llm_tiebreak = False


async def run_verification(spec: GateSpec, provider: str | None = None) -> GateResult:
    """Score decide_verdict classification accuracy on labeled fixture files.

    max_false_verified_rate: 0.0 is the load-bearing safety invariant (SC-003/SC-004).
    """
    fixture_dir = _FIXTURE_DIR
    if not fixture_dir.exists():
        return GateResult(
            gate=spec.name,
            kind=spec.kind,
            provider=provider,
            score={"accuracy": 0.0, "false_verified_rate": 1.0},
            threshold=spec.threshold,
            passed=None,
            blocking=spec.kind == GateKind.required,
            evidence="fixture directory missing",
        )

    total = correct = false_verified = 0
    failed_cases: list[str] = []

    for fixture_path in sorted(fixture_dir.glob("*.json")):
        try:
            data = json.loads(fixture_path.read_text())
        except Exception as exc:
            failed_cases.append(f"{fixture_path.name}:parse_error:{exc}")
            continue

        expected = VerificationVerdict(data["expected_verdict"])
        signals = _load_signals(data.get("signals", []))
        cfg = _DefaultCfg()
        got = decide_verdict(signals, cfg)

        total += 1
        if got == expected:
            correct += 1
        else:
            failed_cases.append(f"{fixture_path.name}: expected={expected.value} got={got.value}")
            # False-verified: a case that should have been unverified/regressed was called verified
            if expected != VerificationVerdict.VERIFIED and got == VerificationVerdict.VERIFIED:
                false_verified += 1

    if total == 0:
        return GateResult(
            gate=spec.name,
            kind=spec.kind,
            provider=provider,
            score={"accuracy": 0.0, "false_verified_rate": 1.0},
            threshold=spec.threshold,
            passed=None,
            blocking=spec.kind == GateKind.required,
            evidence="no fixture files found",
        )

    accuracy = correct / total
    false_verified_rate = false_verified / total
    threshold = spec.threshold
    min_acc = threshold.get("min_accuracy", 0.95)
    max_fvr = threshold.get("max_false_verified_rate", 0.0)
    gate_passed = accuracy >= min_acc and false_verified_rate <= max_fvr

    evidence = f"{correct}/{total} correct; false_verified_rate={false_verified_rate:.3f}"
    if failed_cases:
        evidence += "; failures: " + "; ".join(failed_cases)

    return GateResult(
        gate=spec.name,
        kind=spec.kind,
        provider=provider,
        score={"accuracy": accuracy, "false_verified_rate": false_verified_rate},
        threshold=threshold,
        passed=gate_passed,
        blocking=spec.kind == GateKind.required,
        evidence=evidence,
    )


GATE_REGISTRY["verification"] = run_verification
