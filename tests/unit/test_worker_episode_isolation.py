from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest


class _FakeSession:
    def __init__(self, opened: list):
        self._opened = opened

    async def __aenter__(self):
        self._opened.append(self)
        return self

    async def __aexit__(self, *exc):
        return False


@pytest.mark.asyncio
async def test_record_episode_opens_its_own_session_from_factory():
    from backend.worker import _record_episode_isolated

    opened: list = []

    def factory():
        return _FakeSession(opened)

    # repo.get returns None → coroutine returns early (no terminal incident),
    # but only AFTER it has opened a session from the factory.
    with patch("backend.worker.IncidentRepository") as MockRepo:
        MockRepo.return_value.get = AsyncMock(return_value=None)
        await _record_episode_isolated(
            uuid.uuid4(), "iid", factory, memory=object(), settings=object()
        )

    assert len(opened) == 1  # opened exactly one session from the factory
    MockRepo.assert_called_once_with(opened[0])  # repo built on the factory's session


@pytest.mark.asyncio
async def test_record_episode_swallows_errors():
    from backend.worker import _record_episode_isolated

    def factory():
        raise RuntimeError("boom")

    # Must not raise — best-effort.
    await _record_episode_isolated(
        uuid.uuid4(), "iid", factory, memory=object(), settings=object()
    )
