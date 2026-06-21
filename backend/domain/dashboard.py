"""Read DTOs for the Operations Dashboard (#12).

Pure Pydantic v2. No outward imports (domain-isolation contract).
All response shapes are read-only projections of existing tables.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class IncidentSummary(BaseModel):
    """Queue row — projection of one incidents row."""

    model_config = ConfigDict(extra="ignore")

    id: uuid.UUID
    status: str
    severity: str
    disposition: str | None = None
    source: str
    summary: str | None = None
    is_awaiting_approval: bool
    created_at: datetime
    updated_at: datetime
    acknowledged_at: datetime | None = None
    journey: list[JourneyStep] = Field(default_factory=list)


class JourneyStep(BaseModel):
    """One stop on an incident's path through the pipeline (read-only projection)."""

    stage: str  # "intake" | "triage" | "enrichment" | "response" | "terminal"
    label: str
    outcome: str  # "advance" | "resolved" | "escalated" | "errored"
    detail: str | None = None
    score: float | None = None


class QueuePage(BaseModel):
    """Paginated queue response."""

    items: list[IncidentSummary]
    total: int
    limit: int
    offset: int
    view: Literal["active", "resolved", "all"]
    applied_filters: dict[str, Any]


class AuditView(BaseModel):
    """Single audit log row."""

    actor: str
    action: str
    target: str | None = None
    outcome: str
    created_at: datetime


class AuditPage(BaseModel):
    """Audit trail for one incident."""

    audit: list[AuditView]


class ApprovalView(BaseModel):
    """Projection of approval_requests for the UI."""

    id: int
    incident_id: uuid.UUID
    plan_id: str
    pending_actions: list[dict[str, Any]]
    rationale: str
    status: str
    deadline_at: datetime | None = None
    created_at: datetime
    is_actionable: bool


class IncidentDetailView(BaseModel):
    """Full incident detail including approval + audit."""

    id: uuid.UUID
    status: str
    severity: str
    disposition: str | None = None
    source: str
    summary: str | None = None
    is_awaiting_approval: bool
    created_at: datetime
    updated_at: datetime
    evidence: dict[str, Any] | None = None
    normalized_event: dict[str, Any] | None = None
    correlation_id: str | None = None
    pending_approval: ApprovalView | None = None
    audit: list[AuditView] = Field(default_factory=list)
    journey: list[JourneyStep] = Field(default_factory=list)


class SpanView(BaseModel):
    """Single trace span projection."""

    span_id: str
    parent_span_id: str | None = None
    name: str
    kind: str
    status: str
    started_at: datetime | None = None
    ended_at: datetime | None = None
    latency_ms: int | None = None
    llm_model: str | None = None
    tokens_in: int | None = None  # null = "unknown", never coerced to 0
    tokens_out: int | None = None  # null = "unknown", never coerced to 0
    attributes: dict[str, Any] = Field(default_factory=dict)
    error_message: str | None = None


class TelemetryView(BaseModel):
    """Rollup telemetry for a full trace tree."""

    total_tokens_in: int | None = None
    total_tokens_out: int | None = None
    end_to_end_ms: int | None = None
    step_count: int
    error_steps: int


class TraceTreeView(BaseModel):
    """Full trace tree for the trace inspector."""

    correlation_id: str
    root: SpanView | None = None
    children: dict[str, list[SpanView]] = Field(default_factory=dict)
    telemetry: TelemetryView


# --- Pure journey derivation (shared by services.pipeline_view and repositories.incidents) ---
# Moved here (rather than kept services-only) so the repository layer can derive a
# queue row's journey without importing backend.services, which the layered-architecture
# import-linter contract forbids (repositories must sit below services).

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
    "operator_resolved": "resolved",
}

_ERRORED_DISPOSITIONS = frozenset(
    {
        "escalated_stage_error",
        "escalated_step_cap",
        "escalated_token_cap",
        "escalated_illegal_transition",
    }
)

_STAGE_ORDER = ["triage", "enrichment", "response"]


def _conf(value: Any) -> float | None:
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if 0.0 <= f <= 1.0 else None


