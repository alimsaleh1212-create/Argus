"""LLM-judge for rationale quality scoring (SPEC-eval #13, US4).

Pinned to the cloud primary (Gemini) per R6/EvalSettings.judge_provider.
Every input (incident_context + rationale_text) passes through Redactor first (FR-014).
Returns one of: "grounded", "partially_grounded", "ungrounded".

Structured-output prompt: the judge is given the incident context and the rationale
to score; it responds with the ordinal label only.  cites_supplied_evidence is a
separate boolean check (true iff the rationale references only facts present in the
provided context).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

_SYSTEM_PROMPT = """\
You are a calibrated security-analyst evaluator.
You will be given an incident context and a rationale text written by an AI analyst.
Your task: score the rationale on this ordinal scale.

SCALE:
- grounded          : the rationale references specific facts from the supplied incident context
                      and draws a conclusion that follows from those facts.
- partially_grounded: the rationale mentions some context facts but also makes unsupported claims
                      or logical leaps not justified by the supplied evidence.
- ungrounded        : the rationale does not reference the supplied incident context or contradicts it.

Respond with EXACTLY ONE of the three labels on a single line, no punctuation, no explanation.
"""

_USER_TEMPLATE = """\
INCIDENT CONTEXT:
{context}

RATIONALE TO SCORE:
{rationale}

Label (grounded / partially_grounded / ungrounded):"""

_VALID_LABELS = {"grounded", "partially_grounded", "ungrounded"}


async def judge_rationale(
    incident_context: str,
    rationale_text: str,
    *,
    llm_client,
    redactor,
) -> str:
    """Score one rationale. Returns a RationaleLabel string."""
    from backend.domain.redaction import Boundary

    safe_context = redactor.redact_text(incident_context, Boundary.PROMPT)
    safe_rationale = redactor.redact_text(rationale_text, Boundary.PROMPT)

    prompt = _USER_TEMPLATE.format(context=safe_context, rationale=safe_rationale)
    response = await llm_client.generate(
        system=_SYSTEM_PROMPT,
        user=prompt,
        max_tokens=16,
    )
    raw = response.content.strip().lower().replace("-", "_")
    if raw in _VALID_LABELS:
        return raw
    # Fallback: if model returns something unexpected, default to partially_grounded
    # (conservative — avoids falsely inflating the grounded_rate)
    return "partially_grounded"


async def judge_cites_evidence(
    incident_context: str,
    rationale_text: str,
    *,
    llm_client,
    redactor,
) -> bool:
    """Check whether the rationale cites only supplied evidence. Returns bool."""
    from backend.domain.redaction import Boundary

    safe_context = redactor.redact_text(incident_context, Boundary.PROMPT)
    safe_rationale = redactor.redact_text(rationale_text, Boundary.PROMPT)

    system = "You are a calibrated evaluator. Answer only YES or NO."
    prompt = (
        f"Does the following rationale cite ONLY facts that are explicitly present "
        f"in the supplied incident context, with no external knowledge?\n\n"
        f"INCIDENT CONTEXT:\n{safe_context}\n\n"
        f"RATIONALE:\n{safe_rationale}\n\n"
        f"Answer YES or NO:"
    )
    response = await llm_client.generate(system=system, user=prompt, max_tokens=4)
    raw = response.content.strip().upper()
    return raw.startswith("Y")
