"""Pipeline-map service — composes a PipelineSnapshot from aggregate reads.

Read-only and provider-independent. The status/disposition mapping is pure and
unit-tested without a database (mirrors the KPI service pattern).
"""

from __future__ import annotations

from datetime import UTC, datetime

from backend.domain.dashboard import (
    _DISPOSITION_TO_BRANCH,
    _DISPOSITION_TO_TERMINAL_BRANCH,
    _STATUS_TO_STAGE,
    BranchOutflow,
    PipelineSnapshot,
    StageIncident,
    StageNode,
    TerminalCounts,
    build_journey,  # noqa: F401 — re-exported for backward-compat (routers/tests import it from here)
)

# Ordered rail: (stage key, display label).
STAGES: list[tuple[str, str]] = [
    ("intake", "Intake"),
    ("triage", "Triage"),
    ("enrichment", "Enrichment"),
    ("response", "Response"),
]


def stage_in_flight(status_counts: dict[str, int]) -> dict[str, int]:
    """Sum active-status counts into the four stage buckets (0 when empty)."""
    out: dict[str, int] = {key: 0 for key, _ in STAGES}
    for status, count in status_counts.items():
        stage = _STATUS_TO_STAGE.get(status)
        if stage is not None:
            out[stage] += count
    return out


def stage_branches(disposition_counts: dict[str, int]) -> dict[str, list[BranchOutflow]]:
    """Attribute terminal dispositions to (stage, branch) outflows over the window."""
    acc: dict[str, dict[str, int]] = {key: {} for key, _ in STAGES}
    for disposition, count in disposition_counts.items():
        mapping = _DISPOSITION_TO_BRANCH.get(disposition)
        if mapping is None:
            continue  # unknown disposition is not attributed to a stage
        stage, branch = mapping
        acc[stage][branch] = acc[stage].get(branch, 0) + count
    return {
        stage: [BranchOutflow(to=branch, count=acc[stage][branch]) for branch in sorted(acc[stage])]
        for stage in acc
    }


def terminal_counts(
    status_counts: dict[str, int], disposition_counts: dict[str, int]
) -> TerminalCounts:
    """Roll dispositions into resolved/escalated totals; awaiting is the live count."""
    resolved = 0
    escalated = 0
    for disposition, count in disposition_counts.items():
        branch = _DISPOSITION_TO_TERMINAL_BRANCH.get(disposition)
        if branch is None:
            continue
        if branch == "resolved":
            resolved += count
        elif branch == "escalated":
            escalated += count
    return TerminalCounts(
        resolved=resolved,
        escalated=escalated,
        awaiting=status_counts.get("awaiting_approval", 0),
    )


def _to_stage_incident(incident) -> StageIncident:
    """Project an Incident onto a StageIncident, extracting scores from its evidence.

    Pure: reads only the merged evidence dict. Every score is None when the
    producing stage has not yet completed for this incident.
    """
    evidence = incident.evidence or {}
    triage = evidence.get("triage") or {}
    enrichment = evidence.get("enrichment") or {}
    response = evidence.get("response") or {}
    plan = response.get("plan") or {}
    verification = response.get("verification") or {}

    def _conf(value) -> float | None:
        if value is None:
            return None
        try:
            f = float(value)
        except (TypeError, ValueError):
            return None
        return f if 0.0 <= f <= 1.0 else None

    return StageIncident(
        id=incident.id,
        severity=incident.severity.value,
        status=incident.status.value,
        source=incident.source,
        summary=evidence.get("summary"),
        updated_at=incident.updated_at,
        triage_verdict=triage.get("verdict"),
        triage_confidence=_conf(triage.get("confidence")),
        enrichment_assessment=enrichment.get("assessment"),
        enrichment_confidence=_conf(enrichment.get("confidence")),
        response_plan_id=plan.get("playbook_id"),
        response_selected_by=plan.get("selected_by"),
        response_verdict=verification.get("verdict"),
    )


def stage_incidents(incidents) -> dict[str, list[StageIncident]]:
    """Group in-flight incidents by their current stage and project scores.

    Incidents whose status does not map to a rail stage are dropped.
    """
    by_stage: dict[str, list[StageIncident]] = {key: [] for key, _ in STAGES}
    for incident in incidents:
        stage = _STATUS_TO_STAGE.get(incident.status.value)
        if stage is None:
            continue
        by_stage[stage].append(_to_stage_incident(incident))
    return by_stage


async def build_pipeline_snapshot(repo, *, window_hours: int) -> PipelineSnapshot:
    """Compose a PipelineSnapshot from two read-only aggregate reads."""
    status_counts = await repo.status_counts()
    disposition_counts = await repo.disposition_counts_since(window_hours=window_hours)
    in_flight = stage_in_flight(status_counts)
    branches = stage_branches(disposition_counts)
    incidents_by_stage = stage_incidents(await repo.list_in_flight_with_evidence())
    stages = [
        StageNode(
            key=key,
            label=label,
            in_flight=in_flight[key],
            branches=branches.get(key, []),
            incidents=incidents_by_stage.get(key, []),
        )
        for key, label in STAGES
    ]
    return PipelineSnapshot(
        stages=stages,
        terminals=terminal_counts(status_counts, disposition_counts),
        window_hours=window_hours,
        generated_at=datetime.now(UTC),
    )


# build_journey lives in backend.domain.dashboard (see import above) — moved there in
# B2 so backend.repositories can derive a queue row's journey without importing
# backend.services (forbidden by the layered-architecture import-linter contract).
