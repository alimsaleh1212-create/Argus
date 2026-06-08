"""Enrichment stage handler — stub (#9 replaces this with context-retrieval logic).

Returns ADVANCE so full-depth incidents proceed to the response stage. No LLM call.
"""

from __future__ import annotations

from backend.domain.incident import Incident
from backend.domain.pipeline import StageName, StageOutcome, StageResult


async def run_enrichment(incident: Incident) -> StageResult:
    return StageResult(stage=StageName.ENRICHMENT, outcome=StageOutcome.ADVANCE, tokens_consumed=0)
