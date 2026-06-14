"""Unit tests — decide_outcome happy paths (T011)."""

from __future__ import annotations

from backend.agents.enrichment import decide_outcome
from backend.domain.enrichment import EnrichmentReport
from backend.domain.pipeline import StageOutcome


def _report(**kwargs) -> EnrichmentReport:
    base = {
        "assessment": "confirmed",
        "confidence": 0.85,
        "correlation_summary": "External and internal signals align.",
        "cited_evidence": ["rule_id=100001"],
    }
    base.update(kwargs)
    return EnrichmentReport.model_validate(base)


class _Cfg:
    advance_min_confidence = 0.6
    resolve_min_confidence = 0.7


def test_confirmed_above_advance_min_yields_advance():
    report = _report(assessment="confirmed", confidence=0.8)
    outcome, disp = decide_outcome(report, _Cfg())
    assert outcome == StageOutcome.ADVANCE
    assert disp is None


def test_confirmed_at_advance_min_yields_advance():
    report = _report(assessment="confirmed", confidence=0.6)
    outcome, disp = decide_outcome(report, _Cfg())
    assert outcome == StageOutcome.ADVANCE
    assert disp is None


def test_benign_above_resolve_min_yields_resolved():
    report = _report(assessment="benign", confidence=0.75)
    outcome, disp = decide_outcome(report, _Cfg())
    assert outcome == StageOutcome.RESOLVED
    assert disp == "auto_resolved_enrichment"


def test_benign_at_resolve_min_yields_resolved():
    report = _report(assessment="benign", confidence=0.7)
    outcome, disp = decide_outcome(report, _Cfg())
    assert outcome == StageOutcome.RESOLVED
    assert disp == "auto_resolved_enrichment"


def test_inconclusive_yields_escalate():
    report = _report(assessment="inconclusive", confidence=0.9)
    outcome, disp = decide_outcome(report, _Cfg())
    assert outcome == StageOutcome.ESCALATE
    assert disp == "escalated_enrichment"


def test_confirmed_below_advance_min_yields_escalate():
    report = _report(assessment="confirmed", confidence=0.5)
    outcome, disp = decide_outcome(report, _Cfg())
    assert outcome == StageOutcome.ESCALATE
    assert disp == "escalated_enrichment"
