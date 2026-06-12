"""Dashboard SSE stream producer (T048).

Emits ``snapshot`` / ``delta`` / ``heartbeat`` events over a ``text/event-stream`` response.
Read-only — never mutates incidents or triggers the supervisor.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from datetime import UTC, datetime

from backend.repositories.incidents import IncidentRepository


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


def _heartbeat() -> str:
    return _sse("heartbeat", {"ts": datetime.now(UTC).isoformat()})


async def incident_stream(
    repo: IncidentRepository,
    *,
    poll_seconds: float = 2.0,
) -> AsyncGenerator[str, None]:
    """Async generator for the SSE stream.

    First yield: full ``snapshot`` (queue + kpi_counters).
    Subsequent yields: ``delta`` (changed rows) or ``heartbeat`` (no changes).
    Producer failure → emit heartbeat, never crash.
    """
    prev_updated_at: dict[str, str] = {}

    # Initial snapshot
    try:
        snapshot = await _build_snapshot(repo)
        prev_updated_at = {str(item["id"]): item["updated_at"] for item in snapshot["queue"]}
        yield _sse("snapshot", snapshot)
    except Exception:
        yield _heartbeat()

    while True:
        await asyncio.sleep(poll_seconds)
        try:
            snapshot = await _build_snapshot(repo)
            current = {str(item["id"]): item["updated_at"] for item in snapshot["queue"]}

            # Delta = rows with new or changed updated_at
            changed = [
                item
                for item in snapshot["queue"]
                if current.get(str(item["id"])) != prev_updated_at.get(str(item["id"]))
            ]
            # Also include removed rows (present in prev but not in current)
            prev_updated_at = current

            if changed:
                yield _sse("delta", {"queue": changed, "kpi_counters": snapshot["kpi_counters"]})
            else:
                yield _heartbeat()
        except Exception:
            yield _heartbeat()


async def _build_snapshot(repo: IncidentRepository) -> dict:
    summaries, kpi_counters = await asyncio.gather(
        repo.list_for_queue(view="all", limit=200, offset=0),
        repo.kpi_status_counts(),
    )
    return {
        "queue": [
            {
                "id": str(s.id),
                "status": s.status,
                "severity": s.severity,
                "disposition": s.disposition,
                "source": s.source,
                "summary": s.summary,
                "is_awaiting_approval": s.is_awaiting_approval,
                "created_at": s.created_at.isoformat(),
                "updated_at": s.updated_at.isoformat(),
            }
            for s in summaries
        ],
        "kpi_counters": kpi_counters,
    }
