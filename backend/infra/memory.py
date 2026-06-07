"""Temporal memory seam (Neo4j / Graphiti) — reserved.

RESERVED SEAM. Implemented in SPEC-memory (#6), which adds the Neo4j service to
compose and a driver provider into the registry. Uses the pgvector extension
already available in the Postgres image for the documented
Graphiti → valid_from/valid_to graceful-degradation fallback (Constitution VI).
"""

from __future__ import annotations

from typing import Any


class MemoryProvider:
    """Neo4j driver / Graphiti client provider. Implemented in SPEC-memory (#6)."""

    name = "memory"

    def build(self, settings: Any) -> Any:
        raise NotImplementedError(
            "Temporal memory is a reserved seam; implemented in SPEC-memory (#6)."
        )
