"""Wazuh adapter — parse raw WazuhAlert → NormalizedEvent + severity band."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime

from backend.domain.incident import NormalizedEvent, Severity, WazuhAlert


def level_to_severity(level: int | None) -> Severity:
    """Deterministic rule.level → Severity band (ID6)."""
    if level is None:
        return Severity.MEDIUM
    if level <= 3:
        return Severity.LOW
    if level <= 7:
        return Severity.MEDIUM
    if level <= 11:
        return Severity.HIGH
    return Severity.CRITICAL


def normalize(alert: WazuhAlert) -> NormalizedEvent:
    event_time = None
    if alert.timestamp:
        try:
            event_time = datetime.fromisoformat(
                alert.timestamp.replace("Z", "+00:00")
            )
        except (ValueError, AttributeError):
            pass

    salient: dict = {}
    for key in ("srcip", "dstuser", "process", "dstip"):
        if key in alert.data:
            salient[key] = alert.data[key]

    return NormalizedEvent(
        rule_id=alert.rule.id,
        rule_level=alert.rule.level,
        rule_description=alert.rule.description,
        rule_groups=list(alert.rule.groups),
        agent_id=alert.agent.id if alert.agent else None,
        agent_name=alert.agent.name if alert.agent else None,
        agent_ip=alert.agent.ip if alert.agent else None,
        event_time=event_time,
        fields=salient,
    )


def normalize_with_flags(alert: WazuhAlert) -> tuple[NormalizedEvent, list[str]]:
    flags: list[str] = []
    if alert.rule.level is None:
        flags.append("severity_defaulted")
    if alert.agent is None or (alert.agent.name is None and alert.agent.id is None):
        flags.append("agent_unknown")
    return normalize(alert), flags


def _compute_fingerprint(alert: WazuhAlert, sig: str) -> str:
    """SHA-256 dedup fingerprint over (rule_id, agent_id, content_signature)."""
    rule_id = alert.rule.id or ""
    agent_id = alert.agent.id if alert.agent else ""
    raw = f"{rule_id}:{agent_id}:{sig}"
    return hashlib.sha256(raw.encode()).hexdigest()


def content_signature(alert: WazuhAlert) -> str:
    """SHA-256 over stable alert fields (excludes volatile timestamp)."""
    stable = {
        "rule_id": alert.rule.id,
        "rule_level": alert.rule.level,
        "rule_description": alert.rule.description,
        "agent_id": alert.agent.id if alert.agent else None,
        "agent_name": alert.agent.name if alert.agent else None,
        "full_log": alert.full_log,
    }
    payload = json.dumps(stable, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()
