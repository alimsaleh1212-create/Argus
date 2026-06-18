import uuid
from datetime import UTC, datetime

from backend.domain.incident import Incident, IncidentStatus, Severity
from backend.services.pipeline_view import build_journey


def _incident(*, status, disposition=None, source="wazuh", evidence=None):
    now = datetime.now(UTC)
    return Incident(
        id=uuid.uuid4(),
        status=status,
        severity=Severity.HIGH,
        correlation_id="c",
        dedup_fingerprint="f",
        source=source,
        raw_alert={},
        normalized_event={},
        evidence=evidence or {},
        disposition=disposition,
        attempts=0,
        created_at=now,
        updated_at=now,
    )


def test_noise_autoclosed_at_intake():
    inc = _incident(status=IncidentStatus.RESOLVED, disposition="auto_resolved_noise")
    steps = build_journey(inc)
    assert [s.stage for s in steps] == ["intake", "terminal"]
    assert steps[-1].outcome == "resolved"


def test_full_path_resolved():
    ev = {
        "triage": {"verdict": "real", "confidence": 0.82},
        "enrichment": {"assessment": "malicious", "confidence": 0.91},
        "response": {"plan": {"playbook_id": "isolate_and_ticket"},
                     "verification": {"verdict": "verified"}},
    }
    inc = _incident(status=IncidentStatus.RESOLVED, disposition="remediated", evidence=ev)
    stages = [s.stage for s in build_journey(inc)]
    assert stages == ["intake", "triage", "enrichment", "response", "terminal"]
    triage = next(s for s in build_journey(inc) if s.stage == "triage")
    assert triage.outcome == "advance"
    assert triage.score == 0.82


def test_triage_escalation():
    ev = {"triage": {"verdict": "uncertain", "confidence": 0.4}}
    inc = _incident(status=IncidentStatus.ESCALATED, disposition="escalated_triage", evidence=ev)
    triage = next(s for s in build_journey(inc) if s.stage == "triage")
    assert triage.outcome == "escalated"


def test_safety_net_error_marks_terminal_errored():
    inc = _incident(status=IncidentStatus.ESCALATED, disposition="escalated_stage_error")
    steps = build_journey(inc)
    assert steps[-1].stage == "terminal"
    assert steps[-1].outcome == "errored"


def test_intake_source_label_for_anomaly():
    inc = _incident(status=IncidentStatus.TRIAGING, source="anomaly-detector",
                    evidence={"triage": {"verdict": "real", "confidence": 0.7}})
    assert build_journey(inc)[0].detail == "anomaly-detector"
