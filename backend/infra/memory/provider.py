"""MemoryProvider — lifespan singleton that builds GraphitiMemory or degrades to NullMemory."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from backend.infra.config import MemorySettings
from backend.infra.logging import get_logger
from backend.infra.memory.builders import (
    _needs_gemini,
    build_cross_encoder,
    build_embedder,
    build_llm_client,
)
from backend.infra.memory.store import GraphitiMemory, NullMemory

logger = get_logger(__name__)


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
