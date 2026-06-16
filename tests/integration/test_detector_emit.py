"""Integration tests — T009: detector -> intake.accept -> incident (SPEC-detector #14).

Drives the pure detector (`evaluate` + `fired_alert_to_wazuh_alert`) into the
real `services.intake.accept` path and asserts:
  * a `source="detector"` Incident is persisted and enqueued,
  * re-running the same replay set creates NO duplicate (FR-008).

The session/queue/cache surfaces are mocked at the same seam unit tests
use (`test_intake.py`) — the integration under test is the *composition*:
detector output → WazuhAlert → redact → dedup → persist → enqueue.
The full e2e against a running stack is `tests/e2e/test_detector_e2e.py`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.integration


def _make_alert_from_fired(fired):
    from backend.services.detector import fired_alert_to_wazuh_alert

    return fired_alert_to_wazuh_alert(fired)


def _fired(rule_id: str = "malicious-cmd", severity_value: str = "high"):
    from backend.domain.detector import FiredAlert, RawEvent
    from backend.domain.incident import Severity

    sev = Severity(severity_value)
    return FiredAlert(
        rule_id=rule_id,
        description="d",
        severity=sev,
        technique="T1059",
        event=RawEvent(
            event_time=datetime(2026, 1, 1, tzinfo=UTC),
            fields={"data": {"command_line": "mimikatz"}},
            source_host="host-a",
        ),
    )


def _make_settings():
    from backend.infra.config import IngestSettings, RedisSettings

    settings = MagicMock()
    settings.ingest = IngestSettings()
    settings.redis = RedisSettings()
    return settings


@pytest.mark.asyncio
class TestDetectorEmitsSourceTagged:
    async def test_detector_alert_persists_with_source_detector(self) -> None:
        from backend.domain.incident import Incident, IncidentStatus, Severity
        from backend.services.intake import accept

        mock_session = AsyncMock()
        mock_queue = AsyncMock()
        mock_queue.enqueue = AsyncMock(return_value="ok")
        mock_cache = AsyncMock()

        mock_redactor = MagicMock()
        mock_redactor.redact_mapping = MagicMock(return_value={"rule": {"id": "r"}})

        incident_id = uuid.uuid4()
        created = Incident(
            id=incident_id,
            status=IncidentStatus.RECEIVED,
            severity=Severity.HIGH,
            correlation_id="corr-det",
            dedup_fingerprint="fp-det",
            source="detector",
            raw_alert={"rule": {"id": "r"}},
        )

        mock_repo = AsyncMock()
        mock_repo.create = AsyncMock(return_value=created)
        mock_repo.get_by_fingerprint = AsyncMock(return_value=None)

        alert = _make_alert_from_fired(_fired())
        settings = _make_settings()

        with patch("backend.services.intake.IncidentRepository", return_value=mock_repo):
            with patch("backend.services.intake.claim_fingerprint", return_value=True):
                result = await accept(
                    session=mock_session,
                    queue=mock_queue,
                    cache=mock_cache,
                    redactor=mock_redactor,
                    settings=settings,
                    alert=alert,
                    source="detector",
                )

        # The persisted incident must be tagged with the detector source.
        assert result.deduplicated is False
        mock_repo.create.assert_awaited_once()
        persisted = mock_repo.create.await_args.args[0]
        assert persisted.source == "detector"
        mock_queue.enqueue.assert_awaited_once()

    async def test_dedup_on_replay_returns_existing(self) -> None:
        """Re-running the same replay set creates no duplicate (FR-008).

        First call: claim succeeds, persists, enqueues.
        Second call: claim fails, returns existing incident with
        `deduplicated=True`.
        """
        from backend.domain.incident import Incident, IncidentStatus, Severity
        from backend.services.intake import accept

        mock_session = AsyncMock()
        mock_queue = AsyncMock()
        mock_queue.enqueue = AsyncMock(return_value="ok")
        mock_cache = AsyncMock()

        mock_redactor = MagicMock()
        mock_redactor.redact_mapping = MagicMock(return_value={"rule": {"id": "r"}})

        incident_id = uuid.uuid4()
        first = Incident(
            id=incident_id,
            status=IncidentStatus.RECEIVED,
            severity=Severity.HIGH,
            correlation_id="corr-dedup",
            dedup_fingerprint="fp-dup",
            source="detector",
            raw_alert={"rule": {"id": "r"}},
        )
        existing = Incident(
            id=incident_id,
            status=IncidentStatus.RECEIVED,
            severity=Severity.HIGH,
            correlation_id="corr-dedup",
            dedup_fingerprint="fp-dup",
            source="detector",
            raw_alert={"rule": {"id": "r"}},
        )

        mock_repo = AsyncMock()
        # First call: create new; second call: lookup-by-fp returns existing.
        mock_repo.create = AsyncMock(return_value=first)
        mock_repo.get_by_fingerprint = AsyncMock(return_value=existing)

        alert = _make_alert_from_fired(_fired(rule_id="malicious-cmd"))
        settings = _make_settings()

        # First run: claim succeeds, create, enqueue.
        with patch("backend.services.intake.IncidentRepository", return_value=mock_repo):
            with patch("backend.services.intake.claim_fingerprint", return_value=True):
                first_result = await accept(
                    session=mock_session,
                    queue=mock_queue,
                    cache=mock_cache,
                    redactor=mock_redactor,
                    settings=settings,
                    alert=alert,
                    source="detector",
                )
        assert first_result.deduplicated is False

        # Second run: claim fails → return existing.
        with patch("backend.services.intake.IncidentRepository", return_value=mock_repo):
            with patch("backend.services.intake.claim_fingerprint", return_value=False):
                with patch(
                    "backend.services.intake.lookup_fingerprint",
                    return_value=str(incident_id),
                ):
                    second_result = await accept(
                        session=mock_session,
                        queue=mock_queue,
                        cache=mock_cache,
                        redactor=mock_redactor,
                        settings=settings,
                        alert=alert,
                        source="detector",
                    )
        assert second_result.deduplicated is True
        assert second_result.incident_id == incident_id
        # create was awaited only once (on the first run).
        assert mock_repo.create.await_count == 1

    async def test_benign_events_produce_no_incident(self) -> None:
        """T018 — Replay benign-only events → zero alerts → zero incidents.

        Drives the *committed* benign events from the shared replay fixture
        through `evaluate` and asserts no FiredAlert is produced, so the
        runner would call `intake.accept` zero times.
        """
        from pathlib import Path

        from backend.services.detector import evaluate, load_replay_events, load_rules

        rule_path = Path("tests/fixtures/detector/rules.yaml")
        replay_path = Path("tests/fixtures/detector/replay/scenarios.json")
        rules = load_rules(rule_path)
        events = load_replay_events(replay_path)
        # Only the benign events should be in scope.
        benign_events = [
            ev
            for ev in events
            if (ev.fields.get("data") or {}).get("event_type") in {"login_success", "process_start"}
        ]
        assert benign_events, "test fixture must include benign events"
        alerts = evaluate(benign_events, rules)
        assert alerts == [], f"benign events must not fire alerts; got {alerts}"
