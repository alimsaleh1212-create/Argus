"""Episode assembly service — redact → build IncidentEpisode → store.write_episode.

Best-effort orchestration: write errors are swallowed by the caller (worker.py).
The redaction step is the stored-snapshot boundary (FR-005, Constitution III).
"""

from __future__ import annotations

from datetime import UTC
from typing import TYPE_CHECKING, Any

from backend.domain.incident import Incident, NormalizedEvent
from backend.domain.memory import EntityKind, EntityRef, IncidentEpisode, TemporalFact
from backend.domain.redaction import Boundary
from backend.infra.logging import get_logger

logger = get_logger(__name__)

if TYPE_CHECKING:
    from backend.infra.config import FeedbackSettings
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


def _resolve_target_kind(target: str, action_type: str | None = None) -> EntityKind | None:
    """Best-effort entity-kind resolution for an action target.

    The kind is not used by the memory query (which keys on value), but keeping
    it consistent with _extract_entities makes the stored fact self-describing.
    """
    import re

    if action_type == "block_ip":
        return EntityKind.ADDRESS
    if action_type == "isolate_host":
        return EntityKind.HOST
    if action_type == "disable_user":
        return EntityKind.USER

    if re.match(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$", target):
        return EntityKind.ADDRESS
    if re.match(r"^([0-9a-fA-F]{32}|[0-9a-fA-F]{40}|[0-9a-fA-F]{64})$", target):
        return EntityKind.INDICATOR
    if "." in target and not target.startswith("."):
        return EntityKind.INDICATOR

    return EntityKind.INDICATOR


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


async def record_outcome_facts(
    incident: Incident,
    store: Any,
    redactor: Redactor,
    cfg: FeedbackSettings | None = None,
) -> None:
    """Write one time-valid remediation_outcome TemporalFact per applied target.

    Best-effort; caller wraps in try/except. Errors here never block disposition.
    """
    from datetime import datetime

    fact_type: str = "remediation_outcome"
    if cfg is not None:
        fact_type = getattr(cfg, "outcome_fact_type", fact_type) or fact_type

    evidence_data = incident.evidence or {}
    response_data = evidence_data.get("response")
    if not isinstance(response_data, dict):
        return

    verification = response_data.get("verification")
    if not isinstance(verification, dict):
        return

    verdict = verification.get("verdict")
    if not isinstance(verdict, str) or not verdict:
        return

    results = response_data.get("results", [])
    if not isinstance(results, list):
        return

    observed_at = incident.updated_at or datetime.now(UTC)

    for raw in results:
        if not isinstance(raw, dict):
            continue
        if raw.get("status") != "applied":
            continue
        target = raw.get("target")
        if not isinstance(target, str) or not target:
            continue

        action_type = raw.get("type")
        kind = _resolve_target_kind(target, action_type)
        if kind is None:
            continue

        redacted_target = redactor.redact_text(target, Boundary.MEMORY_WRITE)
        if not redacted_target:
            continue

        entity = EntityRef(kind=kind, value=redacted_target)
        fact = TemporalFact(
            entity=entity,
            fact_type=fact_type,
            value=verdict,
            valid_from=observed_at,
        )
        try:
            await store.write_fact(fact)
        except Exception as exc:
            logger.warning(
                "record_outcome_facts_write_failed",
                incident_id=str(incident.id),
                error=str(exc),
            )
