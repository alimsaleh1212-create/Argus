"""Unit tests — T010: AnomalyFinding -> WazuhAlert mapping (SPEC-ml-anomaly-detector #17).

Asserts:
- rule id is `anomaly-ueba`.
- level is via the shared severity→level inverse.
- data carries score + top_features + entity_id + window features.
- full_log is deterministic and contains key evidence.
- The downstream `level_to_severity` re-derives the same Severity.
"""

from __future__ import annotations

from datetime import datetime

from backend.domain.anomaly import AnomalyFinding, EntityActivityWindow
from backend.domain.incident import Severity
from backend.services.anomaly import finding_to_wazuh_alert
from backend.services.wazuh import level_to_severity


class TestFindingToWazuhAlert:
    def _finding(self, score: float, severity: Severity) -> AnomalyFinding:
        window = EntityActivityWindow(
            entity_id="user-m01",
            window_start=datetime(2024, 1, 1),
            window_end=datetime(2024, 1, 2),
            features={"logon_count": 5.0, "after_hours_count": 3.0},
            raw_event_count=10,
        )
        return AnomalyFinding(
            entity_id="user-m01",
            score=score,
            severity=severity,
            window=window,
            top_features=["logon_count", "after_hours_count"],
        )

    def test_rule_id_and_groups(self) -> None:
        alert = finding_to_wazuh_alert(self._finding(0.85, Severity.HIGH))
        assert alert.rule.id == "anomaly-ueba"
        assert "ueba" in alert.rule.groups
        assert "anomaly" in alert.rule.groups

    def test_level_re_derives_severity(self) -> None:
        for sev in (Severity.LOW, Severity.MEDIUM, Severity.HIGH, Severity.CRITICAL):
            alert = finding_to_wazuh_alert(self._finding(0.85, sev))
            assert level_to_severity(alert.rule.level) == sev

    def test_data_carries_evidence(self) -> None:
        alert = finding_to_wazuh_alert(self._finding(0.85, Severity.HIGH))
        assert alert.data["entity_id"] == "user-m01"
        assert alert.data["score"] == 0.85
        assert alert.data["top_features"] == ["logon_count", "after_hours_count"]
        assert alert.data["logon_count"] == 5.0

    def test_agent_is_entity(self) -> None:
        alert = finding_to_wazuh_alert(self._finding(0.85, Severity.HIGH))
        assert alert.agent is not None
        assert alert.agent.name == "user-m01"

    def test_full_log_deterministic(self) -> None:
        alert = finding_to_wazuh_alert(self._finding(0.85, Severity.HIGH))
        assert alert.full_log.startswith("anomaly: entity=user-m01")
        assert "score=0.8500" in alert.full_log
        assert "logon_count,after_hours_count" in alert.full_log
