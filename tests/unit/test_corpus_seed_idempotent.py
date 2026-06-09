"""Unit tests for seed_reference idempotency and malformed-record handling — T017."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.domain.corpus import ReferenceCorpusEntry, ReferenceKind
from backend.services.corpus import seed_reference, seed_reputation


def _make_redactor():
    """Lightweight pass-through mock — avoids loading spacy/presidio in unit tests."""
    r = MagicMock()
    r.redact_text = lambda text, boundary: text
    r.redact_mapping = lambda data, boundary: data
    return r


# ── seed_reference idempotency ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_seed_reference_calls_upsert_once() -> None:
    redactor = _make_redactor()
    repo = MagicMock()
    repo.upsert_entries = AsyncMock()

    records = {
        "techniques": [
            {"id": "T1110", "title": "Brute Force", "tactic": "credential-access", "mitigations": "Use MFA."}
        ],
        "runbooks": [
            {"key": "rb-bf", "title": "BF Runbook", "techniques": ["T1110"], "steps": "1) do thing."}
        ],
    }

    await seed_reference(records, redactor, repo)
    repo.upsert_entries.assert_called_once()
    entries: list[ReferenceCorpusEntry] = repo.upsert_entries.call_args[0][0]
    assert len(entries) == 2


@pytest.mark.asyncio
async def test_seed_reference_idempotent_same_set() -> None:
    """Calling seed_reference twice with the same data upserts the same entries."""
    redactor = _make_redactor()
    repo = MagicMock()
    repo.upsert_entries = AsyncMock()

    records = {
        "techniques": [
            {"id": "T1110", "title": "Brute Force", "tactic": "cred", "mitigations": "Use MFA."}
        ],
        "runbooks": [],
    }

    await seed_reference(records, redactor, repo)
    first_call_entries = list(repo.upsert_entries.call_args[0][0])

    await seed_reference(records, redactor, repo)
    second_call_entries = list(repo.upsert_entries.call_args[0][0])

    # Same keys — upsert target is the same
    assert [e.key for e in first_call_entries] == [e.key for e in second_call_entries]


@pytest.mark.asyncio
async def test_seed_reference_malformed_record_skipped_not_fatal() -> None:
    redactor = _make_redactor()
    repo = MagicMock()
    repo.upsert_entries = AsyncMock()

    records = {
        "techniques": [
            {"id": "T1110", "title": "Good", "tactic": "cred", "mitigations": "ok"},
            {"MISSING_ID": True},  # malformed — no "id" key
        ],
        "runbooks": [],
    }

    # Must NOT raise
    await seed_reference(records, redactor, repo)
    repo.upsert_entries.assert_called_once()
    entries = repo.upsert_entries.call_args[0][0]
    assert len(entries) == 1  # only the valid entry
    assert entries[0].key == "T1110"


@pytest.mark.asyncio
async def test_seed_reference_all_malformed_no_upsert() -> None:
    redactor = _make_redactor()
    repo = MagicMock()
    repo.upsert_entries = AsyncMock()

    records = {
        "techniques": [{"BROKEN": True}],
        "runbooks": [{"ALSO_BROKEN": True}],
    }

    await seed_reference(records, redactor, repo)
    repo.upsert_entries.assert_not_called()


# ── seed_reputation ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_seed_reputation_calls_write_fact() -> None:
    from backend.infra.memory import NullMemory

    redactor = _make_redactor()
    store = MagicMock()
    store.write_fact = AsyncMock()

    records = [
        {"indicator": "1.2.3.4", "kind": "address", "reputation": "malicious", "as_of": "2026-01-01T00:00:00Z"}
    ]
    await seed_reputation(records, redactor, store)
    store.write_fact.assert_called_once()


@pytest.mark.asyncio
async def test_seed_reputation_null_memory_no_raise() -> None:
    from backend.infra.memory import NullMemory

    redactor = _make_redactor()
    store = NullMemory()

    records = [
        {"indicator": "1.2.3.4", "kind": "address", "reputation": "malicious", "as_of": "2026-01-01T00:00:00Z"}
    ]
    # NullMemory.write_fact is a no-op — must not raise
    await seed_reputation(records, redactor, store)
