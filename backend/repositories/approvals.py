"""ApprovalRepository — lifecycle management for pending approval requests.

All SQL for the approval_requests table lives here.
The supervisor remains the single writer of incidents; this repository owns approval_requests only.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from backend.domain.response import ApprovalStatus


@dataclass
class ApprovalRecord:
    id: int
    incident_id: uuid.UUID
    plan_id: str
    pending_actions: list[dict]
    rationale: str
    status: str
    deadline_at: datetime
    decided_by: str | None
    decided_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ApprovalRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_pending(
        self,
        *,
        incident_id: uuid.UUID,
        plan_id: str,
        pending_actions: list[dict],
        rationale: str,
        deadline_at: datetime,
    ) -> int:
        """Insert a pending approval row and return the approval id.

        Conflict on the partial-unique (incident_id WHERE status='pending')
        means the incident is already parked — returns the existing id (idempotent).
        """
        import json

        result = await self._session.execute(
            sa.text(
                "INSERT INTO approval_requests "
                "(incident_id, plan_id, pending_actions, rationale, status, deadline_at) "
                "VALUES (:incident_id, :plan_id, CAST(:pending_actions AS jsonb), :rationale, 'pending', :deadline_at) "
                "ON CONFLICT (incident_id) WHERE status = 'pending' "
                "DO UPDATE SET updated_at = now() "
                "RETURNING id"
            ),
            {
                "incident_id": str(incident_id),
                "plan_id": plan_id,
                "pending_actions": json.dumps(pending_actions),
                "rationale": rationale,
                "deadline_at": deadline_at,
            },
        )
        await self._session.commit()
        row = result.first()
        return row[0] if row else 0

    async def get(self, approval_id: int) -> ApprovalRecord | None:
        """Return one approval record by id."""
        result = await self._session.execute(
            sa.text("SELECT * FROM approval_requests WHERE id = :id"),
            {"id": approval_id},
        )
        row = result.mappings().first()
        if row is None:
            return None
        return _row_to_record(row)

    async def get_pending_for_incident(self, incident_id: uuid.UUID) -> ApprovalRecord | None:
        """Return the active pending approval for an incident (for dashboard detail view)."""
        result = await self._session.execute(
            sa.text(
                "SELECT * FROM approval_requests "
                "WHERE incident_id = :incident_id AND status = 'pending' "
                "ORDER BY created_at DESC LIMIT 1"
            ),
            {"incident_id": str(incident_id)},
        )
        row = result.mappings().first()
        if row is None:
            return None
        return _row_to_record(row)

    async def get_approved_pending_for(self, incident_id: uuid.UUID) -> ApprovalRecord | None:
        """Return the approved-but-not-yet-consumed approval record for this incident (Pass-B discriminator).

        Returns None if no approved record exists (first-pass or already consumed).
        """
        result = await self._session.execute(
            sa.text(
                "SELECT * FROM approval_requests "
                "WHERE incident_id = :incident_id AND status = 'approved' "
                "ORDER BY created_at DESC LIMIT 1"
            ),
            {"incident_id": str(incident_id)},
        )
        row = result.mappings().first()
        if row is None:
            return None
        return _row_to_record(row)

    async def resolve(
        self,
        approval_id: int,
        *,
        to: ApprovalStatus,
        decided_by: str,
    ) -> bool:
        """Guarded pending→approved|rejected|expired transition.

        Returns True iff the guard held (status was pending before the update).
        First decision wins (RD6 / SC-006).
        """
        from datetime import UTC

        now = datetime.now(UTC).replace(tzinfo=None)
        result = await self._session.execute(
            sa.text(
                "UPDATE approval_requests "
                "SET status = :to, decided_by = :decided_by, decided_at = :decided_at, "
                "    updated_at = now() "
                "WHERE id = :id AND status = 'pending' "
                "RETURNING id"
            ),
            {
                "id": approval_id,
                "to": to.value,
                "decided_by": decided_by,
                "decided_at": now,
            },
        )
        await self._session.commit()
        return result.first() is not None

    async def list_pending_expired(self, now: datetime) -> list[ApprovalRecord]:
        """Return pending approvals whose deadline has passed (for the timeout sweeper — RD7)."""
        result = await self._session.execute(
            sa.text(
                "SELECT * FROM approval_requests "
                "WHERE status = 'pending' AND deadline_at < :now "
                "ORDER BY deadline_at ASC"
            ),
            {"now": now},
        )
        return [_row_to_record(row) for row in result.mappings().all()]


def _row_to_record(row: Any) -> ApprovalRecord:
    import json

    pending_actions = row["pending_actions"]
    if isinstance(pending_actions, str):
        pending_actions = json.loads(pending_actions)
    elif pending_actions is None:
        pending_actions = []

    return ApprovalRecord(
        id=row["id"],
        incident_id=uuid.UUID(str(row["incident_id"])),
        plan_id=row["plan_id"],
        pending_actions=pending_actions,
        rationale=row.get("rationale") or "",
        status=row["status"],
        deadline_at=row["deadline_at"],
        decided_by=row.get("decided_by"),
        decided_at=row.get("decided_at"),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
