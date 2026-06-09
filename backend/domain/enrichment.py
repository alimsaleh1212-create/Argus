"""Pure domain types for the enrichment stage (#9).

No outward imports — domain-isolated like domain/triage.py.
All fields are assumed already redacted before construction.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class EnrichmentAssessment(StrEnum):
    CONFIRMED = "confirmed"
    BENIGN = "benign"
    INCONCLUSIVE = "inconclusive"


class EnrichmentReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    assessment: EnrichmentAssessment
    confidence: float = Field(ge=0.0, le=1.0)
    correlation_summary: str = Field(min_length=1)
    external_findings: list[str] = Field(default_factory=list)
    internal_findings: list[str] = Field(default_factory=list)
    cited_evidence: list[str] = Field(min_length=1)
