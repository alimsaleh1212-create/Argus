"""IncidentRepository — the only module that touches the incidents table."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from backend.domain.dashboard import IncidentSummary, MemoryHit, VolumeBucket
from backend.domain.incident import Evidence, Incident, IncidentStatus, NormalizedEvent, Severity

_TABLE = sa.text  # convenience alias

_TERMINAL_STATUSES = frozenset({"resolved", "escalated", "failed"})
_ACTIVE_STATUSES = frozenset({s.value for s in IncidentStatus} - _TERMINAL_STATUSES)

_ALLOWED_SORTS: dict[str, str] = {
    "-updated_at": "updated_at DESC",
    "updated_at": "updated_at ASC",
    "-created_at": "created_at DESC",
    "created_at": "created_at ASC",
    "-severity": "severity DESC",
    "severity": "severity ASC",
}


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
            sa.text("UPDATE incidents SET status = 'failed', updated_at = :now WHERE id = :id"),
            {"id": str(incident_id), "now": now},
        )
        await self._session.commit()

    async def list_for_queue(
        self,
        *,
        view: str = "active",
        statuses: list[str] | None = None,
        severities: list[str] | None = None,
        sort: str = "-updated_at",
        limit: int = 50,
        offset: int = 0,
    ) -> list[IncidentSummary]:
        where, params = _build_queue_where(view=view, statuses=statuses, severities=severities)
        order = _ALLOWED_SORTS.get(sort, "updated_at DESC")
        params["limit"] = limit
        params["offset"] = offset
        sql = (
            "SELECT id, status, severity, disposition, source, "
            "evidence->>'summary' AS summary, updated_at, created_at "
            f"FROM incidents{where} ORDER BY {order} LIMIT :limit OFFSET :offset"
        )
        result = await self._session.execute(sa.text(sql), params)
        rows = result.mappings().all()
        return [
            IncidentSummary(
                id=row["id"],
                status=row["status"],
                severity=row["severity"],
                disposition=row["disposition"],
                source=row["source"],
                summary=row["summary"],
                is_awaiting_approval=row["status"] == "awaiting_approval",
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
            for row in rows
        ]

    async def count_for_queue(
        self,
        *,
        view: str = "active",
        statuses: list[str] | None = None,
        severities: list[str] | None = None,
    ) -> int:
        where, params = _build_queue_where(view=view, statuses=statuses, severities=severities)
        sql = f"SELECT COUNT(*) FROM incidents{where}"
        result = await self._session.execute(sa.text(sql), params)
        return result.scalar() or 0

    async def kpi_volume_buckets(
        self, *, bucket_hours: int = 1, limit: int = 24
    ) -> list[VolumeBucket]:
        """Return incident counts grouped by created_at in N-hour buckets (most recent first)."""
        sql = (
            "SELECT "
            "  to_timestamp("
            "    (EXTRACT(EPOCH FROM created_at)::bigint / (:bh * 3600)) * (:bh * 3600)"
            "  ) AT TIME ZONE 'UTC' AS bucket, "
            "  COUNT(*) AS count "
            "FROM incidents "
            "GROUP BY bucket "
            "ORDER BY bucket DESC "
            "LIMIT :limit"
        )
        result = await self._session.execute(sa.text(sql), {"bh": bucket_hours, "limit": limit})
        rows = result.mappings().all()
        return [VolumeBucket(bucket=row["bucket"], count=row["count"]) for row in rows]

    async def kpi_disposition_counts(self) -> dict[str, int]:
        """Return counts grouped by disposition (excludes NULL dispositions)."""
        result = await self._session.execute(
            sa.text(
                "SELECT COALESCE(disposition, '_none') AS d, COUNT(*) AS cnt "
                "FROM incidents GROUP BY d"
            )
        )
        return {row["d"]: row["cnt"] for row in result.mappings().all()}

    async def kpi_mean_time_to_disposition_ms(self) -> int | None:
        """Return mean (updated_at - created_at) in ms for terminal incidents with a disposition."""
        result = await self._session.execute(
            sa.text(
                "SELECT AVG(EXTRACT(EPOCH FROM (updated_at - created_at)) * 1000) AS avg_ms "
                "FROM incidents "
                "WHERE status IN ('resolved', 'escalated', 'failed') AND disposition IS NOT NULL"
            )
        )
        avg = result.scalar()
        return int(avg) if avg is not None else None

    async def kpi_enriched_and_hit_counts(self) -> MemoryHit:
        """Return enriched count, memory-hit count, and feedback-bias count.

        Enriched = incident has evidence['enrichment'] key.
        Memory-hit = enriched AND internal_findings is non-empty (proxy for memory retrieval).
        Bias applied = evidence['feedback']['bias_applied'] is true.
        """
        result = await self._session.execute(
            sa.text(
                "SELECT "
                "  SUM(CASE WHEN evidence ? 'enrichment' THEN 1 ELSE 0 END) AS enriched, "
                "  SUM(CASE WHEN evidence ? 'enrichment' "
                "        AND jsonb_array_length(evidence->'enrichment'->'internal_findings') > 0 "
                "        THEN 1 ELSE 0 END) AS hits, "
                "  SUM(CASE WHEN evidence ? 'feedback' "
                "        AND evidence->'feedback'->>'bias_applied' = 'true' "
                "        THEN 1 ELSE 0 END) AS bias_applied "
                "FROM incidents"
            )
        )
        row = result.mappings().one()
        enriched = int(row["enriched"] or 0)
        hits = int(row["hits"] or 0)
        bias_applied = int(row["bias_applied"] or 0)
        rate = (hits / enriched) if enriched > 0 else None
        return MemoryHit(enriched=enriched, hits=hits, rate=rate, bias_applied=bias_applied)

    async def kpi_status_counts(self) -> dict[str, int]:
        """Return counts grouped by broad status buckets for the stream kpi_counters."""
        result = await self._session.execute(
            sa.text("SELECT status, COUNT(*) AS cnt FROM incidents GROUP BY status")
        )
        rows = result.mappings().all()
        raw: dict[str, int] = {row["status"]: row["cnt"] for row in rows}
        active = sum(v for k, v in raw.items() if k in _ACTIVE_STATUSES)
        return {
            "active": active,
            "awaiting_approval": raw.get("awaiting_approval", 0),
            "auto_resolved": raw.get("auto_remediated", 0),
            "escalated": raw.get("escalated", 0),
        }

    async def list_non_terminal(self) -> list[Incident]:
        result = await self._session.execute(
            sa.text(
                "SELECT * FROM incidents WHERE status NOT IN ('grounded', 'failed') "
                "ORDER BY created_at ASC"
            )
        )
        return [_row_to_incident(row) for row in result.mappings().all()]


def _build_queue_where(
    *,
    view: str,
    statuses: list[str] | None,
    severities: list[str] | None,
) -> tuple[str, dict[str, Any]]:
    clauses: list[str] = []
    params: dict[str, Any] = {}

    if view == "active":
        active = list(_ACTIVE_STATUSES)
        placeholders = ", ".join(f":vs{i}" for i in range(len(active)))
        clauses.append(f"status IN ({placeholders})")
        for i, v in enumerate(active):
            params[f"vs{i}"] = v
    elif view == "resolved":
        terminal = list(_TERMINAL_STATUSES)
        placeholders = ", ".join(f":vt{i}" for i in range(len(terminal)))
        clauses.append(f"status IN ({placeholders})")
        for i, v in enumerate(terminal):
            params[f"vt{i}"] = v
    # "all" → no status clause

    if statuses:
        placeholders = ", ".join(f":st{i}" for i in range(len(statuses)))
        clauses.append(f"status IN ({placeholders})")
        for i, v in enumerate(statuses):
            params[f"st{i}"] = v

    if severities:
        placeholders = ", ".join(f":sv{i}" for i in range(len(severities)))
        clauses.append(f"severity IN ({placeholders})")
        for i, v in enumerate(severities):
            params[f"sv{i}"] = v

    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, params


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
