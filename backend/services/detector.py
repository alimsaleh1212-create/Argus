"""Deterministic rule/threshold detector — pure core (SPEC-detector #14).

Two functions live here, both pure (no I/O):

- `load_rules(path)` — read a YAML rule set, fail-fast on malformed
  rules, return an empty `RuleSet` on absent/empty file (FR-005, Edge Cases).
- `evaluate(events, rules)` — apply the rule set to a list of raw events
  and return a list of `FiredAlert`s (one per match; multi-match → single
  highest-severity per D4).

The runner (`backend.detector`) is the only I/O seam — it loads rules,
reads replay events from disk, and calls `intake.accept(source=...)`.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError

from backend.domain.detector import (
    FiredAlert,
    MatchRule,
    RawEvent,
    RuleSet,
    ThresholdRule,
    _BaseMatch,
    severity_rank,
)
from backend.domain.incident import Severity, WazuhAgent, WazuhAlert, WazuhRule

# ---------------------------------------------------------------------------
# Severity ↔ level band (inverse of level_to_severity)
# ---------------------------------------------------------------------------
# Used by fired_alert_to_wazuh_alert to pick a representative Wazuh `level`
# per severity band so the downstream `intake.accept()` re-derives the same
# `Severity` (data-model.md mapping). The midpoint of each band avoids edge
# cases that would land in an adjacent band.
_SEVERITY_TO_LEVEL: dict[Severity, int] = {
    Severity.LOW: 2,
    Severity.MEDIUM: 5,
    Severity.HIGH: 9,
    Severity.CRITICAL: 13,
}


# ---------------------------------------------------------------------------
# Rule-set loader
# ---------------------------------------------------------------------------


class _RawRule(BaseModel):
    """A single rule entry as it appears in YAML (before discriminator dispatch)."""

    model_config = ConfigDict(extra="forbid")

    id: str
    kind: str
    description: str
    field: str | None = None
    op: str | None = None
    value: str | None = None
    list_ref: str | None = None
    severity: str
    technique: str | None = None
    # threshold-only
    match: dict[str, Any] | None = None
    group_by: str | None = None
    count: int | None = None
    window_seconds: int | None = None


def load_rules(path: str | Path) -> RuleSet:
    """Load and validate a rule set from a YAML file (FR-005, Edge Cases).

    - Empty or absent file → empty `RuleSet` (no crash).
    - Invalid `regex` → raises `ValueError` (fail-fast, not per-event).
    - `op: in_list` without a defined `list_ref` → raises `ValueError`.
    - `op` in {equals, contains, regex} without `value` → raises `ValueError`.
    - Unknown top-level keys → raises `ValueError`.
    - Unknown rule keys / extra fields → raises `ValueError`.
    """
    path = Path(path)
    if not path.exists():
        return RuleSet()

    raw = yaml.safe_load(path.read_text())
    if raw is None:
        return RuleSet()
    if not isinstance(raw, dict):
        raise ValueError(f"rule set root must be a mapping, got {type(raw).__name__}")

    lists: dict[str, list[str]] = {}
    if "lists" in raw:
        lists_raw = raw["lists"]
        if not isinstance(lists_raw, dict):
            raise ValueError("'lists' must be a mapping of name → list[str]")
        for name, values in lists_raw.items():
            if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
                raise ValueError(f"list '{name}' must be a list of strings")
            lists[name] = values

    rules_raw = raw.get("rules", [])
    if not isinstance(rules_raw, list):
        raise ValueError("'rules' must be a list")

    rules: list[MatchRule | ThresholdRule] = []
    for i, entry in enumerate(rules_raw):
        rules.append(_parse_rule(entry, lists, index=i))

    return RuleSet(rules=rules, lists=lists)


def _parse_rule(
    entry: Any, lists: dict[str, list[str]], *, index: int
) -> MatchRule | ThresholdRule:
    if not isinstance(entry, dict):
        raise ValueError(f"rules[{index}]: expected a mapping, got {type(entry).__name__}")

    try:
        raw = _RawRule.model_validate(entry)
    except ValidationError as e:
        raise ValueError(f"rules[{index}]: {e}") from e

    if raw.kind == "match":
        if raw.op is None:
            raise ValueError(f"rules[{index}] ({raw.id}): match rule missing 'op'")
        if raw.op == "in_list":
            if not raw.list_ref:
                raise ValueError(f"rules[{index}] ({raw.id}): op='in_list' requires 'list_ref'")
            if raw.list_ref not in lists:
                raise ValueError(
                    f"rules[{index}] ({raw.id}): list_ref '{raw.list_ref}' "
                    f"is not defined under 'lists'"
                )
        else:
            if raw.value is None:
                raise ValueError(f"rules[{index}] ({raw.id}): op='{raw.op}' requires 'value'")
            if raw.op == "regex":
                try:
                    re.compile(raw.value)
                except re.error as e:
                    raise ValueError(
                        f"rules[{index}] ({raw.id}): invalid regex '{raw.value}': {e}"
                    ) from e
        try:
            return MatchRule(
                id=raw.id,
                description=raw.description,
                field=raw.field or "",  # validated above
                op=raw.op,  # type: ignore[arg-type]
                value=raw.value,
                list_ref=raw.list_ref,
                severity=Severity(raw.severity),
                technique=raw.technique,
            )
        except (ValidationError, ValueError) as e:
            raise ValueError(f"rules[{index}] ({raw.id}): {e}") from e

    if raw.kind == "threshold":
        if raw.match is None:
            raise ValueError(f"rules[{index}] ({raw.id}): threshold rule requires 'match' block")
        if raw.group_by is None or raw.count is None or raw.window_seconds is None:
            raise ValueError(
                f"rules[{index}] ({raw.id}): threshold rule requires "
                f"'group_by', 'count', 'window_seconds'"
            )
        try:
            match_block = _BaseMatch.model_validate(raw.match)
        except (ValidationError, ValueError) as e:
            raise ValueError(f"rules[{index}] ({raw.id}): invalid 'match' block: {e}") from e
        if match_block.op == "in_list" and (
            not match_block.list_ref or match_block.list_ref not in lists
        ):
            raise ValueError(
                f"rules[{index}] ({raw.id}): match op='in_list' list_ref "
                f"'{match_block.list_ref}' not defined"
            )
        if match_block.op == "regex":
            if match_block.value is None:
                raise ValueError(f"rules[{index}] ({raw.id}): match op='regex' requires 'value'")
            try:
                re.compile(match_block.value)
            except re.error as e:
                raise ValueError(
                    f"rules[{index}] ({raw.id}): invalid match regex '{match_block.value}': {e}"
                ) from e
        try:
            return ThresholdRule(
                id=raw.id,
                description=raw.description,
                match=match_block,
                group_by=raw.group_by,
                count=raw.count,
                window_seconds=raw.window_seconds,
                severity=Severity(raw.severity),
                technique=raw.technique,
            )
        except (ValidationError, ValueError) as e:
            raise ValueError(f"rules[{index}] ({raw.id}): {e}") from e

    raise ValueError(
        f"rules[{index}] ({raw.id}): unknown kind '{raw.kind}' (expected 'match' or 'threshold')"
    )


# ---------------------------------------------------------------------------
# Field-path resolution
# ---------------------------------------------------------------------------


def _resolve_field(event: RawEvent, dotted: str) -> Any:
    """Walk a dotted path into event.fields (e.g. 'data.src_ip')."""
    cur: Any = event.fields
    for part in dotted.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def _match_condition(match: _BaseMatch, event: RawEvent, lists: dict[str, list[str]]) -> bool:
    actual = _resolve_field(event, match.field)
    if actual is None:
        return False
    if match.op == "equals":
        return str(actual) == match.value
    if match.op == "contains":
        return match.value is not None and match.value in str(actual)
    if match.op == "regex":
        if match.value is None:
            return False
        return re.search(match.value, str(actual)) is not None
    if match.op == "in_list":
        if not match.list_ref or match.list_ref not in lists:
            return False
        return str(actual) in lists[match.list_ref]
    return False


# ---------------------------------------------------------------------------
# Pure evaluate
# ---------------------------------------------------------------------------


def _try_parse_event(rec: Any) -> RawEvent | None:
    """Coerce a JSON-shaped record into a RawEvent; skip malformed (FR-009)."""
    if not isinstance(rec, dict):
        return None
    if "event_time" not in rec:
        return None
    et_raw = rec["event_time"]
    if isinstance(et_raw, datetime):
        event_time = et_raw
    elif isinstance(et_raw, str):
        try:
            event_time = datetime.fromisoformat(et_raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    fields = rec.get("fields") or rec.get("data") or {}
    if not isinstance(fields, dict):
        return None
    source_host = rec.get("source_host")
    return RawEvent(
        event_time=event_time,
        fields=fields,
        source_host=source_host,
    )


def evaluate(events: list[RawEvent], rules: RuleSet) -> list[FiredAlert]:
    """Apply the rule/threshold set to a list of events (FR-002, FR-004, FR-011).

    - Match rules fire at most one alert per event (multi-match → single
      highest-severity, ties broken by config order per D4).
    - Threshold rules group qualifying events by `group_by` and fire **one**
      alert at the Nth qualifying event per group within `window_seconds`
      (windowed over `event_time` — no wall-clock).
    - Benign events / empty rule set → zero alerts (no crash; FR-003).
    - Malformed events are **skipped** by the caller (`_try_parse_event`).

    Pure: no I/O, no time-of-day, no randomness.
    """
    fired: list[FiredAlert] = []

    # ---- 1. signature / match path (FR-002, FR-011) ----
    threshold_rules = [r for r in rules.rules if isinstance(r, ThresholdRule)]
    match_rules = [r for r in rules.rules if isinstance(r, MatchRule)]

    # Per-event best match for match-rules; threshold rules do not feed the
    # signature per-event match (they have their own windowing pass).
    for event in events:
        best_match: tuple[int, int, MatchRule] | None = None
        for idx, rule in enumerate(match_rules):
            # MatchRule IS a _BaseMatch — pass it directly.
            if _match_condition(rule, event, rules.lists):
                rank = severity_rank(rule.severity)
                key = (rank, -idx)  # higher severity wins; lower idx wins ties
                if best_match is None or key > (best_match[0], -best_match[1]):
                    best_match = (key[0], idx, rule)
        if best_match is not None:
            _, _, rule = best_match
            fired.append(
                FiredAlert(
                    rule_id=rule.id,
                    description=rule.description,
                    severity=rule.severity,
                    technique=rule.technique,
                    event=event,
                )
            )

    # ---- 2. threshold / aggregation path (FR-004, SC-007) ----
    # Sort events by event_time once (stable; pass-through otherwise).
    for tr in threshold_rules:
        # Collect qualifying events per group, preserving event_time order.
        per_group: dict[str, list[RawEvent]] = defaultdict(list)
        for event in events:
            if _match_condition(tr.match, event, rules.lists):
                key = _resolve_field(event, tr.group_by)
                if key is None:
                    continue
                per_group[str(key)].append(event)
        for group_key, group_events in per_group.items():
            group_events.sort(key=lambda e: e.event_time)
            fired_alert_emitted = False
            for i, ev in enumerate(group_events):
                # Window: [ev.event_time - W, ev.event_time]; if this is the
                # Nth qualifying event in its window, fire exactly one.
                window_start = ev.event_time.timestamp() - tr.window_seconds
                count_in_window = 0
                for prior in group_events[: i + 1]:
                    if prior.event_time.timestamp() >= window_start:
                        count_in_window += 1
                if count_in_window >= tr.count and not fired_alert_emitted:
                    fired.append(
                        FiredAlert(
                            rule_id=tr.id,
                            description=tr.description,
                            severity=tr.severity,
                            technique=tr.technique,
                            event=ev,
                            group_key=group_key,
                        )
                    )
                    fired_alert_emitted = True
                    break  # exactly one per group (SC-007)

    return fired


# ---------------------------------------------------------------------------
# FiredAlert -> WazuhAlert (emission contract)
# ---------------------------------------------------------------------------


def fired_alert_to_wazuh_alert(fired: FiredAlert) -> WazuhAlert:
    """Map a FiredAlert to the existing WazuhAlert ingestion type (data-model.md).

    The severity→level band is the midpoint of `level_to_severity` so the
    downstream `intake.accept()` re-derives the same `Severity` through the
    existing band thresholds in `services/wazuh.py`.
    """
    level = _SEVERITY_TO_LEVEL[fired.severity]
    groups: list[str] = []
    if fired.technique:
        groups.append(fired.technique)
    return WazuhAlert(
        rule=WazuhRule(
            id=fired.rule_id,
            level=level,
            description=fired.description,
            groups=groups,
        ),
        agent=(WazuhAgent(name=fired.event.source_host) if fired.event.source_host else None),
        data=dict(fired.event.fields),
        full_log=_build_full_log(fired),
    )


def _build_full_log(fired: FiredAlert) -> str:
    """Build a compact deterministic summary string (D5)."""
    parts = [f"rule={fired.rule_id}", f"sev={fired.severity.value}"]
    if fired.group_key:
        parts.append(f"group={fired.group_key}")
    parts.append(f"ts={fired.event.event_time.isoformat()}")
    return "detector: " + " ".join(parts)


# ---------------------------------------------------------------------------
# Replay event loader (used by runner; pure-Python, no I/O surface here)
# ---------------------------------------------------------------------------


def load_replay_events(path: str | Path) -> list[RawEvent]:
    """Load a JSON replay set; malformed entries are skipped (FR-009)."""
    p = Path(path)
    if not p.exists():
        return []
    raw = json.loads(p.read_text())
    if not isinstance(raw, list):
        raise ValueError(f"replay set root must be a list, got {type(raw).__name__}")
    events: list[RawEvent] = []
    for rec in raw:
        ev = _try_parse_event(rec)
        if ev is not None:
            events.append(ev)
    return events
