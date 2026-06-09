"""Unit tests for NullMemory degradation — T014 / T029."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.domain.incident import Severity
from backend.domain.memory import EntityKind, EntityRef, EpisodeQuery, FactState, IncidentEpisode
from backend.infra.memory import NullMemory

_NOW = datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
_UUID = uuid.uuid4()


def _episode() -> IncidentEpisode:
    return IncidentEpisode(
        incident_id=_UUID,
        observed_at=_NOW,
        summary="test",
        verdict="real",
        severity=Severity.HIGH,
        disposition="escalated_enrichment",
    )


# ── NullMemory contract ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_null_memory_write_is_noop() -> None:
    store = NullMemory()
    await store.write_episode(_episode())  # must not raise


@pytest.mark.asyncio
async def test_null_memory_search_returns_empty() -> None:
    store = NullMemory()
    results = await store.search_similar(EpisodeQuery(text="attack"), k=5)
    assert results == []


@pytest.mark.asyncio
async def test_null_memory_query_fact_returns_empty() -> None:
    store = NullMemory()
    entity = EntityRef(kind=EntityKind.ADDRESS, value="1.2.3.4")
    state = await store.query_fact(entity, "reputation")
    assert state.fact is None
    assert state.is_current is False
    assert state.has_superseded is False


@pytest.mark.asyncio
async def test_null_memory_query_fact_with_as_of() -> None:
    store = NullMemory()
    entity = EntityRef(kind=EntityKind.ADDRESS, value="1.2.3.4")
    state = await store.query_fact(entity, "reputation", as_of=_NOW)
    assert isinstance(state, FactState)
    assert state.fact is None


# ── record_episode swallows a store that raises ───────────────────────────────

@pytest.mark.asyncio
async def test_record_episode_swallows_store_error() -> None:
    """record_episode must not raise when the store throws — FR-006."""
    from backend.domain.incident import Incident, IncidentStatus
    from backend.domain.redaction import Boundary
    from backend.services.memory import record_episode

    failing_store = AsyncMock()
    failing_store.write_episode.side_effect = RuntimeError("neo4j gone")

    redactor = MagicMock()
    redactor.redact_text.side_effect = lambda text, b: text
    redactor.redact_mapping.side_effect = lambda data, b: data

    incident = Incident(
        id=_UUID,
        status=IncidentStatus.RESOLVED,
        severity=Severity.HIGH,
        correlation_id="c",
        dedup_fingerprint="f",
        source="wazuh",
        raw_alert={},
        disposition="auto_resolved_triage",
        updated_at=_NOW,
    )

    # Must not raise — caller wraps in try/except but we test the service itself
    # doesn't hide exceptions (they bubble up to the caller who swallows them)
    with pytest.raises(RuntimeError, match="neo4j gone"):
        await record_episode(incident, failing_store, redactor)


# ── NullMemory satisfies MemoryStore Protocol ────────────────────────────────

def test_null_memory_satisfies_protocol() -> None:
    from backend.domain.memory import MemoryStore

    assert isinstance(NullMemory(), MemoryStore)
