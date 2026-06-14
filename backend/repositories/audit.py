"""AuditRepository — append-only accountability ledger for remediation actions.

All SQL for the audit_log table lives here.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass
class AuditRow:
    id: int
    incident_id: uuid.UUID
    actor: str
    action: str
    target: str | None
    outcome: str
    idempotency_key: str | None
    created_at: datetime


class AuditRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def is_applied(self, idempotency_key: str) -> bool:
        """Return True if an applied row with this idempotency_key already exists."""
        result = await self._session.execute(
            sa.text("SELECT 1 FROM audit_log WHERE idempotency_key = :key AND outcome = 'applied'"),
            {"key": idempotency_key},
        )
        return result.first() is not None

    async def append(
        self,
        *,
        incident_id: uuid.UUID,
        actor: str,
        action: str,
        target: str | None = None,
        outcome: str,
        idempotency_key: str | None = None,
    ) -> bool:
        """Append an audit row.

        For applied rows with an idempotency_key: ON CONFLICT DO NOTHING returns False
        (already executed — idempotent skip, RD6). All other rows are always inserted.
        Returns True if a row was inserted, False if the unique constraint blocked it.
        """

        if idempotency_key is not None and outcome == "applied":
            result = await self._session.execute(
                sa.text(
                    "INSERT INTO audit_log (incident_id, actor, action, target, outcome, idempotency_key) "
                    "VALUES (:incident_id, :actor, :action, :target, :outcome, :idem_key) "
                    "ON CONFLICT (idempotency_key) WHERE outcome = 'applied' DO NOTHING "
                    "RETURNING id"
                ),
                {
                    "incident_id": str(incident_id),
                    "actor": actor,
                    "action": action,
                    "target": target,
                    "outcome": outcome,
                    "idem_key": idempotency_key,
                },
            )
            await self._session.commit()
            return result.first() is not None

        await self._session.execute(
            sa.text(
                "INSERT INTO audit_log (incident_id, actor, action, target, outcome, idempotency_key) "
                "VALUES (:incident_id, :actor, :action, :target, :outcome, :idem_key)"
            ),
            {
                "incident_id": str(incident_id),
                "actor": actor,
                "action": action,
                "target": target,
                "outcome": outcome,
                "idem_key": idempotency_key,
            },
        )
        await self._session.commit()
        return True

    async def list_for_incident(self, incident_id: uuid.UUID) -> list[AuditRow]:
        """Return all audit rows for an incident, ordered by creation time."""
        result = await self._session.execute(
            sa.text(
                "SELECT id, incident_id, actor, action, target, outcome, idempotency_key, created_at "
                "FROM audit_log WHERE incident_id = :incident_id ORDER BY created_at ASC"
            ),
            {"incident_id": str(incident_id)},
        )
        rows = []
        for row in result.mappings().all():
            rows.append(
                AuditRow(
                    id=row["id"],
                    incident_id=uuid.UUID(str(row["incident_id"])),
                    actor=row["actor"],
                    action=row["action"],
                    target=row.get("target"),
                    outcome=row["outcome"],
                    idempotency_key=row.get("idempotency_key"),
                    created_at=row["created_at"],
                )
            )
        return rows
