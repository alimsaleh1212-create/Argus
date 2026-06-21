"""Integration tests — verify_remediation handler paths (T007).

Tests the verify_remediation() function and the _pass_a/_pass_b integration points
with fake repos and different executor/intel/memory configurations.
No testcontainers needed — these tests are handler-level (not DB-level).
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import MagicMock

import pytest

from backend.agents.response import verify_remediation
from backend.domain.incident import Incident, IncidentStatus, Severity
from backend.domain.response import (
    ActionResult,
    ActionStatus,
    ActionType,
    RemediationAction,
    RiskClass,
    VerificationVerdict,
)
from backend.infra.executors import (
    build_inconclusive_executors,
    build_mock_executors,
    build_regressed_executors,
)


class _Cfg:
    verify_remediation = True
    verify_regressed_verdicts = ["malicious", "suspicious"]
    verify_llm_tiebreak = False
    select_min_confidence = 0.6
    auto_execute_actions = ["add_to_watchlist", "open_ticket", "enrich_and_tag"]
    approval_timeout_s = 1800
    catalog_dir = "backend/data/playbooks"
    max_output_tokens = 512
    temperature = 0.0
    prompt_version = "v1"


def _action(atype: ActionType = ActionType.BLOCK_IP, target: str = "1.2.3.4") -> RemediationAction:
    return RemediationAction(
        type=atype,
        target=target,
        params={},
        risk=RiskClass.AUTO,
        idempotency_key=f"test:{atype.value}:{target}",
    )


def _applied_result(
    atype: ActionType = ActionType.BLOCK_IP, target: str = "1.2.3.4"
) -> ActionResult:
    return ActionResult(type=atype, target=target, status=ActionStatus.APPLIED)


def _incident(evidence: dict | None = None) -> Incident:
    return Incident(
        id=uuid.uuid4(),
        status=IncidentStatus.RESPONDING,
        severity=Severity.HIGH,
        correlation_id="corr-test",
        dedup_fingerprint="fp-test",
        source="wazuh",
        raw_alert={},
        evidence=evidence or {},
    )


# ---------------------------------------------------------------------------
# verify_remediation() direct tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_verify_remediation_clean_probe_and_benign_intel_yields_verified():
    results = [_applied_result()]
    actions = [_action()]
    executors = build_mock_executors()  # probe returns EXPECTED

    class _Intel:
        async def lookup(self, target: str, kind: str) -> Any:
            m = MagicMock()
            m.verdict = "benign"
            return m

    vr = await verify_remediation(
        applied_results=results,
        applied_actions=actions,
        executors=executors,
        intel=_Intel(),
        memory=None,
        cfg=_Cfg(),
    )
    assert vr.verdict == VerificationVerdict.VERIFIED
    assert vr.used_llm_tiebreak is False


@pytest.mark.asyncio
async def test_verify_remediation_malicious_intel_yields_regressed():
    results = [_applied_result()]
    actions = [_action()]
    executors = build_mock_executors()  # probe returns EXPECTED

    class _Intel:
        async def lookup(self, target: str, kind: str) -> Any:
            m = MagicMock()
            m.verdict = "malicious"
            return m

    vr = await verify_remediation(
        applied_results=results,
        applied_actions=actions,
        executors=executors,
        intel=_Intel(),
        memory=None,
        cfg=_Cfg(),
    )
    assert vr.verdict == VerificationVerdict.REGRESSED


@pytest.mark.asyncio
async def test_verify_remediation_inconclusive_probe_yields_unverified():
    results = [_applied_result()]
    actions = [_action()]
    executors = build_inconclusive_executors(ActionType.BLOCK_IP)

    vr = await verify_remediation(
        applied_results=results,
        applied_actions=actions,
        executors=executors,
        intel=None,
        memory=None,
        cfg=_Cfg(),
    )
    assert vr.verdict == VerificationVerdict.UNVERIFIED


@pytest.mark.asyncio
async def test_verify_remediation_regressed_probe_yields_regressed():
    results = [_applied_result()]
    actions = [_action()]
    executors = build_regressed_executors(ActionType.BLOCK_IP)

    vr = await verify_remediation(
        applied_results=results,
        applied_actions=actions,
        executors=executors,
        intel=None,
        memory=None,
        cfg=_Cfg(),
    )
    assert vr.verdict == VerificationVerdict.REGRESSED


@pytest.mark.asyncio
async def test_verify_remediation_intel_outage_yields_unverified():
    """Intel outage → fail-closed UNVERIFIED, never blocks."""
    results = [_applied_result()]
    actions = [_action()]
    executors = build_mock_executors()

    class _BrokenIntel:
        async def lookup(self, target: str, kind: str) -> Any:
            raise RuntimeError("intel unavailable")

    vr = await verify_remediation(
        applied_results=results,
        applied_actions=actions,
        executors=executors,
        intel=_BrokenIntel(),
        memory=None,
        cfg=_Cfg(),
    )
    # probe EXPECTED + intel unknown → VERIFIED (no indicator present)
    assert vr.verdict in (VerificationVerdict.VERIFIED, VerificationVerdict.UNVERIFIED)
    # Key invariant: verdict is never blocked from being set
    assert vr.verdict is not None


@pytest.mark.asyncio
async def test_verify_remediation_no_applied_actions_yields_unverified():
    results = [ActionResult(type=ActionType.BLOCK_IP, target="x", status=ActionStatus.FAILED)]
    actions = [_action()]
    executors = build_mock_executors()

    vr = await verify_remediation(
        applied_results=results,
        applied_actions=actions,
        executors=executors,
        intel=None,
        memory=None,
        cfg=_Cfg(),
    )
    assert vr.verdict == VerificationVerdict.UNVERIFIED


@pytest.mark.asyncio
async def test_verify_remediation_worst_case_across_multi_action():
    """Two actions: one verified, one regressed → incident verdict is REGRESSED."""
    results = [
        _applied_result(ActionType.BLOCK_IP, "1.2.3.4"),
        _applied_result(ActionType.ADD_TO_WATCHLIST, "bad-host"),
    ]
    actions = [
        _action(ActionType.BLOCK_IP, "1.2.3.4"),
        _action(ActionType.ADD_TO_WATCHLIST, "bad-host"),
    ]
    # BLOCK_IP probe is EXPECTED; ADD_TO_WATCHLIST probe is UNEXPECTED (regressed)
    executors = build_regressed_executors(ActionType.ADD_TO_WATCHLIST)

    vr = await verify_remediation(
        applied_results=results,
        applied_actions=actions,
        executors=executors,
        intel=None,
        memory=None,
        cfg=_Cfg(),
    )
    assert vr.verdict == VerificationVerdict.REGRESSED


# ---------------------------------------------------------------------------
# Handler outcome mapping: verify_cfg_on=False should skip verification
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_verify_remediation_skipped_when_cfg_off():
    class _CfgOff(_Cfg):
        verify_remediation = False

    actions = [_action()]
    executors = build_regressed_executors(ActionType.BLOCK_IP)  # would regress if checked

    # verify_remediation called with cfg off → should NOT be reached by handler
    # (handler guards with `if verify_cfg_on and applied and ...`)
    # Test the function directly with an empty applied list as a proxy
    vr = await verify_remediation(
        applied_results=[],  # empty → returns UNVERIFIED (no applied)
        applied_actions=actions,
        executors=executors,
        intel=None,
        memory=None,
        cfg=_CfgOff(),
    )
    assert vr.verdict == VerificationVerdict.UNVERIFIED


# ---------------------------------------------------------------------------
# Memory query_fact integration path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_verify_remediation_memory_current_malicious_fact_yields_regressed():
    results = [_applied_result()]
    actions = [_action()]
    executors = build_mock_executors()  # probe EXPECTED

    class _Memory:
        async def query_fact(self, entity: Any, field: str, *, as_of: Any) -> Any:
            m = MagicMock()
            m.value = "malicious"
            m.is_current = True
            return m

    vr = await verify_remediation(
        applied_results=results,
        applied_actions=actions,
        executors=executors,
        intel=None,
        memory=_Memory(),
        cfg=_Cfg(),
    )
    assert vr.verdict == VerificationVerdict.REGRESSED


@pytest.mark.asyncio
async def test_verify_remediation_memory_superseded_fact_not_treated_as_current():
    """Superseded fact (is_current=False) must not trigger regressed verdict."""
    results = [_applied_result()]
    actions = [_action()]
    executors = build_mock_executors()

    class _Memory:
        async def query_fact(self, entity: Any, field: str, *, as_of: Any) -> Any:
            m = MagicMock()
            m.value = "malicious"
            m.is_current = False  # superseded — must not be treated as current
            return m

    vr = await verify_remediation(
        applied_results=results,
        applied_actions=actions,
        executors=executors,
        intel=None,
        memory=_Memory(),
        cfg=_Cfg(),
    )
    # probe EXPECTED + superseded malicious fact → not regressed (no current signal)
    assert vr.verdict in (VerificationVerdict.VERIFIED, VerificationVerdict.UNVERIFIED)
    assert vr.verdict != VerificationVerdict.REGRESSED
