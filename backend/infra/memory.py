"""Temporal memory layer — Graphiti on Neo4j 5.26.

graphiti_core and neo4j are imported ONLY from this module.

Consumers depend on the MemoryStore Protocol (domain/memory.py); swapping to
the decided pgvector fallback is a config-toggle change, not a rewrite.
"""

from __future__ import annotations

import asyncio
import json
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
from backend.infra.logging import get_logger

logger = get_logger(__name__)

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
        episode_name = f"fact:{fact.entity.value}:{fact.fact_type}:{fact.valid_from.isoformat()}"
        # Idempotent: an identical fact (same entity/type/valid_from) already exists →
        # full no-op. Checked BEFORE invalidation so re-seeding doesn't invalidate the
        # current fact and then skip rewriting it.
        if await self._episode_exists(episode_name):
            return

        now = datetime.now(UTC)

        # Invalidate (not delete) any currently-open fact of the same (entity, fact_type).
        invalidate_cypher = """
        MATCH (src:Entity)-[r:RELATES_TO]-(tgt:Entity)
        WHERE (src.name = $entity_val OR tgt.name = $entity_val)
          AND r.invalid_at IS NULL
          AND (toLower(r.name) CONTAINS toLower($fact_type)
               OR toLower(r.fact) CONTAINS toLower($fact_type))
        SET r.invalid_at = $now
        """
        await self._graphiti.driver.execute_query(
            invalidate_cypher,
            entity_val=fact.entity.value,
            fact_type=fact.fact_type,
            now=now,
        )

        # Write the new fact as an episode so Graphiti indexes it.
        import json as _json

        body = _json.dumps(
            {
                "entity_kind": fact.entity.kind,
                "entity_value": fact.entity.value,
                "fact_type": fact.fact_type,
                "value": fact.value,
                "valid_from": fact.valid_from.isoformat(),
            }
        )
        from graphiti_core.nodes import EpisodeType

        await self._graphiti.add_episode(
            name=episode_name,
            episode_body=body,
            source_description=f"argus-{fact.fact_type}",
            reference_time=fact.valid_from,
            source=EpisodeType.json,
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


# ── Graphiti component builders (shared by MemoryProvider + seed_corpus) ──────


def _needs_gemini(mem_settings: MemorySettings, llm_settings: Any) -> bool:
    """True if any Graphiti component (embedder, an LLM fallback slot, or a reranker
    in the cross-encoder chain) uses gemini — i.e. the secret/llm key must be fetched."""
    llm_fallback = [getattr(p, "value", p) for p in llm_settings.fallback_order]
    return "gemini" in (
        mem_settings.embedder_provider,
        *llm_fallback,
        *mem_settings.cross_encoder_order,
    )


def build_embedder(mem_settings: MemorySettings, *, gemini_key: str) -> Any:
    """Build the Graphiti embedder per ``embedder_provider``.

    WARNING: do not change embedder_provider after data has been written — vectors
    from different models are incompatible and would corrupt search.
    """
    if mem_settings.embedder_provider == "ollama":
        from graphiti_core.embedder.openai import OpenAIEmbedder, OpenAIEmbedderConfig

        return OpenAIEmbedder(
            config=OpenAIEmbedderConfig(
                api_key="ollama",  # Ollama ignores the key but the field is required
                base_url=f"{mem_settings.ollama_embedder_base_url}/v1",
                embedding_model=mem_settings.ollama_embedder_model,
                embedding_dim=mem_settings.ollama_embedder_dim,
            )
        )

    from graphiti_core.embedder.gemini import GeminiEmbedder, GeminiEmbedderConfig

    return GeminiEmbedder(
        config=GeminiEmbedderConfig(
            api_key=gemini_key,
            embedding_model=mem_settings.gemini_embedding_model,
        )
    )


def _build_single_llm_client(
    provider: Any, mem_settings: MemorySettings, llm_settings: Any, *, gemini_key: str
) -> Any:
    """Build one Graphiti LLM client for a single provider id ("ollama" | "gemini")."""
    if getattr(provider, "value", provider) == "ollama":
        from graphiti_core.llm_client.config import LLMConfig as GenericLLMConfig
        from graphiti_core.llm_client.openai_generic_client import OpenAIGenericClient

        return OpenAIGenericClient(
            config=GenericLLMConfig(
                api_key="ollama",
                base_url=f"{mem_settings.ollama_embedder_base_url}/v1",
                model=llm_settings.ollama_model,
            )
        )

    from graphiti_core.llm_client.gemini_client import GeminiClient, LLMConfig

    return GeminiClient(config=LLMConfig(api_key=gemini_key))


def _wrap_llm_fallback(clients: list[Any]) -> Any:
    """Wrap ordered Graphiti LLM clients so each is tried on failure.

    Graphiti accepts a single ``llm_client``; this gives the memory entity-extraction
    LLM the same provider fallback as the app's LlmClient (``llm.fallback_order``).

    Delegation happens at the PUBLIC ``generate_response`` level (not the protected
    ``_generate_response``) because concrete clients override ``generate_response`` to
    unpack the ``(response, input_tokens, output_tokens)`` tuple and record token
    usage — bypassing that override would return the raw tuple and break Graphiti
    downstream. Each attempt gets a deep copy of the messages because
    ``generate_response`` mutates them in place (schema injection, etc.). Defined
    lazily because graphiti_core is an optional import for this module.
    """
    from graphiti_core.llm_client.client import LLMClient
    from graphiti_core.llm_client.config import ModelSize

    class _FallbackLLMClient(LLMClient):
        def __init__(self, inner: list[Any]) -> None:
            super().__init__(config=inner[0].config, cache=False)
            self._inner = inner

        def set_tracer(self, tracer: Any) -> None:
            super().set_tracer(tracer)
            for client in self._inner:
                client.set_tracer(tracer)

        async def _generate_response(self, *args: Any, **kwargs: Any) -> Any:
            # Never called — generate_response is overridden — but required by the ABC.
            raise NotImplementedError

        async def generate_response(
            self,
            messages: Any,
            response_model: Any = None,
            max_tokens: int | None = None,
            model_size: Any = ModelSize.medium,
            group_id: str | None = None,
            prompt_name: str | None = None,
            *,
            attribute_extraction: bool = False,
        ) -> Any:
            last_exc: Exception | None = None
            for client in self._inner:
                try:
                    # Fresh copy: generate_response mutates messages (schema injection).
                    msgs = [m.model_copy(deep=True) for m in messages]
                    resp = await client.generate_response(
                        msgs,
                        response_model,
                        max_tokens,
                        model_size,
                        group_id,
                        prompt_name,
                        attribute_extraction=attribute_extraction,
                    )
                    # Confirm the shape constructs the expected model so a provider that
                    # returns a malformed response falls through to the next one.
                    if response_model is not None:
                        response_model.model_validate(resp)
                    return resp
                except Exception as exc:
                    last_exc = exc
                    logger.warning(
                        "memory_llm_provider_failed",
                        provider=type(client).__name__,
                        error=str(exc),
                    )
            assert last_exc is not None  # at least one client always present
            raise last_exc

    return _FallbackLLMClient(clients)


def build_llm_client(mem_settings: MemorySettings, llm_settings: Any, *, gemini_key: str) -> Any:
    """Build the Graphiti entity-extraction LLM following ``llm.fallback_order``.

    One provider → that client; multiple → a fallback wrapper that tries each in
    order. Decoupled from ``embedder_provider`` (generation and embedding are
    independent), mirroring the app's own provider fallback.
    """
    clients = [
        _build_single_llm_client(p, mem_settings, llm_settings, gemini_key=gemini_key)
        for p in llm_settings.fallback_order
    ]
    return clients[0] if len(clients) == 1 else _wrap_llm_fallback(clients)


# ── cross-encoder (reranker) ─────────────────────────────────────────────────


def _build_single_cross_encoder(
    provider: Any, mem_settings: MemorySettings, llm_settings: Any, *, gemini_key: str
) -> Any:
    """Build one Graphiti reranker for a single provider id ("gemini" | "ollama").

    Both are LLM-as-reranker clients (not true cross-encoders): gemini scores 0-100
    directly; ollama reuses ``llm.ollama_model`` via OpenAIRerankerClient (logprob-
    limited, hence a last-resort fallback only). Neither needs a dedicated model pull.
    """
    from graphiti_core.llm_client.config import LLMConfig

    if getattr(provider, "value", provider) == "ollama":
        from graphiti_core.cross_encoder.openai_reranker_client import OpenAIRerankerClient

        return OpenAIRerankerClient(
            config=LLMConfig(
                api_key="ollama",  # Ollama ignores the key but the field is required
                base_url=f"{mem_settings.ollama_embedder_base_url}/v1",
                model=llm_settings.ollama_model,
            )
        )

    from graphiti_core.cross_encoder.gemini_reranker_client import GeminiRerankerClient

    return GeminiRerankerClient(config=LLMConfig(api_key=gemini_key))


def _wrap_cross_encoder_fallback(clients: list[Any]) -> Any:
    """Wrap ordered Graphiti rerankers so each is tried on failure.

    Graphiti accepts a single ``cross_encoder``; this gives reranking the same
    provider fallback as the LLM (``cross_encoder_order``) — e.g. gemini primary,
    ollama only when gemini's ``rank`` raises. Defined lazily because graphiti_core
    is an optional import for this module.
    """
    from graphiti_core.cross_encoder.client import CrossEncoderClient

    class _FallbackCrossEncoder(CrossEncoderClient):
        def __init__(self, inner: list[Any]) -> None:
            self._inner = inner

        async def rank(self, query: str, passages: list[str]) -> list[tuple[str, float]]:
            last_exc: Exception | None = None
            for client in self._inner:
                try:
                    return await client.rank(query, passages)
                except Exception as exc:
                    last_exc = exc
                    logger.warning(
                        "memory_reranker_provider_failed",
                        provider=type(client).__name__,
                        error=str(exc),
                    )
            assert last_exc is not None  # at least one client always present
            raise last_exc

    return _FallbackCrossEncoder(clients)


def build_cross_encoder(mem_settings: MemorySettings, llm_settings: Any, *, gemini_key: str) -> Any:
    """Build the Graphiti reranker following ``cross_encoder_order``.

    One provider → that client; multiple → a fallback wrapper that tries each in
    order. Always explicit so Graphiti never falls back to its default
    ``OpenAIRerankerClient`` (which requires a real ``OPENAI_API_KEY``).
    """
    clients = [
        _build_single_cross_encoder(p, mem_settings, llm_settings, gemini_key=gemini_key)
        for p in mem_settings.cross_encoder_order
    ]
    return clients[0] if len(clients) == 1 else _wrap_cross_encoder_fallback(clients)


# ── MemoryProvider ───────────────────────────────────────────────────────────


class MemoryProvider:
    """Lifespan singleton that builds GraphitiMemory or degrades to NullMemory."""

    name = "memory"

    @asynccontextmanager
    async def build(self, settings: Any) -> AsyncGenerator[NullMemory | GraphitiMemory, None]:
        mem_settings: MemorySettings = settings.memory

        if not mem_settings.enabled:
            logger.info("memory_disabled")
            yield NullMemory()
            return

        try:
            from graphiti_core import Graphiti
        except ImportError:
            logger.warning("memory_graphiti_not_installed")
            yield NullMemory()
            return

        graphiti = None
        try:
            # Read Neo4j credentials from the already-resolved Vault singleton
            # (secret/memory is in the worker's required_paths).
            vault = settings._container.vault_client
            creds = vault.get_secret(mem_settings.neo4j_vault_path)
            neo4j_user = creds.get("username", "neo4j")
            neo4j_password = creds.get("password", "")
            neo4j_uri = creds.get("uri", mem_settings.neo4j_uri)

            # The Gemini key (secret/llm) may be needed by the embedder, the
            # cross-encoder, and/or the LLM fallback — fetch it once if any use gemini.
            gemini_key = ""
            if _needs_gemini(mem_settings, settings.llm):
                gemini_key = vault.get_secret(settings.llm.gemini_vault_path).get("api_key", "")

            # Embedder / LLM / cross-encoder selected per settings (shared with seed_corpus).
            embedder = build_embedder(mem_settings, gemini_key=gemini_key)
            llm_client = build_llm_client(mem_settings, settings.llm, gemini_key=gemini_key)
            cross_encoder = build_cross_encoder(mem_settings, settings.llm, gemini_key=gemini_key)
            logger.info(
                "memory_providers",
                embedder=mem_settings.embedder_provider,
                cross_encoder=",".join(mem_settings.cross_encoder_order),
            )

            graphiti = Graphiti(
                uri=neo4j_uri,
                user=neo4j_user,
                password=neo4j_password,
                llm_client=llm_client,
                embedder=embedder,
                cross_encoder=cross_encoder,
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
