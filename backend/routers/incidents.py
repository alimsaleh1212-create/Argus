"""Incidents read router — dashboard (#12).

All endpoints are read-only and protected by get_current_operator
(applied at the router level in routers/__init__.py).
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from backend.dependencies import (
    get_approval_repo,
    get_audit_repo,
    get_incident_repo,
    get_trace_repo,
)
from backend.domain.dashboard import (
    ApprovalView,
    AuditView,
    IncidentDetailView,
    KpiSnapshot,
    QueuePage,
    SpanView,
    TelemetryView,
    TraceTreeView,
)
from backend.domain.telemetry import Span, TelemetryRecord
from backend.services.dashboard_stream import incident_stream
from backend.services.kpis import build_kpi_snapshot

router = APIRouter(prefix="/incidents", tags=["incidents"])


def _span_to_view(span: Span) -> SpanView:
    return SpanView(
        span_id=span.span_id,
        parent_span_id=span.parent_span_id,
        name=span.name,
        kind=span.kind.value,
        status=span.status.value,
        started_at=span.started_at,
        ended_at=span.ended_at,
        latency_ms=span.latency_ms,
        llm_model=span.llm_model,
        tokens_in=span.tokens_in,
        tokens_out=span.tokens_out,
        attributes=span.attributes or {},
        error_message=span.error_message,
    )


_EMPTY_TELEMETRY = TelemetryView(
    total_tokens_in=None,
    total_tokens_out=None,
    end_to_end_ms=None,
    step_count=0,
    error_steps=0,
)


@router.get("", response_model=QueuePage)
async def list_incidents(
    view: Annotated[str, Query(pattern="^(active|resolved|all)$")] = "active",
    status: Annotated[list[str], Query(alias="status")] = None,  # noqa: B006
    severity: Annotated[list[str], Query(alias="severity")] = None,  # noqa: B006
    sort: str = "-updated_at",
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    repo=Depends(get_incident_repo),  # noqa: B008
) -> QueuePage:
    status = status or []
    severity = severity or []
    items = await repo.list_for_queue(
        view=view,
        statuses=status or None,
        severities=severity or None,
        sort=sort,
        limit=limit,
        offset=offset,
    )
    total = await repo.count_for_queue(
        view=view,
        statuses=status or None,
        severities=severity or None,
    )
    return QueuePage(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
        view=view,  # type: ignore[arg-type]
        applied_filters={"status": status, "severity": severity, "sort": sort},
    )


@router.get("/kpis", response_model=KpiSnapshot)
async def get_kpis(
    repo=Depends(get_incident_repo),  # noqa: B008
) -> KpiSnapshot:
    return await build_kpi_snapshot(repo)


@router.get("/stream")
async def stream(
    request: Request,
    repo=Depends(get_incident_repo),  # noqa: B008
) -> StreamingResponse:
    try:
        poll_seconds = float(request.app.state.container.settings.dashboard.stream_poll_seconds)
    except AttributeError:
        poll_seconds = 2.0

    return StreamingResponse(
        incident_stream(repo, poll_seconds=poll_seconds),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/{incident_id}", response_model=IncidentDetailView)
async def get_incident(
    incident_id: uuid.UUID,
    incident_repo=Depends(get_incident_repo),  # noqa: B008
    audit_repo=Depends(get_audit_repo),  # noqa: B008
    approval_repo=Depends(get_approval_repo),  # noqa: B008
) -> IncidentDetailView:
    incident = await incident_repo.get(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")

    audit_rows = await audit_repo.list_for_incident(incident_id)
    audit = [
        AuditView(
            actor=row.actor,
            action=row.action,
            target=row.target,
            outcome=row.outcome,
            created_at=row.created_at,
        )
        for row in audit_rows
    ]

    pending_approval: ApprovalView | None = None
    if incident.status.value == "awaiting_approval":
        rec = await approval_repo.get_pending_for_incident(incident_id)
        if rec is not None:
            pending_approval = ApprovalView(
                id=rec.id,
                incident_id=rec.incident_id,
                plan_id=rec.plan_id,
                pending_actions=rec.pending_actions,
                rationale=rec.rationale,
                status=rec.status,
                deadline_at=rec.deadline_at,
                created_at=rec.created_at,
                is_actionable=True,
            )

    return IncidentDetailView(
        id=incident.id,
        status=incident.status.value,
        severity=incident.severity.value,
        disposition=incident.disposition,
        source=incident.source,
        summary=incident.evidence.get("summary") if incident.evidence else None,
        is_awaiting_approval=incident.status.value == "awaiting_approval",
        created_at=incident.created_at,
        updated_at=incident.updated_at,
        evidence=incident.evidence,
        normalized_event=incident.normalized_event,
        correlation_id=incident.correlation_id,
        pending_approval=pending_approval,
        audit=audit,
    )


@router.get("/{incident_id}/trace", response_model=TraceTreeView)
async def get_trace(
    incident_id: uuid.UUID,
    incident_repo=Depends(get_incident_repo),  # noqa: B008
    trace_repo=Depends(get_trace_repo),  # noqa: B008
) -> TraceTreeView:
    incident = await incident_repo.get(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")

    correlation_id = incident.correlation_id or ""
    if not correlation_id:
        return TraceTreeView(correlation_id="", root=None, children={}, telemetry=_EMPTY_TELEMETRY)

    tree = await trace_repo.get_trace_tree(correlation_id)
    if tree is None:
        return TraceTreeView(
            correlation_id=correlation_id,
            root=None,
            children={},
            telemetry=_EMPTY_TELEMETRY,
        )

    telemetry_rec = TelemetryRecord.from_trace_tree(tree)
    return TraceTreeView(
        correlation_id=correlation_id,
        root=_span_to_view(tree.root),
        children={
            parent_id: [_span_to_view(s) for s in spans]
            for parent_id, spans in tree.children.items()
        },
        telemetry=TelemetryView(
            total_tokens_in=telemetry_rec.total_tokens_in,
            total_tokens_out=telemetry_rec.total_tokens_out,
            end_to_end_ms=telemetry_rec.end_to_end_ms,
            step_count=telemetry_rec.step_count,
            error_steps=telemetry_rec.error_steps,
        ),
    )


@router.get("/{incident_id}/audit")
async def get_audit(
    incident_id: uuid.UUID,
    incident_repo=Depends(get_incident_repo),  # noqa: B008
    audit_repo=Depends(get_audit_repo),  # noqa: B008
) -> dict:
    incident = await incident_repo.get(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")

    rows = await audit_repo.list_for_incident(incident_id)
    return {
        "audit": [
            {
                "actor": row.actor,
                "action": row.action,
                "target": row.target,
                "outcome": row.outcome,
                "created_at": row.created_at.isoformat(),
            }
            for row in rows
        ]
    }
