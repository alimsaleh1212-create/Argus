"""Unit tests — config-backed threshold flips (T019)."""

from __future__ import annotations

from backend.agents.enrichment import decide_outcome
from backend.domain.enrichment import EnrichmentReport
from backend.domain.pipeline import StageOutcome


def _report(**kwargs) -> EnrichmentReport:
    base = {
        "assessment": "confirmed",
        "confidence": 0.75,
        "correlation_summary": "Correlation found.",
        "cited_evidence": ["evidence=1"],
    }
    base.update(kwargs)
    return EnrichmentReport.model_validate(base)


class _Cfg:
    def __init__(self, advance_min: float, resolve_min: float):
        self.advance_min_confidence = advance_min
        self.resolve_min_confidence = resolve_min


def test_same_report_flips_when_threshold_raised():
    """Raising advance_min_confidence above report's confidence flips ADVANCE→ESCALATE."""
    report = _report(assessment="confirmed", confidence=0.75)
    assert decide_outcome(report, _Cfg(0.6, 0.8))[0] == StageOutcome.ADVANCE
    assert decide_outcome(report, _Cfg(0.8, 0.9))[0] == StageOutcome.ESCALATE


def test_same_report_flips_benign_resolve_threshold():
    """Raising resolve_min above report's confidence flips RESOLVED→ESCALATE for benign."""
    report = _report(assessment="benign", confidence=0.75)
    assert decide_outcome(report, _Cfg(0.6, 0.7))[0] == StageOutcome.RESOLVED
    assert decide_outcome(report, _Cfg(0.6, 0.8))[0] == StageOutcome.ESCALATE


def test_lowering_advance_min_flips_escalate_to_advance():
    """Lowering advance_min below report confidence flips ESCALATE→ADVANCE."""
    report = _report(assessment="confirmed", confidence=0.5)
    assert decide_outcome(report, _Cfg(0.6, 0.7))[0] == StageOutcome.ESCALATE
    assert decide_outcome(report, _Cfg(0.3, 0.4))[0] == StageOutcome.ADVANCE


def test_threshold_change_not_hardcoded():
    """Inconclusive always escalates regardless of thresholds — behaviour is assessed-based."""
    report = _report(assessment="inconclusive", confidence=1.0)
    for advance_min in [0.0, 0.5, 0.9]:
        outcome, _ = decide_outcome(report, _Cfg(advance_min, advance_min))
        assert outcome == StageOutcome.ESCALATE
