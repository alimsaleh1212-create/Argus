"""T013 — decide_outcome happy paths (US1/US2)."""

from __future__ import annotations

from backend.agents.triage import decide_outcome
from backend.domain.pipeline import StageOutcome
from backend.domain.triage import TriageJudgment, TriageVerdict
from backend.infra.config import TriageSettings


def _cfg(advance: float = 0.6, resolve: float = 0.7) -> TriageSettings:
    return TriageSettings(advance_min_confidence=advance, resolve_min_confidence=resolve)


def _judgment(verdict: str, confidence: float) -> TriageJudgment:
    return TriageJudgment(
        verdict=TriageVerdict(verdict),
        confidence=confidence,
        rationale="some evidence",
        cited_evidence=["rule_id"],
    )


# --- ADVANCE ---

def test_real_above_advance_min_advances():
    outcome, disp = decide_outcome(_judgment("real", 0.9), _cfg())
    assert outcome == StageOutcome.ADVANCE
    assert disp is None


def test_real_at_advance_min_advances():
    outcome, disp = decide_outcome(_judgment("real", 0.6), _cfg())
    assert outcome == StageOutcome.ADVANCE
    assert disp is None


# --- RESOLVED ---

def test_noise_above_resolve_min_resolves():
    outcome, disp = decide_outcome(_judgment("noise", 0.8), _cfg())
    assert outcome == StageOutcome.RESOLVED
    assert disp == "auto_resolved_triage"


def test_noise_at_resolve_min_resolves():
    outcome, disp = decide_outcome(_judgment("noise", 0.7), _cfg())
    assert outcome == StageOutcome.RESOLVED
    assert disp == "auto_resolved_triage"


# --- ESCALATE (uncertain) ---

def test_uncertain_always_escalates():
    outcome, disp = decide_outcome(_judgment("uncertain", 0.95), _cfg())
    assert outcome == StageOutcome.ESCALATE
    assert disp == "escalated_triage"


# --- ESCALATE (low confidence) ---

def test_real_below_advance_min_escalates():
    outcome, disp = decide_outcome(_judgment("real", 0.59), _cfg())
    assert outcome == StageOutcome.ESCALATE
    assert disp == "escalated_triage"


def test_noise_below_advance_min_escalates():
    outcome, disp = decide_outcome(_judgment("noise", 0.4), _cfg())
    assert outcome == StageOutcome.ESCALATE
    assert disp == "escalated_triage"


# --- ESCALATE (noise above advance but below resolve) ---

def test_noise_between_advance_and_resolve_escalates():
    outcome, disp = decide_outcome(_judgment("noise", 0.65), _cfg())
    assert outcome == StageOutcome.ESCALATE
    assert disp == "escalated_triage"


# --- Boundary: strictly below abstains ---

def test_real_strictly_below_advance_min_escalates():
    outcome, _ = decide_outcome(_judgment("real", 0.5999), _cfg(advance=0.6))
    assert outcome == StageOutcome.ESCALATE


def test_noise_strictly_below_resolve_min_escalates():
    outcome, _ = decide_outcome(_judgment("noise", 0.6999), _cfg(resolve=0.7))
    assert outcome == StageOutcome.ESCALATE
