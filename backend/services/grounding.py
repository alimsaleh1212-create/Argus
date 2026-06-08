"""Grounding service — deterministic NormalizedEvent → Evidence assembly.

Pure function, no I/O, no LLM (ID7). Idempotent: safe to re-run on any status.
"""

from __future__ import annotations

from backend.domain.incident import Evidence, Incident, NormalizedEvent
from backend.services.wazuh import level_to_severity


def ground(incident: Incident) -> Evidence:
    """Assemble Evidence from the Incident's normalized_event. Pure, deterministic."""
    ne_data = incident.normalized_event or {}
    if isinstance(ne_data, dict):
        ne = NormalizedEvent.model_validate(ne_data)
    else:
        ne = ne_data  # type: ignore[assignment]

    severity = level_to_severity(ne.rule_level)
    flags: list[str] = []

    if ne.rule_level is None:
        flags.append("severity_defaulted")
    if ne.agent_name is None and ne.agent_id is None:
        flags.append("agent_unknown")

    agent_label = ne.agent_name or ne.agent_id or "unknown"
    rule_label = ne.rule_description or ne.rule_id or "unknown rule"
    summary = f"{rule_label} on {agent_label}"

    return Evidence(
        verdict="rule_match",
        severity=severity,
        normalized_event=ne,
        summary=summary,
        retrieved_context=[],
        flags=flags,
    )
