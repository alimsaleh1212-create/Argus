"""Eval harness core — load specs, run registry, aggregate EvalReport."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from backend.domain.eval import (
    EvalReport,
    FreezeVerdict,
    GateKind,
    GateProviderDim,
    GateResult,
    GateSpec,
    RationaleScore,
    RunMode,
)

Runner = Callable[[GateSpec, str | None], Awaitable[GateResult]]


async def run_harness(
    specs: list[GateSpec],
    registry: dict[str, Runner],
    *,
    run_mode: RunMode,
    providers: list[str],
    commit_sha: str,
    git_tag: str | None = None,
    rationale: list[RationaleScore] | None = None,
    extra: dict[str, Any] | None = None,
) -> EvalReport:
    """Run all declared gates and return an aggregated EvalReport."""
    gate_results: list[GateResult] = []

    for spec in specs:
        runner = registry[spec.name]
        if spec.provider_dim == GateProviderDim.per_provider:
            # Run once per provider in the current run's provider set
            gate_providers = [p for p in providers if not spec.providers or p in spec.providers]
            if not gate_providers:
                gate_providers = providers
            tasks = [runner(spec, p) for p in gate_providers]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for r in results:
                if isinstance(r, BaseException):
                    gate_results.append(GateResult(
                        gate=spec.name,
                        kind=spec.kind,
                        score=0.0,
                        threshold=spec.threshold,
                        passed=None,
                        blocking=spec.kind == GateKind.required,
                        evidence=f"runner raised: {r}",
                    ))
                else:
                    gate_results.append(r)
        else:
            try:
                result = await runner(spec, None)
                gate_results.append(result)
            except Exception as e:
                gate_results.append(GateResult(
                    gate=spec.name,
                    kind=spec.kind,
                    score=0.0,
                    threshold=spec.threshold,
                    passed=None,
                    blocking=spec.kind == GateKind.required,
                    evidence=f"runner raised: {e}",
                ))

    verdict = _aggregate_verdict(gate_results, run_mode)
    summary = _summarize(gate_results)

    return EvalReport(
        run_id=str(uuid.uuid4()),
        run_mode=run_mode,
        commit_sha=commit_sha,
        git_tag=git_tag,
        created_at=datetime.now(UTC),
        providers=providers,
        gate_results=gate_results,
        rationale=rationale,
        verdict=verdict,
        summary=summary,
    )


def _aggregate_verdict(results: list[GateResult], run_mode: RunMode) -> FreezeVerdict:
    for r in results:
        if r.kind == GateKind.required:
            if r.passed is False:
                return FreezeVerdict.not_certifiable
            if r.passed is None and run_mode in (RunMode.freeze, RunMode.nightly):
                return FreezeVerdict.incomplete
        elif r.kind == GateKind.reported_only:
            # catastrophic floor breach: blocking=True promoted from the runner
            if r.blocking and r.passed is False:
                return FreezeVerdict.not_certifiable
    return FreezeVerdict.certifiable


def _summarize(results: list[GateResult]) -> dict[str, int]:
    passed = failed = reported = unknown = 0
    for r in results:
        if r.passed is None:
            unknown += 1
        elif r.kind == GateKind.reported_only:
            reported += 1
        elif r.passed:
            passed += 1
        else:
            failed += 1
    return {"passed": passed, "failed": failed, "reported": reported, "unknown": unknown}
