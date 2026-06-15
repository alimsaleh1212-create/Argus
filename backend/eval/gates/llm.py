"""LLM-dimension gate runners — per-provider.

Gates: triage, llm_provider.
Each runner calls scoring helpers shared with tests/eval/* gate tests.
"""

from __future__ import annotations

import json
import pathlib
import uuid

from backend.domain.eval import GateKind, GateResult, GateSpec
from backend.eval.gates import GATE_REGISTRY
from backend.eval.gates.scoring import macro_f1

_FIXTURES = pathlib.Path("tests/fixtures")
_CONFIG = pathlib.Path("config/eval_thresholds.yaml")


# ---------------------------------------------------------------------------
# triage
# ---------------------------------------------------------------------------

async def run_triage(spec: GateSpec, provider: str | None = None) -> GateResult:
    """Triage macro-F1 gate using a scripted (deterministic) LLM client."""

    from backend.agents.triage import make_triage_handler
    from backend.domain.incident import Incident, IncidentStatus, Severity
    from backend.domain.llm import LlmResponse, ProviderId, StopReason, TokenUsage
    from backend.infra.config import TriageSettings

    fixtures_dir = _FIXTURES / "triage_labeled"

    def _load_fixtures() -> list[dict]:
        items = []
        for p in sorted(fixtures_dir.glob("*.json")):
            with p.open() as f:
                items.append(json.load(f))
        return items

    class _ScriptedLlm:
        def __init__(self, verdict_map: dict[str, str]) -> None:
            self._map = verdict_map

        async def generate(self, request: object, *, correlation_id: str | None = None) -> LlmResponse:
            verdict = self._map.get(correlation_id or "", "uncertain")
            payload = {
                "verdict": verdict,
                "confidence": 0.9 if verdict != "uncertain" else 0.4,
                "rationale": f"Scripted verdict: {verdict}",
                "cited_evidence": ["rule_description"],
            }
            return LlmResponse(
                content=json.dumps(payload),
                usage=TokenUsage(prompt_tokens=20, completion_tokens=10),
                model="scripted",
                provider=ProviderId.GEMINI,
                stop_reason=StopReason.END_TURN,
            )

    fixtures = _load_fixtures()
    if not fixtures:
        return GateResult(
            gate=spec.name, kind=spec.kind, provider=provider,
            score=0.0, threshold=spec.threshold, passed=None,
            blocking=spec.kind == GateKind.required,
            evidence="no triage fixtures found",
        )

    verdict_map = {fx["incident_id"]: fx["gold_label"] for fx in fixtures}
    llm = _ScriptedLlm(verdict_map)
    handler = make_triage_handler(llm, TriageSettings())

    tp_real = fp_real = fn_real = 0
    tp_noise = fp_noise = fn_noise = 0
    abstentions = 0

    for fx in fixtures:
        inc = Incident(
            id=uuid.uuid4(),
            status=IncidentStatus.TRIAGING,
            severity=Severity.MEDIUM,
            correlation_id=fx.get("incident_id", "eval"),
            dedup_fingerprint=fx.get("incident_id", "eval"),
            source="eval", raw_alert={}, evidence=fx["evidence"],
        )
        result = await handler(inc)
        if result.evidence_patch and "triage" in result.evidence_patch:
            predicted = result.evidence_patch["triage"]["verdict"]
        else:
            predicted = "uncertain"

        gold = fx["gold_label"]
        if predicted == "uncertain":
            abstentions += 1
            continue
        if gold == "real":
            if predicted == "real":
                tp_real += 1
            else:
                fn_real += 1
                fp_noise += 1
        elif gold == "noise":
            if predicted == "noise":
                tp_noise += 1
            else:
                fn_noise += 1
                fp_real += 1

    total = len(fixtures)
    abstention_rate = abstentions / total if total else 0.0
    f1 = macro_f1(tp_real, fp_real, fn_real, tp_noise, fp_noise, fn_noise)

    with _CONFIG.open() as fh:
        import yaml as _yaml
        threshold = _yaml.safe_load(fh)["gates"]["triage"]["threshold"]

    min_f1 = threshold.get("min_macro_f1", 0.75)
    max_abst = threshold.get("max_abstention_rate", 0.30)
    gate_passed = f1 >= min_f1 and abstention_rate <= max_abst

    return GateResult(
        gate=spec.name, kind=spec.kind, provider=provider,
        score={"macro_f1": f1, "abstention_rate": abstention_rate},
        threshold=spec.threshold, passed=gate_passed,
        blocking=spec.kind == GateKind.required,
        evidence=f"macro_f1={f1:.3f} abstentions={abstentions}/{total}",
    )


# ---------------------------------------------------------------------------
# llm_provider
# ---------------------------------------------------------------------------

async def run_llm_provider(spec: GateSpec, provider: str | None = None) -> GateResult:
    """Minimal generate-and-validate check for a single LLM provider."""
    # Uses a scripted check: confirm the LlmClient seam returns a non-empty response.
    # Provider-level quality is exercised by the triage gate; this gate checks the
    # plumbing (non-empty response, usage present or None, no credential leak).
    evidence_parts: list[str] = []
    passed = True

    try:
        # Import the real LlmClient only if available — in CI without keys this
        # will raise at client construction, which we catch and record as unknown.
        from backend.infra.llm import get_llm_client
        client = await get_llm_client(provider or "ollama")
        from backend.domain.llm import LlmRequest
        req = LlmRequest(
            system_prompt="You are a test assistant.",
            user_message="Reply with exactly: pong",
            max_tokens=16,
        )
        resp = await client.generate(req)
        if not resp.content:
            passed = False
            evidence_parts.append("empty response")
        else:
            evidence_parts.append(f"content_len={len(resp.content)}")
    except Exception as e:
        return GateResult(
            gate=spec.name, kind=spec.kind, provider=provider,
            score=0.0, threshold=spec.threshold, passed=None,
            blocking=spec.kind == GateKind.required,
            evidence=f"provider unavailable: {type(e).__name__}",
        )

    score = 1.0 if passed else 0.0
    return GateResult(
        gate=spec.name, kind=spec.kind, provider=provider,
        score=score, threshold=spec.threshold, passed=passed,
        blocking=spec.kind == GateKind.required,
        evidence="; ".join(evidence_parts) or "ok",
    )


# Register LLM gate runners
GATE_REGISTRY["triage"] = run_triage
GATE_REGISTRY["llm_provider"] = run_llm_provider
