"""Unit tests for seed_reputation — T026."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.infra.memory import NullMemory
from backend.services.corpus import seed_reputation


def _make_redactor():
    r = MagicMock()
    r.redact_text = lambda text, boundary: text
    return r


@pytest.mark.asyncio
async def test_seed_reputation_maps_to_temporal_facts() -> None:
    redactor = _make_redactor()
    store = MagicMock()
    store.write_fact = AsyncMock()

    records = [
        {"indicator": "1.2.3.4", "kind": "address", "reputation": "malicious", "as_of": "2026-01-01T00:00:00Z"},
        {"indicator": "5.6.7.8", "kind": "address", "reputation": "benign", "as_of": "2026-02-01T00:00:00Z"},
    ]
    await seed_reputation(records, redactor, store)
    assert store.write_fact.call_count == 2

    facts = [call.args[0] for call in store.write_fact.call_args_list]
    assert all(f.fact_type == "reputation" for f in facts)
    values = {f.entity.value for f in facts}
    assert "1.2.3.4" in values
    assert "5.6.7.8" in values


@pytest.mark.asyncio
async def test_seed_reputation_redacts_indicator() -> None:
    redactor = MagicMock()
    redactor.redact_text = lambda text, boundary: "[REDACTED]"
    store = MagicMock()
    store.write_fact = AsyncMock()

    records = [
        {"indicator": "secret-host", "kind": "host", "reputation": "malicious", "as_of": "2026-01-01T00:00:00Z"}
    ]
    await seed_reputation(records, redactor, store)
    fact = store.write_fact.call_args[0][0]
    assert fact.entity.value == "[REDACTED]"


@pytest.mark.asyncio
async def test_seed_reputation_null_memory_no_raise() -> None:
    """NullMemory.write_fact no-ops; seeding must still complete successfully."""
    redactor = _make_redactor()
    store = NullMemory()

    records = [
        {"indicator": "1.2.3.4", "kind": "address", "reputation": "malicious", "as_of": "2026-01-01T00:00:00Z"}
    ]
    await seed_reputation(records, redactor, store)  # must not raise


@pytest.mark.asyncio
async def test_seed_reputation_malformed_record_skipped() -> None:
    redactor = _make_redactor()
    store = MagicMock()
    store.write_fact = AsyncMock()

    records = [
        {"indicator": "1.2.3.4", "kind": "address", "reputation": "malicious", "as_of": "2026-01-01T00:00:00Z"},
        {"BROKEN": True},  # missing required fields
    ]
    await seed_reputation(records, redactor, store)
    assert store.write_fact.call_count == 1
