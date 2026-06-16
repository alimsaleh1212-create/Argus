"""Deterministic rule/threshold detector — pure domain types (SPEC-detector #14).

Domain-isolated: no I/O, no infra imports. Pydantic v2, extra="forbid",
frozen where natural. The only outward import is `Severity` from
`domain/incident.py` (domain→domain is allowed under the isolation contract).

The detector is a *decoupled detection source*: it consumes replayed raw
events, applies a config-backed rule/threshold set, and emits
`FiredAlert`s for the existing ingestion contract to consume. It does
**no** I/O — `evaluate()` is pure, the runner is the only I/O seam.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.domain.incident import Severity

# Severity ordering for D4 (multi-match → highest severity, ties broken by config order).
_SEVERITY_RANK: dict[Severity, int] = {
    Severity.LOW: 0,
    Severity.MEDIUM: 1,
    Severity.HIGH: 2,
    Severity.CRITICAL: 3,
}


class RawEvent(BaseModel):
    """A replayed source event the detector evaluates (FR-001)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    event_time: datetime
    fields: dict[str, Any] = Field(default_factory=dict)
    source_host: str | None = None


class _BaseMatch(BaseModel):
    """Shared shape for `match` conditions embedded in a rule or a threshold."""

    model_config = ConfigDict(extra="forbid")

    field: str
    op: Literal["equals", "contains", "regex", "in_list"]
    value: str | None = None
    list_ref: str | None = None

    @model_validator(mode="after")
    def _value_or_list(self) -> _BaseMatch:
        if self.op == "in_list":
            if not self.list_ref:
                raise ValueError("op='in_list' requires list_ref")
        else:
            if self.value is None:
                raise ValueError(f"op='{self.op}' requires value")
        return self


class MatchRule(_BaseMatch):
    """A signature rule — one match per event (FR-002)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["match"] = "match"
    id: str
    description: str
    severity: Severity
    technique: str | None = None


class ThresholdRule(BaseModel):
    """An aggregation rule — N qualifying events in W seconds, grouped (FR-004)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["threshold"] = "threshold"
    id: str
    description: str
    match: _BaseMatch
    group_by: str
    count: int = Field(gt=0)
    window_seconds: int = Field(gt=0)
    severity: Severity
    technique: str | None = None

    @field_validator("match", mode="before")
    @classmethod
    def _coerce_match(cls, v: Any) -> Any:
        # YAML may hand us a plain dict; let pydantic coerce.
        return v


# Discriminated union — `kind` is the discriminator.
DetectionRule = MatchRule | ThresholdRule


class RuleSet(BaseModel):
    """Ordered rule set + named lists (FR-005)."""

    model_config = ConfigDict(extra="forbid")

    rules: list[DetectionRule] = Field(default_factory=list)
    lists: dict[str, list[str]] = Field(default_factory=dict)


class FiredAlert(BaseModel):
    """Pure output of `evaluate()` — one per detection, pre-mapping."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    rule_id: str
    description: str
    severity: Severity
    technique: str | None = None
    event: RawEvent
    group_key: str | None = None


def severity_rank(severity: Severity) -> int:
    """D4 ordering: low<medium<high<critical."""
    return _SEVERITY_RANK[severity]