def build_journey(incident: Any) -> list[JourneyStep]:
    """Derive an incident's ordered stage path from evidence + status + disposition.

    Pure: only reads the incident projection. Stages that were never reached are
    omitted; a reached stage with no downstream progress and a safety-net escalation
    is marked 'errored'.
    """
    evidence = incident.evidence or {}
    status = incident.status.value
    disposition = incident.disposition
    triage = evidence.get("triage") or {}
    enrichment = evidence.get("enrichment") or {}
    response = evidence.get("response") or {}

    steps: list[JourneyStep] = [
        JourneyStep(stage="intake", label="Intake", outcome="advance", detail=incident.source)
    ]
    if disposition == "auto_resolved_noise":
        steps[0].outcome = "resolved"

    present = {
        "triage": bool(triage),
        "enrichment": bool(enrichment),
        "response": bool(response),
    }
    current_stage = _STATUS_TO_STAGE.get(status)

    for i, stage in enumerate(_STAGE_ORDER):
        reached = present[stage] or current_stage == stage
        if not reached:
            continue
        downstream_reached = any(present[s] for s in _STAGE_ORDER[i + 1 :]) or (
            current_stage in _STAGE_ORDER[i + 1 :]
        )

        if stage == "triage":
            detail = triage.get("verdict")
            score = _conf(triage.get("confidence"))
        elif stage == "enrichment":
            detail = enrichment.get("assessment")
            score = _conf(enrichment.get("confidence"))
        else:  # response
            plan = response.get("plan") or {}
            verification = response.get("verification") or {}
            detail = plan.get("playbook_id") or verification.get("verdict")
            score = None

        if downstream_reached:
            outcome = "advance"
        else:
            mapped = _DISPOSITION_TO_BRANCH.get(disposition or "")
            if mapped and mapped[0] == stage:
                outcome = mapped[1]
            elif current_stage == stage:
                outcome = "advance"  # in-flight, no terminal yet
            else:
                outcome = "advance"
        steps.append(
            JourneyStep(
                stage=stage, label=stage.capitalize(), outcome=outcome, detail=detail, score=score
            )
        )

    if status in ("resolved", "escalated", "failed"):
        if disposition in _ERRORED_DISPOSITIONS:
            term_outcome = "errored"
        else:
            term_outcome = _DISPOSITION_TO_TERMINAL_BRANCH.get(disposition or "", "escalated")
        steps.append(
            JourneyStep(
                stage="terminal",
                label=disposition or status,
                outcome=term_outcome,
                detail=disposition,
            )
        )
    return steps


class VolumeBucket(BaseModel):
    bucket: datetime
    count: int


class MemoryHit(BaseModel):
    enriched: int
    hits: int
    rate: float | None = None  # None when enriched == 0
    bias_applied: int = 0


class KpiSnapshot(BaseModel):
    """KPI data for the dashboard KPI view."""

    volume_over_time: list[VolumeBucket]
    disposition_split: dict[str, int]
    mean_time_to_disposition_ms: int | None = None
    memory_hit: MemoryHit
    generated_at: datetime


class BranchOutflow(BaseModel):
    """One terminal exit from a stage over the rolling window."""

    to: str  # "resolved" | "escalated"
    count: int


class StageIncident(BaseModel):
    """One in-flight incident projected onto a pipeline stage with its latest scores.

    Scores are derived from the incident's merged evidence (triage/enrichment/response
    patches) so the map can show triage/enrichment/response results "on the fly" for
    ongoing incidents. Any score field is None when that stage has not yet produced a
    result for this incident.
    """

    id: uuid.UUID
    severity: str
    status: str
    source: str
    summary: str | None = None
    updated_at: datetime
    triage_verdict: str | None = None
    triage_confidence: float | None = None
    enrichment_assessment: str | None = None
    enrichment_confidence: float | None = None
    response_plan_id: str | None = None
    response_selected_by: str | None = None
    response_verdict: str | None = None


class StageNode(BaseModel):
    """One stage on the pipeline rail."""

    key: str  # "intake" | "triage" | "enrichment" | "response"
    label: str
    in_flight: int
    branches: list[BranchOutflow] = Field(default_factory=list)
    incidents: list[StageIncident] = Field(default_factory=list)


class TerminalCounts(BaseModel):
    """Rolling-window terminal totals + live awaiting-approval count."""

    resolved: int
    escalated: int
    awaiting: int


class PipelineSnapshot(BaseModel):
    """Aggregate read for the SOC pipeline-map view (read-only)."""

    stages: list[StageNode]
    terminals: TerminalCounts
    window_hours: int
    generated_at: datetime


class LoginRequest(BaseModel):
    """Auth login body — extra='forbid' to reject unknown fields."""

    model_config = ConfigDict(extra="forbid")

    username: str
    password: SecretStr


class TokenResponse(BaseModel):
    """Returned on successful login."""

    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
    role: str


class OperatorSession(BaseModel):
    """Decoded JWT session — returned by get_current_operator dependency."""

    subject: str
    role: str
    expires_at: datetime


class StreamSnapshot(BaseModel):
    """SSE snapshot/delta payload."""

    queue: list[IncidentSummary]
    kpi_counters: dict[str, int]


class StreamHeartbeat(BaseModel):
    """SSE heartbeat payload."""

    ts: datetime
