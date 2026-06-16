"""Feedback-effectiveness eval gate (SPEC-memory-feedback-loop #16).

Deterministic / provider-independent: drives baseline-vs-repeat fixture pairs
through the pure bias rules and asserts the repeat is escalated sooner and/or
selects a stronger playbook than the baseline.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from backend.agents.response.selection import _criteria_match
from backend.domain.eval import GateKind, GateResult, GateSpec
from backend.domain.feedback import (
    FeedbackSignal,
    RemediationOutcome,
    decide_severity_bias,
    has_prior_failure,
    prefer_stronger_playbook,
)
from backend.domain.incident import Incident, IncidentStatus, Severity
from backend.eval.gates import GATE_REGISTRY
from backend.infra.config import FeedbackSettings, SupervisorSettings
from backend.services.supervisor import route_grounded

_FIXTURE_DIR = Path("tests/fixtures/feedback")


class _SimpleCandidate:
    def __init__(self, data: dict) -> None:
        self.id = data["id"]
        self.strength = data.get("strength", 0)
        self.criteria = data.get("criteria", {})
        self.actions = data.get("actions", [])


def _load_signals(seed: dict) -> list[FeedbackSignal]:
    return [
        FeedbackSignal(
            indicator=seed["indicator"],
            outcome=RemediationOutcome(seed["value"]),
            is_current=seed.get("is_current", True),
            observed_at=None,
        )
    ]


def _make_incident(severity: str, prior_outcome: dict | None = None) -> Incident:
    evidence: dict[str, Any] = {
        "severity": severity,
        "verdict": "rule_match",
        "normalized_event": {},
        "summary": "feedback eval",
    }
    if prior_outcome:
        evidence["prior_outcome"] = prior_outcome
    return Incident(
        id=__import__("uuid").uuid4(),
        status=IncidentStatus.GROUNDED,
        severity=Severity(severity),
        correlation_id="corr-feedback",
        dedup_fingerprint="fp-feedback",
        source="wazuh",
        raw_alert={},
        evidence=evidence,
    )


def _route_for(severity: str, flags: list[str]) -> str:
    inc = _make_incident(severity)
    inc = inc.model_copy(update={"evidence": {**(inc.evidence or {}), "flags": flags}})
    return route_grounded(inc, SupervisorSettings())


def _evaluate_fixture(fixture: dict) -> tuple[bool, str]:
    cfg = FeedbackSettings()
    kind = fixture.get("kind", "severity_route")
    seed = fixture["seed_outcome"]
    signals = _load_signals(seed)

    if kind == "severity_route":
        base_sev = decide_severity_bias(Severity(fixture["severity"]), [], cfg)
        base_route = _route_for(base_sev.value, [])
        repeat_sev = decide_severity_bias(Severity(fixture["severity"]), signals, cfg)
        repeat_flags = ["prior_failure"] if has_prior_failure(signals, cfg) else []
        repeat_route = _route_for(repeat_sev.value, repeat_flags)

        base_ok = base_route.replace("route:", "") == fixture["baseline"]["expected_route"]
        allowed_repeat = fixture["repeat"]["expected_route"].split("_or_")
        repeat_ok = repeat_route.replace("route:", "") in allowed_repeat
        if repeat_sev.value != fixture["repeat"]["expected_severity"]:
            repeat_ok = False
        if fixture["repeat"].get("expect_severity_bumped") and repeat_sev == base_sev:
            repeat_ok = False
        return (
            base_ok and repeat_ok,
            f"base={base_sev.value}/{base_route} repeat={repeat_sev.value}/{repeat_route}",
        )

    if kind == "playbook_strength":
        candidates = [_SimpleCandidate(c) for c in fixture["candidates"]]

        def _choose(effective_severity: str, signals: list[FeedbackSignal]) -> str | None:
            evidence = {"severity": effective_severity, "normalized_event": {}}
            matches = [c for c in candidates if _criteria_match(c.criteria, evidence)]
            if not matches:
                return None
            if len(matches) == 1:
                return matches[0].id
            stronger = prefer_stronger_playbook(matches, signals, cfg)
            return stronger.id if stronger else matches[0].id

        base_id = _choose(fixture["severity"], [])
        repeat_sev = decide_severity_bias(Severity(fixture["severity"]), signals, cfg).value
        repeat_id = _choose(repeat_sev, signals)

        base_ok = base_id == fixture["baseline"]["expected_playbook"]
        repeat_ok = repeat_id == fixture["repeat"]["expected_playbook"]
        return (
            base_ok and repeat_ok,
            f"base={base_id} repeat={repeat_id}",
        )

    return False, f"unknown fixture kind: {kind}"


async def run_feedback(spec: GateSpec, provider: str | None = None) -> GateResult:
    """Run feedback-effectiveness fixtures and score baseline-vs-repeat deltas."""
    fixture_dir = _FIXTURE_DIR
    if not fixture_dir.exists():
        return GateResult(
            gate=spec.name,
            kind=spec.kind,
            provider=provider,
            score=0.0,
            threshold=spec.threshold,
            passed=None,
            blocking=spec.kind == GateKind.required,
            evidence="fixture directory missing",
        )

    required = set(spec.threshold.get("fixtures", []))
    if not required:
        return GateResult(
            gate=spec.name,
            kind=spec.kind,
            provider=provider,
            score=0.0,
            threshold=spec.threshold,
            passed=None,
            blocking=spec.kind == GateKind.required,
            evidence="no fixtures declared in threshold",
        )

    passed = 0
    total = 0
    failed_cases: list[str] = []

    for fixture_path in sorted(fixture_dir.glob("*.json")):
        data = json.loads(fixture_path.read_text())
        name = data.get("name", fixture_path.stem)
        if name not in required:
            continue
        total += 1
        ok, detail = _evaluate_fixture(data)
        if ok:
            passed += 1
        else:
            failed_cases.append(f"{name}: {detail}")

    if total == 0:
        return GateResult(
            gate=spec.name,
            kind=spec.kind,
            provider=provider,
            score=0.0,
            threshold=spec.threshold,
            passed=None,
            blocking=spec.kind == GateKind.required,
            evidence="no required fixture files found",
        )

    pass_rate = passed / total
    threshold_rate = spec.threshold.get("pass_rate", 1.0)
    evidence = f"{passed}/{total} fixtures passed"
    if failed_cases:
        evidence += "; failures: " + "; ".join(failed_cases)

    return GateResult(
        gate=spec.name,
        kind=spec.kind,
        provider=provider,
        score=pass_rate,
        threshold=spec.threshold,
        passed=pass_rate >= threshold_rate,
        blocking=spec.kind == GateKind.required,
        evidence=evidence,
    )


GATE_REGISTRY["feedback"] = run_feedback
