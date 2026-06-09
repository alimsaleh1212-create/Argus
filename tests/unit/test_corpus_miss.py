"""Unit tests for corpus miss / empty-store behaviour — T032."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.domain.corpus import ReferenceQuery
from backend.repositories.corpus import CorpusRepository


@pytest.mark.asyncio
async def test_empty_query_returns_empty_list() -> None:
    session = MagicMock()
    repo = CorpusRepository(session)
    result = await repo.search_reference(ReferenceQuery(), k=5)
    assert result == []


@pytest.mark.asyncio
async def test_no_match_returns_empty_list() -> None:
    session = MagicMock()

    async def _execute(stmt, params=None):
        result = MagicMock()
        result.__iter__ = lambda s: iter([])
        return result

    session.execute = AsyncMock(side_effect=_execute)
    repo = CorpusRepository(session)
    result = await repo.search_reference(ReferenceQuery(technique_ids=["T9999"]), k=5)
    assert result == []


@pytest.mark.asyncio
async def test_cold_store_returns_empty() -> None:
    """An unseeded (empty) store returns [] — cold/miss is a normal outcome."""
    session = MagicMock()

    async def _execute(stmt, params=None):
        result = MagicMock()
        result.__iter__ = lambda s: iter([])
        return result

    session.execute = AsyncMock(side_effect=_execute)
    repo = CorpusRepository(session)
    result = await repo.search_reference(
        ReferenceQuery(technique_ids=["T1110"], terms=["brute", "credential"]),
        k=5,
    )
    assert result == []
    assert isinstance(result, list)
