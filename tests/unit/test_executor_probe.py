"""Unit tests — probe() on mock executors and test helpers (T006).

Tests are written FIRST per Constitution II. They will fail (ImportError / AttributeError)
until T013 adds probe() to the mock executor classes.
"""

from __future__ import annotations

import asyncio

import pytest

from backend.domain.response import (
    ActionType,
    ProbeState,
    RemediationAction,
    RiskClass,
)
from backend.infra.executors import (
    build_inconclusive_executors,
    build_mock_executors,
    build_regressed_executors,
)


def _action(atype: ActionType = ActionType.BLOCK_IP) -> RemediationAction:
    return RemediationAction(
        type=atype,
        target="1.2.3.4",
        params={},
        risk=RiskClass.AUTO,
        idempotency_key=f"test:{atype.value}",
    )


def _run(coro):  # type: ignore[no-untyped-def]
    return asyncio.get_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
# Default mock executors return EXPECTED
# ---------------------------------------------------------------------------


def test_mock_executor_probe_returns_expected():
    registry = build_mock_executors()
    executor = registry[ActionType.BLOCK_IP]
    result = _run(executor.probe(_action(ActionType.BLOCK_IP)))
    assert result.state == ProbeState.EXPECTED


def test_mock_executor_probe_all_types():
    registry = build_mock_executors()
    for atype in ActionType:
        result = _run(registry[atype].probe(_action(atype)))
        assert result.state == ProbeState.EXPECTED, f"Expected EXPECTED for {atype}"


# ---------------------------------------------------------------------------
# build_regressed_executors returns UNEXPECTED for specified types
# ---------------------------------------------------------------------------


def test_build_regressed_executors_specific_type():
    registry = build_regressed_executors(ActionType.BLOCK_IP)
    result = _run(registry[ActionType.BLOCK_IP].probe(_action(ActionType.BLOCK_IP)))
    assert result.state == ProbeState.UNEXPECTED


def test_build_regressed_executors_non_specified_stays_expected():
    registry = build_regressed_executors(ActionType.BLOCK_IP)
    result = _run(registry[ActionType.ISOLATE_HOST].probe(_action(ActionType.ISOLATE_HOST)))
    assert result.state == ProbeState.EXPECTED


def test_build_regressed_executors_multiple_types():
    registry = build_regressed_executors(ActionType.BLOCK_IP, ActionType.ISOLATE_HOST)
    for atype in (ActionType.BLOCK_IP, ActionType.ISOLATE_HOST):
        result = _run(registry[atype].probe(_action(atype)))
        assert result.state == ProbeState.UNEXPECTED


# ---------------------------------------------------------------------------
# build_inconclusive_executors returns INCONCLUSIVE for specified types
# ---------------------------------------------------------------------------


def test_build_inconclusive_executors_specific_type():
    registry = build_inconclusive_executors(ActionType.ADD_TO_WATCHLIST)
    result = _run(registry[ActionType.ADD_TO_WATCHLIST].probe(_action(ActionType.ADD_TO_WATCHLIST)))
    assert result.state == ProbeState.INCONCLUSIVE


def test_build_inconclusive_executors_non_specified_stays_expected():
    registry = build_inconclusive_executors(ActionType.ADD_TO_WATCHLIST)
    result = _run(registry[ActionType.BLOCK_IP].probe(_action(ActionType.BLOCK_IP)))
    assert result.state == ProbeState.EXPECTED


# ---------------------------------------------------------------------------
# ProbeResult type and target match the action
# ---------------------------------------------------------------------------


def test_probe_result_type_matches_action():
    registry = build_mock_executors()
    action = _action(ActionType.DISABLE_USER)
    action = action.model_copy(update={"target": "alice@example.com"})
    result = _run(registry[ActionType.DISABLE_USER].probe(action))
    assert result.type == ActionType.DISABLE_USER
    assert result.target == "alice@example.com"
