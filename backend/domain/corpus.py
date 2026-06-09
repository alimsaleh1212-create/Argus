"""Pure domain types for the knowledge corpus layer (#5).

No outward imports — only domain→domain reuse from domain/memory.py.
All text fields are assumed to be already redacted before construction.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, field_validator

from backend.domain.memory import (  # noqa: F401 — re-exported for consumers
    EntityKind,
    EntityRef,
    TemporalFact,
)


class ReferenceKind(StrEnum):
    TECHNIQUE = "technique"
    RUNBOOK = "runbook"


class ReferenceCorpusEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: ReferenceKind
    key: str
    title: str
    content: str
    tags: list[str] = []

    @field_validator("key")
    @classmethod
    def _key_nonempty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("key must not be empty")
        return v

    @field_validator("tags", mode="before")
    @classmethod
    def _normalise_tags(cls, v: object) -> list[str]:
        if not isinstance(v, list):
            raise ValueError("tags must be a list")
        seen: dict[str, None] = {}
        for tag in v:
            lowered = str(tag).lower()
            seen[lowered] = None
        return list(seen)


class ReferenceQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    technique_ids: list[str] = []
    terms: list[str] = []


class ReferenceHit(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    entry: ReferenceCorpusEntry
    relevance: float
    matched_on: Literal["technique", "tag", "term"]

    @field_validator("relevance")
    @classmethod
    def _relevance_range(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError(f"relevance must be in [0, 1], got {v}")
        return v


class IntelVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    indicator: str
    verdict: Literal["benign", "malicious", "suspicious", "unknown"]
    source: str
    observed_at: datetime


@runtime_checkable
class CorpusRetriever(Protocol):
    async def search_reference(self, query: ReferenceQuery, *, k: int) -> list[ReferenceHit]: ...


__all__ = [
    "ReferenceKind",
    "ReferenceCorpusEntry",
    "ReferenceQuery",
    "ReferenceHit",
    "IntelVerdict",
    "CorpusRetriever",
    # re-exported for consumers
    "EntityKind",
    "EntityRef",
    "TemporalFact",
]
