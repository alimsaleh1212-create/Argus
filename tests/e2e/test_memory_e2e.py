"""E2E tests for memory integration — T018 / T030.

T018: Worker reaches terminal disposition → episode written → retrievable.
T030: With NullMemory (Neo4j down), worker still reaches terminal disposition (no crash).

Uses mocked providers to avoid heavy ML models / Neo4j in memory-constrained CI.
"""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.domain.incident import (
    Evidence,
    Incident,
    IncidentStatus,
    NormalizedEvent,
    Severity,
)
from backend.domain.memory import EpisodeQuery, IncidentEpisode
from backend.infra.memory import NullMemory


def _make_grounded_incident(
    incident_id: uuid.UUID | None = None,
    status: IncidentStatus = IncidentStatus.RESOLVED,
    disposition: str = "auto_resolved_triage",
) -> Incident:
    iid = incident_id or uuid.uuid4()
    ne = NormalizedEvent(
        rule_id="5763",
        rule_level=10,
        rule_description="SSH brute force",
        agent_name="web-server-01",
        agent_ip="10.0.0.1",
    )
    return Incident(
        id=iid,
        status=status,
        severity=Severity.HIGH,
        correlation_id=str(iid),
        dedup_fingerprint=f"fp-{iid}",
        source="wazuh",
        raw_alert={},
        normalized_event=ne.model_dump(mode="json"),
        evidence={
            "verdict": "real",
            "severity": "high",
            "normalized_event": ne.model_dump(mode="json"),
            "summary": "SSH brute force detected",
        },
        disposition=disposition,
    )


@pytest.mark.e2e
class TestMemoryE2E:
    # T018 — episode written and retrievable
    @pytest.mark.asyncio
    async def test_disposition_writes_episode(self) -> None:
        """After terminal disposition, record_episode writes an episode with the incident data."""
        from backend.infra.redaction import build_redactor
        from backend.services.memory import record_episode

        incident = _make_grounded_incident()
        store = AsyncMock()
        redactor = build_redactor(presidio_enabled=False)

        await record_episode(incident, store, redactor)

        store.write_episode.assert_called_once()
        episode: IncidentEpisode = store.write_episode.call_args[0][0]
        assert episode.incident_id == incident.id
        assert episode.disposition == "auto_resolved_triage"

    @pytest.mark.asyncio
    async def test_episode_retrievable_via_null_memory(self) -> None:
        """NullMemory.search_similar always returns [] without error (cold-start)."""
        store = NullMemory()
        results = await store.search_similar(EpisodeQuery(text="ssh brute force"), k=5)
        assert results == []

    # T030 — Neo4j down: worker still reaches terminal disposition, no crash
    @pytest.mark.asyncio
    async def test_worker_run_with_null_memory_no_crash(self) -> None:
        """With NullMemory, _run processes one incident without error."""
        from backend.worker import _run

        incident = _make_grounded_incident(
            status=IncidentStatus.RESOLVED,
            disposition="auto_resolved_triage",
        )
        incident_id = incident.id

        settings = MagicMock()
        settings.ingest.max_attempts = 3
        settings.observability.presidio_enabled = False

        call_count = 0

        async def dequeue_once():
            nonlocal call_count
            if call_count == 0:
                call_count += 1
                return str(incident_id)
            raise asyncio.CancelledError()

        queue = AsyncMock()
        queue.dequeue.side_effect = dequeue_once
        queue.recover = AsyncMock()
        queue.ack = AsyncMock()

        repo = AsyncMock()
        repo.claim_for_grounding = AsyncMock(return_value=True)
        repo.get = AsyncMock(return_value=incident)
        repo.set_grounded = AsyncMock()
        repo.bump_attempt = AsyncMock(return_value=1)

        ne = NormalizedEvent(rule_id="5763", rule_level=10, rule_description="test")
        evidence = Evidence(
            verdict="real",
            severity=Severity.HIGH,
            normalized_event=ne,
            summary="test",
        )

        with (
            patch("backend.services.grounding.ground", return_value=evidence),
            patch("backend.services.pipeline.dispatch_to_pipeline"),
        ):
            with pytest.raises(asyncio.CancelledError):
                await _run(settings, queue, repo, tracer=None, memory=NullMemory())

        queue.ack.assert_called_once_with(str(incident_id))

    @pytest.mark.asyncio
    async def test_worker_run_memory_error_does_not_block_disposition(self) -> None:
        """Even if the memory store raises on write, the disposition ack completes."""
        from backend.worker import _run

        incident = _make_grounded_incident(
            status=IncidentStatus.RESOLVED,
            disposition="auto_resolved_triage",
        )
        incident_id = incident.id

        settings = MagicMock()
        settings.ingest.max_attempts = 3
        settings.observability.presidio_enabled = False

        call_count = 0

        async def dequeue_once():
            nonlocal call_count
            if call_count == 0:
                call_count += 1
                return str(incident_id)
            raise asyncio.CancelledError()

        queue = AsyncMock()
        queue.dequeue.side_effect = dequeue_once
        queue.recover = AsyncMock()
        queue.ack = AsyncMock()

        repo = AsyncMock()
        repo.claim_for_grounding = AsyncMock(return_value=True)
        repo.get = AsyncMock(return_value=incident)
        repo.set_grounded = AsyncMock()
        repo.bump_attempt = AsyncMock(return_value=1)

        failing_memory = AsyncMock()
        failing_memory.write_episode.side_effect = RuntimeError("neo4j down")

        ne = NormalizedEvent(rule_id="5763", rule_level=10, rule_description="test")
        evidence = Evidence(
            verdict="real",
            severity=Severity.HIGH,
            normalized_event=ne,
            summary="test",
        )

        with (
            patch("backend.services.grounding.ground", return_value=evidence),
            patch("backend.services.pipeline.dispatch_to_pipeline"),
        ):
            with pytest.raises(asyncio.CancelledError):
                await _run(settings, queue, repo, tracer=None, memory=failing_memory)

        queue.ack.assert_called_once_with(str(incident_id))
