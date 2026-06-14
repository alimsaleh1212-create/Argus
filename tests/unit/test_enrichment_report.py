"""Unit tests — EnrichmentReport validation (T004)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.domain.enrichment import EnrichmentAssessment, EnrichmentReport


def _valid_report(**overrides) -> dict:
    base = {
        "assessment": "confirmed",
        "confidence": 0.85,
        "correlation_summary": "External indicator matches known threat; prior incident on same host.",
        "external_findings": ["MITRE T1059 mapping found in corpus"],
        "internal_findings": ["Similar prior incident 3 days ago on same host"],
        "cited_evidence": ["rule.id=100001", "prior_incident_id=abc123"],
    }
    base.update(overrides)
    return base


def test_valid_report_with_findings():
    r = EnrichmentReport.model_validate(_valid_report())
    assert r.assessment == EnrichmentAssessment.CONFIRMED
    assert 0.0 <= r.confidence <= 1.0
    assert len(r.cited_evidence) >= 1


def test_valid_report_without_findings():
    r = EnrichmentReport.model_validate(_valid_report(external_findings=[], internal_findings=[]))
    assert r.external_findings == []
    assert r.internal_findings == []


def test_rejects_out_of_vocabulary_assessment():
    with pytest.raises(ValidationError):
        EnrichmentReport.model_validate(_valid_report(assessment="maybe"))


def test_rejects_confidence_above_one():
    with pytest.raises(ValidationError):
        EnrichmentReport.model_validate(_valid_report(confidence=1.01))


def test_rejects_confidence_below_zero():
    with pytest.raises(ValidationError):
        EnrichmentReport.model_validate(_valid_report(confidence=-0.01))


def test_rejects_empty_correlation_summary():
    with pytest.raises(ValidationError):
        EnrichmentReport.model_validate(_valid_report(correlation_summary=""))


def test_rejects_empty_cited_evidence():
    with pytest.raises(ValidationError):
        EnrichmentReport.model_validate(_valid_report(cited_evidence=[]))


def test_rejects_extra_fields():
    with pytest.raises(ValidationError):
        EnrichmentReport.model_validate(_valid_report(unknown_field="oops"))


def test_frozen():
    r = EnrichmentReport.model_validate(_valid_report())
    with pytest.raises(ValidationError):
        r.confidence = 0.5  # type: ignore[misc]


@pytest.mark.parametrize("assessment", ["confirmed", "benign", "inconclusive"])
def test_all_assessments_accepted(assessment: str):
    r = EnrichmentReport.model_validate(_valid_report(assessment=assessment))
    assert r.assessment.value == assessment
