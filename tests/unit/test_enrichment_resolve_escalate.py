"""Unit tests — decide_outcome escalate/resolve branches (T018)."""

from __future__ import annotations

from backend.agents.enrichment import decide_outcome
from backend.domain.enrichment import EnrichmentReport
from backend.domain.pipeline import StageOutcome


def _report(**kwargs) -> EnrichmentReport:
    base = {
        "assessment": "confirmed",
        "confidence": 0.85,
        "correlation_summary": "Signals aligned.",
        "cited_evidence": ["rule_id=100"],
    }
    base.update(kwargs)
    return EnrichmentReport.model_validate(base)


class _Cfg:
    advance_min_confidence = 0.6
    resolve_min_confidence = 0.7


def test_inconclusive_always_escalates():
    report = _report(assessment="inconclusive", confidence=0.99)
    outcome, disp = decide_outcome(report, _Cfg())
    assert outcome == StageOutcome.ESCALATE
    assert disp == "escalated_enrichment"


def test_confidence_below_advance_min_escalates():
    report = _report(assessment="confirmed", confidence=0.59)
    outcome, disp = decide_outcome(report, _Cfg())
    assert outcome == StageOutcome.ESCALATE
    assert disp == "escalated_enrichment"


def test_benign_between_advance_and_resolve_escalates():
    # advance_min=0.6 <= conf < resolve_min=0.7 → ESCALATE
    report = _report(assessment="benign", confidence=0.65)
    outcome, disp = decide_outcome(report, _Cfg())
    assert outcome == StageOutcome.ESCALATE
    assert disp == "escalated_enrichment"


def test_benign_at_resolve_min_resolves():
    report = _report(assessment="benign", confidence=0.7)
    outcome, disp = decide_outcome(report, _Cfg())
    assert outcome == StageOutcome.RESOLVED
    assert disp == "auto_resolved_enrichment"


def test_benign_above_resolve_min_resolves():
    report = _report(assessment="benign", confidence=0.9)
    outcome, disp = decide_outcome(report, _Cfg())
    assert outcome == StageOutcome.RESOLVED
    assert disp == "auto_resolved_enrichment"


def test_benign_exactly_at_advance_min_below_resolve_escalates():
    # at advance_min (0.6) but < resolve_min (0.7) → ESCALATE for benign
    report = _report(assessment="benign", confidence=0.6)
    outcome, disp = decide_outcome(report, _Cfg())
    assert outcome == StageOutcome.ESCALATE


def test_confirmed_exactly_at_advance_min_advances():
    report = _report(assessment="confirmed", confidence=0.6)
    outcome, disp = decide_outcome(report, _Cfg())
    assert outcome == StageOutcome.ADVANCE


def test_confirmed_just_below_advance_min_escalates():
    report = _report(assessment="confirmed", confidence=0.599)
    outcome, disp = decide_outcome(report, _Cfg())
    assert outcome == StageOutcome.ESCALATE
