"""Bounded retrieval fan-out + reasoning-bundle assembly (best-effort, degraded-on-error).

`gather_context` runs the four retrieval directions concurrently — reference corpus, similar prior
incidents, time-valid reputation facts, and threat-intel verdicts — each individually guarded so a
single retriever outage degrades to empty context rather than failing the stage. It returns the
assembled external/internal finding lists ready for the LLM reasoning bundle.
"""

from __future__ import annotations

import asyncio

from backend.agents.enrichment._log import get_logger
from backend.domain.corpus import (
    CorpusRetriever,
    EntityKind,
    EntityRef,
    IntelVerdict,
    ReferenceQuery,
)
from backend.domain.memory import EpisodeQuery, FactState, MemoryStore

_logger = get_logger(__name__)


async def _safe(coro: object, fallback: object) -> object:
    """Run a coroutine, returning fallback on any exception (degraded context)."""
    try:
        return await coro  # type: ignore[misc]
    except Exception as exc:
        _logger.debug("enrichment_retrieval_error", error=str(exc))
        return fallback


async def gather_context(
    *,
    corpus: CorpusRetriever | None,
    memory: MemoryStore | None,
    intel: object | None,
    query: ReferenceQuery,
    entities: list[EntityRef],
    summary: str,
    corpus_k: int,
    memory_k: int,
    max_indicators: int,
    consult_intel: bool,
) -> tuple[list[dict], list[dict]]:
    """Fan out all four retrieval directions and assemble (external_raw, internal_raw)."""

    async def _corpus_hits() -> list:
        if corpus is None:
            return []
        return await _safe(corpus.search_reference(query, k=corpus_k), [])  # type: ignore[return-value]

    async def _memory_similar() -> list:
        if memory is None:
            return []
        eq = EpisodeQuery(text=summary, entities=entities)
        return await _safe(memory.search_similar(eq, k=memory_k), [])  # type: ignore[return-value]

    async def _reputation_facts() -> list[FactState]:
        if memory is None:
            return []
        tasks = [
            _safe(memory.query_fact(e, "reputation", as_of=None), FactState())
            for e in entities[:max_indicators]
        ]
        if not tasks:
            return []
        results = await asyncio.gather(*tasks)
        return list(results)  # type: ignore[return-value]

    async def _intel_verdicts() -> list[IntelVerdict]:
        if intel is None or not consult_intel:
            return []
        indicator_entities = [
            e
            for e in entities[:max_indicators]
            if e.kind in (EntityKind.INDICATOR, EntityKind.ADDRESS)
        ]
        tasks = [
            _safe(intel.lookup(e.value, e.kind), None)  # type: ignore[union-attr]
            for e in indicator_entities
        ]
        if not tasks:
            return []
        raw = await asyncio.gather(*tasks)
        return [r for r in raw if r is not None]

    corpus_hits, similar_priors, rep_facts, intel_verdicts = await asyncio.gather(
        _corpus_hits(),
        _memory_similar(),
        _reputation_facts(),
        _intel_verdicts(),
    )

    return (
        _assemble_external(corpus_hits, intel_verdicts),
        _assemble_internal(similar_priors, rep_facts),
    )


def _assemble_external(corpus_hits: list, intel_verdicts: list) -> list[dict]:
    """Flatten corpus hits + intel verdicts into the external-context finding list."""
    external_raw: list[dict] = []
    for hit in corpus_hits:
        entry = getattr(hit, "entry", hit)
        external_raw.append(
            {
                "type": "corpus",
                "key": getattr(entry, "key", ""),
                "title": getattr(entry, "title", ""),
                "content": getattr(entry, "content", ""),
                "relevance": getattr(hit, "relevance", 0.0),
            }
        )
    for verdict in intel_verdicts:
        external_raw.append(
            {
                "type": "intel",
                "indicator": getattr(verdict, "indicator", ""),
                "verdict": getattr(verdict, "verdict", "unknown"),
                "source": getattr(verdict, "source", ""),
            }
        )
    return external_raw


def _assemble_internal(similar_priors: list, rep_facts: list) -> list[dict]:
    """Flatten prior incidents + reputation facts into the internal-context finding list."""
    internal_raw: list[dict] = []
    for hit in similar_priors:
        internal_raw.append(
            {
                "type": "prior_incident",
                "incident_id": str(getattr(hit, "incident_id", "")),
                "summary": getattr(hit, "summary", ""),
                "disposition": getattr(hit, "disposition", ""),
                "relevance": getattr(hit, "relevance", 0.0),
            }
        )
    for fact_state in rep_facts:
        fact = getattr(fact_state, "fact", None)
        if fact is None:
            continue
        entity = getattr(fact, "entity", None)
        internal_raw.append(
            {
                "type": "reputation_fact",
                "entity": f"{getattr(entity, 'kind', '')}:{getattr(entity, 'value', '')}",
                "value": getattr(fact, "value", ""),
                "is_current": getattr(fact_state, "is_current", False),
                "has_superseded": getattr(fact_state, "has_superseded", False),
            }
        )
    return internal_raw
