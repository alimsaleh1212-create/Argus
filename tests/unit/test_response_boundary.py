"""Unit tests — T029: structural boundary — only the response stage is injected executors (US3)."""

from __future__ import annotations

import pytest

from backend.agents.response import make_response_handler
from backend.domain.response import ActionType
from backend.infra.executors import build_mock_executors


def test_response_handler_factory_accepts_executors():
    """make_response_handler accepts executors without error."""
    import contextlib
    executors = build_mock_executors()

    @contextlib.asynccontextmanager
    async def _sf():
        yield None

    from backend.agents.response import PlaybookEntry
    catalog = [PlaybookEntry("p", "d", {}, [])]
    from backend.infra.config import ResponseSettings

    # Should construct without error
    handler = make_response_handler(
        llm=None,
        session_factory=_sf,
        executors=executors,
        cfg=ResponseSettings(),
        catalog=catalog,
    )
    assert callable(handler)


def test_triage_handler_has_no_executors():
    """make_triage_handler signature does not accept executors — structural boundary."""
    import inspect
    from backend.agents.triage import make_triage_handler

    sig = inspect.signature(make_triage_handler)
    param_names = set(sig.parameters.keys())
    assert "executors" not in param_names, (
        "Triage handler must not accept executors (Constitution III / FR-002)"
    )


def test_enrichment_handler_has_no_executors():
    """make_enrichment_handler signature does not accept executors — structural boundary."""
    import inspect
    from backend.agents.enrichment import make_enrichment_handler

    sig = inspect.signature(make_enrichment_handler)
    param_names = set(sig.parameters.keys())
    assert "executors" not in param_names, (
        "Enrichment handler must not accept executors (Constitution III / FR-002)"
    )


def test_response_handler_signature_has_executors():
    """make_response_handler is the ONLY factory that accepts executors."""
    import inspect

    sig = inspect.signature(make_response_handler)
    assert "executors" in sig.parameters, (
        "Response handler factory must accept executors"
    )


def test_build_failing_executors_overrides_specified_types():
    """build_failing_executors marks specified types as failing, others as mock."""
    from backend.infra.executors import build_failing_executors

    executors = build_failing_executors(ActionType.ISOLATE_HOST, ActionType.BLOCK_IP)
    # All types present
    for atype in ActionType:
        assert atype in executors
    # The failing ones return a distinct executor
    assert executors[ActionType.ISOLATE_HOST] is not executors[ActionType.ADD_TO_WATCHLIST]


def test_all_action_types_covered_in_mock_executors():
    """Mock executor registry must cover all catalog ActionTypes."""
    executors = build_mock_executors()
    for atype in ActionType:
        assert atype in executors, f"No mock executor for {atype}"


def test_catalog_action_types_are_allowlisted_or_approval():
    """Every ActionType is either in the auto allowlist or is approval-required — nothing is dropped silently."""
    from backend.infra.config import ResponseSettings
    from backend.agents.response import classify, PlaybookEntry, _build_actions
    import uuid

    cfg = ResponseSettings()
    inc_id = str(uuid.uuid4())
    plan_id = "testplan"

    all_actions = _build_actions(
        [{"type": t.value} for t in ActionType], inc_id, plan_id
    )

    from backend.domain.response import RemediationPlan, RiskClass
    plan = RemediationPlan(
        plan_id=plan_id,
        playbook_id="test",
        actions=all_actions,
        rationale="all actions",
        selected_by="deterministic",
    )
    classified = classify(plan, cfg)

    for action in classified.actions:
        assert action.risk in (RiskClass.AUTO, RiskClass.APPROVAL_REQUIRED), (
            f"Action {action.type} has unexpected risk class {action.risk}"
        )
