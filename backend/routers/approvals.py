"""Human-in-the-loop approval router.

GET  /approvals              — list pending (or filtered) approvals for the operator queue.
POST /approvals/{id}/decision — record approve/reject and synchronously resume the supervisor.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.dependencies import get_approval_repo, get_audit_repo, get_incident_repo, get_supervisor

router = APIRouter(prefix="/approvals", tags=["approvals"])


class DecisionRequest(BaseModel):
    decision: str  # "approve" | "reject"
    note: str = ""


@router.get("")
async def list_approvals(
    status: str = "pending",
    limit: int = 50,
    approval_repo=Depends(get_approval_repo),
) -> dict[str, Any]:
    """List approval requests filtered by status (default: pending)."""
    from backend.domain.response import ApprovalStatus
    from backend.repositories.approvals import ApprovalRepository

    repo: ApprovalRepository = approval_repo
    if status == "pending":
        from datetime import UTC, datetime
        # Return all pending (not yet expired by sweeper)
        records = await repo.list_pending_expired(datetime(9999, 12, 31))
        # Actually we want ALL pending, not just expired — use a full list
        # Re-implement: list all pending without deadline filter
        records = await _list_all_pending(repo)
    else:
        records = []

    return {
        "approvals": [
            {
                "id": r.id,
                "incident_id": str(r.incident_id),
                "plan_id": r.plan_id,
                "pending_actions": r.pending_actions,
                "rationale": r.rationale,
                "status": r.status,
                "deadline_at": r.deadline_at.isoformat() if r.deadline_at else None,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in records[:limit]
        ]
    }


async def _list_all_pending(repo: Any) -> list:
    """Retrieve all pending approvals (helper for list endpoint)."""
    import sqlalchemy as sa
    from backend.repositories.approvals import _row_to_record

    result = await repo._session.execute(
        sa.text(
            "SELECT * FROM approval_requests WHERE status = 'pending' "
            "ORDER BY created_at ASC"
        )
    )
    return [_row_to_record(row) for row in result.mappings().all()]


@router.post("/{approval_id}/decision")
async def post_decision(
    approval_id: int,
    body: DecisionRequest,
    approval_repo=Depends(get_approval_repo),
    audit_repo=Depends(get_audit_repo),
    incident_repo=Depends(get_incident_repo),
    supervisor=Depends(get_supervisor),
) -> dict[str, Any]:
    """Record an approve/reject decision and drive the supervisor resume.

    approve → AWAITING_APPROVAL → RESPONDING → re-runs response stage → resolved/remediated.
    reject  → AWAITING_APPROVAL → RESOLVED (rejected_by_human) + audit row.
    """
    from backend.domain.response import ApprovalDecision, ApprovalStatus

    if body.decision not in ("approve", "reject"):
        raise HTTPException(status_code=422, detail=f"Invalid decision: {body.decision!r}")

    record = await approval_repo.get(approval_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Approval not found")

    if record.status != "pending":
        raise HTTPException(
            status_code=409,
            detail=f"Approval already decided (status={record.status})",
        )

    # Resolve the approval record (guarded pending → approved/rejected)
    to_status = ApprovalStatus.APPROVED if body.decision == "approve" else ApprovalStatus.REJECTED
    actor = "admin"
    resolved = await approval_repo.resolve(
        approval_id,
        to=to_status,
        decided_by=actor,
    )
    if not resolved:
        raise HTTPException(
            status_code=409,
            detail="Approval was already decided by another request",
        )

    # Resume the supervisor (drives execution or writes rejection audit row)
    disposition = await supervisor.resume_incident(
        record.incident_id,
        body.decision,
        incident_repo,
        audit_repo=audit_repo,
        actor=actor,
    )

    # Fetch final incident state
    incident = await incident_repo.get(record.incident_id)
    final_status = incident.status.value if incident else "unknown"
    final_disposition = disposition or (incident.disposition if incident else None)

    return {
        "incident_id": str(record.incident_id),
        "decision": body.decision,
        "status": final_status,
        "disposition": final_disposition,
    }
