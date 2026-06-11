"""Unit tests — T010: pure default-deny classify() policy (US1)."""

from __future__ import annotations

import uuid

import pytest

from backend.agents.response import classify
from backend.domain.response import ActionType, RemediationAction, RemediationPlan, RiskClass
from backend.infra.config import ResponseSettings


def _plan(action_types: list[ActionType]) -> RemediationPlan:
    inc_id = str(uuid.uuid4())
    plan_id = "testplan"
    return RemediationPlan(
        plan_id=plan_id,
        playbook_id="test",
        actions=[
            RemediationAction(
                type=t,
                target="host1",
                risk=RiskClass.AUTO,  # placeholder, overwritten by classify
                idempotency_key=f"{inc_id}:{plan_id}:{t.value}:host1",
            )
            for t in action_types
        ],
        rationale="test plan",
        selected_by="deterministic",
    )


def test_allowlist_actions_get_auto():
    cfg = ResponseSettings()
    plan = classify(_plan([ActionType.ADD_TO_WATCHLIST, ActionType.OPEN_TICKET, ActionType.ENRICH_AND_TAG]), cfg)
    for action in plan.actions:
        assert action.risk == RiskClass.AUTO


def test_destructive_actions_get_approval_required():
    cfg = ResponseSettings()
    plan = classify(_plan([ActionType.ISOLATE_HOST, ActionType.DISABLE_USER, ActionType.BLOCK_IP]), cfg)
    for action in plan.actions:
        assert action.risk == RiskClass.APPROVAL_REQUIRED


def test_mixed_plan_classified_correctly():
    cfg = ResponseSettings()
    plan = classify(
        _plan([ActionType.ADD_TO_WATCHLIST, ActionType.ISOLATE_HOST, ActionType.OPEN_TICKET]),
        cfg,
    )
    risks = {a.type: a.risk for a in plan.actions}
    assert risks[ActionType.ADD_TO_WATCHLIST] == RiskClass.AUTO
    assert risks[ActionType.ISOLATE_HOST] == RiskClass.APPROVAL_REQUIRED
    assert risks[ActionType.OPEN_TICKET] == RiskClass.AUTO


def test_default_deny_custom_allowlist():
    """With a custom allowlist, only explicitly listed types are AUTO."""
    cfg = ResponseSettings(auto_execute_actions=["add_to_watchlist"])
    plan = classify(_plan([ActionType.ADD_TO_WATCHLIST, ActionType.OPEN_TICKET]), cfg)
    risks = {a.type: a.risk for a in plan.actions}
    assert risks[ActionType.ADD_TO_WATCHLIST] == RiskClass.AUTO
    assert risks[ActionType.OPEN_TICKET] == RiskClass.APPROVAL_REQUIRED


def test_empty_allowlist_all_approval_required():
    cfg = ResponseSettings(auto_execute_actions=[])
    plan = classify(_plan([ActionType.ADD_TO_WATCHLIST, ActionType.OPEN_TICKET]), cfg)
    for action in plan.actions:
        assert action.risk == RiskClass.APPROVAL_REQUIRED


def test_has_approval_required_property():
    cfg = ResponseSettings()
    auto_plan = classify(_plan([ActionType.ADD_TO_WATCHLIST]), cfg)
    assert auto_plan.has_approval_required is False

    mixed = classify(_plan([ActionType.ADD_TO_WATCHLIST, ActionType.ISOLATE_HOST]), cfg)
    assert mixed.has_approval_required is True
