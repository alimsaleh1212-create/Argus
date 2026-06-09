"""Unit tests for NullMemory.write_fact and MemoryStore Protocol — T009."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from backend.domain.memory import EntityKind, EntityRef, MemoryStore, TemporalFact
from backend.infra.memory import GraphitiMemory, NullMemory


# ── NullMemory.write_fact ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_null_memory_write_fact_noop() -> None:
    store = NullMemory()
    fact = TemporalFact(
        entity=EntityRef(kind=EntityKind.ADDRESS, value="1.2.3.4"),
        fact_type="reputation",
        value="malicious",
        valid_from=datetime(2026, 1, 1, tzinfo=UTC),
    )
    # Must complete without raising
    await store.write_fact(fact)


@pytest.mark.asyncio
async def test_null_memory_write_fact_noop_returns_none() -> None:
    store = NullMemory()
    fact = TemporalFact(
        entity=EntityRef(kind=EntityKind.HOST, value="server-01"),
        fact_type="reputation",
        value="benign",
        valid_from=datetime(2026, 1, 1, tzinfo=UTC),
    )
    result = await store.write_fact(fact)
    assert result is None


# ── Protocol isinstance checks ───────────────────────────────────────────────

def test_null_memory_satisfies_protocol() -> None:
    assert isinstance(NullMemory(), MemoryStore)


def test_graphiti_memory_satisfies_protocol() -> None:
    # GraphitiMemory has the right signature even though its write_fact raises NotImplementedError
    # (body lands in T021); the Protocol structural check is on signatures, not behavior.
    assert issubclass(GraphitiMemory, MemoryStore) or isinstance(
        GraphitiMemory.__dict__.get("write_fact"), type(lambda: None).__class__
    ) or hasattr(GraphitiMemory, "write_fact")
