"""Detection eval gate (SPEC-detector #14).

Deterministic / provider-independent: drives the labeled replay set through
the pure `services.detector.evaluate()` and computes precision and recall
over match/threshold scenarios.

Scoring (per `contracts/detection-eval.md`):
  - TP: a malicious-labeled event (or threshold group) produces an alert
        attributed to its `expected_rule`.
  - FP: a benign-labeled event produces any alert.
  - FN: a malicious-labeled event/group produces no alert.

Threshold scenarios count as ONE expected detection per group (the Nth
qualifying event fires once — SC-007). Firing on fewer than N is an FN;
firing more than once per group is an FP.

Registered in the same change as the yaml declaration — orphan/stale
mismatch is a hard error (exit 2) per #13.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from backend.domain.eval import GateKind, GateResult, GateSpec
from backend.eval.gates import GATE_REGISTRY
from backend.services.detector import (
    evaluate,
    load_replay_events,
    load_rules,
)

_RULE_PATH = Path("tests/fixtures/detector/rules.yaml")
_REPLAY_PATH = Path("tests/fixtures/detector/replay/scenarios.json")


def _iso(value: object) -> str:
    """Normalize a timestamp value to an ISO string for key equality."""
    if isinstance(value, datetime):
        return value.isoformat()
    if value is None:
        return ""
    return str(value)


def _load_labels() -> list[dict]:
    """Load the labeled replay set (raw, with labels and groups)."""
    if not _REPLAY_PATH.exists():
        return []
    raw = json.loads(_REPLAY_PATH.read_text())
    if not isinstance(raw, list):
        return []
    return raw


async def run_detection(spec: GateSpec, provider: str | None = None) -> GateResult:
    """Run the labeled replay set and compute precision + recall."""
    if not _RULE_PATH.exists() or not _REPLAY_PATH.exists():
        return GateResult(
            gate=spec.name,
            kind=spec.kind,
            provider=provider,
            score={"precision": 0.0, "recall": 0.0},
            threshold=spec.threshold,
            passed=None,
            blocking=spec.kind == GateKind.required,
            evidence="detector fixtures missing",
        )

    rules = load_rules(_RULE_PATH)
    events = load_replay_events(_REPLAY_PATH)
    labels = _load_labels()

    fired = evaluate(events, rules)

    # Index fired alerts by (group_key) for threshold groups, by a hashable
    # event-time+source+fields key for per-event matches. JSON-stringify the
    # fields to keep it deterministic and hashable; normalize the time to
    # ISO-string so label and fired-alert keys can match.
    fired_by_event: dict[tuple, str] = {}
    fired_by_group: dict[str, str] = {}
    for f in fired:
        if f.group_key is not None:
            fired_by_group.setdefault(f.group_key, f.rule_id)
        else:
            key = (
                _iso(f.event.event_time),
                f.event.source_host,
                json.dumps(f.event.fields, sort_keys=True, default=str),
            )
            fired_by_event[key] = f.rule_id

    def _expected_event_match(rec: dict) -> str | None:
        et = _iso(rec.get("event_time"))
        sh = rec.get("source_host")
        fields = rec.get("fields") or rec.get("data") or {}
        key = (et, sh, json.dumps(fields, sort_keys=True, default=str))
        return fired_by_event.get(key)

    def _expected_group_match(rec: dict) -> str | None:
        # Threshold labels carry `group_key` (the value of the rule's
        # `group_by` field) — that's what `FiredAlert.group_key` holds.
        return fired_by_group.get(rec.get("group_key", ""))

    tp = fp = fn = 0
    failures: list[str] = []

    # Threshold scenarios: count groups, not events.
    threshold_groups: dict[str, dict] = {}
    for rec in labels:
        if rec.get("label") == "malicious" and "group" in rec:
            gid = rec["group"]
            threshold_groups[gid] = rec  # any record carries the expected_rule
    for gid, rec in threshold_groups.items():
        expected = rec.get("expected_rule")
        actual = _expected_group_match(rec)
        if actual == expected:
            tp += 1
        else:
            fn += 1
            failures.append(f"threshold group {gid}: expected={expected} got={actual}")

    # Per-event scenarios (label+expected_rule, no group).
    for rec in labels:
        if rec.get("label") != "malicious" or "group" in rec:
            continue
        expected = rec.get("expected_rule")
        actual = _expected_event_match(rec)
        if actual == expected:
            tp += 1
        else:
            fn += 1
            failures.append(
                f"per-event expected={expected} got={actual} event_time={rec.get('event_time')}"
            )

    # Benign events: any fired alert here is an FP.
    benign_records = [r for r in labels if r.get("label") == "benign"]
    for rec in benign_records:
        et = _iso(rec.get("event_time"))
        sh = rec.get("source_host")
        fields = rec.get("fields") or rec.get("data") or {}
        key = (et, sh, json.dumps(fields, sort_keys=True, default=str))
        actual = fired_by_event.get(key)
        if actual is not None:
            fp += 1
            failures.append(f"benign event fired alert {actual} event_time={et}")

    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    threshold = spec.threshold
    p_min = float(threshold.get("precision_min", 0.90))
    r_min = float(threshold.get("recall_min", 0.90))
    passed = precision >= p_min and recall >= r_min
    evidence = f"tp={tp} fp={fp} fn={fn} precision={precision:.2f} recall={recall:.2f}"
    if failures:
        evidence += "; " + "; ".join(failures)

    return GateResult(
        gate=spec.name,
        kind=spec.kind,
        provider=provider,
        score={"precision": precision, "recall": recall},
        threshold=spec.threshold,
        passed=passed,
        blocking=spec.kind == GateKind.required,
        evidence=evidence,
    )


GATE_REGISTRY["detection"] = run_detection
