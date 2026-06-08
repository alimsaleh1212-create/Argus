"""Intake service — validate → redact → dedup → persist → enqueue → IngestResult."""

from __future__ import annotations

import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from backend.domain.incident import (
    Incident,
    IncidentStatus,
    IngestResult,
    WazuhAlert,
)
from backend.domain.redaction import Boundary
from backend.infra.cache import claim_fingerprint, lookup_fingerprint
from backend.repositories.incidents import IncidentRepository
from backend.services.wazuh import (
    _compute_fingerprint,
    content_signature,
    level_to_severity,
    normalize_with_flags,
)


async def accept(
    *,
    session: AsyncSession,
    queue: Any,
    cache: Any,
    redactor: Any,
    settings: Any,
    alert: WazuhAlert,
) -> IngestResult:
    """Accept a Wazuh alert: redact → dedup → persist → enqueue → IngestResult.

    Atomic: enqueue failure deletes the just-inserted row (no orphan Incident).
    Fails closed on redaction error (exception propagates before any persist).
    """
    ingest_cfg = settings.ingest
    redis_cfg = settings.redis

    # Step 1: Redact at SNAPSHOT boundary — fail closed (any exception propagates)
    redacted_alert = redactor.redact_mapping(alert.model_dump(mode="json"), Boundary.SNAPSHOT)

    # Step 2: Compute dedup fingerprint over stable (non-timestamp) fields
    sig = content_signature(alert)
    fingerprint = _compute_fingerprint(alert, sig)

    # Step 3: Dedup — try to claim the fingerprint key (SET NX EX)
    claimed = await claim_fingerprint(
        cache,
        fingerprint,
        "",  # placeholder id; overwritten after persist
        redis_cfg.dedup_prefix,
        ingest_cfg.dedup_window_s,
    )
    if not claimed:
        # Duplicate: look up the existing incident
        existing_id = await lookup_fingerprint(cache, fingerprint, redis_cfg.dedup_prefix)
        if existing_id:
            repo = IncidentRepository(session)
            existing = await repo.get_by_fingerprint(fingerprint)
            if existing is not None:
                return IngestResult(
                    incident_id=existing.id,
                    status=existing.status,
                    deduplicated=True,
                )

    # Step 4: Normalize
    _normalized_event, _flags = normalize_with_flags(alert)
    severity = level_to_severity(alert.rule.level)

    # Step 5: Persist Incident(received)
    repo = IncidentRepository(session)
    incident = Incident(
        id=uuid.uuid4(),
        status=IncidentStatus.RECEIVED,
        severity=severity,
        correlation_id=str(uuid.uuid4()),
        dedup_fingerprint=fingerprint,
        source="wazuh",
        raw_alert=redacted_alert,
    )
    created = await repo.create(incident)

    # Step 6: Enqueue — if this raises, delete the orphan and re-raise (→ 503)
    try:
        await queue.enqueue(str(created.id))
    except Exception:
        await session.execute(
            sa.text("DELETE FROM incidents WHERE id = :id"),
            {"id": str(created.id)},
        )
        await session.commit()
        raise

    # Step 7: Update the dedup key with the real incident id
    await claim_fingerprint(
        cache,
        fingerprint,
        str(created.id),
        redis_cfg.dedup_prefix,
        ingest_cfg.dedup_window_s,
    )

    return IngestResult(
        incident_id=created.id,
        status=IncidentStatus.RECEIVED,
        deduplicated=False,
    )
