"""Task-queue seam — reserved.

RESERVED SEAM. Implemented in SPEC-ingestion (#4) / SPEC-supervisor (#5).

The API enqueues an accepted Wazuh alert and returns 202 immediately; the
worker (``backend.worker``) consumes the queue and runs the
triage → enrichment → response graph. Durable human-in-the-loop state is the
supervisor's LangGraph checkpointer on Postgres (already provisioned), not this
queue — this seam only handles dispatch.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class TaskQueue(Protocol):
    """Minimal enqueue/dequeue contract for alert dispatch."""

    async def enqueue(self, topic: str, payload: dict) -> str: ...


class QueueProvider:
    """Queue provider. Implemented in SPEC-ingestion (#4)."""

    name = "queue"

    def build(self, settings: Any) -> Any:
        raise NotImplementedError(
            "Task queue is a reserved seam; implemented in SPEC-ingestion (#4)."
        )
