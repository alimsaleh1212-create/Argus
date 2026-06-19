"""Unit tests for backend.infra.memory.store pure helpers.

`_to_native_dt` converts the Neo4j driver's neo4j.time.DateTime into a native
datetime; without it, query_fact's TemporalFact construction raised a pydantic
validation error and never returned a fact (the fix's read-side regression).
"""

from __future__ import annotations

from datetime import UTC, datetime

from backend.infra.memory.store import _to_native_dt


class _FakeNeo4jDateTime:
    """Mimics neo4j.time.DateTime — only the .to_native() contract matters here."""

    def __init__(self, dt: datetime) -> None:
        self._dt = dt

    def to_native(self) -> datetime:
        return self._dt


def test_to_native_dt_converts_neo4j_datetime() -> None:
    native = datetime(2026, 3, 1, tzinfo=UTC)
    assert _to_native_dt(_FakeNeo4jDateTime(native)) == native


def test_to_native_dt_passes_through_plain_datetime() -> None:
    dt = datetime(2026, 1, 1, tzinfo=UTC)
    assert _to_native_dt(dt) is dt


def test_to_native_dt_passes_through_none() -> None:
    assert _to_native_dt(None) is None
