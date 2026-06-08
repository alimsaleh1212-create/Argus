"""Triage stage handler — stub (#8 replaces this with a real LLM-backed handler).

Returns ADVANCE so ambiguous incidents proceed to enrichment (exercises the full
spine in e2e before the real agents land). No LLM call.
"""

from __future__ import annotations

from backend.domain.incident import Incident
from backend.domain.pipeline import StageName, StageOutcome, StageResult


async def run_triage(incident: Incident) -> StageResult:
    return StageResult(stage=StageName.TRIAGE, outcome=StageOutcome.ADVANCE, tokens_consumed=0)
