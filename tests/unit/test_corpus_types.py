"""Unit tests for backend/domain/corpus.py — T005."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from backend.domain.corpus import (
    IntelVerdict,
    ReferenceCorpusEntry,
    ReferenceHit,
    ReferenceKind,
    ReferenceQuery,
)


# ── ReferenceCorpusEntry ─────────────────────────────────────────────────────

def test_entry_basic() -> None:
    e = ReferenceCorpusEntry(
        kind=ReferenceKind.TECHNIQUE, key="T1110", title="Brute Force", content="Use MFA."
    )
    assert e.kind == ReferenceKind.TECHNIQUE
    assert e.key == "T1110"


def test_entry_key_nonempty() -> None:
    with pytest.raises(ValidationError):
        ReferenceCorpusEntry(kind=ReferenceKind.TECHNIQUE, key="   ", title="x", content="y")


def test_entry_tags_lowercased_deduped() -> None:
    e = ReferenceCorpusEntry(
        kind=ReferenceKind.RUNBOOK,
        key="rb-1",
        title="t",
        content="c",
        tags=["T1110", "t1110", "CRED", "cred"],
    )
    assert sorted(e.tags) == sorted(["t1110", "cred"])
    assert len(e.tags) == 2


def test_entry_extra_forbid() -> None:
    with pytest.raises(ValidationError):
        ReferenceCorpusEntry(  # type: ignore[call-arg]
            kind=ReferenceKind.TECHNIQUE, key="T1", title="t", content="c", unknown="x"
        )


def test_entry_frozen() -> None:
    e = ReferenceCorpusEntry(kind=ReferenceKind.TECHNIQUE, key="T1", title="t", content="c")
    with pytest.raises(Exception):
        e.key = "other"  # type: ignore[misc]


# ── ReferenceHit ─────────────────────────────────────────────────────────────

def _make_entry() -> ReferenceCorpusEntry:
    return ReferenceCorpusEntry(
        kind=ReferenceKind.TECHNIQUE, key="T1110", title="Brute Force", content="Use MFA."
    )


def test_hit_relevance_bounds_ok() -> None:
    ReferenceHit(entry=_make_entry(), relevance=0.0, matched_on="technique")
    ReferenceHit(entry=_make_entry(), relevance=1.0, matched_on="technique")
    ReferenceHit(entry=_make_entry(), relevance=0.5, matched_on="tag")


def test_hit_relevance_out_of_range() -> None:
    with pytest.raises(ValidationError):
        ReferenceHit(entry=_make_entry(), relevance=1.1, matched_on="technique")

    with pytest.raises(ValidationError):
        ReferenceHit(entry=_make_entry(), relevance=-0.1, matched_on="technique")


def test_hit_extra_forbid() -> None:
    with pytest.raises(ValidationError):
        ReferenceHit(  # type: ignore[call-arg]
            entry=_make_entry(), relevance=0.5, matched_on="tag", unknown="x"
        )


# ── IntelVerdict ─────────────────────────────────────────────────────────────

def test_intel_verdict_enum_bounded() -> None:
    for v in ("benign", "malicious", "suspicious", "unknown"):
        iv = IntelVerdict(
            indicator="1.2.3.4",
            verdict=v,  # type: ignore[arg-type]
            source="demo",
            observed_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        assert iv.verdict == v


def test_intel_verdict_invalid() -> None:
    with pytest.raises(ValidationError):
        IntelVerdict(
            indicator="x",
            verdict="evil",  # type: ignore[arg-type]
            source="demo",
            observed_at=datetime(2026, 1, 1, tzinfo=UTC),
        )


def test_intel_verdict_extra_forbid() -> None:
    with pytest.raises(ValidationError):
        IntelVerdict(  # type: ignore[call-arg]
            indicator="x",
            verdict="unknown",
            source="demo",
            observed_at=datetime(2026, 1, 1, tzinfo=UTC),
            extra_field="bad",
        )


# ── ReferenceQuery ───────────────────────────────────────────────────────────

def test_reference_query_defaults() -> None:
    q = ReferenceQuery()
    assert q.technique_ids == []
    assert q.terms == []


def test_reference_query_extra_forbid() -> None:
    with pytest.raises(ValidationError):
        ReferenceQuery(unknown="x")  # type: ignore[call-arg]
