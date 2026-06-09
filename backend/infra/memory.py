"""Temporal memory layer — Graphiti on Neo4j 5.26.

graphiti_core and neo4j are imported ONLY from this module.

Consumers depend on the MemoryStore Protocol (domain/memory.py); swapping to
the decided pgvector fallback is a config-toggle change, not a rewrite.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
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

logger = logging.getLogger(__name__)

_EMPTY_FACT_STATE = FactState(fact=None, is_current=False, has_superseded=False)


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


# ── GraphitiMemory ───────────────────────────────────────────────────────────

class GraphitiMemory:
    """Graphiti + Neo4j 5.26 implementation of the MemoryStore Protocol."""

    def __init__(self, graphiti: Any, settings: MemorySettings) -> None:
        self._graphiti = graphiti
        self._settings = settings

    # -- write ----------------------------------------------------------------

    async def write_episode(self, episode: IncidentEpisode) -> None:
        from graphiti_core.nodes import EpisodeType

        body = json.dumps(
            {
                "incident_id": str(episode.incident_id),
                "summary": episode.summary,
                "verdict": episode.verdict,
                "severity": episode.severity.value,
                "disposition": episode.disposition,
                "entities": [
                    {"kind": e.kind.value, "value": e.value} for e in episode.entities
                ],
                "fields": episode.fields,
            }
        )
        # Idempotent: uuid=str(incident_id) → MERGE on the Episodic node
        await self._graphiti.add_episode(
            name=str(episode.incident_id),
            episode_body=body,
            source_description="sentinel-incident",
            reference_time=episode.observed_at,
            source=EpisodeType.json,
            uuid=str(episode.incident_id),
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

    async def _search_similar_inner(
        self, query: EpisodeQuery, *, k: int
    ) -> list[MemoryHit]:
        edges = await self._graphiti.search(query.text, num_results=k * 5)
        if not edges:
            return []

        # Collect unique episode UUIDs from matching edges (preserve relevance order)
        seen: dict[str, int] = {}
        for rank, edge in enumerate(edges):
            for ep_uuid in (edge.episodes or []):
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
        rows = result.records if hasattr(result, "records") else []

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

        has_superseded = any(
            row.get("valid_until") is not None for row in rows
        )

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


# ── MemoryProvider ───────────────────────────────────────────────────────────

class MemoryProvider:
    """Lifespan singleton that builds GraphitiMemory or degrades to NullMemory."""

    name = "memory"

    @asynccontextmanager
    async def build(
        self, settings: Any
    ) -> AsyncGenerator[NullMemory | GraphitiMemory, None]:
        mem_settings: MemorySettings = settings.memory

        if not mem_settings.enabled:
            logger.info("memory_disabled")
            yield NullMemory()
            return

        try:
            from graphiti_core import Graphiti
            from graphiti_core.embedder.gemini import GeminiEmbedder, GeminiEmbedderConfig
            from graphiti_core.llm_client.gemini_client import GeminiClient, LLMConfig
        except ImportError:
            logger.warning("memory_graphiti_not_installed")
            yield NullMemory()
            return

        graphiti = None
        try:
            # Resolve Neo4j credentials from Vault
            from backend.infra.vault import VaultClient

            vault = VaultClient(settings.vault)
            creds = await vault.get_secret(mem_settings.neo4j_vault_path)
            neo4j_user = creds.get("username", "neo4j")
            neo4j_password = creds.get("password", "")
            neo4j_uri = creds.get("uri", mem_settings.neo4j_uri)

            # Build embedder — chosen once at deploy time via embedder_provider setting.
            # WARNING: do not change embedder_provider after data has been written;
            # vectors from different models are not compatible and would corrupt search.
            if mem_settings.embedder_provider == "ollama":
                from graphiti_core.embedder.openai import OpenAIEmbedder, OpenAIEmbedderConfig

                embedder = OpenAIEmbedder(
                    config=OpenAIEmbedderConfig(
                        api_key="ollama",  # Ollama ignores the key but the field is required
                        base_url=f"{mem_settings.ollama_embedder_base_url}/v1",
                        embedding_model=mem_settings.ollama_embedder_model,
                        embedding_dim=mem_settings.ollama_embedder_dim,
                    )
                )
                # Ollama also provides an OpenAI-compatible chat endpoint for the Graphiti LLM
                from graphiti_core.llm_client.config import LLMConfig as GenericLLMConfig
                from graphiti_core.llm_client.openai_generic_client import OpenAIGenericClient

                llm_client = OpenAIGenericClient(
                    config=GenericLLMConfig(
                        api_key="ollama",
                        base_url=f"{mem_settings.ollama_embedder_base_url}/v1",
                        model=settings.llm.ollama_model,
                    )
                )
                logger.info(
                    "memory_embedder_ollama",
                    model=mem_settings.ollama_embedder_model,
                    base_url=mem_settings.ollama_embedder_base_url,
                )
            else:
                # Default: Gemini — shares the api_key already in Vault at secret/llm
                llm_key_secret = await vault.get_secret(settings.llm.gemini_vault_path)
                gemini_key = llm_key_secret.get("api_key", "")
                llm_client = GeminiClient(config=LLMConfig(api_key=gemini_key))
                embedder = GeminiEmbedder(
                    config=GeminiEmbedderConfig(
                        api_key=gemini_key,
                        embedding_model=mem_settings.gemini_embedding_model,
                    )
                )
                logger.info(
                    "memory_embedder_gemini",
                    model=mem_settings.gemini_embedding_model,
                )

            graphiti = Graphiti(
                uri=neo4j_uri,
                user=neo4j_user,
                password=neo4j_password,
                llm_client=llm_client,
                embedder=embedder,
            )
            await graphiti.build_indices_and_constraints()
            logger.info("memory_graphiti_ready", uri=neo4j_uri)
            yield GraphitiMemory(graphiti=graphiti, settings=mem_settings)
        except Exception as exc:
            logger.warning("memory_startup_error", error=str(exc))
            yield NullMemory()
        finally:
            if graphiti is not None:
                try:
                    await graphiti.close()
                except Exception:
                    pass
