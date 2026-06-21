"""Integration tests — T011/T021/T022: anomaly detector -> intake.accept (SPEC-ml-anomaly-detector #17).

Drives the anomaly runner into the real `services.intake.accept` path using a
`FakeAnomalyModel` and asserts:
  * a `source="anomaly-detector"` Incident is persisted and enqueued,
  * re-running the same replay set creates NO duplicate (FR-013),
  * normal-only windows produce zero incidents (US2/SC-003),
  * #14 detector + #17 anomaly detector coexist over the same source (US3/SC-005).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.domain.anomaly import AnomalyFinding, EntityActivityWindow
from backend.domain.incident import Incident, IncidentStatus, Severity
from backend.services.anomaly import finding_to_wazuh_alert

pytestmark = pytest.mark.integration

FIXTURE_DIR = Path("tests/fixtures/anomaly")


def _make_anomaly_finding(score: float = 0.85, entity_id: str = "user-m01") -> AnomalyFinding:
    window = EntityActivityWindow(
        entity_id=entity_id,
        window_start=datetime(2024, 1, 1, tzinfo=UTC),
        window_end=datetime(2024, 1, 2, tzinfo=UTC),
        features={"logon_count": 10.0, "after_hours_count": 5.0},
        raw_event_count=20,
    )
    return AnomalyFinding(
        entity_id=entity_id,
        score=score,
        severity=Severity.HIGH,
        window=window,
        top_features=["logon_count", "after_hours_count"],
    )


def _make_settings():
    from backend.infra.config import IngestSettings, RedisSettings

    settings = MagicMock()
    settings.ingest = IngestSettings()
    settings.redis = RedisSettings()
    return settings


class TestAnomalyEmitsSourceTagged:
    async def test_anomaly_alert_persists_with_source_tag(self) -> None:
        from backend.services.intake import accept

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
            severity=Severity.HIGH,
            correlation_id="corr-anom",
            dedup_fingerprint="fp-anom",
            source="anomaly-detector",
            raw_alert={"rule": {"id": "anomaly-ueba"}},
        )

        mock_repo = AsyncMock()
        mock_repo.create = AsyncMock(return_value=created)
        mock_repo.get_by_fingerprint = AsyncMock(return_value=None)

        alert = finding_to_wazuh_alert(_make_anomaly_finding())
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
                    source="anomaly-detector",
                )

        assert result.deduplicated is False
        mock_repo.create.assert_awaited_once()
        persisted = mock_repo.create.await_args.args[0]
        assert persisted.source == "anomaly-detector"
        mock_queue.enqueue.assert_awaited_once()

    async def test_dedup_on_replay_returns_existing(self) -> None:
        from backend.services.intake import accept

        mock_session = AsyncMock()
        mock_queue = AsyncMock()
        mock_queue.enqueue = AsyncMock(return_value="ok")
        mock_cache = AsyncMock()
        mock_redactor = MagicMock()
        mock_redactor.redact_mapping = MagicMock(return_value={"rule": {"id": "anomaly-ueba"}})

        incident_id = uuid.uuid4()
        first = Incident(
            id=incident_id,
            status=IncidentStatus.RECEIVED,
            severity=Severity.HIGH,
            correlation_id="corr-anom-dedup",
            dedup_fingerprint="fp-anom-dedup",
            source="anomaly-detector",
            raw_alert={"rule": {"id": "anomaly-ueba"}},
        )
        existing = first.model_copy()

        mock_repo = AsyncMock()
        mock_repo.create = AsyncMock(return_value=first)
        mock_repo.get_by_fingerprint = AsyncMock(return_value=existing)

        alert = finding_to_wazuh_alert(_make_anomaly_finding())
        settings = _make_settings()

        with patch("backend.services.intake.IncidentRepository", return_value=mock_repo):
            with patch("backend.services.intake.claim_fingerprint", return_value=True):
                await accept(
                    session=mock_session,
                    queue=mock_queue,
                    cache=mock_cache,
                    redactor=mock_redactor,
                    settings=settings,
                    alert=alert,
                    source="anomaly-detector",
                )

        with patch("backend.services.intake.IncidentRepository", return_value=mock_repo):
            with patch("backend.services.intake.claim_fingerprint", return_value=False):
                with patch(
                    "backend.services.intake.lookup_fingerprint",
                    return_value=str(incident_id),
                ):
                    second = await accept(
                        session=mock_session,
                        queue=mock_queue,
                        cache=mock_cache,
                        redactor=mock_redactor,
                        settings=settings,
                        alert=alert,
                        source="anomaly-detector",
                    )

        assert second.deduplicated is True
        assert second.incident_id == incident_id
        assert mock_repo.create.await_count == 1

    async def test_runner_emits_anomalous_window(self) -> None:
        """End-to-end runner → intake.accept with FakeAnomalyModel."""
        from backend.anomaly_detector import make_anomaly_runner
        from backend.domain.anomaly import parse_window
        from backend.infra.config import AnomalySettings, IngestSettings, RedisSettings
        from tests.helpers.anomaly import FakeAnomalyModel

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
            severity=Severity.HIGH,
            correlation_id="corr-run",
            dedup_fingerprint="fp-run",
            source="anomaly-detector",
            raw_alert={"rule": {"id": "anomaly-ueba"}},
        )

        mock_repo = AsyncMock()
        mock_repo.create = AsyncMock(return_value=created)
        mock_repo.get_by_fingerprint = AsyncMock(return_value=None)

        settings = MagicMock()
        settings.ingest = IngestSettings()
        settings.redis = RedisSettings()

        model = FakeAnomalyModel(
            feature_spec=[
                "logon_count",
                "device_count",
                "file_count",
                "email_count",
                "http_count",
                "distinct_pc",
                "after_hours_count",
                "removable_copy_count",
                "external_email_count",
                "flagged_http_count",
            ],
            scores={"attacker_m01": 0.95},
        )

        runner = make_anomaly_runner(
            settings=settings,
            session_factory=lambda: mock_session,
            queue=mock_queue,
            cache=mock_cache,
            redactor=mock_redactor,
            model=model,
        )

        cfg = AnomalySettings(
            enabled=True,
            replay_path=str(FIXTURE_DIR / "replay" / "scenarios.jsonl"),
            window=parse_window("1d"),
            fire_threshold=0.60,
            max_events=100_000,
            source_tag="anomaly-detector",
        )

        with patch("backend.services.intake.IncidentRepository", return_value=mock_repo):
            with patch("backend.services.intake.claim_fingerprint", return_value=True):
                code = await runner(cfg)

        assert code == 0
        assert mock_repo.create.await_count >= 1
        persisted = mock_repo.create.await_args.args[0]
        assert persisted.source == "anomaly-detector"

    async def test_runner_suppresses_normal_windows(self) -> None:
        """US2: normal-only fixture windows score below threshold → zero incidents."""
        from backend.anomaly_detector import make_anomaly_runner
        from backend.domain.anomaly import parse_window
        from backend.infra.config import AnomalySettings, IngestSettings, RedisSettings
        from tests.helpers.anomaly import FakeAnomalyModel

        mock_session = AsyncMock()
        mock_queue = AsyncMock()
        mock_queue.enqueue = AsyncMock(return_value="ok")
        mock_cache = AsyncMock()
        mock_redactor = MagicMock()
        mock_redactor.redact_mapping = MagicMock(return_value={"rule": {"id": "anomaly-ueba"}})

        mock_repo = AsyncMock()
        mock_repo.create = AsyncMock(return_value=None)
        mock_repo.get_by_fingerprint = AsyncMock(return_value=None)

        settings = MagicMock()
        settings.ingest = IngestSettings()
        settings.redis = RedisSettings()

        # All fixture entities score below threshold.
        model = FakeAnomalyModel(
            feature_spec=[
                "logon_count",
                "device_count",
                "file_count",
                "email_count",
                "http_count",
                "distinct_pc",
                "after_hours_count",
                "removable_copy_count",
                "external_email_count",
                "flagged_http_count",
            ],
            scores={
                "attacker_m01": 0.10,
                "attacker_m02": 0.10,
                "user_n01": 0.10,
                "user_n02": 0.10,
            },
        )

        runner = make_anomaly_runner(
            settings=settings,
            session_factory=lambda: mock_session,
            queue=mock_queue,
            cache=mock_cache,
            redactor=mock_redactor,
            model=model,
        )

        cfg = AnomalySettings(
            enabled=True,
            replay_path=str(FIXTURE_DIR / "replay" / "scenarios.jsonl"),
            window=parse_window("1d"),
            fire_threshold=0.60,
            max_events=100_000,
            source_tag="anomaly-detector",
        )

        with patch("backend.services.intake.IncidentRepository", return_value=mock_repo):
            with patch("backend.services.intake.claim_fingerprint", return_value=True):
                code = await runner(cfg)

        assert code == 0
        mock_repo.create.assert_not_awaited()

    async def test_detector_and_anomaly_coexist(self) -> None:
        """US3: #14 detector + #17 anomaly over the same source produce distinct sources."""
        from backend.infra.config import IngestSettings, RedisSettings
        from backend.services.detector import fired_alert_to_wazuh_alert
        from backend.services.intake import accept

        mock_session = AsyncMock()
        mock_queue = AsyncMock()
        mock_queue.enqueue = AsyncMock(return_value="ok")
        mock_cache = AsyncMock()
        mock_redactor = MagicMock()
        mock_redactor.redact_mapping = MagicMock(return_value={"rule": {"id": "anomaly-ueba"}})

        from backend.domain.detector import FiredAlert, RawEvent

        fired = FiredAlert(
            rule_id="rule-detector",
            description="detector rule",
            severity=Severity.HIGH,
            event=RawEvent(
                event_time=datetime(2024, 1, 1, tzinfo=UTC),
                fields={"data": {"command_line": "evil"}},
                source_host="host-a",
            ),
        )
        detector_alert = fired_alert_to_wazuh_alert(fired)
        anomaly_alert = finding_to_wazuh_alert(_make_anomaly_finding(entity_id="user-m01"))

        incident_ids = [uuid.uuid4(), uuid.uuid4()]
        created = [
            Incident(
                id=incident_ids[0],
                status=IncidentStatus.RECEIVED,
                severity=Severity.HIGH,
                correlation_id="c1",
                dedup_fingerprint="fp1",
                source="detector",
                raw_alert={"rule": {"id": "rule-detector"}},
            ),
            Incident(
                id=incident_ids[1],
                status=IncidentStatus.RECEIVED,
                severity=Severity.HIGH,
                correlation_id="c2",
                dedup_fingerprint="fp2",
                source="anomaly-detector",
                raw_alert={"rule": {"id": "anomaly-ueba"}},
            ),
        ]

        mock_repo = AsyncMock()
        mock_repo.create = AsyncMock(side_effect=created)
        mock_repo.get_by_fingerprint = AsyncMock(return_value=None)

        settings = MagicMock()
        settings.ingest = IngestSettings()
        settings.redis = RedisSettings()

        # Run #14 detector alert through intake
        with patch("backend.services.intake.IncidentRepository", return_value=mock_repo):
            with patch("backend.services.intake.claim_fingerprint", return_value=True):
                result1 = await accept(
                    session=mock_session,
                    queue=mock_queue,
                    cache=mock_cache,
                    redactor=mock_redactor,
                    settings=settings,
                    alert=detector_alert,
                    source="detector",
                )

        # Run #17 anomaly alert through intake
        with patch("backend.services.intake.IncidentRepository", return_value=mock_repo):
            with patch("backend.services.intake.claim_fingerprint", return_value=True):
                result2 = await accept(
                    session=mock_session,
                    queue=mock_queue,
                    cache=mock_cache,
                    redactor=mock_redactor,
                    settings=settings,
                    alert=anomaly_alert,
                    source="anomaly-detector",
                )

        assert result1.incident_id == incident_ids[0]
        assert result2.incident_id == incident_ids[1]
        assert result1.incident_id != result2.incident_id
        # The persisted incidents carried the distinct source tags.
        persisted_sources = {mock_repo.create.await_args_list[i].args[0].source for i in range(2)}
        assert persisted_sources == {"detector", "anomaly-detector"}
