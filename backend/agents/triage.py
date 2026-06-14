"""Triage stage handler — one structured-output LLM call, fail-closed."""

from __future__ import annotations

import json

from backend.domain.incident import Incident
from backend.domain.llm import LlmError, LlmErrorKind, LlmMessage, LlmRequest
from backend.domain.pipeline import StageHandler, StageName, StageOutcome, StageResult, ToolError
from backend.domain.triage import TriageJudgment, TriageVerdict

# ---------------------------------------------------------------------------
# Schema — passed as response_schema to the adapter (first validation layer)
# ---------------------------------------------------------------------------

TRIAGE_JUDGMENT_SCHEMA: dict = {
    "type": "object",
    "required": ["verdict", "confidence", "rationale", "cited_evidence"],
    "properties": {
        "verdict": {"type": "string", "enum": ["real", "noise", "uncertain"]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "assessed_severity": {"type": "string", "enum": ["low", "medium", "high", "critical"]},
        "rationale": {"type": "string", "minLength": 1},
        "cited_evidence": {"type": "array", "items": {"type": "string"}, "minItems": 1},
    },
}

# ---------------------------------------------------------------------------
# System prompt (v1) — pinned by cfg.prompt_version
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT_V1 = """\
You are a junior SOC analyst reviewing an alert that an upstream detector has already flagged.
Your task is NOT to re-decide from general knowledge whether the indicator is malicious — it is
to judge, based solely on the supplied evidence, whether this specific incident is real, noise,
or uncertain.

Rules:
1. Reason only over the supplied evidence fields. Do not use background knowledge to override them.
2. Your verdict must be one of: real, noise, uncertain.
   - real: the evidence supports a genuine, actionable threat.
   - noise: the evidence indicates a false positive or benign activity.
   - uncertain: the evidence is insufficient to call either way — prefer this over guessing.
3. Cite at least one specific evidence item in cited_evidence.
4. Your confidence must honestly reflect your certainty (0.0-1.0). Do not inflate it.
5. Return ONLY the JSON object matching the required schema — no extra text.
"""

_SYSTEM_PROMPTS: dict[str, str] = {"v1": _SYSTEM_PROMPT_V1}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_request(incident: Incident, cfg: object) -> LlmRequest:
    ev = incident.evidence or {}
    evidence_text = json.dumps(
        {
            "verdict": ev.get("verdict"),
            "severity": ev.get("severity"),
            "normalized_event": ev.get("normalized_event"),
            "summary": ev.get("summary"),
            "retrieved_context": ev.get("retrieved_context") or "none",
        },
        indent=2,
    )
    prompt_version: str = getattr(cfg, "prompt_version", "v1")
    system = _SYSTEM_PROMPTS.get(prompt_version, _SYSTEM_PROMPT_V1)
    return LlmRequest(
        system=system,
        messages=[LlmMessage(role="user", content=evidence_text)],
        response_schema=TRIAGE_JUDGMENT_SCHEMA,
        max_tokens=getattr(cfg, "max_output_tokens", 512),
        temperature=getattr(cfg, "temperature", 0.0),
    )


def _judgment_from_response(response: object) -> TriageJudgment:
    content: str = getattr(response, "content", "")
    data = json.loads(content)
    return TriageJudgment.model_validate(data)


def _tokens(response: object) -> int:
    usage = getattr(response, "usage", None)
    if usage is None:
        return 0
    prompt = getattr(usage, "prompt_tokens", None) or 0
    completion = getattr(usage, "completion_tokens", None) or 0
    return prompt + completion


def decide_outcome(judgment: TriageJudgment, cfg: object) -> tuple[StageOutcome, str | None]:
    """Pure config-threshold-gated outcome mapping. Top-to-bottom precedence."""
    advance_min: float = getattr(cfg, "advance_min_confidence", 0.6)
    resolve_min: float = getattr(cfg, "resolve_min_confidence", 0.7)

    if judgment.verdict == TriageVerdict.UNCERTAIN:
        return StageOutcome.ESCALATE, "escalated_triage"
    if judgment.confidence < advance_min:
        return StageOutcome.ESCALATE, "escalated_triage"
    if judgment.verdict == TriageVerdict.REAL:
        return StageOutcome.ADVANCE, None
    # verdict == NOISE from here
    if judgment.confidence >= resolve_min:
        return StageOutcome.RESOLVED, "auto_resolved_triage"
    return StageOutcome.ESCALATE, "escalated_triage"


# ---------------------------------------------------------------------------
# Error mapping (TD7) — LlmError → ToolError, fail-closed
# ---------------------------------------------------------------------------

_RETRYABLE_KINDS = frozenset({LlmErrorKind.TRANSIENT, LlmErrorKind.EXHAUSTED})


def _map_llm_error(exc: LlmError) -> ToolError:
    if exc.kind in _RETRYABLE_KINDS:
        return ToolError(retryable=True, kind=f"llm_{exc.kind.value}")
    return ToolError(retryable=False, kind=f"llm_{exc.kind.value}")


# ---------------------------------------------------------------------------
# Factory (DI by closure)
# ---------------------------------------------------------------------------


def make_triage_handler(llm: object, cfg: object) -> StageHandler:
    """Return a StageHandler closed over the LlmClient and TriageSettings."""

    async def run_triage(incident: Incident) -> StageResult:
        try:
            request = _build_request(incident, cfg)
            response = await llm.generate(  # type: ignore[union-attr]
                request, correlation_id=incident.correlation_id
            )
        except LlmError as exc:
            raise _map_llm_error(exc) from exc
        except Exception as exc:
            raise ToolError(retryable=False, kind="llm_unexpected") from exc

        try:
            judgment = _judgment_from_response(response)
        except Exception as exc:
            raise ToolError(retryable=False, kind="malformed_output") from exc

        outcome, disposition = decide_outcome(judgment, cfg)
        tokens = _tokens(response)
        note = (f"verdict={judgment.verdict} conf={judgment.confidence:.2f}: {judgment.rationale}")[
            :200
        ]

        return StageResult(
            stage=StageName.TRIAGE,
            outcome=outcome,
            tokens_consumed=tokens,
            disposition=disposition,
            evidence_patch={"triage": judgment.model_dump(mode="json")},
            note=note,
        )

    return run_triage
