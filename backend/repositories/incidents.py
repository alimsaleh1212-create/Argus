"""IncidentRepository — the only module that touches the incidents table."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from backend.domain.incident import Evidence, Incident, IncidentStatus, NormalizedEvent, Severity

_TABLE = sa.text  # convenience alias


class IncidentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, incident: Incident) -> Incident:
        now = datetime.now(UTC)
        await self._session.execute(
            sa.text(
                "INSERT INTO incidents "
                "(id, status, severity, correlation_id, dedup_fingerprint, source, "
                " raw_alert, normalized_event, evidence, attempts, created_at, updated_at) "
                "VALUES (:id, :status, :severity, :correlation_id, :dedup_fingerprint, "
                "        :source, CAST(:raw_alert AS jsonb), CAST(:normalized_event AS jsonb), "
                "        CAST(:evidence AS jsonb), :attempts, :created_at, :updated_at)"
            ),
            {
                "id": str(incident.id),
                "status": incident.status.value,
                "severity": incident.severity.value,
                "correlation_id": incident.correlation_id,
                "dedup_fingerprint": incident.dedup_fingerprint,
                "source": incident.source,
                "raw_alert": _json(incident.raw_alert),
                "normalized_event": _json(incident.normalized_event),
                "evidence": _json(incident.evidence),
                "attempts": incident.attempts,
                "created_at": now,
                "updated_at": now,
            },
        )
        await self._session.commit()
        return await self.get(incident.id)  # type: ignore[return-value]

    async def get(self, incident_id: uuid.UUID) -> Incident | None:
        result = await self._session.execute(
            sa.text("SELECT * FROM incidents WHERE id = :id"),
            {"id": str(incident_id)},
        )
        row = result.mappings().first()
        if row is None:
            return None
        return _row_to_incident(row)

    async def get_by_fingerprint(self, fingerprint: str) -> Incident | None:
        result = await self._session.execute(
            sa.text("SELECT * FROM incidents WHERE dedup_fingerprint = :fp LIMIT 1"),
            {"fp": fingerprint},
        )
        row = result.mappings().first()
        if row is None:
            return None
        return _row_to_incident(row)

    async def claim_for_grounding(self, incident_id: uuid.UUID) -> bool:
        """Atomic status transition received → grounding.

        Returns True if the claim succeeded (row was in 'received' state),
        False if it was already claimed or in a non-claimable state.
        """
        now = datetime.now(UTC)
        result = await self._session.execute(
            sa.text(
                "UPDATE incidents SET status = 'grounding', updated_at = :now "
                "WHERE id = :id AND status = 'received' "
                "RETURNING id"
            ),
            {"id": str(incident_id), "now": now},
        )
        await self._session.commit()
        return result.first() is not None

    async def set_grounded(
        self,
        incident_id: uuid.UUID,
        normalized_event: NormalizedEvent,
        evidence: Evidence,
        severity: Severity,
    ) -> None:
        import json

        now = datetime.now(UTC)
        await self._session.execute(
            sa.text(
                "UPDATE incidents SET status = 'grounded', severity = :severity, "
                "normalized_event = CAST(:ne AS jsonb), evidence = CAST(:ev AS jsonb), updated_at = :now "
                "WHERE id = :id"
            ),
            {
                "id": str(incident_id),
                "severity": severity.value,
                "ne": json.dumps(normalized_event.model_dump(mode="json")),
                "ev": json.dumps(evidence.model_dump(mode="json")),
                "now": now,
            },
        )
        await self._session.commit()

    async def bump_attempt(self, incident_id: uuid.UUID) -> int:
        now = datetime.now(UTC)
        result = await self._session.execute(
            sa.text(
                "UPDATE incidents SET attempts = attempts + 1, updated_at = :now "
                "WHERE id = :id RETURNING attempts"
            ),
            {"id": str(incident_id), "now": now},
        )
        await self._session.commit()
        row = result.first()
        return row[0] if row else 0

    async def advance_status(
        self,
        incident_id: uuid.UUID,
        *,
        expected: IncidentStatus,
        target: IncidentStatus,
        disposition: str | None = None,
        evidence_patch: dict[str, Any] | None = None,
    ) -> bool:
        """Atomic guarded transition: UPDATE … WHERE status = :expected.

        Returns True iff the row was updated (i.e., the guard held).
        Returns False if another worker already moved the row.
        When evidence_patch is provided it is JSONB-merged in the same guarded UPDATE.
        """
        import json

        now = datetime.now(UTC)
        params: dict[str, Any] = {
            "id": str(incident_id),
            "target": target.value,
            "expected": expected.value,
            "now": now,
        }

        set_clauses = ["status = :target", "updated_at = :now"]
        if disposition is not None:
            set_clauses.append("disposition = :disposition")
            params["disposition"] = disposition
        if evidence_patch is not None:
            set_clauses.append(
                "evidence = COALESCE(evidence, '{}'::jsonb) || CAST(:evidence_patch AS jsonb)"
            )
            params["evidence_patch"] = json.dumps(evidence_patch)

        sql = (
            f"UPDATE incidents SET {', '.join(set_clauses)} "
            "WHERE id = :id AND status = :expected "
            "RETURNING id"
        )
        result = await self._session.execute(sa.text(sql), params)
        await self._session.commit()
        return result.first() is not None

    async def mark_failed(self, incident_id: uuid.UUID, reason: str = "") -> None:
        now = datetime.now(UTC)
        await self._session.execute(
            sa.text(
                "UPDATE incidents SET status = 'failed', updated_at = :now WHERE id = :id"
            ),
            {"id": str(incident_id), "now": now},
        )
        await self._session.commit()

    async def list_non_terminal(self) -> list[Incident]:
        result = await self._session.execute(
            sa.text(
                "SELECT * FROM incidents WHERE status NOT IN ('grounded', 'failed') "
                "ORDER BY created_at ASC"
            )
        )
        return [_row_to_incident(row) for row in result.mappings().all()]


def _json(value: Any) -> str:
    import json

    if value is None:
        return "null"
    return json.dumps(value)


def _row_to_incident(row: Any) -> Incident:
    return Incident(
        id=uuid.UUID(str(row["id"])),
        status=IncidentStatus(row["status"]),
        severity=Severity(row["severity"]),
        correlation_id=row["correlation_id"],
        dedup_fingerprint=row["dedup_fingerprint"],
        source=row["source"],
        raw_alert=row["raw_alert"] if row["raw_alert"] is not None else {},
        normalized_event=row["normalized_event"],
        evidence=row["evidence"],
        disposition=row.get("disposition"),
        attempts=row["attempts"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
