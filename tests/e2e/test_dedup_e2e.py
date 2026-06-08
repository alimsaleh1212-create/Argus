"""e2e test — T037: duplicate alerts collapse to one Incident.

Uses mocked repo+queue to avoid needing a live stack.
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


@pytest.mark.e2e
class TestDedupE2E:
    async def test_same_alert_twice_returns_deduplicated(self) -> None:
        """Second POST of the same alert returns deduplicated=True with existing id."""
        from backend.domain.incident import Incident, IncidentStatus, IngestResult, Severity
        from backend.infra.cache import claim_fingerprint, lookup_fingerprint
        from backend.services.intake import accept

        existing_id = uuid.uuid4()
        existing_incident = Incident(
            id=existing_id,
            status=IncidentStatus.RECEIVED,
            severity=Severity.HIGH,
            correlation_id=str(existing_id),
            dedup_fingerprint="known-fp",
            source="wazuh",
            raw_alert={"rule": {"level": 10}},
        )

        mock_session = AsyncMock()
        mock_queue = AsyncMock()
        mock_queue.enqueue = AsyncMock(return_value=str(existing_id))
        mock_cache = AsyncMock()

        mock_redactor = MagicMock()
        mock_redactor.redact_mapping = MagicMock(return_value={"rule": {"level": 10}})

        mock_repo = AsyncMock()
        mock_repo.create = AsyncMock(return_value=existing_incident)
        mock_repo.get_by_fingerprint = AsyncMock(return_value=existing_incident)

        alert = _make_alert()
        settings = _make_settings()

        # First call: claim_fingerprint returns True (first sighting)
        with patch("backend.services.intake.IncidentRepository", return_value=mock_repo):
            with patch("backend.services.intake.claim_fingerprint", return_value=True) as mock_claim:
                result1 = await accept(
                    session=mock_session,
                    queue=mock_queue,
                    cache=mock_cache,
                    redactor=mock_redactor,
                    settings=settings,
                    alert=alert,
                )

        assert result1.deduplicated is False

        # Second call: claim_fingerprint returns False (duplicate within TTL)
        with patch("backend.services.intake.IncidentRepository", return_value=mock_repo):
            with patch("backend.services.intake.claim_fingerprint", return_value=False):
                with patch("backend.services.intake.lookup_fingerprint", return_value=str(existing_id)):
                    result2 = await accept(
                        session=mock_session,
                        queue=mock_queue,
                        cache=mock_cache,
                        redactor=mock_redactor,
                        settings=settings,
                        alert=alert,
                    )

        assert result2.deduplicated is True
        assert result2.incident_id == existing_id

    async def test_new_alert_after_window_creates_new_incident(self) -> None:
        """After the dedup window, the same alert creates a fresh Incident."""
        from backend.domain.incident import Incident, IncidentStatus, Severity
        from backend.services.intake import accept

        new_id = uuid.uuid4()
        new_incident = Incident(
            id=new_id,
            status=IncidentStatus.RECEIVED,
            severity=Severity.HIGH,
            correlation_id=str(new_id),
            dedup_fingerprint="known-fp-2",
            source="wazuh",
            raw_alert={"rule": {"level": 10}},
        )

        mock_session = AsyncMock()
        mock_queue = AsyncMock()
        mock_queue.enqueue = AsyncMock(return_value=str(new_id))
        mock_cache = AsyncMock()

        mock_redactor = MagicMock()
        mock_redactor.redact_mapping = MagicMock(return_value={"rule": {"level": 10}})

        mock_repo = AsyncMock()
        mock_repo.create = AsyncMock(return_value=new_incident)
        mock_repo.get_by_fingerprint = AsyncMock(return_value=None)

        alert = _make_alert()
        settings = _make_settings()

        # After window: claim_fingerprint returns True again (key expired)
        with patch("backend.services.intake.IncidentRepository", return_value=mock_repo):
            with patch("backend.services.intake.claim_fingerprint", return_value=True):
                result = await accept(
                    session=mock_session,
                    queue=mock_queue,
                    cache=mock_cache,
                    redactor=mock_redactor,
                    settings=settings,
                    alert=alert,
                )

        assert result.deduplicated is False
        assert result.incident_id == new_id
