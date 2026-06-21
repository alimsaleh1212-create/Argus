"""E2E tests — memory feedback changes how a repeat incident is handled (US2, T011).

Demonstrates the brief #5 demo: a second occurrence of a known-failed indicator
is escalated sooner (severity biased) and/or selects a stronger playbook.
No real DB or LLM required — fake repos and a minimal catalog.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from backend.agents.response import PlaybookEntry, _pass_a
from backend.domain.feedback import FeedbackSignal, RemediationOutcome
from backend.domain.incident import Incident, IncidentStatus, Severity
from backend.domain.pipeline import StageOutcome
from backend.infra.config import FeedbackSettings


class _Cfg:
    verify_remediation = False
    select_min_confidence = 0.6
    auto_execute_actions = ["add_to_watchlist", "open_ticket", "enrich_and_tag"]
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
    async def get_approved_pending_for(self, incident_id: Any) -> Any:
        return None

    async def create_pending(self, **kwargs: Any) -> int:
        return 1


class _DoNothingExecutors(dict):
    async def execute(self, action):
        class _R:
            status = "applied"
            detail = "ok"

        return _R()


def _make_executors() -> dict:
    from backend.infra.executors import build_mock_executors

    return build_mock_executors()


def _incident(*, severity: Severity, prior_outcome: dict | None = None) -> Incident:
    effective_severity = (
        prior_outcome.get("biased_severity", severity.value) if prior_outcome else severity.value
    )
    evidence: dict[str, Any] = {
        "severity": effective_severity,
        "normalized_event": {
            "severity": 10 if effective_severity == "critical" else 8,
            "rule_groups": ["attack"],
        },
    }
    if prior_outcome:
        evidence["prior_outcome"] = prior_outcome
    return Incident(
        id=uuid.uuid4(),
        status=IncidentStatus.RESPONDING,
        severity=severity,
        correlation_id=f"corr-e2e-{uuid.uuid4().hex[:8]}",
        dedup_fingerprint=f"fp-e2e-{uuid.uuid4().hex[:8]}",
        source="wazuh",
        raw_alert={},
        evidence=evidence,
    )


def _catalog() -> list[PlaybookEntry]:
    return [
        PlaybookEntry(
            id="watch_and_ticket",
            description="Watchlist + ticket (weaker)",
            criteria={"severity": ["high"]},
            actions=[{"type": "add_to_watchlist"}, {"type": "open_ticket"}],
            strength=1,
        ),
        PlaybookEntry(
            id="isolate_and_ticket",
            description="Isolate host + ticket (stronger)",
            criteria={"severity": ["critical"]},
            actions=[{"type": "isolate_host"}, {"type": "open_ticket"}],
            strength=3,
        ),
    ]


def _feedback_cfg() -> FeedbackSettings:
    return FeedbackSettings(
        enabled=True,
        severity_bias="bump_one",
        prefer_stronger_playbook=True,
        escalate_on=["regressed", "unverified"],
    )


def _prior_outcome(value: str = "regressed") -> dict:
    return {
        "signals": [
            {
                "indicator": "server-01",
                "outcome": value,
                "is_current": True,
                "observed_at": None,
            }
        ],
        "biased_severity": "critical" if value in ("regressed", "unverified") else "high",
    }


@pytest.mark.asyncio
async def test_first_occurrence_selects_weaker_playbook() -> None:
    """Baseline: no prior outcome → severity stays high → weaker playbook matches."""
    audit = _FakeAuditRepo()
    approval = _FakeApprovalRepo()
    incident = _incident(severity=Severity.HIGH, prior_outcome=None)
    executors = _make_executors()
    feedback_cfg = _feedback_cfg()

    result = await _pass_a(
        incident=incident,
        catalog=_catalog(),
        llm=None,
        cfg=_Cfg(),
        audit_repo=audit,
        approval_repo=approval,
        executors=executors,
        feedback_cfg=feedback_cfg,
    )

    plan = result.evidence_patch["response"]["plan"]
    assert plan["playbook_id"] == "watch_and_ticket"


@pytest.mark.asyncio
async def test_second_occurrence_selects_stronger_playbook() -> None:
    """Repeat with prior failure → severity biased to critical → stronger playbook matches."""
    audit = _FakeAuditRepo()
    approval = _FakeApprovalRepo()
    incident = _incident(severity=Severity.HIGH, prior_outcome=_prior_outcome("regressed"))
    executors = _make_executors()
    feedback_cfg = _feedback_cfg()

    result = await _pass_a(
        incident=incident,
        catalog=_catalog(),
        llm=None,
        cfg=_Cfg(),
        audit_repo=audit,
        approval_repo=approval,
        executors=executors,
        feedback_cfg=feedback_cfg,
    )

    plan = result.evidence_patch["response"]["plan"]
    assert plan["playbook_id"] == "isolate_and_ticket"
    # Destructive action parks for approval rather than auto-executing
    assert result.outcome == StageOutcome.NEEDS_APPROVAL


@pytest.mark.asyncio
async def test_verified_prior_selects_weaker_playbook() -> None:
    """A current verified prior applies no bias — baseline handling."""
    audit = _FakeAuditRepo()
    approval = _FakeApprovalRepo()
    incident = _incident(severity=Severity.HIGH, prior_outcome=_prior_outcome("verified"))
    executors = _make_executors()
    feedback_cfg = _feedback_cfg()

    result = await _pass_a(
        incident=incident,
        catalog=_catalog(),
        llm=None,
        cfg=_Cfg(),
        audit_repo=audit,
        approval_repo=approval,
        executors=executors,
        feedback_cfg=feedback_cfg,
    )

    plan = result.evidence_patch["response"]["plan"]
    assert plan["playbook_id"] == "watch_and_ticket"


@pytest.mark.asyncio
async def test_feedback_bias_rules_are_pure() -> None:
    """The same inputs always produce the same biased severity and playbook choice."""
    from backend.domain.feedback import decide_severity_bias, prefer_stronger_playbook

    cfg = _feedback_cfg()
    signals = [FeedbackSignal(indicator="x", outcome=RemediationOutcome.REGRESSED, is_current=True)]

    severity_a = decide_severity_bias(Severity.HIGH, signals, cfg)
    severity_b = decide_severity_bias(Severity.HIGH, signals, cfg)
    assert severity_a == severity_b == Severity.CRITICAL

    catalog = _catalog()
    stronger_a = prefer_stronger_playbook(catalog, signals, cfg)
    stronger_b = prefer_stronger_playbook(catalog, signals, cfg)
    assert stronger_a is not None and stronger_a.id == "isolate_and_ticket"
    assert stronger_b is not None and stronger_b.id == "isolate_and_ticket"
