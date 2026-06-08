"""Downstream pipeline handoff — delegates to the deterministic supervisor.

The seam is backward-compatible: one-arg calls (incident only) still work
but are a no-op when no supervisor is wired (pre-#7 integration tests).
The worker passes repo and supervisor explicitly.
"""

from __future__ import annotations

from backend.domain.incident import Incident
from backend.infra.logging import get_logger

logger = get_logger(__name__)


async def dispatch_to_pipeline(
    incident: Incident,
    repo: object = None,
    supervisor: object = None,
) -> None:
    """Hand a grounded incident to the supervisor.

    When supervisor is None (pre-wiring integration or old callers), logs a warning
    and returns — safe no-op. Otherwise delegates to supervisor.run_incident.
    """
    if supervisor is None:
        logger.warning(
            "pipeline_no_supervisor",
            incident_id=str(incident.id),
            status=incident.status,
        )
        return

    await supervisor.run_incident(incident.id, repo)
