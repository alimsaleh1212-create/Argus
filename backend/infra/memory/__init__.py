"""Temporal memory layer — Graphiti on Neo4j 5.26.

graphiti_core and neo4j are imported ONLY from within this package (store/builders/provider).
Consumers depend on the MemoryStore Protocol (domain/memory.py); swapping to the decided pgvector
fallback is a config-toggle change, not a rewrite.

The implementation is split across the package modules for readability; this module re-exports the
stable public + test/seed-facing surface so `backend.infra.memory.<name>` keeps working:
  - store    — NullMemory + GraphitiMemory (the MemoryStore implementations)
  - builders — Graphiti embedder / LLM / cross-encoder factories (shared with seed_corpus)
  - provider — MemoryProvider lifespan singleton
"""

from __future__ import annotations

from backend.infra.memory.builders import (
    _needs_gemini,
    build_cross_encoder,
    build_embedder,
    build_llm_client,
)
from backend.infra.memory.provider import MemoryProvider
from backend.infra.memory.store import GraphitiMemory, NullMemory

__all__ = [
    "GraphitiMemory",
    "MemoryProvider",
    "NullMemory",
    "_needs_gemini",
    "build_cross_encoder",
    "build_embedder",
    "build_llm_client",
]
