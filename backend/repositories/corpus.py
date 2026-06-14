"""CorpusRepository — the only module that touches the reference_corpus table.

Implements the CorpusRetriever Protocol: deterministic keyed/lexical retrieval,
no LLM, no embeddings in v1 (CD1).
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from backend.domain.corpus import (
    ReferenceCorpusEntry,
    ReferenceHit,
    ReferenceKind,
    ReferenceQuery,
)
from backend.infra.logging import get_logger

logger = get_logger(__name__)

_UPSERT_SQL = sa.text(
    """
    INSERT INTO reference_corpus (kind, key, title, content, tags, updated_at)
    VALUES (:kind, :key, :title, :content, :tags, now())
    ON CONFLICT (kind, key) DO UPDATE
        SET title      = EXCLUDED.title,
            content    = EXCLUDED.content,
            tags       = EXCLUDED.tags,
            updated_at = now()
    """
)


class CorpusRepository:
    """Async SQLAlchemy–backed CorpusRetriever over the reference_corpus table."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # -- write ----------------------------------------------------------------

    async def upsert_entries(self, entries: list[ReferenceCorpusEntry]) -> None:
        for entry in entries:
            await self._session.execute(
                _UPSERT_SQL,
                {
                    "kind": entry.kind.value,
                    "key": entry.key,
                    "title": entry.title,
                    "content": entry.content,
                    "tags": list(entry.tags),
                },
            )
        await self._session.commit()

    # -- read -----------------------------------------------------------------

    async def search_reference(self, query: ReferenceQuery, *, k: int) -> list[ReferenceHit]:
        if not query.technique_ids and not query.terms:
            return []

        hits: dict[tuple[str, str], ReferenceHit] = {}

        # 1. Technique-keyed match (relevance 1.0)
        if query.technique_ids:
            ids_lower = [t.lower() for t in query.technique_ids]
            rows = await self._session.execute(
                sa.text(
                    """
                    SELECT kind, key, title, content, tags
                    FROM reference_corpus
                    WHERE lower(key) = ANY(:ids)
                       OR tags && :ids_arr
                    """
                ),
                {"ids": ids_lower, "ids_arr": ids_lower},
            )
            for row in rows:
                entry = _row_to_entry(row)
                _update_hit(hits, entry, 1.0, "technique")

        # 2. Tag match (relevance proportional to overlap)
        if query.terms:
            terms_lower = [t.lower() for t in query.terms]
            rows = await self._session.execute(
                sa.text(
                    """
                    SELECT kind, key, title, content, tags
                    FROM reference_corpus
                    WHERE tags && :terms
                    """
                ),
                {"terms": terms_lower},
            )
            for row in rows:
                entry = _row_to_entry(row)
                overlap = len(set(entry.tags) & set(terms_lower))
                relevance = min(0.9, overlap / max(len(terms_lower), 1))
                relevance = max(0.01, relevance)
                _update_hit(hits, entry, relevance, "tag")

        # 3. Lexical / ILIKE match (lower relevance band)
        if query.terms:
            for term in query.terms:
                rows = await self._session.execute(
                    sa.text(
                        """
                        SELECT kind, key, title, content, tags
                        FROM reference_corpus
                        WHERE title ILIKE :pattern OR content ILIKE :pattern
                        """
                    ),
                    {"pattern": f"%{term}%"},
                )
                for row in rows:
                    entry = _row_to_entry(row)
                    _update_hit(hits, entry, 0.3, "term")

        if not hits:
            return []

        result = sorted(hits.values(), key=lambda h: (-h.relevance, h.entry.key))
        return result[:k]


# ── helpers ──────────────────────────────────────────────────────────────────


def _row_to_entry(row: sa.engine.Row) -> ReferenceCorpusEntry:  # type: ignore[type-arg]
    return ReferenceCorpusEntry(
        kind=ReferenceKind(row.kind),
        key=row.key,
        title=row.title,
        content=row.content,
        tags=list(row.tags or []),
    )


def _update_hit(
    hits: dict[tuple[str, str], ReferenceHit],
    entry: ReferenceCorpusEntry,
    relevance: float,
    matched_on: str,
) -> None:
    key = (entry.kind.value, entry.key)
    existing = hits.get(key)
    if existing is None or relevance > existing.relevance:
        hits[key] = ReferenceHit(
            entry=entry,
            relevance=relevance,
            matched_on=matched_on,  # type: ignore[arg-type]
        )
