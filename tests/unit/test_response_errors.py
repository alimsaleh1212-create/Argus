"""Unit tests — T031: fail-closed + error mapping (US3).

Covers: LlmError → ToolError mapping, executor failure → ToolError, catalog miss → ESCALATE,
malformed LLM output → ToolError, no_executor → ToolError.
"""

from __future__ import annotations

import contextlib
import uuid

import pytest

from backend.domain.incident import Incident, IncidentStatus, Severity
from backend.domain.pipeline import ToolError
from backend.domain.response import ActionType, RiskClass


def _incident(severity: str = "critical") -> Incident:
    ne = {"severity": severity, "rule_groups": []}
    return Incident(
        id=uuid.uuid4(),
        status=IncidentStatus.RESPONDING,
        severity=Severity.CRITICAL,
        correlation_id="corr-err",
        dedup_fingerprint=f"fp-err-{uuid.uuid4().hex}",
        source="wazuh",
        raw_alert={},
        evidence={"severity": severity, "normalized_event": ne},
    )


# ---------------------------------------------------------------------------
# LLM error mapping
# ---------------------------------------------------------------------------


def test_map_llm_transient_error_is_retryable():
    from backend.agents.response import _map_and_raise_llm_error
    from backend.domain.llm import LlmError, LlmErrorKind

    exc = LlmError(kind=LlmErrorKind.TRANSIENT, message="timeout")
    with pytest.raises(ToolError) as exc_info:
        _map_and_raise_llm_error(exc)

    assert exc_info.value.retryable is True
    assert "transient" in exc_info.value.kind


def test_map_llm_exhausted_error_is_retryable():
    from backend.agents.response import _map_and_raise_llm_error
    from backend.domain.llm import LlmError, LlmErrorKind

    exc = LlmError(kind=LlmErrorKind.EXHAUSTED, message="quota exceeded")
    with pytest.raises(ToolError) as exc_info:
        _map_and_raise_llm_error(exc)

    assert exc_info.value.retryable is True


def test_map_llm_invalid_request_error_is_not_retryable():
    from backend.agents.response import _map_and_raise_llm_error
    from backend.domain.llm import LlmError, LlmErrorKind

    exc = LlmError(kind=LlmErrorKind.INVALID_REQUEST, message="bad request")
    with pytest.raises(ToolError) as exc_info:
        _map_and_raise_llm_error(exc)

    assert exc_info.value.retryable is False


def test_map_non_llm_error_wraps_as_unexpected():
    from backend.agents.response import _map_and_raise_llm_error

    exc = RuntimeError("something unexpected")
    with pytest.raises(ToolError) as exc_info:
        _map_and_raise_llm_error(exc)

    assert exc_info.value.retryable is False
    assert exc_info.value.kind == "llm_unexpected"


# ---------------------------------------------------------------------------
# Catalog miss
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_playbook_match_raises_tool_error():
    """No matching playbook → ToolError(kind=no_playbook_match)."""
    from backend.agents.response import PlaybookEntry, select_playbook
    from backend.infra.config import ResponseSettings

    incident = _incident(severity="impossible_severity")
    catalog = [
        PlaybookEntry("p1", "critical only", {"severity": ["critical"]}, [{"type": "open_ticket"}])
    ]
    cfg = ResponseSettings()

    with pytest.raises(ToolError) as exc_info:
        await select_playbook(incident, catalog, llm=None, cfg=cfg)

    assert exc_info.value.kind == "no_playbook_match"


@pytest.mark.asyncio
async def test_empty_catalog_raises_tool_error():
    from backend.agents.response import select_playbook
    from backend.infra.config import ResponseSettings

    incident = _incident()
    with pytest.raises(ToolError) as exc_info:
        await select_playbook(incident, [], llm=None, cfg=ResponseSettings())

    assert exc_info.value.kind == "empty_catalog"


@pytest.mark.asyncio
async def test_ambiguous_match_no_llm_raises_tool_error():
    """Multiple matching playbooks with no LLM available → ToolError."""
    from backend.agents.response import PlaybookEntry, select_playbook
    from backend.infra.config import ResponseSettings

    incident = _incident(severity="critical")
    catalog = [
        PlaybookEntry("p1", "first", {"severity": ["critical"]}, [{"type": "open_ticket"}]),
        PlaybookEntry("p2", "second", {"severity": ["critical"]}, [{"type": "enrich_and_tag"}]),
    ]
    cfg = ResponseSettings()

    with pytest.raises(ToolError) as exc_info:
        await select_playbook(incident, catalog, llm=None, cfg=cfg)

    assert exc_info.value.kind == "no_llm_for_ambiguous_selection"


