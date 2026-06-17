"""Pipeline-map service — composes a PipelineSnapshot from aggregate reads.

Read-only and provider-independent. The status/disposition mapping is pure and
unit-tested without a database (mirrors the KPI service pattern).
"""

from __future__ import annotations

from datetime import UTC, datetime

from backend.domain.dashboard import (
    BranchOutflow,
    PipelineSnapshot,
    StageNode,
    TerminalCounts,
)

# Ordered rail: (stage key, display label).
STAGES: list[tuple[str, str]] = [
    ("intake", "Intake"),
    ("triage", "Triage"),
    ("enrichment", "Enrichment"),
    ("response", "Response"),
]

# Active (in-flight) statuses → the stage they sit in. Terminal statuses
# (resolved/escalated/failed) intentionally map to no stage.
_STATUS_TO_STAGE: dict[str, str] = {
    "received": "intake",
    "grounding": "intake",
    "grounded": "intake",
    "triaging": "triage",
    "enriching": "enrichment",
    "responding": "response",
    "awaiting_approval": "response",
}

# Stage-tagged dispositions → (stage that produced it, terminal branch).
_DISPOSITION_TO_BRANCH: dict[str, tuple[str, str]] = {
    "auto_resolved_noise": ("intake", "resolved"),
    "auto_resolved_triage": ("triage", "resolved"),
    "escalated_triage": ("triage", "escalated"),
    "auto_resolved_enrichment": ("enrichment", "resolved"),
    "escalated_enrichment": ("enrichment", "escalated"),
    "auto_remediated": ("response", "resolved"),
    "remediated": ("response", "resolved"),
    "rejected_by_human": ("response", "resolved"),
    "remediation_unverified": ("response", "escalated"),
    "approval_expired": ("response", "escalated"),
    "escalated_response": ("response", "escalated"),
}

# Every terminal disposition → its branch ("resolved" | "escalated"), independent of
# stage attribution. Includes everything in _DISPOSITION_TO_BRANCH (stage-attributable)
# PLUS supervisor safety-net escalations that can fire from any in-flight stage and so
# cannot be attributed to a single rail stage — these must still count toward the
# headline escalated total, just not toward any one stage's breakdown.
_DISPOSITION_TO_TERMINAL_BRANCH: dict[str, str] = {
    **{disposition: branch for disposition, (_, branch) in _DISPOSITION_TO_BRANCH.items()},
    "escalated_step_cap": "escalated",
    "escalated_token_cap": "escalated",
    "escalated_stage_error": "escalated",
    "escalated_illegal_transition": "escalated",
}


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


async def build_pipeline_snapshot(repo, *, window_hours: int) -> PipelineSnapshot:
    """Compose a PipelineSnapshot from two read-only aggregate reads."""
    status_counts = await repo.status_counts()
    disposition_counts = await repo.disposition_counts_since(window_hours=window_hours)
    in_flight = stage_in_flight(status_counts)
    branches = stage_branches(disposition_counts)
    stages = [
        StageNode(key=key, label=label, in_flight=in_flight[key], branches=branches.get(key, []))
        for key, label in STAGES
    ]
    return PipelineSnapshot(
        stages=stages,
        terminals=terminal_counts(status_counts, disposition_counts),
        window_hours=window_hours,
        generated_at=datetime.now(UTC),
    )
