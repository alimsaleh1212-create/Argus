"""T021 — same judgment flips outcome when config thresholds change (FR-004 AC2)."""

from __future__ import annotations

from backend.agents.triage import decide_outcome
from backend.domain.pipeline import StageOutcome
from backend.domain.triage import TriageJudgment, TriageVerdict
from backend.infra.config import TriageSettings


def _j(verdict: str, confidence: float) -> TriageJudgment:
    return TriageJudgment(
        verdict=TriageVerdict(verdict),
        confidence=confidence,
        rationale="borderline",
        cited_evidence=["summary"],
    )


def test_raising_advance_min_converts_advance_to_escalate():
    judgment = _j("real", 0.65)
    # With default advance_min=0.6 → ADVANCE
    assert decide_outcome(judgment, TriageSettings())[0] == StageOutcome.ADVANCE
    # Raise advance_min to 0.7 → ESCALATE
    assert (
        decide_outcome(judgment, TriageSettings(advance_min_confidence=0.7))[0]
        == StageOutcome.ESCALATE
    )


def test_lowering_resolve_min_converts_noise_escalate_to_resolved():
    judgment = _j("noise", 0.65)
    # Default resolve_min=0.7 → ESCALATE (0.65 < 0.7)
    assert decide_outcome(judgment, TriageSettings())[0] == StageOutcome.ESCALATE
    # Lower resolve_min to 0.6 → RESOLVED (0.65 >= 0.6)
    assert (
        decide_outcome(
            judgment, TriageSettings(advance_min_confidence=0.5, resolve_min_confidence=0.6)
        )[0]
        == StageOutcome.RESOLVED
    )


def test_raising_resolve_min_converts_resolved_to_escalate():
    judgment = _j("noise", 0.75)
    # Default resolve_min=0.7 → RESOLVED
    assert decide_outcome(judgment, TriageSettings())[0] == StageOutcome.RESOLVED
    # Raise resolve_min to 0.8 → ESCALATE
    assert (
        decide_outcome(judgment, TriageSettings(resolve_min_confidence=0.8))[0]
        == StageOutcome.ESCALATE
    )


def test_equal_thresholds_still_work():
    """advance_min == resolve_min is valid — noise at exactly that value resolves."""
    cfg = TriageSettings(advance_min_confidence=0.7, resolve_min_confidence=0.7)
    j = _j("noise", 0.7)
    outcome, _ = decide_outcome(j, cfg)
    assert outcome == StageOutcome.RESOLVED
