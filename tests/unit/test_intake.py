"""Unit tests — T020: intake.accept() with faked dependencies.

TDD: must FAIL before services/intake.py is implemented.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_alert():
    from backend.domain.incident import WazuhAlert, WazuhRule

    return WazuhAlert(rule=WazuhRule(level=10, id="5763", description="SSH brute force"))


def _make_settings():
    from backend.infra.config import IngestSettings, RedisSettings

    settings = MagicMock()
    settings.ingest = IngestSettings()
    settings.redis = RedisSettings()
    return settings


class TestIntakeAccept:
    async def test_happy_path_returns_received_result(self) -> None:
        from backend.domain.incident import Incident, IncidentStatus, IngestResult, Severity
        from backend.services.intake import accept

        mock_session = AsyncMock()
        mock_queue = AsyncMock()
        mock_queue.enqueue = AsyncMock(return_value="fake-id")
        mock_cache = AsyncMock()
        mock_cache.set = AsyncMock(return_value=True)

        mock_redactor = MagicMock()
        mock_redactor.redact_mapping = MagicMock(return_value={"rule": {"level": 10, "id": "5763"}})

        fake_incident = Incident(
            id=uuid.uuid4(),
            status=IncidentStatus.RECEIVED,
            severity=Severity.HIGH,
            correlation_id="corr-1",
            dedup_fingerprint="fp-1",
            source="wazuh",
            raw_alert={"rule": {"level": 10}},
        )

        mock_repo = AsyncMock()
        mock_repo.create = AsyncMock(return_value=fake_incident)
        mock_repo.get_by_fingerprint = AsyncMock(return_value=None)

        alert = _make_alert()
        settings = _make_settings()

        with patch("backend.services.intake.IncidentRepository", return_value=mock_repo):
            with patch("backend.services.intake.claim_fingerprint", return_value=True):
                result: IngestResult = await accept(
                    session=mock_session,
                    queue=mock_queue,
                    cache=mock_cache,
                    redactor=mock_redactor,
                    settings=settings,
                    alert=alert,
                )

        assert result.deduplicated is False
        assert result.status == IncidentStatus.RECEIVED

    async def test_enqueue_failure_raises_and_does_not_persist(self) -> None:
        from backend.domain.incident import Incident, IncidentStatus, Severity
        from backend.services.intake import accept

        mock_session = AsyncMock()
        mock_queue = AsyncMock()
        mock_queue.enqueue = AsyncMock(side_effect=RuntimeError("Redis down"))
        mock_cache = AsyncMock()

        mock_redactor = MagicMock()
        mock_redactor.redact_mapping = MagicMock(return_value={"rule": {"level": 10}})

        fake_incident = Incident(
            id=uuid.uuid4(),
            status=IncidentStatus.RECEIVED,
            severity=Severity.HIGH,
            correlation_id="corr-2",
            dedup_fingerprint="fp-2",
            source="wazuh",
            raw_alert={},
        )

        mock_repo = AsyncMock()
        mock_repo.create = AsyncMock(return_value=fake_incident)
        mock_repo.delete = AsyncMock()
        mock_repo.get_by_fingerprint = AsyncMock(return_value=None)

        alert = _make_alert()
        settings = _make_settings()

        with patch("backend.services.intake.IncidentRepository", return_value=mock_repo):
            with patch("backend.services.intake.claim_fingerprint", return_value=True):
                with pytest.raises(RuntimeError, match="Redis down"):
                    await accept(
                        session=mock_session,
                        queue=mock_queue,
                        cache=mock_cache,
                        redactor=mock_redactor,
                        settings=settings,
                        alert=alert,
                    )

    async def test_redaction_error_fails_closed(self) -> None:
        from backend.services.intake import accept

        mock_session = AsyncMock()
        mock_queue = AsyncMock()
        mock_cache = AsyncMock()

        mock_redactor = MagicMock()
        mock_redactor.redact_mapping = MagicMock(side_effect=ValueError("Redaction failed"))

        mock_repo = AsyncMock()
        mock_repo.get_by_fingerprint = AsyncMock(return_value=None)
        alert = _make_alert()
        settings = _make_settings()

        with patch("backend.services.intake.IncidentRepository", return_value=mock_repo):
            with pytest.raises(ValueError, match="Redaction failed"):
                await accept(
                    session=mock_session,
                    queue=mock_queue,
                    cache=mock_cache,
                    redactor=mock_redactor,
                    settings=settings,
                    alert=alert,
                )

        mock_repo.create.assert_not_called()
        mock_queue.enqueue.assert_not_called()
