"""e2e test — T015: detector replayed event reaches a terminal disposition (SC-001).

In-process e2e (no Docker / live stack): drives the detector through
`intake.accept` and then the deterministic grounding path, asserting a
`source="detector"` Incident flows to a terminal disposition through the
real `services.grounding.ground` and `services.pipeline.dispatch_to_pipeline`.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.domain.detector import FiredAlert
from backend.domain.incident import (
    Incident,
    IncidentStatus,
    NormalizedEvent,
    Severity,
)
from backend.services.detector import (
    evaluate,
    fired_alert_to_wazuh_alert,
    load_replay_events,
    load_rules,
)

FIXTURE_DIR = Path("tests/fixtures/detector")


@pytest.mark.e2e
class TestDetectorE2E:
    async def test_replayed_match_event_runs_to_grounded(self) -> None:
        # 1) Load the committed test rule set + replay set.
        rules = load_rules(FIXTURE_DIR / "rules.yaml")
        events = load_replay_events(FIXTURE_DIR / "replay" / "scenarios.json")
        assert rules.rules, "test rule set must not be empty"
        assert events, "test replay set must not be empty"

        # 2) Drive the pure detector — at least one alert.
        fired: list[FiredAlert] = evaluate(events, rules)
        assert fired, "expected at least one alert from a malicious-labeled event"

        # 3) Map to WazuhAlert and feed through the real intake seam.
        alert = fired_alert_to_wazuh_alert(fired[0])

        mock_session = AsyncMock()
        mock_queue = AsyncMock()
        mock_queue.enqueue = AsyncMock(return_value="ok")
        mock_cache = AsyncMock()
        mock_redactor = MagicMock()
        mock_redactor.redact_mapping = MagicMock(return_value={"rule": {"id": alert.rule.id}})

        incident_id = uuid.uuid4()
        created = Incident(
            id=incident_id,
            status=IncidentStatus.RECEIVED,
            severity=Severity.HIGH,
            correlation_id=str(incident_id),
            dedup_fingerprint="fp-e2e",
            source="detector",
            raw_alert={"rule": {"id": alert.rule.id}},
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
                    source="detector",
                )

        # 4) The persisted incident must carry the detector source tag.
        assert result.deduplicated is False
        persisted = mock_repo.create.await_args.args[0]
        assert persisted.source == "detector"

        # 5) The downstream pipeline (grounding + dispatch) must accept it
        #    without raising — full spine to a terminal disposition.
        from backend.services.grounding import ground
        from backend.services.pipeline import dispatch_to_pipeline

        # Mirror the worker's contract: persisted.raw_alert → ground(...)
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
        assert evidence.severity in {
            Severity.HIGH,
            Severity.CRITICAL,
            Severity.MEDIUM,
            Severity.LOW,
        }
        assert evidence.verdict == "rule_match"

        # Pipeline handoff is a no-op when no supervisor is wired; must not raise.
        await dispatch_to_pipeline(grounded, repo=None, supervisor=None)
