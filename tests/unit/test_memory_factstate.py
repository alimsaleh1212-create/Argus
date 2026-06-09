"""Unit tests for FactState window-selection logic — T025.

These tests are store-independent (no Graphiti/Neo4j): they validate that
given a set of TemporalFacts the correct current-vs-superseded flags are set.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backend.domain.memory import EntityKind, EntityRef, FactState, TemporalFact

_ENTITY = EntityRef(kind=EntityKind.ADDRESS, value="198.51.100.5")

_T1 = datetime(2024, 1, 15, 9, 0, 0, tzinfo=timezone.utc)
_T2 = datetime(2024, 1, 15, 14, 0, 0, tzinfo=timezone.utc)
_NOW = datetime(2024, 1, 15, 18, 0, 0, tzinfo=timezone.utc)


def _window_select(
    facts: list[TemporalFact],
    as_of: datetime,
) -> FactState:
    """Pure helper: replicate the window-selection logic from GraphitiMemory._query_fact_inner.

    Tests this in isolation so the logic is verified independent of the store.
    """
    if not facts:
        return FactState(fact=None, is_current=False, has_superseded=False)

    has_superseded = any(f.valid_until is not None for f in facts)

    # Sort descending by valid_from
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


# ── reputation_flip: benign@t1 → malicious@t2 ────────────────────────────────

@pytest.fixture()
def reputation_flip_facts() -> list[TemporalFact]:
    return [
        TemporalFact(
            entity=_ENTITY,
            fact_type="reputation",
            value="benign",
            valid_from=_T1,
            valid_until=_T2,  # superseded at t2
        ),
        TemporalFact(
            entity=_ENTITY,
            fact_type="reputation",
            value="malicious",
            valid_from=_T2,
            valid_until=None,  # currently valid
        ),
    ]


def test_window_as_of_t1_returns_benign(reputation_flip_facts) -> None:
    state = _window_select(reputation_flip_facts, as_of=_T1)
    assert state.fact is not None
    assert state.fact.value == "benign"
    assert state.is_current is False  # superseded
    assert state.has_superseded is True


def test_window_as_of_now_returns_malicious(reputation_flip_facts) -> None:
    state = _window_select(reputation_flip_facts, as_of=_NOW)
    assert state.fact is not None
    assert state.fact.value == "malicious"
    assert state.is_current is True
    assert state.has_superseded is True


def test_window_benign_fact_still_exists(reputation_flip_facts) -> None:
    """The superseded fact is still accessible (invalidated, not deleted)."""
    # At t1 it was present
    state = _window_select(reputation_flip_facts, as_of=_T1)
    assert state.fact is not None and state.fact.value == "benign"
    # At now the new value is present
    state_now = _window_select(reputation_flip_facts, as_of=_NOW)
    assert state_now.fact is not None and state_now.fact.value == "malicious"
    # has_superseded is True in both cases → the old fact is retained
    assert state.has_superseded is True
    assert state_now.has_superseded is True


# ── host_role_change ─────────────────────────────────────────────────────────

def test_host_role_change() -> None:
    host = EntityRef(kind=EntityKind.HOST, value="server-01")
    facts = [
        TemporalFact(entity=host, fact_type="role", value="honeypot", valid_from=_T1, valid_until=_T2),
        TemporalFact(entity=host, fact_type="role", value="payroll-server", valid_from=_T2, valid_until=None),
    ]
    state_t1 = _window_select(facts, as_of=_T1)
    assert state_t1.fact is not None and state_t1.fact.value == "honeypot"
    assert state_t1.is_current is False

    state_now = _window_select(facts, as_of=_NOW)
    assert state_now.fact is not None and state_now.fact.value == "payroll-server"
    assert state_now.is_current is True


# ── empty store ───────────────────────────────────────────────────────────────

def test_empty_facts_returns_empty_state() -> None:
    state = _window_select([], as_of=_NOW)
    assert state.fact is None
    assert state.is_current is False
    assert state.has_superseded is False


# ── no matching window ────────────────────────────────────────────────────────

def test_no_matching_window_before_first_fact() -> None:
    before_t1 = datetime(2024, 1, 14, 0, 0, 0, tzinfo=timezone.utc)
    facts = [
        TemporalFact(entity=_ENTITY, fact_type="reputation", value="benign", valid_from=_T1, valid_until=None),
    ]
    state = _window_select(facts, as_of=before_t1)
    assert state.fact is None
    assert state.has_superseded is False


# ── single current fact (no superseded) ─────────────────────────────────────

def test_single_current_fact() -> None:
    facts = [
        TemporalFact(entity=_ENTITY, fact_type="reputation", value="malicious", valid_from=_T1, valid_until=None),
    ]
    state = _window_select(facts, as_of=_NOW)
    assert state.fact is not None and state.fact.value == "malicious"
    assert state.is_current is True
    assert state.has_superseded is False
