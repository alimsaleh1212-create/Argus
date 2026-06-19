"""MemoryStore implementations — NullMemory (degraded) + GraphitiMemory (Neo4j 5.26).

graphiti_core and neo4j are imported ONLY from within the backend.infra.memory package.
Consumers depend on the MemoryStore Protocol (domain/memory.py); swapping to the decided
pgvector fallback is a config-toggle change, not a rewrite.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from backend.domain.memory import (
    EntityRef,
    EpisodeQuery,
    FactState,
    IncidentEpisode,
    MemoryHit,
    TemporalFact,
)
from backend.infra.config import MemorySettings
from backend.infra.logging import get_logger

logger = get_logger(__name__)

_EMPTY_FACT_STATE = FactState(fact=None, is_current=False, has_superseded=False)


def _to_native_dt(value: Any) -> datetime | None:
    """Convert a ``neo4j.time.DateTime`` to a native ``datetime`` (pass through None/datetime).

    The Neo4j driver returns temporal properties as ``neo4j.time.DateTime``, which
    pydantic's ``TemporalFact`` rejects; ``.to_native()`` yields a stdlib ``datetime``.
    """
    if value is None:
        return None
    to_native = getattr(value, "to_native", None)
    if callable(to_native):
        return to_native()
    return value


# ── NullMemory ───────────────────────────────────────────────────────────────


class NullMemory:
    """No-op MemoryStore — used when memory is disabled or Neo4j is unreachable."""

    async def write_episode(self, episode: IncidentEpisode) -> None:
        pass

    async def search_similar(self, query: EpisodeQuery, *, k: int) -> list[MemoryHit]:
        return []

    async def query_fact(
        self,
        entity: EntityRef,
        fact_type: str,
        *,
        as_of: datetime | None = None,
    ) -> FactState:
        return FactState()

    async def write_fact(self, fact: TemporalFact) -> None:
        pass


# ── GraphitiMemory ───────────────────────────────────────────────────────────


class GraphitiMemory:
    """Graphiti + Neo4j 5.26 implementation of the MemoryStore Protocol."""

    def __init__(self, graphiti: Any, settings: MemorySettings) -> None:
        self._graphiti = graphiti
        self._settings = settings

    # -- write ----------------------------------------------------------------

    async def _episode_exists(self, name: str) -> bool:
        """Idempotency check: has an Episodic node with this name been written?

        Graphiti's ``add_episode(uuid=...)`` does NOT upsert — it calls
        ``EpisodicNode.get_by_uuid`` and raises ``node <uuid> not found`` when the
        node is absent (i.e. on every first write). So we dedup by name ourselves
        and create with a fresh uuid instead of passing ``uuid=``.
        """
        result = await self._graphiti.driver.execute_query(
            "MATCH (e:Episodic {name: $name}) RETURN e.uuid AS uuid LIMIT 1",
            name=name,
        )
        return len(result.records) > 0

    async def write_episode(self, episode: IncidentEpisode) -> None:
        from graphiti_core.nodes import EpisodeType

        name = str(episode.incident_id)
        # Idempotent: skip if this incident's episode was already written.
        if await self._episode_exists(name):
            return

        body = json.dumps(
            {
                "incident_id": str(episode.incident_id),
                "summary": episode.summary,
                "verdict": episode.verdict,
                "severity": episode.severity.value,
                "disposition": episode.disposition,
                "entities": [{"kind": e.kind.value, "value": e.value} for e in episode.entities],
                "fields": episode.fields,
            }
        )
        await asyncio.wait_for(
            self._graphiti.add_episode(
                name=name,
                episode_body=body,
                source_description="argus-incident",
                reference_time=episode.observed_at,
                source=EpisodeType.json,
            ),
            timeout=self._settings.write_timeout_s,
        )

    # -- retrieve -------------------------------------------------------------

    async def search_similar(self, query: EpisodeQuery, *, k: int) -> list[MemoryHit]:
        timeout = self._settings.retrieval_timeout_s
        try:
            return await asyncio.wait_for(
                self._search_similar_inner(query, k=k),
                timeout=timeout,
            )
        except TimeoutError:
            logger.warning("memory_search_timeout", timeout_s=timeout)
            return []
        except Exception as exc:
            logger.warning("memory_search_error", error=str(exc))
            return []

    async def _search_similar_inner(self, query: EpisodeQuery, *, k: int) -> list[MemoryHit]:
        edges = await self._graphiti.search(query.text, num_results=k * 5)
        if not edges:
            return []

        # Collect unique episode UUIDs from matching edges (preserve relevance order)
        seen: dict[str, int] = {}
        for rank, edge in enumerate(edges):
            for ep_uuid in edge.episodes or []:
                if ep_uuid not in seen:
                    seen[ep_uuid] = rank

        if not seen:
            return []

        ordered_uuids = sorted(seen, key=lambda u: seen[u])[:k]

        # Load episode nodes to extract stored metadata
        episodes = await self._graphiti.driver.episode_node_ops.get_by_uuids(
            self._graphiti.driver, ordered_uuids
        )

        hits: list[MemoryHit] = []
        total = max(len(seen), 1)
        for ep in episodes:
            try:
                data = json.loads(ep.content)
                rank = seen.get(ep.uuid, total)
                relevance = max(0.0, min(1.0, 1.0 - rank / total))
                hits.append(
                    MemoryHit(
                        incident_id=uuid.UUID(data["incident_id"]),
                        summary=data.get("summary", ""),
                        disposition=data.get("disposition", ""),
                        observed_at=ep.valid_at or datetime.now(UTC),
                        relevance=relevance,
                    )
                )
            except Exception as exc:
                logger.debug("memory_hit_parse_error", error=str(exc))

        hits.sort(key=lambda h: h.relevance, reverse=True)
        return hits[:k]

    # -- write fact -----------------------------------------------------------

    async def write_fact(self, fact: TemporalFact) -> None:
        """Write a time-bounded reputation edge, invalidating any open prior
        fact of the same (entity, fact_type)."""
        await asyncio.wait_for(
            self._write_fact_inner(fact),
            timeout=self._settings.write_timeout_s,
        )

    async def _write_fact_inner(self, fact: TemporalFact) -> None:
        now = datetime.now(UTC)
        value_node = f"{fact.fact_type}:{fact.value}"

        # Invalidate (not delete) any currently-open fact of the same (entity, fact_type)
        # whose validity differs from this one — preserves temporal history while keeping
        # re-seeding the SAME fact idempotent (the matching-valid_at edge is left open).
        invalidate_cypher = """
        MATCH (src:Entity)-[r:RELATES_TO]-(tgt:Entity)
        WHERE (src.name = $entity_val OR tgt.name = $entity_val)
          AND r.invalid_at IS NULL
          AND (toLower(r.name) CONTAINS toLower($fact_type)
               OR toLower(r.fact) CONTAINS toLower($fact_type))
          AND r.valid_at <> $valid_from
        SET r.invalid_at = $now
        """
        await self._graphiti.driver.execute_query(
            invalidate_cypher,
            entity_val=fact.entity.value,
            fact_type=fact.fact_type,
            valid_from=fact.valid_from,
            now=now,
        )

        # Upsert a deterministic, immediately-queryable reputation edge. We do NOT rely
        # on Graphiti's LLM entity-extraction for facts: a single-entity reputation fact
        # ("<ip> is malicious") does not reliably yield a RELATES_TO edge, so query_fact
        # would never see it. The edge carries name=fact_type and fact=value so the
        # query_fact Cypher matches and returns the value. MERGE keyed on
        # (entity, fact_type, valid_at) keeps re-seeding idempotent.
        upsert_cypher = """
        MERGE (e:Entity {name: $entity_val})
        MERGE (v:Entity {name: $value_node})
        MERGE (e)-[r:RELATES_TO {name: $fact_type, valid_at: $valid_from}]->(v)
        SET r.fact = $value, r.invalid_at = null
        """
        await self._graphiti.driver.execute_query(
            upsert_cypher,
            entity_val=fact.entity.value,
            value_node=value_node,
            fact_type=fact.fact_type,
            valid_from=fact.valid_from,
            value=fact.value,
        )

    # -- temporal fact --------------------------------------------------------

    async def query_fact(
        self,
        entity: EntityRef,
        fact_type: str,
        *,
        as_of: datetime | None = None,
    ) -> FactState:
        timeout = self._settings.retrieval_timeout_s
        try:
            return await asyncio.wait_for(
                self._query_fact_inner(entity, fact_type, as_of=as_of),
                timeout=timeout,
            )
        except TimeoutError:
            logger.warning("memory_query_fact_timeout", timeout_s=timeout)
            return _EMPTY_FACT_STATE
        except Exception as exc:
            logger.warning("memory_query_fact_error", error=str(exc))
            return _EMPTY_FACT_STATE

    async def _query_fact_inner(
        self,
        entity: EntityRef,
        fact_type: str,
        *,
        as_of: datetime | None = None,
    ) -> FactState:
        now = datetime.now(UTC)
        point_in_time = as_of if as_of is not None else now

        # Query all edges related to this entity, filter by fact_type in the fact text
        cypher = """
        MATCH (src:Entity)-[r:RELATES_TO]-(tgt:Entity)
        WHERE (src.name = $entity_val OR tgt.name = $entity_val)
          AND (toLower(r.name) CONTAINS toLower($fact_type)
               OR toLower(r.fact) CONTAINS toLower($fact_type))
        RETURN
            r.fact        AS fact_text,
            r.name        AS fact_name,
            r.valid_at    AS valid_from,
            r.invalid_at  AS valid_until
        ORDER BY r.valid_at DESC
        """
        result = await self._graphiti.driver.execute_query(
            cypher,
            entity_val=entity.value,
            fact_type=fact_type,
        )
        raw_rows = result.records if hasattr(result, "records") else []
        # The Neo4j driver returns temporal properties as neo4j.time.DateTime, which
        # pydantic's TemporalFact rejects. Normalise every row to native datetimes up
        # front so the window-matching and TemporalFact construction below are safe.
        rows = [
            {
                "fact_text": r.get("fact_text"),
                "fact_name": r.get("fact_name"),
                "valid_from": _to_native_dt(r.get("valid_from")),
                "valid_until": _to_native_dt(r.get("valid_until")),
            }
            for r in raw_rows
        ]

        if not rows:
            return _EMPTY_FACT_STATE

        # Find the row whose window contains point_in_time
        matching_row = None
        for row in rows:
            valid_from: datetime | None = row.get("valid_from")
            valid_until: datetime | None = row.get("valid_until")

            if valid_from is None:
                continue

            # Normalise to UTC-aware
            if valid_from.tzinfo is None:
                valid_from = valid_from.replace(tzinfo=UTC)
            if valid_until is not None and valid_until.tzinfo is None:
                valid_until = valid_until.replace(tzinfo=UTC)

            in_window = valid_from <= point_in_time and (
                valid_until is None or valid_until > point_in_time
            )
            if in_window:
                matching_row = row
                break  # rows are DESC by valid_at; first match wins

        has_superseded = any(row.get("valid_until") is not None for row in rows)

        if matching_row is None:
            return FactState(fact=None, is_current=False, has_superseded=has_superseded)

        valid_until_val: datetime | None = matching_row.get("valid_until")
        if valid_until_val is not None and valid_until_val.tzinfo is None:
            valid_until_val = valid_until_val.replace(tzinfo=UTC)
        valid_from_val: datetime = matching_row["valid_from"]
        if valid_from_val.tzinfo is None:
            valid_from_val = valid_from_val.replace(tzinfo=UTC)

        temporal_fact = TemporalFact(
            entity=entity,
            fact_type=fact_type,
            value=matching_row.get("fact_text") or matching_row.get("fact_name") or "",
            valid_from=valid_from_val,
            valid_until=valid_until_val,
        )
        is_current = valid_until_val is None
        return FactState(
            fact=temporal_fact,
            is_current=is_current,
            has_superseded=has_superseded,
        )
