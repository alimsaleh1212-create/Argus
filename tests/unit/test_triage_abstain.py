"""T020 — decide_outcome escalate branches (US2, FR-004, SC-003)."""

from __future__ import annotations

from backend.agents.triage import decide_outcome
from backend.domain.pipeline import StageOutcome
from backend.domain.triage import TriageJudgment, TriageVerdict
from backend.infra.config import TriageSettings


def _cfg(advance: float = 0.6, resolve: float = 0.7) -> TriageSettings:
    return TriageSettings(advance_min_confidence=advance, resolve_min_confidence=resolve)


def _j(verdict: str, confidence: float) -> TriageJudgment:
    return TriageJudgment(
        verdict=TriageVerdict(verdict),
        confidence=confidence,
        rationale="not enough info",
        cited_evidence=["rule_id"],
    )


def test_uncertain_escalates_regardless_of_confidence():
    for conf in [0.0, 0.5, 0.95, 1.0]:
        outcome, disp = decide_outcome(_j("uncertain", conf), _cfg())
        assert outcome == StageOutcome.ESCALATE
        assert disp == "escalated_triage"


def test_real_below_advance_min_escalates():
    outcome, disp = decide_outcome(_j("real", 0.59), _cfg(advance=0.6))
    assert outcome == StageOutcome.ESCALATE
    assert disp == "escalated_triage"


def test_noise_below_advance_min_escalates():
    outcome, disp = decide_outcome(_j("noise", 0.4), _cfg(advance=0.6))
    assert outcome == StageOutcome.ESCALATE


def test_noise_between_advance_and_resolve_escalates():
    """advance_min <= conf < resolve_min → escalate (not enough for auto-close)."""
    outcome, disp = decide_outcome(_j("noise", 0.65), _cfg(advance=0.6, resolve=0.7))
    assert outcome == StageOutcome.ESCALATE
    assert disp == "escalated_triage"


def test_boundary_exactly_at_advance_passes():
    outcome, _ = decide_outcome(_j("real", 0.6), _cfg(advance=0.6))
    assert outcome == StageOutcome.ADVANCE


def test_boundary_strictly_below_advance_fails():
    outcome, _ = decide_outcome(_j("real", 0.5999), _cfg(advance=0.6))
    assert outcome == StageOutcome.ESCALATE


def test_boundary_exactly_at_resolve_passes():
    outcome, _ = decide_outcome(_j("noise", 0.7), _cfg(resolve=0.7))
    assert outcome == StageOutcome.RESOLVED


def test_boundary_strictly_below_resolve_fails():
    outcome, _ = decide_outcome(_j("noise", 0.6999), _cfg(resolve=0.7))
    assert outcome == StageOutcome.ESCALATE
