"""Corpus seeding services — pure-ish, unit-testable.

seed_reference: map technique/runbook records → ReferenceCorpusEntry, redact,
                upsert into the reference_corpus table.
seed_reputation: map IOC reputation records → TemporalFact, redact indicator,
                 write via store.write_fact (no-op if NullMemory / Neo4j down).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from backend.domain.corpus import ReferenceCorpusEntry, ReferenceKind
from backend.domain.memory import EntityRef, TemporalFact
from backend.domain.redaction import Boundary
from backend.infra.logging import get_logger

logger = get_logger(__name__)


async def seed_reference(
    records: dict[str, list[dict[str, Any]]],
    redactor: Any,
    repo: Any,
) -> None:
    """Map bundled technique/runbook records to ReferenceCorpusEntry rows and upsert.

    A malformed record is skipped with a logged warning — a partial corpus is
    better than no boot (CD4).
    """
    entries: list[ReferenceCorpusEntry] = []

    for raw in records.get("techniques", []):
        try:
            tactic = raw.get("tactic", "")
            tags = _build_tags([raw["id"], tactic] + _keyword_tokens(raw.get("title", "")))
            entries.append(
                ReferenceCorpusEntry(
                    kind=ReferenceKind.TECHNIQUE,
                    key=raw["id"],
                    title=redactor.redact_text(raw["title"], Boundary.MEMORY_WRITE),
                    content=redactor.redact_text(raw["mitigations"], Boundary.MEMORY_WRITE),
                    tags=tags,
                )
            )
        except Exception as exc:
            logger.warning("seed_reference_technique_skip", error=str(exc), raw=str(raw)[:200])

    for raw in records.get("runbooks", []):
        try:
            techniques = raw.get("techniques", [])
            tags = _build_tags(techniques + _keyword_tokens(raw.get("title", "")))
            entries.append(
                ReferenceCorpusEntry(
                    kind=ReferenceKind.RUNBOOK,
                    key=raw["key"],
                    title=redactor.redact_text(raw["title"], Boundary.MEMORY_WRITE),
                    content=redactor.redact_text(raw["steps"], Boundary.MEMORY_WRITE),
                    tags=tags,
                )
            )
        except Exception as exc:
            logger.warning("seed_reference_runbook_skip", error=str(exc), raw=str(raw)[:200])

    if entries:
        await repo.upsert_entries(entries)
        logger.info("seed_reference_done", count=len(entries))
    else:
        logger.warning("seed_reference_empty")


async def seed_reputation(
    records: list[dict[str, Any]],
    redactor: Any,
    store: Any,
) -> None:
    """Map IOC reputation records to TemporalFacts and write via store.write_fact.

    If the store is NullMemory (Neo4j down), fact writes are no-ops and seeding
    still succeeds for the reference table (graceful degradation, FR-008).
    """
    written = 0
    for raw in records:
        try:
            indicator = raw["indicator"]
            kind_str = raw.get("kind", "indicator")
            reputation = raw["reputation"]
            as_of = datetime.fromisoformat(raw["as_of"].replace("Z", "+00:00"))

            redacted_indicator = redactor.redact_text(indicator, Boundary.MEMORY_WRITE)
            entity = EntityRef(kind=kind_str, value=redacted_indicator)  # type: ignore[arg-type]
            fact = TemporalFact(
                entity=entity,
                fact_type="reputation",
                value=reputation,
                valid_from=as_of,
            )
            await store.write_fact(fact)
            written += 1
        except NotImplementedError:
            # GraphitiMemory.write_fact shell — not yet implemented (US2/T021)
            logger.debug("seed_reputation_write_fact_not_implemented")
        except Exception as exc:
            logger.warning("seed_reputation_record_skip", error=str(exc), raw=str(raw)[:200])

    logger.info("seed_reputation_done", written=written)


# ── helpers ──────────────────────────────────────────────────────────────────


def _build_tags(raw_tags: list[str]) -> list[str]:
    seen: dict[str, None] = {}
    for t in raw_tags:
        lowered = t.strip().lower()
        if lowered:
            seen[lowered] = None
    return list(seen)


def _keyword_tokens(title: str) -> list[str]:
    stop = {"the", "a", "an", "for", "and", "or", "of", "in", "to", "on", "at"}
    return [w.lower() for w in title.split() if w.lower() not in stop and len(w) > 2]
