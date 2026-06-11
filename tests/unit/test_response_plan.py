"""Unit tests — T011: RemediationPlan / RemediationAction / ActionResult validation (US1)."""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from backend.domain.response import (
    ActionResult,
    ActionStatus,
    ActionType,
    ApprovalDecision,
    ApprovalStatus,
    RemediationAction,
    RemediationPlan,
    RiskClass,
    VerificationVerdict,
)


def _key() -> str:
    return f"{uuid.uuid4().hex}:plan1:add_to_watchlist:host1"


# ---------------------------------------------------------------------------
# RemediationAction
# ---------------------------------------------------------------------------

def test_action_valid():
    a = RemediationAction(
        type=ActionType.ADD_TO_WATCHLIST,
        target="10.0.0.1",
        risk=RiskClass.AUTO,
        idempotency_key=_key(),
    )
    assert a.type == ActionType.ADD_TO_WATCHLIST
    assert a.risk == RiskClass.AUTO
    assert a.params == {}


def test_action_extra_fields_forbidden():
    with pytest.raises(ValidationError):
        RemediationAction(
            type=ActionType.ADD_TO_WATCHLIST,
            target="host",
            risk=RiskClass.AUTO,
            idempotency_key=_key(),
            unknown_field="bad",
        )


def test_action_invalid_type():
    with pytest.raises(ValidationError):
        RemediationAction(
            type="not_a_real_type",  # type: ignore[arg-type]
            target="host",
            risk=RiskClass.AUTO,
            idempotency_key=_key(),
        )


# ---------------------------------------------------------------------------
# RemediationPlan
# ---------------------------------------------------------------------------

def _action(atype: ActionType = ActionType.ADD_TO_WATCHLIST) -> RemediationAction:
    return RemediationAction(
        type=atype,
        target="host1",
        risk=RiskClass.AUTO,
        idempotency_key=_key(),
    )


def test_plan_valid():
    plan = RemediationPlan(
        plan_id="abc123",
        playbook_id="watchlist_and_ticket",
        actions=[_action()],
        rationale="Evidence supports watchlist",
        selected_by="deterministic",
    )
    assert plan.selected_by == "deterministic"
    assert plan.has_approval_required is False


def test_plan_requires_at_least_one_action():
    with pytest.raises(ValidationError):
        RemediationPlan(
            plan_id="x",
            playbook_id="y",
            actions=[],
            rationale="should fail",
            selected_by="deterministic",
        )


def test_plan_rationale_nonempty():
    with pytest.raises(ValidationError):
        RemediationPlan(
            plan_id="x",
            playbook_id="y",
            actions=[_action()],
            rationale="",
            selected_by="deterministic",
        )


def test_plan_frozen():
    plan = RemediationPlan(
        plan_id="abc",
        playbook_id="pb",
        actions=[_action()],
        rationale="r",
        selected_by="llm",
    )
    with pytest.raises(Exception):
        plan.plan_id = "changed"  # type: ignore[misc]


def test_plan_has_approval_required_true():
    plan = RemediationPlan(
        plan_id="abc",
        playbook_id="pb",
        actions=[_action(ActionType.ISOLATE_HOST)],
        rationale="isolate",
        selected_by="deterministic",
    )
    # classify hasn't run; but the property reads risk directly
    # Manually set risk to APPROVAL_REQUIRED for this test
    action_with_approval = _action(ActionType.ISOLATE_HOST).model_copy(
        update={"risk": RiskClass.APPROVAL_REQUIRED}
    )
    plan2 = plan.model_copy(update={"actions": [action_with_approval]})
    assert plan2.has_approval_required is True


# ---------------------------------------------------------------------------
# ActionResult
# ---------------------------------------------------------------------------

def test_result_applied_no_verification():
    r = ActionResult(
        type=ActionType.ADD_TO_WATCHLIST,
        target="host",
        status=ActionStatus.APPLIED,
    )
    assert r.verification is None  # RESERVED §v2c — always None in v1
    assert r.status == ActionStatus.APPLIED


def test_result_failed():
    r = ActionResult(
        type=ActionType.ISOLATE_HOST,
        target="host",
        status=ActionStatus.FAILED,
        detail="timeout",
    )
    assert r.status == ActionStatus.FAILED
    assert r.verification is None


def test_result_not_executed():
    r = ActionResult(
        type=ActionType.DISABLE_USER,
        target="user1",
        status=ActionStatus.NOT_EXECUTED,
    )
    assert r.status == ActionStatus.NOT_EXECUTED


# ---------------------------------------------------------------------------
# Enums round-trip
# ---------------------------------------------------------------------------

def test_approval_status_values():
    assert ApprovalStatus.PENDING.value == "pending"
    assert ApprovalStatus.APPROVED.value == "approved"
    assert ApprovalStatus.REJECTED.value == "rejected"
    assert ApprovalStatus.EXPIRED.value == "expired"


def test_approval_decision_values():
    assert ApprovalDecision.APPROVE.value == "approve"
    assert ApprovalDecision.REJECT.value == "reject"


def test_verification_verdict_reserved():
    assert VerificationVerdict.VERIFIED.value == "verified"
    assert VerificationVerdict.UNVERIFIED.value == "unverified"
    assert VerificationVerdict.REGRESSED.value == "regressed"
