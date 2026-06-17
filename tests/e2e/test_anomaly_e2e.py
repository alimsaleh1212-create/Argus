"""e2e test — T019: anomaly replayed window reaches a terminal disposition (SC-001).

In-process e2e (no Docker): drives an anomalous window through the anomaly
runner's intake.accept path and then the deterministic grounding path,
asserting a `source="anomaly-detector"` Incident flows to a terminal
disposition through grounding + pipeline dispatch.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.domain.anomaly import AnomalyFinding, EntityActivityWindow
from backend.domain.incident import Incident, IncidentStatus, NormalizedEvent, Severity
from backend.services.anomaly import finding_to_wazuh_alert

FIXTURE_DIR = Path("tests/fixtures/anomaly")


@pytest.mark.e2e
class TestAnomalyE2E:
    async def test_replayed_anomalous_window_runs_to_grounded(self) -> None:
        window = EntityActivityWindow(
            entity_id="attacker_m01",
            window_start=datetime(2024, 1, 1, tzinfo=UTC),
            window_end=datetime(2024, 1, 2, tzinfo=UTC),
            features={
                "logon_count": 5.0,
                "device_count": 8.0,
                "file_count": 15.0,
                "email_count": 15.0,
                "http_count": 18.0,
                "distinct_pc": 1.0,
                "after_hours_count": 5.0,
                "removable_copy_count": 10.0,
                "external_email_count": 10.0,
                "flagged_http_count": 10.0,
            },
            raw_event_count=100,
        )
        finding = AnomalyFinding(
            entity_id="attacker_m01",
            score=0.95,
            severity=Severity.CRITICAL,
            window=window,
            top_features=["flagged_http_count", "external_email_count", "removable_copy_count"],
        )
        alert = finding_to_wazuh_alert(finding)

        mock_session = AsyncMock()
        mock_queue = AsyncMock()
        mock_queue.enqueue = AsyncMock(return_value="ok")
        mock_cache = AsyncMock()
        mock_redactor = MagicMock()
        mock_redactor.redact_mapping = MagicMock(return_value={"rule": {"id": "anomaly-ueba"}})

        incident_id = uuid.uuid4()
        created = Incident(
            id=incident_id,
            status=IncidentStatus.RECEIVED,
            severity=Severity.CRITICAL,
            correlation_id=str(incident_id),
            dedup_fingerprint="fp-anom-e2e",
            source="anomaly-detector",
            raw_alert={"rule": {"id": "anomaly-ueba"}},
        )
        mock_repo = AsyncMock()
        mock_repo.create = AsyncMock(return_value=created)
        mock_repo.get_by_fingerprint = AsyncMock(return_value=None)

        from backend.infra.config import IngestSettings, RedisSettings
        from backend.services.intake import accept

        settings = MagicMock()
        settings.ingest = IngestSettings()
        settings.redis = RedisSettings()

        with patch("backend.services.intake.IncidentRepository", return_value=mock_repo):
            with patch("backend.services.intake.claim_fingerprint", return_value=True):
                result = await accept(
                    session=mock_session,
                    queue=mock_queue,
                    cache=mock_cache,
                    redactor=mock_redactor,
                    settings=settings,
                    alert=alert,
                    source="anomaly-detector",
                )

        assert result.deduplicated is False
        persisted = mock_repo.create.await_args.args[0]
        assert persisted.source == "anomaly-detector"

        from backend.services.grounding import ground
        from backend.services.pipeline import dispatch_to_pipeline

        ne = NormalizedEvent(
            rule_id=alert.rule.id,
            rule_level=alert.rule.level,
            rule_description=alert.rule.description or "",
            agent_name=alert.agent.name if alert.agent else None,
        )
        grounded = persisted.model_copy(
            update={
                "status": IncidentStatus.RECEIVED,
                "normalized_event": ne.model_dump(mode="json"),
            }
        )
        evidence = ground(grounded)
        assert evidence.severity == Severity.CRITICAL
        assert evidence.verdict == "rule_match"

        await dispatch_to_pipeline(grounded, repo=None, supervisor=None)
