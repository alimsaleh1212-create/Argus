"""Mock ActionExecutor registry — one executor per ActionType (RD9).

Real connectors (SOAR, EDR, firewall) are a later drop-in — same Protocol, no policy/audit changes needed.
Injected into the response stage ONLY (Constitution III).
"""

from __future__ import annotations

from backend.domain.response import (
    ActionExecutor,
    ActionResult,
    ActionStatus,
    ActionType,
    RemediationAction,
)


class _MockExecutor:
    """Generic mock executor that always returns applied."""

    def __init__(self, action_type: ActionType) -> None:
        self._type = action_type

    async def execute(self, action: RemediationAction) -> ActionResult:
        return ActionResult(
            type=action.type,
            target=action.target,
            status=ActionStatus.APPLIED,
            detail=f"mock:{self._type.value}:ok",
        )


class _FailingExecutor:
    """Mock executor that always returns failed (for test injection)."""

    def __init__(self, action_type: ActionType) -> None:
        self._type = action_type

    async def execute(self, action: RemediationAction) -> ActionResult:
        return ActionResult(
            type=action.type,
            target=action.target,
            status=ActionStatus.FAILED,
            detail=f"mock:{self._type.value}:failed",
        )


def build_mock_executors() -> dict[ActionType, ActionExecutor]:
    """Build the mock executor registry — one executor per catalog ActionType."""
    return {atype: _MockExecutor(atype) for atype in ActionType}


def build_failing_executors(*types: ActionType) -> dict[ActionType, ActionExecutor]:
    """Build a registry where the specified types always fail (for tests)."""
    registry: dict[ActionType, ActionExecutor] = {atype: _MockExecutor(atype) for atype in ActionType}
    for atype in types:
        registry[atype] = _FailingExecutor(atype)
    return registry
