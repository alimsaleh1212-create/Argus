"""Unit tests for backend/domain/memory.py — T007."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from backend.domain.incident import Severity
from backend.domain.memory import (
    EntityKind,
    EntityRef,
    EpisodeQuery,
    FactState,
    IncidentEpisode,
    MemoryHit,
    MemoryStore,
    TemporalFact,
)

_NOW = datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
_UUID = uuid.uuid4()


# ── EntityRef ────────────────────────────────────────────────────────────────

def test_entity_ref_valid() -> None:
    ref = EntityRef(kind=EntityKind.ADDRESS, value="10.0.0.1")
    assert ref.kind == EntityKind.ADDRESS
    assert ref.value == "10.0.0.1"


def test_entity_ref_invalid_kind() -> None:
    with pytest.raises(ValidationError):
        EntityRef(kind="unknown_kind", value="x")  # type: ignore[arg-type]


def test_entity_ref_extra_forbid() -> None:
    with pytest.raises(ValidationError):
        EntityRef(kind=EntityKind.HOST, value="host1", extra_field="x")  # type: ignore[call-arg]


# ── IncidentEpisode ──────────────────────────────────────────────────────────

def _episode(**overrides: object) -> IncidentEpisode:
    base: dict = {
        "incident_id": _UUID,
        "observed_at": _NOW,
        "summary": "test summary",
        "verdict": "real",
        "severity": Severity.HIGH,
        "disposition": "escalated_enrichment",
    }
    base.update(overrides)
    return IncidentEpisode(**base)


def test_episode_requires_incident_id() -> None:
    with pytest.raises(ValidationError):
        IncidentEpisode(
            observed_at=_NOW,
            summary="s",
            verdict="real",
            severity=Severity.HIGH,
            disposition="d",
        )


def test_episode_requires_observed_at() -> None:
    with pytest.raises(ValidationError):
        IncidentEpisode(
            incident_id=_UUID,
            summary="s",
            verdict="real",
            severity=Severity.HIGH,
            disposition="d",
        )


def test_episode_extra_forbid() -> None:
    with pytest.raises(ValidationError):
        _episode(unknown_field="x")  # type: ignore[call-arg]


def test_episode_defaults() -> None:
    ep = _episode()
    assert ep.entities == []
    assert ep.fields == {}


def test_episode_with_entities() -> None:
    ep = _episode(entities=[EntityRef(kind=EntityKind.ADDRESS, value="1.2.3.4")])
    assert len(ep.entities) == 1


# ── MemoryHit ────────────────────────────────────────────────────────────────

def test_memory_hit_relevance_bounds() -> None:
    with pytest.raises(ValidationError):
        MemoryHit(
            incident_id=_UUID,
            summary="s",
            disposition="d",
            observed_at=_NOW,
            relevance=1.5,
        )


def test_memory_hit_relevance_zero() -> None:
    hit = MemoryHit(incident_id=_UUID, summary="s", disposition="d", observed_at=_NOW, relevance=0.0)
    assert hit.relevance == 0.0


def test_memory_hit_extra_forbid() -> None:
    with pytest.raises(ValidationError):
        MemoryHit(
            incident_id=_UUID,
            summary="s",
            disposition="d",
            observed_at=_NOW,
            relevance=0.5,
            extra="x",  # type: ignore[call-arg]
        )


# ── FactState ────────────────────────────────────────────────────────────────

def test_factstate_defaults() -> None:
    fs = FactState()
    assert fs.fact is None
    assert fs.is_current is False
    assert fs.has_superseded is False


def test_factstate_extra_forbid() -> None:
    with pytest.raises(ValidationError):
        FactState(extra_key="x")  # type: ignore[call-arg]


# ── TemporalFact ─────────────────────────────────────────────────────────────

def test_temporal_fact_valid_until_none() -> None:
    tf = TemporalFact(
        entity=EntityRef(kind=EntityKind.ADDRESS, value="1.2.3.4"),
        fact_type="reputation",
        value="malicious",
        valid_from=_NOW,
        valid_until=None,
    )
    assert tf.valid_until is None


# ── MemoryStore Protocol ──────────────────────────────────────────────────────

def test_memory_store_is_protocol() -> None:
    # The Protocol is runtime-checkable; a class with the right methods satisfies it.
    class FakeStore:
        async def write_episode(self, episode: IncidentEpisode) -> None:
            pass

        async def search_similar(self, query: EpisodeQuery, *, k: int) -> list[MemoryHit]:
            return []

        async def query_fact(
            self, entity: EntityRef, fact_type: str, *, as_of=None
        ) -> FactState:
            return FactState()

    assert isinstance(FakeStore(), MemoryStore)
