"""T026 — both-providers matrix: a required gate failing on either provider → not_certifiable."""

from __future__ import annotations

import pytest

from backend.domain.eval import (
    FreezeVerdict,
    GateKind,
    GateProviderDim,
    GateResult,
    GateSpec,
    RunMode,
)


def _spec_per_provider(name: str, providers: list[str]) -> GateSpec:
    return GateSpec(
        name=name,
        description="test",
        kind=GateKind.required,
        provider_dim=GateProviderDim.per_provider,
        threshold={"pass_rate": 1.0},
        providers=providers,
    )


async def _pass_for(spec: GateSpec, provider: str | None = None) -> GateResult:
    return GateResult(
        gate=spec.name, kind=spec.kind, provider=provider,
        score=1.0, threshold=spec.threshold, passed=True,
        blocking=True, evidence="ok",
    )


async def _fail_for_gemini(spec: GateSpec, provider: str | None = None) -> GateResult:
    passed = provider != "gemini"
    return GateResult(
        gate=spec.name, kind=spec.kind, provider=provider,
        score=1.0 if passed else 0.0,
        threshold=spec.threshold, passed=passed,
        blocking=True, evidence="gemini fail" if not passed else "ok",
    )


@pytest.mark.asyncio
async def test_both_providers_pass_yields_certifiable():
    from backend.eval.harness import run_harness

    specs = [_spec_per_provider("triage", ["gemini", "ollama"])]
    registry = {"triage": _pass_for}
    report = await run_harness(
        specs, registry, run_mode=RunMode.freeze,
        providers=["gemini", "ollama"], commit_sha="abc",
    )
    assert report.verdict == FreezeVerdict.certifiable
    triage_results = [r for r in report.gate_results if r.gate == "triage"]
    assert len(triage_results) == 2
    assert all(r.passed for r in triage_results)


@pytest.mark.asyncio
async def test_fail_on_gemini_yields_not_certifiable():
    from backend.eval.harness import run_harness

    specs = [_spec_per_provider("triage", ["gemini", "ollama"])]
    registry = {"triage": _fail_for_gemini}
    report = await run_harness(
        specs, registry, run_mode=RunMode.freeze,
        providers=["gemini", "ollama"], commit_sha="abc",
    )
    assert report.verdict == FreezeVerdict.not_certifiable
    failed = [r for r in report.gate_results if not r.passed]
    assert len(failed) == 1
    assert failed[0].provider == "gemini"


@pytest.mark.asyncio
async def test_per_pr_uses_single_provider():
    """per-PR mode uses only providers_per_pr (ollama), not both."""
    from backend.eval.harness import run_harness

    called: list[str] = []

    async def _track(spec: GateSpec, provider: str | None = None) -> GateResult:
        called.append(provider or "none")
        return GateResult(
            gate=spec.name, kind=spec.kind, provider=provider,
            score=1.0, threshold=spec.threshold, passed=True,
            blocking=True, evidence="ok",
        )

    specs = [_spec_per_provider("triage", ["gemini", "ollama"])]
    registry = {"triage": _track}
    await run_harness(
        specs, registry, run_mode=RunMode.per_pr,
        providers=["ollama"], commit_sha="abc",
    )
    assert called == ["ollama"], f"Expected only ollama, got {called}"
