"""Unit tests for the FiredAlert -> WazuhAlert mapping (SPEC-detector #14, T008).

The mapping is the load-bearing bridge: the downstream `intake.accept()`
re-derives the same `Severity` from the chosen `rule.level` band, so the
inverse mapping must stay in lockstep with `services.wazuh.level_to_severity`.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from backend.domain.detector import FiredAlert, RawEvent
from backend.domain.incident import Severity
from backend.services.detector import fired_alert_to_wazuh_alert
from backend.services.wazuh import level_to_severity


def _ev(
    fields: dict,
    *,
    event_time: datetime | None = None,
    source_host: str | None = None,
) -> RawEvent:
    return RawEvent(
        event_time=event_time or datetime(2026, 1, 1, tzinfo=UTC),
        fields=fields,
        source_host=source_host,
    )


class TestFiredAlertToWazuhAlert:
    def test_rule_id_level_description_groups_preserved(self) -> None:
        fired = FiredAlert(
            rule_id="malicious-cmd",
            description="cmd match",
            severity=Severity.HIGH,
            technique="T1059",
            event=_ev({"data": {"command_line": "mimikatz"}}),
        )
        alert = fired_alert_to_wazuh_alert(fired)
        assert alert.rule.id == "malicious-cmd"
        assert alert.rule.description == "cmd match"
        assert alert.rule.groups == ["T1059"]
        # HIGH band → 9 (mid of 8-11)
        assert alert.rule.level == 9

    def test_data_fields_propagate(self) -> None:
        fields = {"data": {"command_line": "mimikatz"}, "src": "host-a"}
        fired = FiredAlert(
            rule_id="r",
            description="d",
            severity=Severity.HIGH,
            event=_ev(fields),
        )
        alert = fired_alert_to_wazuh_alert(fired)
        assert alert.data == fields

    def test_source_host_maps_to_agent_name(self) -> None:
        fired = FiredAlert(
            rule_id="r",
            description="d",
            severity=Severity.MEDIUM,
            event=_ev({"x": 1}, source_host="host-b"),
        )
        alert = fired_alert_to_wazuh_alert(fired)
        assert alert.agent is not None
        assert alert.agent.name == "host-b"

    def test_missing_source_host_yields_no_agent(self) -> None:
        fired = FiredAlert(
            rule_id="r",
            description="d",
            severity=Severity.MEDIUM,
            event=_ev({"x": 1}),
        )
        alert = fired_alert_to_wazuh_alert(fired)
        assert alert.agent is None

    def test_full_log_deterministic_summary(self) -> None:
        fired = FiredAlert(
            rule_id="r",
            description="d",
            severity=Severity.MEDIUM,
            event=_ev({"x": 1}),
        )
        alert = fired_alert_to_wazuh_alert(fired)
        assert alert.full_log is not None
        assert "detector:" in alert.full_log
        assert "rule=r" in alert.full_log
        assert "sev=medium" in alert.full_log

    def test_threshold_fired_alert_carries_group_key_in_log(self) -> None:
        fired = FiredAlert(
            rule_id="brute",
            description="bruteforce",
            severity=Severity.HIGH,
            event=_ev({"x": 1}),
            group_key="1.1.1.1",
        )
        alert = fired_alert_to_wazuh_alert(fired)
        assert "group=1.1.1.1" in (alert.full_log or "")

    @pytest.mark.parametrize(
        "severity,level",
        [
            (Severity.LOW, 2),
            (Severity.MEDIUM, 5),
            (Severity.HIGH, 9),
            (Severity.CRITICAL, 13),
        ],
    )
    def test_severity_to_level_round_trips(self, severity, level) -> None:
        """The level we pick must round-trip through level_to_severity()."""
        fired = FiredAlert(
            rule_id="r",
            description="d",
            severity=severity,
            event=_ev({"x": 1}),
        )
        alert = fired_alert_to_wazuh_alert(fired)
        assert alert.rule.level == level
        assert level_to_severity(alert.rule.level) == severity
