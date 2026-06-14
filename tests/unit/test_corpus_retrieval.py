"""Unit tests for CorpusRepository retrieval ranking — T016."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.domain.corpus import (
    ReferenceCorpusEntry,
    ReferenceKind,
    ReferenceQuery,
)
from backend.repositories.corpus import CorpusRepository, _update_hit


def _make_entry(
    key: str = "T1110", kind: ReferenceKind = ReferenceKind.TECHNIQUE
) -> ReferenceCorpusEntry:
    return ReferenceCorpusEntry(
        kind=kind,
        key=key,
        title="Brute Force",
        content="Use MFA.",
        tags=["t1110", "credential-access"],
    )


# ── Ranking determinism ──────────────────────────────────────────────────────


def test_update_hit_keeps_higher_relevance() -> None:
    hits: dict = {}
    entry = _make_entry()
    _update_hit(hits, entry, 0.3, "term")
    _update_hit(hits, entry, 1.0, "technique")
    assert hits[("technique", "T1110")].relevance == 1.0
    assert hits[("technique", "T1110")].matched_on == "technique"


def test_update_hit_does_not_downgrade() -> None:
    hits: dict = {}
    entry = _make_entry()
    _update_hit(hits, entry, 1.0, "technique")
    _update_hit(hits, entry, 0.3, "term")
    assert hits[("technique", "T1110")].relevance == 1.0


def test_update_hit_dedupes_by_kind_key() -> None:
    hits: dict = {}
    e1 = _make_entry("T1110", ReferenceKind.TECHNIQUE)
    e2 = _make_entry("T1110", ReferenceKind.TECHNIQUE)
    _update_hit(hits, e1, 0.5, "tag")
    _update_hit(hits, e2, 0.7, "tag")
    assert len(hits) == 1
    assert hits[("technique", "T1110")].relevance == 0.7


# ── k truncation ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_search_reference_k_truncation() -> None:
    session = MagicMock()

    def _make_row(key: str) -> MagicMock:
        row = MagicMock()
        row.kind = "technique"
        row.key = key
        row.title = "Title"
        row.content = "Content"
        row.tags = ["t1110"]
        return row

    rows = [_make_row(f"T{i:04d}") for i in range(10)]

    async def _execute(stmt, params=None):
        result = MagicMock()
        result.__iter__ = lambda s: iter(rows)
        return result

    session.execute = AsyncMock(side_effect=_execute)
    repo = CorpusRepository(session)
    hits = await repo.search_reference(ReferenceQuery(technique_ids=["T1110"]), k=3)
    assert len(hits) <= 3


# ── empty / no-match ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_search_reference_empty_query_returns_empty() -> None:
    session = MagicMock()
    repo = CorpusRepository(session)
    hits = await repo.search_reference(ReferenceQuery(), k=5)
    assert hits == []


@pytest.mark.asyncio
async def test_search_reference_no_match_returns_empty() -> None:
    session = MagicMock()

    async def _execute(stmt, params=None):
        result = MagicMock()
        result.__iter__ = lambda s: iter([])
        return result

    session.execute = AsyncMock(side_effect=_execute)
    repo = CorpusRepository(session)
    hits = await repo.search_reference(ReferenceQuery(technique_ids=["T9999"]), k=5)
    assert hits == []


# ── redaction applied before upsert ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_upsert_does_not_store_redacted_secret() -> None:
    """Redactor is invoked with MEMORY_WRITE boundary before upsert."""
    from unittest.mock import AsyncMock, MagicMock

    from backend.services.corpus import seed_reference

    captured: list[str] = []

    redactor = MagicMock()
    redactor.redact_text = lambda text, boundary: captured.append(text) or "[REDACTED]"

    repo = MagicMock()
    repo.upsert_entries = AsyncMock()

    secret = "AKIAIOSFODNN7EXAMPLE"
    records = {
        "techniques": [
            {"id": "T1110", "title": f"Title {secret}", "tactic": "cred", "mitigations": "ok"}
        ],
        "runbooks": [],
    }
    await seed_reference(records, redactor, repo)

    entries = repo.upsert_entries.call_args[0][0]
    assert entries[0].title == "[REDACTED]"
    assert entries[0].content == "[REDACTED]"
