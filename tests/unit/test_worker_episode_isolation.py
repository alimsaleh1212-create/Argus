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


@pytest.mark.asyncio
async def test_record_episode_swallows_memory_write_failure_but_attempts_it():
    """A failing memory.write_episode must be swallowed, but the write must
    actually be attempted — proves the off-path write failure never raises
    while still exercising the write itself (deterministic, no fire-and-forget
    timing dependence).
    """
    from unittest.mock import AsyncMock, MagicMock, patch

    from backend.domain.incident import (
        Evidence,
        Incident,
        IncidentStatus,
        NormalizedEvent,
        Severity,
    )
    from backend.worker import _record_episode_isolated

    incident_id = uuid.uuid4()
    ne = NormalizedEvent(rule_id="5763", rule_level=10, rule_description="test")
    evidence = Evidence(
        verdict="real",
        severity=Severity.HIGH,
        normalized_event=ne,
        summary="test",
    )
    incident = Incident(
        id=incident_id,
        status=IncidentStatus.RESOLVED,
        severity=Severity.HIGH,
        correlation_id=str(incident_id),
        dedup_fingerprint=f"fp-{incident_id}",
        source="wazuh",
        raw_alert={},
        normalized_event=ne.model_dump(mode="json"),
        evidence=evidence.model_dump(mode="json"),
        disposition="auto_resolved_triage",
    )

    opened: list = []

    def factory():
        return _FakeSession(opened)

    settings = MagicMock()
    settings.observability.presidio_enabled = False
    settings.feedback = None

    failing_memory = AsyncMock()
    failing_memory.write_episode.side_effect = RuntimeError("neo4j down")

    with patch("backend.worker.IncidentRepository") as MockRepo:
        MockRepo.return_value.get = AsyncMock(return_value=incident)
        # Must not raise — best-effort, even though the write itself fails.
        await _record_episode_isolated(
            incident_id, "iid", factory, memory=failing_memory, settings=settings
        )

    failing_memory.write_episode.assert_called_once()
