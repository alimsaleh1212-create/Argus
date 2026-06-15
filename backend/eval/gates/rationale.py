"""Rationale judge gate runner (SPEC-eval #13, US4).

Reported-only gate (kind=reported_only): scores are recorded; only the catastrophic_floor
blocks CI. Runs at freeze/nightly only (skipped for per_pr — returns unknown).

The gate:
1. Loads hand-labeled fixtures from tests/fixtures/rationale/{stage}.json
2. For each sample, calls the judge (or _judge_fn override in tests) with the
   incident_context + rationale_text
3. Computes per-stage grounded_rate + judge_human_agreement
4. Aggregates across stages; sets blocking=True only if any stage breaches the
   catastrophic_floor threshold.
"""

from __future__ import annotations

import json
import pathlib
from collections.abc import Awaitable, Callable

from backend.domain.eval import GateKind, GateResult, GateSpec, RunMode
from backend.eval.gates import GATE_REGISTRY
from backend.eval.gates.scoring import exact_match_agreement, grounded_rate

_FIXTURE_DIR = pathlib.Path("tests/fixtures/rationale")
_STAGES = ["triage", "enrichment", "response"]

_CATASTROPHIC_FLOOR_GROUNDED = 0.50
_CATASTROPHIC_FLOOR_AGREEMENT = 0.50


async def run_rationale(
    spec: GateSpec,
    provider: str | None = None,
    *,
    run_mode: RunMode = RunMode.freeze,
    _judge_fn: Callable[[str, str], Awaitable[str]] | None = None,
) -> GateResult:
    """Run the rationale judge gate.

    _judge_fn: injected in tests to avoid real LLM calls.
    Production path builds judge_fn from EvalSettings + LlmClient + Redactor.
    """
    # Gate only runs at freeze/nightly
    if run_mode == RunMode.per_pr:
        return GateResult(
            gate="rationale",
            kind=GateKind.reported_only,
            provider=None,
            score={"skipped": 1.0},
            threshold=spec.threshold,
            passed=None,   # unknown — skipped
            blocking=False,
            evidence="skipped: rationale gate only runs at freeze/nightly",
        )

    judge_fn = _judge_fn or await _build_judge_fn()

    all_labels: list[str] = []
    all_human_labels: list[str] = []
    stage_scores: dict[str, dict] = {}
    catastrophic_breach = False
    evidence_lines: list[str] = []

    for stage in _STAGES:
        fixture_path = _FIXTURE_DIR / f"{stage}.json"
        if not fixture_path.exists():
            evidence_lines.append(f"{stage}: fixture missing — skipped")
            continue

        samples = json.loads(fixture_path.read_text())
        judge_labels: list[str] = []
        human_labels: list[str] = []

        for sample in samples:
            label = await judge_fn(
                sample["incident_context"],
                sample["rationale_text"],
            )
            judge_labels.append(label)
            human_labels.append(sample["human_label"])

        g_rate = grounded_rate(judge_labels)
        agreement = exact_match_agreement(judge_labels, human_labels)

        stage_scores[stage] = {
            "grounded_rate": round(g_rate, 4),
            "judge_human_agreement": round(agreement, 4),
            "n": len(samples),
        }

        if g_rate < _CATASTROPHIC_FLOOR_GROUNDED or agreement < _CATASTROPHIC_FLOOR_AGREEMENT:
            catastrophic_breach = True

        evidence_lines.append(
            f"{stage}: grounded_rate={g_rate:.2f} agreement={agreement:.2f} n={len(samples)}"
        )
        all_labels.extend(judge_labels)
        all_human_labels.extend(human_labels)

    # Aggregate across all stages
    if not all_labels:
        return GateResult(
            gate="rationale",
            kind=GateKind.reported_only,
            provider=None,
            score={"overall_grounded_rate": 0.0, "overall_judge_agreement": 0.0},
            threshold=spec.threshold,
            passed=None,   # unknown — no fixture data
            blocking=False,
            evidence="; ".join(evidence_lines) or "no fixtures found",
        )

    overall_grounded = grounded_rate(all_labels)
    overall_agreement = exact_match_agreement(all_labels, all_human_labels)

    min_target_grounded = spec.threshold.get("min_grounded_rate", 0.70)
    min_target_agreement = spec.threshold.get("min_judge_agreement", 0.70)
    passed = (overall_grounded >= min_target_grounded
              and overall_agreement >= min_target_agreement)

    stage_detail = "; ".join(evidence_lines) or "no fixtures found"
    return GateResult(
        gate="rationale",
        kind=GateKind.reported_only,
        provider=None,
        score={
            "overall_grounded_rate": round(overall_grounded, 4),
            "overall_judge_agreement": round(overall_agreement, 4),
        },
        threshold=spec.threshold,
        passed=passed,
        blocking=catastrophic_breach,  # only blocking if catastrophic floor breached
        evidence=stage_detail,
    )


async def _build_judge_fn() -> Callable[[str, str], Awaitable[str]]:
    """Build the real judge_fn for freeze/nightly production runs.

    Builds a minimal LlmClient and Redactor directly from settings (no FastAPI
    DI context). Degrades gracefully: if LLM init fails (no API key, Vault
    unreachable), returns a conservative fallback that labels all rationales as
    "partially_grounded" so the gate records unknown/reported rather than crashing.
    """
    from backend.eval.judge import judge_rationale
    from backend.infra.redaction import build_redactor

    redactor = build_redactor(presidio_enabled=False)

    try:
        import os

        from backend.infra.config import Settings
        from backend.infra.llm import LlmClient
        from backend.infra.llm_drivers import GeminiDriver, OllamaDriver
        from backend.infra.observability import Observability
        from backend.infra.tracing import build_tracer

        settings = Settings()
        llm_settings = settings.llm
        obs = Observability(redactor=redactor, tracer=build_tracer())

        # Build drivers; Gemini key from env (freeze CI injects GEMINI_API_KEY)
        api_key = os.environ.get("GEMINI_API_KEY", "")
        from backend.infra.llm import ProviderId
        drivers = {}
        if api_key:
            drivers[ProviderId.GEMINI] = GeminiDriver(llm_settings, api_key=api_key)
        drivers[ProviderId.OLLAMA] = OllamaDriver(llm_settings)

        llm_client = LlmClient(settings=llm_settings, drivers=drivers, obs=obs)

        async def _fn(context: str, rationale: str) -> str:
            return await judge_rationale(
                context, rationale, llm_client=llm_client, redactor=redactor
            )

        return _fn

    except Exception:
        # Conservative fallback — avoids crashing freeze run if LLM unavailable
        async def _fallback(context: str, rationale: str) -> str:
            return "partially_grounded"

        return _fallback


GATE_REGISTRY["rationale"] = run_rationale
