"""Unit tests — T004: Incident domain contract.

TDD: these must FAIL before domain/incident.py is implemented.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError


class TestIncidentStatusEnum:
    def test_all_values_present(self) -> None:
        from backend.domain.incident import IncidentStatus

        assert IncidentStatus.RECEIVED == "received"
        assert IncidentStatus.GROUNDING == "grounding"
        assert IncidentStatus.GROUNDED == "grounded"
        assert IncidentStatus.FAILED == "failed"

    def test_is_str_enum(self) -> None:
        from backend.domain.incident import IncidentStatus

        assert isinstance(IncidentStatus.RECEIVED, str)


class TestSeverityEnum:
    def test_all_values_present(self) -> None:
        from backend.domain.incident import Severity

        assert Severity.LOW == "low"
        assert Severity.MEDIUM == "medium"
        assert Severity.HIGH == "high"
        assert Severity.CRITICAL == "critical"

    def test_is_str_enum(self) -> None:
        from backend.domain.incident import Severity

        assert isinstance(Severity.HIGH, str)


class TestWazuhAlert:
    def test_valid_minimal_alert(self) -> None:
        from backend.domain.incident import WazuhAlert, WazuhRule

        alert = WazuhAlert(rule=WazuhRule(level=10, id="5763", description="SSH brute force"))
        assert alert.rule.level == 10

    def test_extra_fields_ignored(self) -> None:
        """WazuhAlert must tolerate unknown Wazuh fields (extra='ignore')."""
        from backend.domain.incident import WazuhAlert

        alert = WazuhAlert.model_validate(
            {
                "rule": {"level": 5, "id": "1001", "description": "test"},
                "unknown_future_field": "ignored",
                "another_extra": {"nested": True},
            }
        )
        assert alert.rule.level == 5

    def test_agent_is_optional(self) -> None:
        from backend.domain.incident import WazuhAlert

        alert = WazuhAlert.model_validate({"rule": {"level": 3}})
        assert alert.agent is None

    def test_data_defaults_to_empty_dict(self) -> None:
        from backend.domain.incident import WazuhAlert

        alert = WazuhAlert.model_validate({"rule": {"level": 3}})
        assert alert.data == {}

    def test_rule_groups_defaults_to_empty_list(self) -> None:
        from backend.domain.incident import WazuhAlert

        alert = WazuhAlert.model_validate({"rule": {"level": 3}})
        assert alert.rule.groups == []


class TestNormalizedEvent:
    def test_valid_normalized_event(self) -> None:
        from backend.domain.incident import NormalizedEvent

        event = NormalizedEvent(
            rule_id="5763",
            rule_level=10,
            rule_description="SSH brute force",
            agent_name="web-server-01",
        )
        assert event.rule_id == "5763"
        assert event.fields == {}

    def test_optional_fields_default_none(self) -> None:
        from backend.domain.incident import NormalizedEvent

        event = NormalizedEvent()
        assert event.rule_id is None
        assert event.agent_id is None
        assert event.event_time is None


class TestEvidence:
    def test_valid_evidence(self) -> None:
        from backend.domain.incident import Evidence, NormalizedEvent, Severity

        ev = Evidence(
            verdict="rule_match",
            severity=Severity.HIGH,
            normalized_event=NormalizedEvent(rule_id="5763"),
            summary="SSH brute force on web-server-01",
        )
        assert ev.verdict == "rule_match"
        assert ev.retrieved_context == []
        assert ev.flags == []

    def test_retrieved_context_defaults_empty(self) -> None:
        from backend.domain.incident import Evidence, NormalizedEvent, Severity

        ev = Evidence(
            verdict="rule_match",
            severity=Severity.MEDIUM,
            normalized_event=NormalizedEvent(),
            summary="test",
        )
        assert ev.retrieved_context == []


class TestIncident:
    def test_valid_incident(self) -> None:
        from backend.domain.incident import Incident, IncidentStatus, Severity

        inc = Incident(
            id=uuid.uuid4(),
            status=IncidentStatus.RECEIVED,
            severity=Severity.HIGH,
            correlation_id="test-corr-id",
            dedup_fingerprint="abc123",
            source="wazuh",
            raw_alert={"rule": {"level": 10}},
        )
        assert inc.status == IncidentStatus.RECEIVED
        assert inc.attempts == 0

    def test_attempts_defaults_to_zero(self) -> None:
        from backend.domain.incident import Incident, IncidentStatus, Severity

        inc = Incident(
            id=uuid.uuid4(),
            status=IncidentStatus.RECEIVED,
            severity=Severity.MEDIUM,
            correlation_id="c1",
            dedup_fingerprint="fp1",
            source="wazuh",
            raw_alert={},
        )
        assert inc.attempts == 0

    def test_normalized_event_defaults_none(self) -> None:
        from backend.domain.incident import Incident, IncidentStatus, Severity

        inc = Incident(
            id=uuid.uuid4(),
            status=IncidentStatus.RECEIVED,
            severity=Severity.LOW,
            correlation_id="c1",
            dedup_fingerprint="fp1",
            source="wazuh",
            raw_alert={},
        )
        assert inc.normalized_event is None
        assert inc.evidence is None


class TestIngestResult:
    def test_new_incident_result(self) -> None:
        from backend.domain.incident import IngestResult, IncidentStatus

        result = IngestResult(
            incident_id=uuid.uuid4(),
            status=IncidentStatus.RECEIVED,
            deduplicated=False,
        )
        assert result.deduplicated is False

    def test_dedup_result(self) -> None:
        from backend.domain.incident import IngestResult, IncidentStatus

        result = IngestResult(
            incident_id=uuid.uuid4(),
            status=IncidentStatus.GROUNDED,
            deduplicated=True,
        )
        assert result.deduplicated is True
