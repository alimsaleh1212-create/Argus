"""E2E tests — full incident → applied remediation → verification → disposition (T008).

Covers the three verdict classes on both the auto path (_pass_a) and the approved path (_pass_b),
plus the fail-closed invariant (outage never blocks disposition) and idempotence.
No real DB or LLM required — uses fake repos and mock executors.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import MagicMock

import pytest

from backend.agents.response import _pass_a, _pass_b
from backend.domain.incident import Incident, IncidentStatus, Severity
from backend.domain.pipeline import StageOutcome
from backend.domain.response import (
    ActionType,
    RemediationAction,
    RiskClass,
)
from backend.infra.executors import (
    build_inconclusive_executors,
    build_mock_executors,
    build_regressed_executors,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _Cfg:
    verify_remediation = True
    verify_regressed_verdicts = ["malicious", "suspicious"]
    verify_llm_tiebreak = False
    select_min_confidence = 0.6
    auto_execute_actions = [
        "add_to_watchlist",
        "open_ticket",
        "enrich_and_tag",
        "isolate_host",
        "block_ip",
        "disable_user",
    ]
    approval_timeout_s = 1800
    catalog_dir = "backend/data/playbooks"
    max_output_tokens = 512
    temperature = 0.0
    prompt_version = "v1"


class _FakeAuditRepo:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    async def is_applied(self, key: str) -> bool:
        return False

    async def append(
        self,
        *,
        incident_id: Any,
        actor: str,
        action: str,
        target: Any = None,
        outcome: str,
        idempotency_key: Any = None,
    ) -> bool:
        self.rows.append({"actor": actor, "action": action, "outcome": outcome})
        return True


class _FakeApprovalRepo:
    def __init__(self, approved: Any = None) -> None:
        self._approved = approved

    async def get_approved_pending_for(self, incident_id: Any) -> Any:
        return self._approved

    async def create_pending(self, **kwargs: Any) -> int:
        return 1


class _FakeApprovedRow:
    def __init__(self, actions: list[dict]) -> None:
        self.id = 42
        self.plan_id = "plan-b"
        self.pending_actions = actions
        self.decided_by = "human-operator"


_BASE_EVIDENCE = {
    "severity": "high",
    "normalized_event": {
        "severity": "high",
        "rule_groups": ["attack"],
    },
}


def _incident(evidence: dict | None = None) -> Incident:
    ev = dict(_BASE_EVIDENCE)
    if evidence:
        ev.update(evidence)
    return Incident(
        id=uuid.uuid4(),
        status=IncidentStatus.RESPONDING,
        severity=Severity.HIGH,
        correlation_id=f"corr-e2e-{uuid.uuid4().hex[:8]}",
        dedup_fingerprint=f"fp-e2e-{uuid.uuid4().hex[:8]}",
        source="wazuh",
        raw_alert={},
        evidence=ev,
    )


def _auto_action(atype: ActionType = ActionType.BLOCK_IP) -> RemediationAction:
    return RemediationAction(
        type=atype,
        target="1.2.3.4",
        params={},
        risk=RiskClass.AUTO,
        idempotency_key=f"e2e:{atype.value}:1.2.3.4",
    )


class _BenignIntel:
    async def lookup(self, target: str, kind: str) -> Any:
        m = MagicMock()
        m.verdict = "benign"
        return m


class _MaliciousIntel:
    async def lookup(self, target: str, kind: str) -> Any:
        m = MagicMock()
        m.verdict = "malicious"
        return m


# ---------------------------------------------------------------------------
# Pass A (auto path) — three verdict classes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pass_a_clean_probe_and_benign_intel_resolves_auto_remediated():
    """Auto path: probe EXPECTED + benign intel → verdict VERIFIED → auto_remediated."""
    audit = _FakeAuditRepo()
    approval = _FakeApprovalRepo()
    incident = _incident()
    executors = build_mock_executors()

    from backend.agents.response import PlaybookEntry

    catalog = [
        PlaybookEntry(
            id="block-ip",
            description="block ip",
            criteria={"severity": ["high", "critical"]},
            actions=[{"type": "block_ip", "target": "1.2.3.4"}],
        )
    ]

    result = await _pass_a(
        incident=incident,
        catalog=catalog,
        llm=None,
        cfg=_Cfg(),
        audit_repo=audit,
        approval_repo=approval,
        executors=executors,
        intel=_BenignIntel(),
        memory=None,
    )

    assert result.outcome == StageOutcome.RESOLVED
    assert result.disposition == "auto_remediated"
    assert result.evidence_patch["response"]["verification"]["verdict"] == "verified"


@pytest.mark.asyncio
async def test_pass_a_regressed_probe_escalates_as_unverified():
    """Auto path: probe UNEXPECTED → verdict REGRESSED → UNVERIFIED outcome → escalated."""
    audit = _FakeAuditRepo()
    approval = _FakeApprovalRepo()
    incident = _incident()
    executors = build_regressed_executors(ActionType.BLOCK_IP)

    from backend.agents.response import PlaybookEntry

    catalog = [
        PlaybookEntry(
            id="block-ip",
            description="block ip",
            criteria={"severity": ["high", "critical"]},
            actions=[{"type": "block_ip", "target": "1.2.3.4"}],
        )
    ]

    result = await _pass_a(
        incident=incident,
        catalog=catalog,
        llm=None,
        cfg=_Cfg(),
        audit_repo=audit,
        approval_repo=approval,
        executors=executors,
        intel=None,
        memory=None,
    )

    assert result.outcome == StageOutcome.UNVERIFIED
    assert result.disposition is None
    assert result.evidence_patch["response"]["verification"]["verdict"] == "regressed"
    # Audit row appended for verifier
    verifier_rows = [r for r in audit.rows if r["actor"] == "verifier"]
    assert len(verifier_rows) == 1


@pytest.mark.asyncio
async def test_pass_a_inconclusive_probe_escalates_as_unverified():
    """Auto path: probe INCONCLUSIVE → verdict UNVERIFIED → UNVERIFIED outcome."""
    audit = _FakeAuditRepo()
    approval = _FakeApprovalRepo()
    incident = _incident()
    executors = build_inconclusive_executors(ActionType.BLOCK_IP)

    from backend.agents.response import PlaybookEntry

    catalog = [
        PlaybookEntry(
            id="block-ip",
            description="block ip",
            criteria={"severity": ["high", "critical"]},
            actions=[{"type": "block_ip", "target": "1.2.3.4"}],
        )
    ]

    result = await _pass_a(
        incident=incident,
        catalog=catalog,
        llm=None,
        cfg=_Cfg(),
        audit_repo=audit,
        approval_repo=approval,
        executors=executors,
        intel=None,
        memory=None,
    )

    assert result.outcome == StageOutcome.UNVERIFIED
    assert result.evidence_patch["response"]["verification"]["verdict"] == "unverified"


# ---------------------------------------------------------------------------
# Pass B (approved path) — two verdict classes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pass_b_clean_probe_resolves_remediated():
    """Approved path: probe EXPECTED → VERIFIED → remediated."""
    audit = _FakeAuditRepo()
    incident = _incident()
    executors = build_mock_executors()

    approved = _FakeApprovedRow(
        actions=[
            {
                "type": "block_ip",
                "target": "1.2.3.4",
                "params": {},
                "risk": "auto",
                "idempotency_key": "e2e:block_ip:1.2.3.4",
            }
        ]
    )

    result = await _pass_b(
        incident=incident,
        approved=approved,
        audit_repo=audit,
        executors=executors,
        cfg=_Cfg(),
        intel=_BenignIntel(),
        memory=None,
    )

    assert result.outcome == StageOutcome.RESOLVED
    assert result.disposition == "remediated"
    assert result.evidence_patch["response"]["verification"]["verdict"] == "verified"


@pytest.mark.asyncio
async def test_pass_b_malicious_intel_escalates():
    """Approved path: intel still malicious → REGRESSED → UNVERIFIED outcome."""
    audit = _FakeAuditRepo()
    incident = _incident()
    executors = build_mock_executors()

    approved = _FakeApprovedRow(
        actions=[
            {
                "type": "block_ip",
                "target": "1.2.3.4",
                "params": {},
                "risk": "auto",
                "idempotency_key": "e2e:block_ip:1.2.3.4",
            }
        ]
    )

    result = await _pass_b(
        incident=incident,
        approved=approved,
        audit_repo=audit,
        executors=executors,
        cfg=_Cfg(),
        intel=_MaliciousIntel(),
        memory=None,
    )

    assert result.outcome == StageOutcome.UNVERIFIED
    verifier_rows = [r for r in audit.rows if r["actor"] == "verifier"]
    assert len(verifier_rows) == 1


# ---------------------------------------------------------------------------
# Idempotency — already-verified incident skips verification
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pass_a_idempotent_when_verification_already_present():
    """Worker resume on an already-verified incident → no disposition change."""
    audit = _FakeAuditRepo()
    approval = _FakeApprovalRepo()
    # incident already carries a verification record in evidence (merged with base evidence)
    incident = _incident(
        evidence={
            "severity": "high",
            "normalized_event": {"severity": "high", "rule_groups": ["attack"]},
            "response": {
                "verification": {"verdict": "verified"},
            },
        }
    )
    executors = build_regressed_executors(ActionType.BLOCK_IP)  # would regress if re-checked

    from backend.agents.response import PlaybookEntry

    catalog = [
        PlaybookEntry(
            id="block-ip",
            description="block ip",
            criteria={"severity": ["high", "critical"]},
            actions=[{"type": "block_ip", "target": "1.2.3.4"}],
        )
    ]

    result = await _pass_a(
        incident=incident,
        catalog=catalog,
        llm=None,
        cfg=_Cfg(),
        audit_repo=audit,
        approval_repo=approval,
        executors=executors,
        intel=None,
        memory=None,
    )

    # Verification already present → skipped; resolves with existing disposition
    assert result.outcome == StageOutcome.RESOLVED
    assert result.disposition == "auto_remediated"
    # No verifier audit row (idempotent skip)
    verifier_rows = [r for r in audit.rows if r["actor"] == "verifier"]
    assert len(verifier_rows) == 0


# ---------------------------------------------------------------------------
# Fail-closed: verification error never blocks disposition (SC-005)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pass_a_verification_error_never_blocks_disposition():
    """If verify_remediation raises unexpectedly, the incident still reaches terminal state."""
    audit = _FakeAuditRepo()
    approval = _FakeApprovalRepo()
    incident = _incident()

    class _BrokenExecutors(dict):
        def get(self, key, default=None):
            executor = build_mock_executors().get(key, default)
            if executor is not None:

                async def _broken_probe(action: Any) -> Any:
                    raise RuntimeError("probe exploded")

                executor.probe = _broken_probe
            return executor

    # Use a broken executor that crashes on probe
    executors_dict = build_mock_executors()
    for _k, v in executors_dict.items():

        async def _err_probe(action: Any) -> Any:
            raise RuntimeError("probe exploded")

        v.probe = _err_probe

    from backend.agents.response import PlaybookEntry

    catalog = [
        PlaybookEntry(
            id="block-ip",
            description="block ip",
            criteria={"severity": ["high", "critical"]},
            actions=[{"type": "block_ip", "target": "1.2.3.4"}],
        )
    ]

    result = await _pass_a(
        incident=incident,
        catalog=catalog,
        llm=None,
        cfg=_Cfg(),
        audit_repo=audit,
        approval_repo=approval,
        executors=executors_dict,
        intel=None,
        memory=None,
    )

    # Verification failed but incident still reached a terminal outcome (UNVERIFIED = escalated)
    assert result.outcome in (StageOutcome.RESOLVED, StageOutcome.UNVERIFIED)
    assert "response" in result.evidence_patch
