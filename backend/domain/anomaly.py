"""ML anomaly detection — pure domain types (SPEC-ml-anomaly-detector #17).

Domain-isolated: no I/O, no infra imports. The only outward import is
`Severity` from `domain/incident.py` (domain→domain is allowed under the
isolation contract).

`AnomalyModel` is a pure Protocol so tests can inject `FakeAnomalyModel`
without loading scikit-learn or the real artifact.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.domain.incident import Severity


class EntityActivityWindow(BaseModel):
    """A single entity's replayed activity aggregated over a configured window."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    entity_id: str
    window_start: datetime
    window_end: datetime
    features: dict[str, float] = Field(default_factory=dict)
    raw_event_count: int = Field(ge=0, default=0)

    @model_validator(mode="after")
    def _window_valid(self) -> EntityActivityWindow:
        if self.window_end <= self.window_start:
            raise ValueError("window_end must be after window_start")
        return self


class FeatureVector(BaseModel):
    """Ordered, model-ready numeric vector derived from a window's features."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    entity_id: str
    values: list[float] = Field(default_factory=list)


class AnomalyFinding(BaseModel):
    """Pure output of the scoring pass — one per window over fire_threshold."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    entity_id: str
    score: float = Field(ge=0.0, le=1.0)
    severity: Severity
    window: EntityActivityWindow
    top_features: list[str] = Field(default_factory=list)


class ScoreBands(BaseModel):
    """Config-backed score→severity mapping + fire threshold."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    fire_threshold: float = Field(ge=0.0, le=1.0, default=0.60)
    band_medium: float = Field(ge=0.0, le=1.0, default=0.60)
    band_high: float = Field(ge=0.0, le=1.0, default=0.75)
    band_critical: float = Field(ge=0.0, le=1.0, default=0.90)

    @model_validator(mode="after")
    def _bands_ordered(self) -> ScoreBands:
        if not (self.band_medium <= self.band_high <= self.band_critical):
            raise ValueError("bands must satisfy medium <= high <= critical")
        return self


class AnomalyModel(Protocol):
    """Pure protocol for anomaly scoring — implemented by infra, faked in tests."""

    feature_spec: list[str]

    def score(self, vectors: list[FeatureVector]) -> list[float]:
        ...
        # returns a [0,1] anomaly score per vector (higher = more anomalous)


class RawLogEvent(BaseModel):
    """A single replayed source record the anomaly detector aggregates."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    event_time: datetime
    entity_id: str
    fields: dict[str, Any] = Field(default_factory=dict)

    @field_validator("event_time", mode="before")
    @classmethod
    def _coerce_event_time(cls, v: object) -> object:
        if isinstance(v, datetime):
            return v
        if isinstance(v, str):
            try:
                return datetime.fromisoformat(v.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError(f"invalid event_time: {v}") from exc
        raise ValueError(f"event_time must be datetime or str, got {type(v).__name__}")


def parse_window(value: str | int | timedelta) -> timedelta:
    """Parse a window spec into a timedelta.

    Supports ISO-8601 durations ("P1D", "PT1H") or simple suffixes
    ("1d", "1h", "30m", "1s"). Defaults to seconds for bare integers.
    """
    if isinstance(value, timedelta):
        return value
    if isinstance(value, int):
        return timedelta(seconds=value)
    s = str(value).strip()
    if s.startswith("P"):
        # Minimal ISO-8601 duration parser for day/hour/minute/second.
        import re

        m = re.match(r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?", s)
        if not m:
            raise ValueError(f"invalid ISO-8601 duration: {value}")
        days = int(m.group(1) or 0)
        hours = int(m.group(2) or 0)
        minutes = int(m.group(3) or 0)
        seconds = int(m.group(4) or 0)
        return timedelta(days=days, hours=hours, minutes=minutes, seconds=seconds)
    suffix_map = {"d": 86400, "h": 3600, "m": 60, "s": 1}
    if s[-1] in suffix_map:
        try:
            return timedelta(seconds=int(s[:-1]) * suffix_map[s[-1]])
        except ValueError as exc:
            raise ValueError(f"invalid window duration: {value}") from exc
    try:
        return timedelta(seconds=int(s))
    except ValueError as exc:
        raise ValueError(f"invalid window duration: {value}") from exc
