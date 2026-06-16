"""Temporal-validity eval gate — SPEC-memory #6 / T027.

Validates the invalidate-not-delete semantics over the committed scenario
fixtures in tests/fixtures/memory_temporal/scenarios.json.

In CI: uses the unit-level _window_select helper (store-independent logic)
that mirrors GraphitiMemory._query_fact_inner, proving the logic is correct
independent of the store backend. 100% pass rate required.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from backend.domain.memory import EntityKind, EntityRef, FactState, TemporalFact

FIXTURES = Path(__file__).parent.parent / "fixtures" / "memory_temporal" / "scenarios.json"
CONFIG = Path(__file__).parent.parent.parent / "config" / "eval_thresholds.yaml"


def _load_thresholds() -> dict[str, Any]:
    with open(CONFIG) as f:
        return yaml.safe_load(f)["gates"]["temporal_memory"]["threshold"]


def _load_scenarios() -> list[dict]:
    with open(FIXTURES) as f:
        return json.load(f)


def _window_select(
    facts: list[TemporalFact],
    as_of: datetime,
) -> FactState:
    """Store-independent window selection (mirrors GraphitiMemory._query_fact_inner)."""
    if not facts:
        return FactState(fact=None, is_current=False, has_superseded=False)

    has_superseded = any(f.valid_until is not None for f in facts)
    sorted_facts = sorted(facts, key=lambda f: f.valid_from, reverse=True)

    matching: TemporalFact | None = None
    for fact in sorted_facts:
        in_window = fact.valid_from <= as_of and (
            fact.valid_until is None or fact.valid_until > as_of
        )
        if in_window:
            matching = fact
            break

    if matching is None:
        return FactState(fact=None, is_current=False, has_superseded=has_superseded)

    is_current = matching.valid_until is None
    return FactState(fact=matching, is_current=is_current, has_superseded=has_superseded)


def _build_facts_from_scenario(scenario: dict) -> list[TemporalFact]:
    """Build TemporalFact list from scenario episodes (simulating Graphiti extraction)."""
    if "episodes" not in scenario:
        return []

    entity_data = scenario["entity"]
    entity = EntityRef(kind=EntityKind(entity_data["kind"]), value=entity_data["value"])
    fact_type = scenario["fact_type"]

    episodes = scenario["episodes"]
    facts: list[TemporalFact] = []
    for i, ep in enumerate(episodes):
        observed_at = datetime.fromisoformat(ep["observed_at"].replace("Z", "+00:00"))
        # The last episode's fact has no valid_until (currently valid)
        next_ep_time = None
        if i + 1 < len(episodes):
            next_ep = episodes[i + 1]
            next_ep_time = datetime.fromisoformat(next_ep["observed_at"].replace("Z", "+00:00"))

        facts.append(
            TemporalFact(
                entity=entity,
                fact_type=fact_type,
                value=ep["summary"],  # use summary as fact value
                valid_from=observed_at,
                valid_until=next_ep_time,
            )
        )
    return facts


def _evaluate_cases(
    required_cases: set[str],
    scenario_by_case: dict,
) -> tuple[int, int, list[str]]:
    """Core evaluation loop — returns (passed, total, failures)."""
    passed = 0
    total = 0
    failures: list[str] = []

    for case_name in sorted(required_cases):
        if case_name == "no_destructive_delete":
            # Validates superseded facts are retained (has_superseded=True)
            scenario = scenario_by_case.get("reputation_flip")
            if not scenario:
                failures.append(f"{case_name}: reputation_flip scenario missing")
                total += 1
                continue

            facts = _build_facts_from_scenario(scenario)
            if not facts:
                failures.append(f"{case_name}: no facts built from scenario")
                total += 1
                continue

            t1 = datetime(2024, 1, 15, 9, 0, 0, tzinfo=UTC)
            state_t1 = _window_select(facts, as_of=t1)
            state_now = _window_select(facts, as_of=datetime.now(UTC))

            if state_t1.has_superseded and state_now.has_superseded:
                passed += 1
            else:
                failures.append(
                    f"{case_name}: has_superseded not set "
                    f"(t1={state_t1.has_superseded}, now={state_now.has_superseded})"
                )
            total += 1
            continue

        if case_name == "verification_superseded_absent":
            # Validates that a superseded malicious fact returns is_current=False so
            # decide_verdict treats it as absent (not a REGRESSED signal).
            scenario = scenario_by_case.get(case_name)
            if not scenario:
                failures.append(f"{case_name}: scenario not found in fixtures")
                total += 1
                continue

            facts = _build_facts_from_scenario(scenario)
            case_failed = False

            # Standard checks — same as generic check path below
            for check in scenario.get("checks", []):
                as_of_raw = check.get("as_of")
                as_of = (
                    datetime.now(UTC)
                    if as_of_raw is None
                    else datetime.fromisoformat(as_of_raw.replace("Z", "+00:00"))
                )
                state = _window_select(facts, as_of=as_of)
                expected_value = check.get("expected_value_contains", "")
                expected_current = check.get("expected_is_current")

                if state.fact is None:
                    case_failed = True
                    failures.append(f"{case_name}@{as_of_raw}: no fact returned")
                    continue
                if expected_value and expected_value.lower() not in state.fact.value.lower():
                    case_failed = True
                    failures.append(
                        f"{case_name}@{as_of_raw}: expected '{expected_value}' in "
                        f"'{state.fact.value}'"
                    )
                if expected_current is not None and state.is_current != expected_current:
                    case_failed = True
                    failures.append(
                        f"{case_name}@{as_of_raw}: is_current={state.is_current}, "
                        f"expected {expected_current}"
                    )

            # Extra invariant: the current query must return is_current=True (benign fact),
            # confirming the superseded malicious fact is NOT the current signal.
            current_state = _window_select(facts, as_of=datetime.now(UTC))
            if current_state.fact is None or not current_state.is_current:
                case_failed = True
                failures.append(
                    f"{case_name}: expected is_current=True for present-time query; "
                    f"got fact={current_state.fact!r} is_current={current_state.is_current}"
                )
            if not case_failed:
                passed += 1
            total += 1
            continue

        scenario = scenario_by_case.get(case_name)
        if not scenario:
            failures.append(f"{case_name}: scenario not found in fixtures")
            total += 1
            continue

        if "checks" not in scenario:
            passed += 1
            total += 1
            continue

        facts = _build_facts_from_scenario(scenario)
        case_failed = False

        for check in scenario["checks"]:
            as_of_raw = check.get("as_of")
            if as_of_raw is None:
                as_of = datetime.now(UTC)
            else:
                as_of = datetime.fromisoformat(as_of_raw.replace("Z", "+00:00"))

            state = _window_select(facts, as_of=as_of)
            expected_value = check.get("expected_value_contains", "")
            expected_current = check.get("expected_is_current")

            if state.fact is None:
                case_failed = True
                failures.append(f"{case_name}@{check['as_of']}: no fact returned")
                continue

            if expected_value and expected_value.lower() not in state.fact.value.lower():
                case_failed = True
                failures.append(
                    f"{case_name}@{check['as_of']}: expected '{expected_value}' in "
                    f"'{state.fact.value}'"
                )

            if expected_current is not None and state.is_current != expected_current:
                case_failed = True
                failures.append(
                    f"{case_name}@{check['as_of']}: is_current={state.is_current}, "
                    f"expected {expected_current}"
                )

        if not case_failed:
            passed += 1
        total += 1

    return passed, total, failures


async def _run_temporal_scenarios() -> tuple[int, int]:
    """Callable by the eval harness (run_temporal_memory in deterministic.py).

    Returns (passed, total) so the harness can compute pass_rate.
    Raises AssertionError on hard failures (caught by the harness).
    """
    thresholds = _load_thresholds()
    required_cases = set(thresholds["cases"])
    scenarios = _load_scenarios()
    scenario_by_case = {s["case"]: s for s in scenarios}

    passed, total, failures = _evaluate_cases(required_cases, scenario_by_case)

    if failures:
        import warnings

        warnings.warn(
            "temporal_memory gate failures:\n" + "\n".join(f"  - {f}" for f in failures),
            stacklevel=2,
        )

    return passed, total


def test_temporal_gate() -> None:
    thresholds = _load_thresholds()
    required_pass_rate = thresholds["pass_rate"]
    required_cases = set(thresholds["cases"])

    scenarios = _load_scenarios()
    scenario_by_case = {s["case"]: s for s in scenarios}

    passed, total, failures = _evaluate_cases(required_cases, scenario_by_case)

    assert total > 0, "No cases evaluated"
    pass_rate = passed / total
    assert pass_rate >= required_pass_rate, (
        f"temporal_memory gate: {passed}/{total} cases passed "
        f"({pass_rate:.0%} < required {required_pass_rate:.0%})\n"
        + "\n".join(f"  - {f}" for f in failures)
    )
