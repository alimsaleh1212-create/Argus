"""Response stage handler — stub (#10 replaces this with playbook + approval logic).

Returns RESOLVED (auto_remediated) normally, or NEEDS_APPROVAL when the incident's
evidence flags contain "destructive" — so the awaiting_approval park is exercisable
in e2e before the real response agent lands. No LLM call.
"""

from __future__ import annotations

from backend.domain.incident import Incident
from backend.domain.pipeline import StageName, StageOutcome, StageResult


async def run_response(incident: Incident) -> StageResult:
    flags: list[str] = (incident.evidence or {}).get("flags", [])
    if "destructive" in flags:
        return StageResult(stage=StageName.RESPONSE, outcome=StageOutcome.NEEDS_APPROVAL, tokens_consumed=0)
    return StageResult(
        stage=StageName.RESPONSE,
        outcome=StageOutcome.RESOLVED,
        disposition="auto_remediated",
        tokens_consumed=0,
    )
