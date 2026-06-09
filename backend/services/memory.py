"""Episode assembly service — redact → build IncidentEpisode → store.write_episode.

Best-effort orchestration: write errors are swallowed by the caller (worker.py).
The redaction step is the stored-snapshot boundary (FR-005, Constitution III).
"""

from __future__ import annotations

from datetime import UTC
from typing import TYPE_CHECKING, Any

from backend.domain.incident import Incident, NormalizedEvent
from backend.domain.memory import EntityKind, EntityRef, IncidentEpisode
from backend.domain.redaction import Boundary

if TYPE_CHECKING:
    from backend.infra.redaction import Redactor


def _extract_entities(normalized_event: NormalizedEvent, redactor: Redactor) -> list[EntityRef]:
    """Pull entity refs from structured NormalizedEvent fields.

    Absent fields yield no entity — never an error.
    """
    refs: list[EntityRef] = []
    fields = normalized_event.fields or {}

    # address — agent IP
    if normalized_event.agent_ip:
        val = redactor.redact_text(normalized_event.agent_ip, Boundary.MEMORY_WRITE)
        if val:
            refs.append(EntityRef(kind=EntityKind.ADDRESS, value=val))

    # address — src/dst IPs in fields
    for key in ("srcip", "dstip", "src_ip", "dst_ip"):
        raw = fields.get(key)
        if isinstance(raw, str) and raw:
            val = redactor.redact_text(raw, Boundary.MEMORY_WRITE)
            if val:
                refs.append(EntityRef(kind=EntityKind.ADDRESS, value=val))

    # host — agent name
    if normalized_event.agent_name:
        val = redactor.redact_text(normalized_event.agent_name, Boundary.MEMORY_WRITE)
        if val:
            refs.append(EntityRef(kind=EntityKind.HOST, value=val))

    # user — various field names
    for key in ("user", "srcuser", "dstuser"):
        raw = fields.get(key)
        if isinstance(raw, str) and raw:
            val = redactor.redact_text(raw, Boundary.MEMORY_WRITE)
            if val:
                refs.append(EntityRef(kind=EntityKind.USER, value=val))

    # indicators — hashes and domains (bounded, best-effort)
    for key in ("md5", "sha1", "sha256", "hash", "domain", "url"):
        raw = fields.get(key)
        if isinstance(raw, str) and raw:
            val = redactor.redact_text(raw, Boundary.MEMORY_WRITE)
            if val:
                refs.append(EntityRef(kind=EntityKind.INDICATOR, value=val))

    # deduplicate by (kind, value) preserving order
    seen: set[tuple[str, str]] = set()
    unique: list[EntityRef] = []
    for ref in refs:
        key = (ref.kind, ref.value)
        if key not in seen:
            seen.add(key)
            unique.append(ref)

    return unique


def _redact_fields(fields: dict[str, Any], redactor: Redactor) -> dict[str, Any]:
    """Redact a bounded slice of normalized_event.fields."""
    if not fields:
        return {}
    redacted = redactor.redact_mapping(dict(fields), Boundary.MEMORY_WRITE)
    return dict(redacted) if isinstance(redacted, dict) else {}


async def record_episode(
    incident: Incident,
    store: Any,  # MemoryStore protocol — typed loosely to avoid circular imports
    redactor: Redactor,
) -> None:
    """Redact, build, and write one IncidentEpisode for a terminal incident.

    Idempotent on incident.id (the store uses it as the episode UUID).
    The caller (worker) wraps this in try/except — errors here never block
    the disposition acknowledgement.
    """
    from datetime import datetime

    evidence_data = incident.evidence or {}
    summary_raw = evidence_data.get("summary", "")
    verdict_raw = evidence_data.get("verdict", "")

    ne_data = incident.normalized_event or {}
    ne = NormalizedEvent.model_validate(ne_data) if isinstance(ne_data, dict) else ne_data

    # ── redact ──────────────────────────────────────────────────────────────
    summary = redactor.redact_text(str(summary_raw), Boundary.MEMORY_WRITE)
    fields = _redact_fields(ne.fields, redactor)
    entities = _extract_entities(ne, redactor)

    observed_at = incident.updated_at or datetime.now(UTC)

    episode = IncidentEpisode(
        incident_id=incident.id,
        observed_at=observed_at,
        summary=summary,
        verdict=str(verdict_raw),
        severity=incident.severity,
        disposition=str(incident.disposition or ""),
        entities=entities,
        fields=fields,
    )
    await store.write_episode(episode)
