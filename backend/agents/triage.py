"""Triage stage handler — one structured-output LLM call, fail-closed.

Optional Jev engine (DECISIONS.md JD2–JD4): `cfg.engine` selects "llm" (v1),
"shadow" (LLM decides, Jev recorded alongside) or "jev" (Jev decides, LLM fallback).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, NamedTuple

from backend.domain.decision import (
    ChoiceAnswer,
    ChoiceQuestion,
    DecisionError,
    DecisionRequest,
    DecisionResponse,
)
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


def _evidence_bundle(incident: Incident) -> dict[str, Any]:
    ev = incident.evidence or {}
    return {
        "verdict": ev.get("verdict"),
        "severity": ev.get("severity"),
        "normalized_event": ev.get("normalized_event"),
        "summary": ev.get("summary"),
        "retrieved_context": ev.get("retrieved_context") or "none",
    }


def _build_request(incident: Incident, cfg: object) -> LlmRequest:
    evidence_text = json.dumps(_evidence_bundle(incident), indent=2)
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


def _split_tokens(response: object) -> tuple[int | None, int | None, str | None]:
    """Return (tokens_in, tokens_out, model) from the LLM response usage, or Nones."""
    usage = getattr(response, "usage", None)
    model = getattr(response, "model", None)
    if usage is None:
        return None, None, model
    return (
        getattr(usage, "prompt_tokens", None),
        getattr(usage, "completion_tokens", None),
        model,
    )


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
# Jev engine (JD2–JD4) — one choice question over the same evidence bundle
# ---------------------------------------------------------------------------

_JEV_QUESTION_ID = "triage_verdict"

JEV_TRIAGE_QUESTION = ChoiceQuestion(
    instructions=(
        "You are reviewing an alert an upstream detector already flagged. Judge, based solely on "
        "the supplied evidence fields, whether this specific incident is real, noise, or "
        "uncertain. Do not use background knowledge to override the evidence; prefer uncertain "
        "over guessing."
    ),
    criteria={
        TriageVerdict.REAL.value: "The evidence supports a genuine, actionable threat.",
        TriageVerdict.NOISE.value: "The evidence indicates a false positive or benign activity.",
        TriageVerdict.UNCERTAIN.value: "The evidence is insufficient to call either way.",
    },
)


class JevTriage(NamedTuple):
    judgment: TriageJudgment
    answer: ChoiceAnswer
    response: DecisionResponse


def _cited_fields(bundle: dict[str, Any]) -> list[str]:
    return [k for k, v in bundle.items() if v not in (None, "", "none", {}, [])] or ["none"]


def _attribution_rationale(answer: ChoiceAnswer, model: str, cited: list[str]) -> str:
    """Deterministic attribution rationale (JD4) — not model-authored reasoning."""
    dist = ", ".join(f"{k}={v:.2f}" for k, v in sorted(answer.probabilities.items()))
    return (
        f"Jev ({model}) classified the supplied evidence as {answer.choice} with confidence "
        f"{answer.confidence:.2f} (probabilities: {dist or 'n/a'}). "
        f"Evidence fields considered: {', '.join(cited)}."
    )


async def _jev_judgment(decision: Any, incident: Incident) -> JevTriage:
    """One Jev call → TriageJudgment. Raises DecisionError on any failure."""
    bundle = _evidence_bundle(incident)
    request = DecisionRequest(
        state=json.dumps(bundle, indent=2, default=str),
        questions={_JEV_QUESTION_ID: JEV_TRIAGE_QUESTION},
    )
    response: DecisionResponse = await decision.decide(
        request, correlation_id=incident.correlation_id
    )
    answer = response.answers[_JEV_QUESTION_ID]
    cited = _cited_fields(bundle)
    judgment = TriageJudgment(
        verdict=TriageVerdict(answer.choice),
        confidence=answer.confidence,
        rationale=_attribution_rationale(answer, response.model, cited),
        cited_evidence=cited,
    )
    return JevTriage(judgment, answer, response)


def _jev_record(mode: str, jev: JevTriage) -> dict[str, Any]:
    return {
        "mode": mode,
        "model": jev.response.model,
        "verdict": jev.answer.choice,
        "confidence": jev.answer.confidence,
        "probabilities": dict(jev.answer.probabilities),
    }


def _error_record(mode: str, exc: BaseException) -> dict[str, Any]:
    kind = exc.kind.value if isinstance(exc, DecisionError) else "unexpected"
    return {"mode": mode, "error": kind}


def _with_patch(result: StageResult, patch: dict[str, Any]) -> StageResult:
    merged = {**(result.evidence_patch or {}), **patch}
    return result.model_copy(update={"evidence_patch": merged})


def _result_from_judgment(
    judgment: TriageJudgment,
    cfg: object,
    *,
    tokens: int,
    tokens_in: int | None,
    tokens_out: int | None,
    model: str | None,
) -> StageResult:
    outcome, disposition = decide_outcome(judgment, cfg)
    note = (f"verdict={judgment.verdict} conf={judgment.confidence:.2f}: {judgment.rationale}")[
        :200
    ]
    return StageResult(
        stage=StageName.TRIAGE,
        outcome=outcome,
        tokens_consumed=tokens,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        llm_model=model,
        disposition=disposition,
        evidence_patch={"triage": judgment.model_dump(mode="json")},
        note=note,
    )


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


def make_triage_handler(llm: object, cfg: object, decision: object | None = None) -> StageHandler:
    """Return a StageHandler closed over the LlmClient, TriageSettings and optional Jev client."""
    engine: str = getattr(cfg, "engine", "llm") if decision is not None else "llm"

    async def run_llm_triage(incident: Incident) -> StageResult:
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

        tokens_in, tokens_out, llm_model = _split_tokens(response)
        return _result_from_judgment(
            judgment,
            cfg,
            tokens=_tokens(response),
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            model=llm_model,
        )

    async def run_jev_triage(incident: Incident) -> StageResult:
        try:
            jev = await _jev_judgment(decision, incident)
        except Exception as exc:
            # Any Jev failure → the v1 LLM path decides (JD3); record why.
            result = await run_llm_triage(incident)
            return _with_patch(
                result, {"triage_jev": {**_error_record("jev", exc), "fallback": "llm"}}
            )
        result = _result_from_judgment(
            jev.judgment,
            cfg,
            tokens=jev.response.total_tokens,
            tokens_in=jev.response.input_tokens,
            tokens_out=jev.response.output_tokens,
            model=jev.response.model,
        )
        return _with_patch(result, {"triage_jev": _jev_record("jev", jev)})

    async def run_shadow_triage(incident: Incident) -> StageResult:
        # LLM stays authoritative; Jev runs concurrently and never affects the outcome (JD3).
        shadow = asyncio.create_task(_jev_judgment(decision, incident))
        try:
            result = await run_llm_triage(incident)
        except BaseException:
            shadow.cancel()
            raise
        try:
            jev = await shadow
        except Exception as exc:
            return _with_patch(result, {"triage_jev": _error_record("shadow", exc)})
        llm_verdict = (result.evidence_patch or {}).get("triage", {}).get("verdict")
        record = {**_jev_record("shadow", jev), "agrees_with_llm": jev.answer.choice == llm_verdict}
        return _with_patch(result, {"triage_jev": record})

    if engine == "jev":
        return run_jev_triage
    if engine == "shadow":
        return run_shadow_triage
    return run_llm_triage
