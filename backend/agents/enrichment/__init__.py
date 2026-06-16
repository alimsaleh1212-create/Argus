"""Enrichment stage handler — bounded retrieval fan-out + one LLM cross-correlation call.

Retrieval-only (Constitution III): reads both directions (external corpus/intel, internal
memory/reputation), correlates them in a single structured-output LLM call, and maps the result to a
StageOutcome. The implementation is split across the package modules for readability; this module
re-exports the stable public + test-facing surface so `backend.agents.enrichment.<name>` keeps
working:
  - queries   — deterministic ReferenceQuery / EntityRef builders
  - context   — retrieval fan-out + reasoning-bundle assembly
  - reasoning — request assembly, validation, outcome + error mapping
  - handler   — the StageHandler factory
"""

from __future__ import annotations

from backend.agents.enrichment.handler import make_enrichment_handler
from backend.agents.enrichment.queries import build_reference_query, extract_entities
from backend.agents.enrichment.reasoning import (
    ENRICHMENT_REPORT_SCHEMA,
    decide_outcome,
)

__all__ = [
    "ENRICHMENT_REPORT_SCHEMA",
    "build_reference_query",
    "decide_outcome",
    "extract_entities",
    "make_enrichment_handler",
]
