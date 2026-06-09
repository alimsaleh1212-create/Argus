"""Pure domain types for the temporal incident-memory layer (SPEC-memory #6).

No outward imports — only domain→domain (Severity from domain/incident.py).
All text fields are assumed to be already redacted before construction.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.domain.incident import Severity


class EntityKind(StrEnum):
    ADDRESS = "address"
    HOST = "host"
    USER = "user"
    INDICATOR = "indicator"


class EntityRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: EntityKind
    value: str


class IncidentEpisode(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    incident_id: uuid.UUID
    observed_at: datetime
    summary: str
    verdict: str
    severity: Severity
    disposition: str
    entities: list[EntityRef] = Field(default_factory=list)
    fields: dict[str, Any] = Field(default_factory=dict)


class TemporalFact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    entity: EntityRef
    fact_type: str
    value: str
    valid_from: datetime
    valid_until: datetime | None = None


class FactState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    fact: TemporalFact | None = None
    is_current: bool = False
    has_superseded: bool = False


class MemoryHit(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    incident_id: uuid.UUID
    summary: str
    disposition: str
    observed_at: datetime
    relevance: float

    @field_validator("relevance")
    @classmethod
    def _relevance_range(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError(f"relevance must be in [0, 1], got {v}")
        return v


class EpisodeQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str
    entities: list[EntityRef] = Field(default_factory=list)


@runtime_checkable
class MemoryStore(Protocol):
    async def write_episode(self, episode: IncidentEpisode) -> None: ...

    async def search_similar(self, query: EpisodeQuery, *, k: int) -> list[MemoryHit]: ...

    async def query_fact(
        self,
        entity: EntityRef,
        fact_type: str,
        *,
        as_of: datetime | None = None,
    ) -> FactState: ...

    async def write_fact(self, fact: TemporalFact) -> None: ...
