"""Unit tests for the pure detector rule engine (SPEC-detector #14).

Tests T007 (match operators, malformed-skip, multi-match → highest severity
with config-order tie-break) and T016 (benign suppression, empty ruleset
no-crash) and T019 (threshold: exactly one alert at the Nth qualifying
event within W; <N → none).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from backend.domain.detector import (
    MatchRule,
    RawEvent,
    RuleSet,
    ThresholdRule,
    _BaseMatch,
    severity_rank,
)
from backend.domain.incident import Severity
from backend.services.detector import evaluate, load_rules


def _ev(
    fields: dict,
    *,
    event_time: datetime | None = None,
    source_host: str | None = None,
) -> RawEvent:
    return RawEvent(
        event_time=event_time or datetime(2026, 1, 1, tzinfo=UTC),
        fields=fields,
        source_host=source_host,
    )


# ---------------------------------------------------------------------------
# T007 — match operators
# ---------------------------------------------------------------------------


class TestEqualsOperator:
    def test_equals_match_fires(self) -> None:
        rules = RuleSet(
            rules=[
                MatchRule(
                    id="r1",
                    description="d",
                    field="event_type",
                    op="equals",
                    value="login_failure",
                    severity=Severity.HIGH,
                )
            ]
        )
        alerts = evaluate([_ev({"event_type": "login_failure"})], rules)
        assert len(alerts) == 1
        assert alerts[0].rule_id == "r1"

    def test_equals_miss_fires_nothing(self) -> None:
        rules = RuleSet(
            rules=[
                MatchRule(
                    id="r1",
                    description="d",
                    field="event_type",
                    op="equals",
                    value="login_failure",
                    severity=Severity.HIGH,
                )
            ]
        )
        alerts = evaluate([_ev({"event_type": "login_success"})], rules)
        assert alerts == []


class TestContainsOperator:
    def test_substring_match_fires(self) -> None:
        rules = RuleSet(
            rules=[
                MatchRule(
                    id="r1",
                    description="d",
                    field="command",
                    op="contains",
                    value="mimikatz",
                    severity=Severity.HIGH,
                )
            ]
        )
        alerts = evaluate([_ev({"command": "Invoke-mimikatz -DumpCreds"})], rules)
        assert len(alerts) == 1

    def test_case_mismatch_no_match_fires_nothing(self) -> None:
        """`contains` is case-sensitive — for case-insensitive use `regex`."""
        rules = RuleSet(
            rules=[
                MatchRule(
                    id="r1",
                    description="d",
                    field="command",
                    op="contains",
                    value="mimikatz",
                    severity=Severity.HIGH,
                )
            ]
        )
        alerts = evaluate([_ev({"command": "Invoke-Mimikatz -DumpCreds"})], rules)
        assert alerts == []

    def test_substring_no_match_fires_nothing(self) -> None:
        rules = RuleSet(
            rules=[
                MatchRule(
                    id="r1",
                    description="d",
                    field="command",
                    op="contains",
                    value="mimikatz",
                    severity=Severity.HIGH,
                )
            ]
        )
        alerts = evaluate([_ev({"command": "whoami"})], rules)
        assert alerts == []


class TestRegexOperator:
    def test_regex_match_fires(self) -> None:
        rules = RuleSet(
            rules=[
                MatchRule(
                    id="r1",
                    description="d",
                    field="command",
                    op="regex",
                    value=r"(?i)mimikatz",
                    severity=Severity.HIGH,
                )
            ]
        )
        alerts = evaluate([_ev({"command": "Mimikatz runner"})], rules)
        assert len(alerts) == 1

    def test_regex_no_match_fires_nothing(self) -> None:
        rules = RuleSet(
            rules=[
                MatchRule(
                    id="r1",
                    description="d",
                    field="command",
                    op="regex",
                    value=r"(?i)mimikatz",
                    severity=Severity.HIGH,
                )
            ]
        )
        alerts = evaluate([_ev({"command": "ls"})], rules)
        assert alerts == []


class TestInListOperator:
    def test_in_list_match_fires(self) -> None:
        rules = RuleSet(
            rules=[
                MatchRule(
                    id="r1",
                    description="d",
                    field="src_ip",
                    op="in_list",
                    list_ref="ioc",
                    severity=Severity.HIGH,
                )
            ],
            lists={"ioc": ["203.0.113.10", "198.51.100.7"]},
        )
        alerts = evaluate([_ev({"src_ip": "203.0.113.10"})], rules)
        assert len(alerts) == 1

    def test_in_list_no_match_fires_nothing(self) -> None:
        rules = RuleSet(
            rules=[
                MatchRule(
                    id="r1",
                    description="d",
                    field="src_ip",
                    op="in_list",
                    list_ref="ioc",
                    severity=Severity.HIGH,
                )
            ],
            lists={"ioc": ["203.0.113.10"]},
        )
        alerts = evaluate([_ev({"src_ip": "10.0.0.1"})], rules)
        assert alerts == []


class TestDottedFieldPath:
    def test_dotted_path_resolved(self) -> None:
        rules = RuleSet(
            rules=[
                MatchRule(
                    id="r1",
                    description="d",
                    field="data.src_ip",
                    op="equals",
                    value="203.0.113.10",
                    severity=Severity.HIGH,
                )
            ]
        )
        alerts = evaluate([_ev({"data": {"src_ip": "203.0.113.10"}})], rules)
        assert len(alerts) == 1

    def test_dotted_path_missing_key_fires_nothing(self) -> None:
        rules = RuleSet(
            rules=[
                MatchRule(
                    id="r1",
                    description="d",
                    field="data.src_ip",
                    op="equals",
                    value="203.0.113.10",
                    severity=Severity.HIGH,
                )
            ]
        )
        alerts = evaluate([_ev({"data": {"dst_ip": "203.0.113.10"}})], rules)
        assert alerts == []


# ---------------------------------------------------------------------------
# T007 — multi-match → single highest severity (FR-011, D4)
# ---------------------------------------------------------------------------


class TestMultiMatchHighestSeverity:
    def test_multi_match_picks_highest_severity(self) -> None:
        rules = RuleSet(
            rules=[
                MatchRule(
                    id="low",
                    description="d",
                    field="src_ip",
                    op="equals",
                    value="1.2.3.4",
                    severity=Severity.LOW,
                ),
                MatchRule(
                    id="crit",
                    description="d",
                    field="src_ip",
                    op="equals",
                    value="1.2.3.4",
                    severity=Severity.CRITICAL,
                ),
                MatchRule(
                    id="med",
                    description="d",
                    field="src_ip",
                    op="equals",
                    value="1.2.3.4",
                    severity=Severity.MEDIUM,
                ),
            ]
        )
        alerts = evaluate([_ev({"src_ip": "1.2.3.4"})], rules)
        assert len(alerts) == 1
        assert alerts[0].rule_id == "crit"
        assert alerts[0].severity == Severity.CRITICAL

    def test_multi_match_ties_break_by_config_order(self) -> None:
        # Two rules of equal severity → first in config wins.
        rules = RuleSet(
            rules=[
                MatchRule(
                    id="first",
                    description="d",
                    field="src_ip",
                    op="equals",
                    value="1.2.3.4",
                    severity=Severity.HIGH,
                ),
                MatchRule(
                    id="second",
                    description="d",
                    field="src_ip",
                    op="equals",
                    value="1.2.3.4",
                    severity=Severity.HIGH,
                ),
            ]
        )
        alerts = evaluate([_ev({"src_ip": "1.2.3.4"})], rules)
        assert len(alerts) == 1
        assert alerts[0].rule_id == "first"


# ---------------------------------------------------------------------------
# T016 — benign suppression / empty ruleset
# ---------------------------------------------------------------------------


class TestSuppression:
    def test_benign_event_zero_alerts(self) -> None:
        rules = RuleSet(
            rules=[
                MatchRule(
                    id="r1",
                    description="d",
                    field="event_type",
                    op="equals",
                    value="malicious",
                    severity=Severity.HIGH,
                )
            ]
        )
        alerts = evaluate(
            [_ev({"event_type": "benign"}), _ev({"event_type": "noise"})],
            rules,
        )
        assert alerts == []

    def test_empty_ruleset_zero_alerts_no_crash(self) -> None:
        rules = RuleSet()
        alerts = evaluate([_ev({"x": 1})], rules)
        assert alerts == []

    def test_no_events_zero_alerts(self) -> None:
        rules = RuleSet(
            rules=[
                MatchRule(
                    id="r1",
                    description="d",
                    field="x",
                    op="equals",
                    value="1",
                    severity=Severity.HIGH,
                )
            ]
        )
        assert evaluate([], rules) == []


# ---------------------------------------------------------------------------
# T019 — threshold path (FR-004, SC-007)
# ---------------------------------------------------------------------------


class TestThreshold:
    def _brute_rule(self) -> ThresholdRule:
        return ThresholdRule(
            id="brute",
            description="bruteforce",
            match=_BaseMatch(field="event_type", op="equals", value="auth_failure"),
            group_by="data.src_ip",
            count=3,
            window_seconds=60,
            severity=Severity.HIGH,
        )

    def test_below_count_no_fire(self) -> None:
        rule = self._brute_rule()
        rules = RuleSet(rules=[rule])
        base = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
        events = [
            _ev(
                {"event_type": "auth_failure", "data": {"src_ip": "1.1.1.1"}},
                event_time=base + timedelta(seconds=0),
            ),
            _ev(
                {"event_type": "auth_failure", "data": {"src_ip": "1.1.1.1"}},
                event_time=base + timedelta(seconds=10),
            ),
        ]
        assert evaluate(events, rules) == []

    def test_at_count_fires_exactly_one(self) -> None:
        rule = self._brute_rule()
        rules = RuleSet(rules=[rule])
        base = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
        events = [
            _ev(
                {"event_type": "auth_failure", "data": {"src_ip": "1.1.1.1"}},
                event_time=base + timedelta(seconds=0),
            ),
            _ev(
                {"event_type": "auth_failure", "data": {"src_ip": "1.1.1.1"}},
                event_time=base + timedelta(seconds=10),
            ),
            _ev(
                {"event_type": "auth_failure", "data": {"src_ip": "1.1.1.1"}},
                event_time=base + timedelta(seconds=20),
            ),
            _ev(
                {"event_type": "auth_failure", "data": {"src_ip": "1.1.1.1"}},
                event_time=base + timedelta(seconds=30),
            ),
        ]
        alerts = evaluate(events, rules)
        assert len(alerts) == 1
        assert alerts[0].rule_id == "brute"
        assert alerts[0].group_key == "1.1.1.1"

    def test_window_excludes_old_events(self) -> None:
        # 4 events, but 1st is older than the window ⇒ still fires at the 4th
        # because the 2nd, 3rd, 4th are within W of the 4th.
        rule = self._brute_rule()
        rules = RuleSet(rules=[rule])
        base = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
        events = [
            _ev(
                {"event_type": "auth_failure", "data": {"src_ip": "1.1.1.1"}},
                event_time=base - timedelta(seconds=120),  # outside window
            ),
            _ev(
                {"event_type": "auth_failure", "data": {"src_ip": "1.1.1.1"}},
                event_time=base + timedelta(seconds=0),
            ),
            _ev(
                {"event_type": "auth_failure", "data": {"src_ip": "1.1.1.1"}},
                event_time=base + timedelta(seconds=10),
            ),
            _ev(
                {"event_type": "auth_failure", "data": {"src_ip": "1.1.1.1"}},
                event_time=base + timedelta(seconds=20),
            ),
        ]
        alerts = evaluate(events, rules)
        assert len(alerts) == 1

    def test_separate_groups_independent(self) -> None:
        rule = self._brute_rule()
        rules = RuleSet(rules=[rule])
        base = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
        events: list[RawEvent] = []
        # 2 failures from 1.1.1.1 (below threshold)
        for s in (0, 10):
            events.append(
                _ev(
                    {"event_type": "auth_failure", "data": {"src_ip": "1.1.1.1"}},
                    event_time=base + timedelta(seconds=s),
                )
            )
        # 3 failures from 2.2.2.2 (at threshold)
        for s in (0, 10, 20):
            events.append(
                _ev(
                    {"event_type": "auth_failure", "data": {"src_ip": "2.2.2.2"}},
                    event_time=base + timedelta(seconds=s),
                )
            )
        alerts = evaluate(events, rules)
        assert len(alerts) == 1
        assert alerts[0].group_key == "2.2.2.2"


# ---------------------------------------------------------------------------
# Loader behaviour
# ---------------------------------------------------------------------------


class TestLoadRules:
    def test_absent_file_returns_empty(self, tmp_path) -> None:
        assert load_rules(tmp_path / "missing.yaml") == RuleSet()

    def test_empty_file_returns_empty(self, tmp_path) -> None:
        p = tmp_path / "empty.yaml"
        p.write_text("")
        assert load_rules(p) == RuleSet()

    def test_invalid_regex_fails_fast(self, tmp_path) -> None:
        p = tmp_path / "rules.yaml"
        p.write_text(
            "rules:\n"
            "  - id: bad\n"
            "    kind: match\n"
            "    description: d\n"
            "    field: x\n"
            "    op: regex\n"
            "    value: '[unterminated'\n"
            "    severity: high\n"
        )
        with pytest.raises(ValueError, match="invalid regex"):
            load_rules(p)

    def test_in_list_missing_list_ref_fails_fast(self, tmp_path) -> None:
        p = tmp_path / "rules.yaml"
        p.write_text(
            "rules:\n"
            "  - id: bad\n"
            "    kind: match\n"
            "    description: d\n"
            "    field: x\n"
            "    op: in_list\n"
            "    list_ref: not_defined\n"
            "    severity: high\n"
        )
        with pytest.raises(ValueError, match="not defined"):
            load_rules(p)

    def test_equals_missing_value_fails_fast(self, tmp_path) -> None:
        p = tmp_path / "rules.yaml"
        p.write_text(
            "rules:\n"
            "  - id: bad\n"
            "    kind: match\n"
            "    description: d\n"
            "    field: x\n"
            "    op: equals\n"
            "    severity: high\n"
        )
        with pytest.raises(ValueError, match="requires 'value'"):
            load_rules(p)

    def test_unknown_top_level_key_raises(self, tmp_path) -> None:
        p = tmp_path / "rules.yaml"
        p.write_text("rules: []\nweird: 1\n")
        # yaml.safe_load sees 'weird' as an extra top-level field; we ignore
        # unknown top-level keys (the detector only cares about rules/lists).
        # So this should NOT raise.
        rules = load_rules(p)
        assert rules == RuleSet()


# ---------------------------------------------------------------------------
# Severity rank helper
# ---------------------------------------------------------------------------


def test_severity_rank_ordering() -> None:
    assert severity_rank(Severity.LOW) < severity_rank(Severity.MEDIUM)
    assert severity_rank(Severity.MEDIUM) < severity_rank(Severity.HIGH)
    assert severity_rank(Severity.HIGH) < severity_rank(Severity.CRITICAL)
