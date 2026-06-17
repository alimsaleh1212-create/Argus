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


class StageNode(BaseModel):
    """One stage on the pipeline rail."""

    key: str  # "intake" | "triage" | "enrichment" | "response"
    label: str
    in_flight: int
    branches: list[BranchOutflow] = Field(default_factory=list)


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
