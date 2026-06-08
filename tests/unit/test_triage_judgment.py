"""T004 — TriageJudgment validation (FR-002, FR-007)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.domain.triage import TriageJudgment, TriageVerdict


def _valid() -> dict:
    return {
        "verdict": "real",
        "confidence": 0.8,
        "rationale": "The rule_id matches a known exploit pattern.",
        "cited_evidence": ["rule_description"],
    }


def test_valid_judgment():
    j = TriageJudgment.model_validate(_valid())
    assert j.verdict == TriageVerdict.REAL
    assert j.confidence == 0.8
    assert j.assessed_severity is None


def test_valid_with_assessed_severity():
    data = {**_valid(), "assessed_severity": "high"}
    j = TriageJudgment.model_validate(data)
    assert j.assessed_severity is not None
    assert j.assessed_severity.value == "high"


def test_oov_verdict_rejected():
    with pytest.raises(ValidationError):
        TriageJudgment.model_validate({**_valid(), "verdict": "banana"})


def test_confidence_below_zero_rejected():
    with pytest.raises(ValidationError):
        TriageJudgment.model_validate({**_valid(), "confidence": -0.1})


def test_confidence_above_one_rejected():
    with pytest.raises(ValidationError):
        TriageJudgment.model_validate({**_valid(), "confidence": 1.1})


def test_empty_rationale_rejected():
    with pytest.raises(ValidationError):
        TriageJudgment.model_validate({**_valid(), "rationale": ""})


def test_empty_cited_evidence_rejected():
    with pytest.raises(ValidationError):
        TriageJudgment.model_validate({**_valid(), "cited_evidence": []})


def test_extra_field_rejected():
    with pytest.raises(ValidationError):
        TriageJudgment.model_validate({**_valid(), "unknown_key": "x"})


def test_uncertain_verdict_valid():
    j = TriageJudgment.model_validate({**_valid(), "verdict": "uncertain", "confidence": 0.3})
    assert j.verdict == TriageVerdict.UNCERTAIN


def test_confidence_boundary_zero():
    j = TriageJudgment.model_validate({**_valid(), "confidence": 0.0})
    assert j.confidence == 0.0


def test_confidence_boundary_one():
    j = TriageJudgment.model_validate({**_valid(), "confidence": 1.0})
    assert j.confidence == 1.0
