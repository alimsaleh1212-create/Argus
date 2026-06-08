"""Downstream pipeline handoff stub.

Logging no-op at #4. SPEC-incident-state-machine (#7) fills the body;
this signature is the seam contract — do not change.
"""

from __future__ import annotations

from backend.domain.incident import Incident
from backend.infra.logging import get_logger

logger = get_logger(__name__)


async def dispatch_to_pipeline(incident: Incident) -> None:
    """Hand a grounded incident to the supervisor.

    STUB at #4: logs and returns. Filled by SPEC-incident-state-machine (#7).
    Signature is frozen — the caller and #7 must agree on it.
    """
    logger.info(
        "pipeline_handoff_stub",
        incident_id=str(incident.id),
        status=incident.status,
    )