# ---------------------------------------------------------------------------
# Malformed LLM output
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_malformed_llm_output_raises_tool_error():
    """Malformed JSON from LLM → ToolError(malformed_output)."""
    from backend.agents.response import PlaybookEntry, select_playbook
    from backend.infra.config import ResponseSettings

    incident = _incident(severity="critical")
    catalog = [
        PlaybookEntry("p1", "first", {"severity": ["critical"]}, [{"type": "open_ticket"}]),
        PlaybookEntry("p2", "second", {"severity": ["critical"]}, [{"type": "enrich_and_tag"}]),
    ]

    class _FakeLlm:
        async def generate(self, req, **kw):
            class _R:
                content = "not json {{ broken"
                usage = None

            return _R()

    cfg = ResponseSettings()
    with pytest.raises(ToolError) as exc_info:
        await select_playbook(incident, catalog, llm=_FakeLlm(), cfg=cfg)

    assert exc_info.value.kind == "malformed_output"


@pytest.mark.asyncio
async def test_low_confidence_llm_raises_tool_error():
    """LLM confidence below threshold → ToolError(low_confidence_selection)."""
    import json

    from backend.agents.response import PlaybookEntry, select_playbook
    from backend.infra.config import ResponseSettings

    incident = _incident(severity="critical")
    catalog = [
        PlaybookEntry("p1", "first", {"severity": ["critical"]}, [{"type": "open_ticket"}]),
        PlaybookEntry("p2", "second", {"severity": ["critical"]}, [{"type": "enrich_and_tag"}]),
    ]

    class _FakeLlm:
        async def generate(self, req, **kw):
            class _R:
                content = json.dumps(
                    {"playbook_id": "p1", "confidence": 0.3, "rationale": "not sure"}
                )
                usage = None

            return _R()

    cfg = ResponseSettings(select_min_confidence=0.6)
    with pytest.raises(ToolError) as exc_info:
        await select_playbook(incident, catalog, llm=_FakeLlm(), cfg=cfg)

    assert exc_info.value.kind == "low_confidence_selection"


# ---------------------------------------------------------------------------
# No executor for action type
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_executor_raises_tool_error():
    """Missing executor for action type → ToolError(no_executor_for_action)."""
    from backend.agents.response import _execute_with_audit
    from backend.domain.response import RemediationAction

    incident_id = uuid.uuid4()
    action = RemediationAction(
        type=ActionType.ISOLATE_HOST,
        target="srv-01",
        risk=RiskClass.APPROVAL_REQUIRED,
        idempotency_key=f"{incident_id}:p1:isolate_host:srv-01",
    )

    class _AuditRepo:
        async def is_applied(self, key):
            return False

        async def append(self, **kw):
            return True

    with pytest.raises(ToolError) as exc_info:
        await _execute_with_audit(
            action=action,
            incident_id=incident_id,
            actor="agent",
            audit_repo=_AuditRepo(),
            executors={},  # no executors registered
        )

    assert exc_info.value.kind == "no_executor_for_action"


@pytest.mark.asyncio
async def test_executor_exception_wraps_as_retryable():
    """Executor raising an unexpected exception → ToolError(retryable=True)."""
    from backend.agents.response import _execute_with_audit
    from backend.domain.response import ActionExecutor, RemediationAction

    incident_id = uuid.uuid4()
    action = RemediationAction(
        type=ActionType.ADD_TO_WATCHLIST,
        target="host",
        risk=RiskClass.AUTO,
        idempotency_key=f"{incident_id}:p1:add_to_watchlist:host",
    )

    class _FailingExecutor(ActionExecutor):
        async def execute(self, act):
            raise ConnectionError("downstream unreachable")

    class _AuditRepo:
        async def is_applied(self, key):
            return False

        async def append(self, **kw):
            return True

    with pytest.raises(ToolError) as exc_info:
        await _execute_with_audit(
            action=action,
            incident_id=incident_id,
            actor="agent",
            audit_repo=_AuditRepo(),
            executors={ActionType.ADD_TO_WATCHLIST: _FailingExecutor()},
        )

    assert exc_info.value.retryable is True
    assert exc_info.value.kind == "executor_transient"


# ---------------------------------------------------------------------------
# Handler fail-closed: select_playbook raises → handler surfaces ToolError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handler_propagates_select_tool_error():
    """When select_playbook fails, make_response_handler propagates the ToolError."""
    from backend.agents.response import make_response_handler
    from backend.infra.config import ResponseSettings
    from backend.infra.executors import build_mock_executors

    class _FakeAuditRepo:
        async def is_applied(self, key):
            return False

        async def append(self, **kw):
            return True

    class _FakeApprovalRepo:
        async def get_approved_pending_for(self, iid):
            return None

        async def create_pending(self, **kw):
            return 1

    @contextlib.asynccontextmanager
    async def _sf():
        yield None

    from unittest.mock import patch

    with (
        patch("backend.agents.response.ApprovalRepository", return_value=_FakeApprovalRepo()),
        patch("backend.agents.response.AuditRepository", return_value=_FakeAuditRepo()),
    ):
        handler = make_response_handler(
            llm=None,
            session_factory=_sf,
            executors=build_mock_executors(),
            cfg=ResponseSettings(),
            catalog=[],  # empty catalog → no_playbook_match or empty_catalog
        )
        with pytest.raises(ToolError) as exc_info:
            await handler(_incident())

    assert exc_info.value.kind in ("empty_catalog", "no_playbook_match")
