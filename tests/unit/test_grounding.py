"""Unit tests — T028: grounding.ground() deterministic Evidence assembly.

TDD: must FAIL before services/grounding.py is implemented.
"""

from __future__ import annotations

import uuid

import pytest


def _make_incident(level: int | None = 10, agent_name: str | None = "web-01"):
    from backend.domain.incident import (
        Incident,
        IncidentStatus,
        NormalizedEvent,
        Severity,
    )

    return Incident(
        id=uuid.uuid4(),
        status=IncidentStatus.GROUNDING,
        severity=Severity.HIGH,
        correlation_id="corr-1",
        dedup_fingerprint="fp-1",
        source="wazuh",
        raw_alert={"rule": {"level": level}},
        normalized_event={
            "rule_id": "5763",
            "rule_level": level,
            "rule_description": "SSH brute force",
            "rule_groups": ["sshd"],
            "agent_id": "001",
            "agent_name": agent_name,
            "agent_ip": "10.0.0.42",
            "event_time": None,
            "fields": {},
        },
    )


class TestGrounding:
    def test_verdict_is_rule_match(self) -> None:
        from backend.services.grounding import ground

        inc = _make_incident()
        ev = ground(inc)
        assert ev.verdict == "rule_match"

    def test_severity_from_band(self) -> None:
        from backend.domain.incident import Severity
        from backend.services.grounding import ground

        inc = _make_incident(level=10)
        ev = ground(inc)
        assert ev.severity == Severity.HIGH

    def test_summary_one_line(self) -> None:
        from backend.services.grounding import ground

        inc = _make_incident(level=10, agent_name="web-server-01")
        ev = ground(inc)
        assert "SSH brute force" in ev.summary
        assert "web-server-01" in ev.summary
        assert "\n" not in ev.summary

    def test_retrieved_context_empty(self) -> None:
        from backend.services.grounding import ground

        ev = ground(_make_incident())
        assert ev.retrieved_context == []

    def test_flags_empty_for_normal_alert(self) -> None:
        from backend.services.grounding import ground

        ev = ground(_make_incident(level=10, agent_name="web-01"))
        assert "severity_defaulted" not in ev.flags

    def test_severity_defaulted_flag_when_level_none(self) -> None:
        from backend.services.grounding import ground

        inc = _make_incident(level=None)
        ev = ground(inc)
        assert "severity_defaulted" in ev.flags

    def test_ground_is_pure_no_io(self) -> None:
        """ground() must be a pure function — no side effects, deterministic output."""
        from backend.services.grounding import ground

        inc = _make_incident()
        ev1 = ground(inc)
        ev2 = ground(inc)
        assert ev1.verdict == ev2.verdict
        assert ev1.severity == ev2.severity
        assert ev1.summary == ev2.summary

    def test_idempotent_on_grounded_incident(self) -> None:
        """Re-running ground() on an already-grounded Incident must be a no-op (idempotent)."""
        from backend.domain.incident import IncidentStatus
        from backend.services.grounding import ground

        inc = _make_incident()
        # Simulate already grounded status
        object.__setattr__(inc, "status", IncidentStatus.GROUNDED)
        # Should still return valid Evidence without raising
        ev = ground(inc)
        assert ev.verdict == "rule_match"
