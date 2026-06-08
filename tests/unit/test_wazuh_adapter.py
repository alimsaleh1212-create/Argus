"""Unit tests — T019: Wazuh adapter.

TDD: must FAIL before services/wazuh.py is implemented.
"""

from __future__ import annotations

import pytest


class TestWazuhToNormalized:
    def test_happy_path_mapping(self) -> None:
        from backend.domain.incident import WazuhAlert, WazuhAgent, WazuhRule
        from backend.services.wazuh import normalize

        alert = WazuhAlert(
            id="alert-001",
            timestamp="2026-06-08T10:00:00.000Z",
            rule=WazuhRule(level=10, id="5763", description="SSH brute force", groups=["sshd"]),
            agent=WazuhAgent(id="001", name="web-server-01", ip="10.0.0.42"),
            data={"srcip": "192.168.1.100"},
        )
        event = normalize(alert)
        assert event.rule_id == "5763"
        assert event.rule_level == 10
        assert event.rule_description == "SSH brute force"
        assert event.rule_groups == ["sshd"]
        assert event.agent_id == "001"
        assert event.agent_name == "web-server-01"
        assert event.agent_ip == "10.0.0.42"

    def test_missing_agent_produces_none_fields(self) -> None:
        from backend.domain.incident import WazuhAlert, WazuhRule
        from backend.services.wazuh import normalize

        alert = WazuhAlert(rule=WazuhRule(level=5))
        event = normalize(alert)
        assert event.agent_id is None
        assert event.agent_name is None

    def test_timestamp_parsed_to_utc(self) -> None:
        from backend.domain.incident import WazuhAlert, WazuhRule
        from backend.services.wazuh import normalize

        alert = WazuhAlert(
            timestamp="2026-06-08T10:00:00.000Z",
            rule=WazuhRule(level=5),
        )
        event = normalize(alert)
        assert event.event_time is not None
        assert event.event_time.tzinfo is not None


class TestSeverityBand:
    @pytest.mark.parametrize(
        "level,expected",
        [
            (0, "low"),
            (3, "low"),
            (4, "medium"),
            (7, "medium"),
            (8, "high"),
            (11, "high"),
            (12, "critical"),
            (15, "critical"),
        ],
    )
    def test_level_to_severity_band(self, level: int, expected: str) -> None:
        from backend.services.wazuh import level_to_severity

        assert level_to_severity(level).value == expected

    def test_missing_level_returns_medium_with_flag(self) -> None:
        from backend.domain.incident import WazuhAlert, WazuhRule
        from backend.services.wazuh import normalize, level_to_severity
        from backend.domain.incident import Severity

        result = level_to_severity(None)
        assert result == Severity.MEDIUM

    def test_missing_level_flag_on_normalize(self) -> None:
        from backend.domain.incident import WazuhAlert, WazuhRule
        from backend.services.wazuh import normalize_with_flags

        alert = WazuhAlert(rule=WazuhRule(level=None))
        _event, flags = normalize_with_flags(alert)
        assert "severity_defaulted" in flags


class TestContentSignature:
    def test_signature_excludes_timestamp(self) -> None:
        from backend.domain.incident import WazuhAlert, WazuhRule
        from backend.services.wazuh import content_signature

        alert1 = WazuhAlert(
            timestamp="2026-06-08T10:00:00.000Z",
            rule=WazuhRule(level=10, id="5763", description="SSH"),
        )
        alert2 = WazuhAlert(
            timestamp="2026-06-09T11:00:00.000Z",
            rule=WazuhRule(level=10, id="5763", description="SSH"),
        )
        assert content_signature(alert1) == content_signature(alert2)

    def test_different_rule_different_signature(self) -> None:
        from backend.domain.incident import WazuhAlert, WazuhRule
        from backend.services.wazuh import content_signature

        a1 = WazuhAlert(rule=WazuhRule(level=10, id="5763"))
        a2 = WazuhAlert(rule=WazuhRule(level=10, id="9999"))
        assert content_signature(a1) != content_signature(a2)
