"""Unit tests for services/memory.py — T012 (record_episode + redaction-before-write)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.domain.incident import Incident, IncidentStatus, NormalizedEvent, Severity
from backend.domain.memory import EntityKind, EntityRef, IncidentEpisode
from backend.domain.redaction import Boundary
from backend.services.memory import _extract_entities, record_episode

_NOW = datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
_INCIDENT_ID = uuid.uuid4()


def _make_incident(**overrides: Any) -> Incident:
    base: dict[str, Any] = {
        "id": _INCIDENT_ID,
        "status": IncidentStatus.RESOLVED,
        "severity": Severity.HIGH,
        "correlation_id": "corr-1",
        "dedup_fingerprint": "fp-1",
        "source": "wazuh",
        "raw_alert": {},
        "normalized_event": {
            "agent_name": "web-01",
            "agent_ip": "10.0.0.1",
            "fields": {"user": "alice", "srcip": "192.168.1.2"},
        },
        "evidence": {
            "summary": "Suspicious login from 10.0.0.1",
            "verdict": "real",
            "severity": "high",
            "normalized_event": {},
        },
        "disposition": "escalated_enrichment",
        "updated_at": _NOW,
    }
    base.update(overrides)
    return Incident(**base)


def _make_redactor(planted_secret: str = "AKIAIOSFODNN7EXAMPLE") -> MagicMock:
    """Real-ish redactor mock: replaces planted_secret with [REDACTED]."""
    redactor = MagicMock()

    def redact_text(text: str, boundary: Boundary) -> str:
        return text.replace(planted_secret, "[REDACTED]")

    def redact_mapping(data: dict, boundary: Boundary) -> dict:
        return {
            k: v.replace(planted_secret, "[REDACTED]") if isinstance(v, str) else v
            for k, v in data.items()
        }

    redactor.redact_text.side_effect = redact_text
    redactor.redact_mapping.side_effect = redact_mapping
    return redactor


# ── _extract_entities ────────────────────────────────────────────────────────

def test_extract_entities_basic() -> None:
    ne = NormalizedEvent(
        agent_ip="10.0.0.1",
        agent_name="host-01",
        fields={"user": "alice"},
    )
    redactor = _make_redactor()
    refs = _extract_entities(ne, redactor)
    kinds = {r.kind for r in refs}
    values = {r.value for r in refs}
    assert EntityKind.ADDRESS in kinds
    assert EntityKind.HOST in kinds
    assert EntityKind.USER in kinds
    assert "10.0.0.1" in values
    assert "host-01" in values
    assert "alice" in values


def test_extract_entities_missing_fields_no_error() -> None:
    ne = NormalizedEvent()  # all optional, all absent
    redactor = _make_redactor()
    refs = _extract_entities(ne, redactor)
    assert refs == []


def test_extract_entities_dedup() -> None:
    ne = NormalizedEvent(
        agent_ip="1.2.3.4",
        fields={"srcip": "1.2.3.4"},  # same IP → dedup
    )
    redactor = _make_redactor()
    refs = _extract_entities(ne, redactor)
    addresses = [r for r in refs if r.kind == EntityKind.ADDRESS]
    assert len(addresses) == 1


# ── record_episode ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_record_episode_calls_store() -> None:
    store = AsyncMock()
    redactor = _make_redactor()
    incident = _make_incident()

    await record_episode(incident, store, redactor)

    store.write_episode.assert_called_once()
    episode: IncidentEpisode = store.write_episode.call_args[0][0]
    assert episode.incident_id == _INCIDENT_ID
    assert episode.disposition == "escalated_enrichment"
    assert episode.severity == Severity.HIGH


@pytest.mark.asyncio
async def test_record_episode_redacts_secret() -> None:
    secret = "AKIAIOSFODNN7EXAMPLE"
    store = AsyncMock()
    redactor = _make_redactor(planted_secret=secret)
    incident = _make_incident(
        evidence={
            "summary": f"Alert: key={secret}",
            "verdict": "real",
            "severity": "high",
            "normalized_event": {},
        }
    )

    await record_episode(incident, store, redactor)

    episode: IncidentEpisode = store.write_episode.call_args[0][0]
    assert secret not in episode.summary
    assert "[REDACTED]" in episode.summary


@pytest.mark.asyncio
async def test_record_episode_redacts_entity_values() -> None:
    secret = "AKIAIOSFODNN7EXAMPLE"
    store = AsyncMock()
    redactor = _make_redactor(planted_secret=secret)
    incident = _make_incident(
        normalized_event={
            "agent_ip": secret,  # planted in entity value
            "agent_name": "host-01",
            "fields": {},
        }
    )

    await record_episode(incident, store, redactor)

    episode: IncidentEpisode = store.write_episode.call_args[0][0]
    for entity in episode.entities:
        assert secret not in entity.value


@pytest.mark.asyncio
async def test_record_episode_missing_fields_no_error() -> None:
    store = AsyncMock()
    redactor = _make_redactor()
    incident = _make_incident(normalized_event=None, evidence=None)

    await record_episode(incident, store, redactor)

    store.write_episode.assert_called_once()
    episode: IncidentEpisode = store.write_episode.call_args[0][0]
    assert episode.entities == []


@pytest.mark.asyncio
async def test_record_episode_entities_extracted() -> None:
    store = AsyncMock()
    redactor = _make_redactor()
    incident = _make_incident()

    await record_episode(incident, store, redactor)

    episode: IncidentEpisode = store.write_episode.call_args[0][0]
    kinds = {e.kind for e in episode.entities}
    assert EntityKind.ADDRESS in kinds
    assert EntityKind.HOST in kinds
