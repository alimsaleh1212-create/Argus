"""Pure triage judgment types — no infra imports."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field, field_validator

from backend.domain.incident import Severity


class TriageVerdict(StrEnum):
    REAL = "real"
    NOISE = "noise"
    UNCERTAIN = "uncertain"


class TriageJudgment(BaseModel):
    model_config = {"extra": "forbid"}

    verdict: TriageVerdict
    confidence: float = Field(ge=0.0, le=1.0)
    assessed_severity: Severity | None = None
    rationale: str = Field(min_length=1)
    cited_evidence: list[str] = Field(min_length=1)

    @field_validator("cited_evidence")
    @classmethod
    def _at_least_one(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("cited_evidence must contain at least one item")
        return v
