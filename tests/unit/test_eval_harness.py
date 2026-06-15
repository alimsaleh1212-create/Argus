"""T009 — unit tests for the eval harness aggregation and verdict logic."""

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


def _spec(name: str, kind: GateKind = GateKind.required,
          dim: GateProviderDim = GateProviderDim.provider_independent,
          providers: list[str] | None = None) -> GateSpec:
    return GateSpec(
        name=name,
        description="test",
        kind=kind,
        provider_dim=dim,
        threshold={"pass_rate": 1.0},
        providers=providers or [],
    )


def _result(gate: str, passed: bool | None, kind: GateKind = GateKind.required,
            provider: str | None = None) -> GateResult:
    return GateResult(
        gate=gate,
        kind=kind,
        provider=provider,
        score=1.0 if passed else 0.0,
        threshold={"pass_rate": 1.0},
        passed=passed,
        blocking=kind == GateKind.required,
        evidence="test",
    )


async def _pass_runner(spec: GateSpec, provider: str | None = None) -> GateResult:
    return _result(spec.name, True, spec.kind, provider)


async def _fail_runner(spec: GateSpec, provider: str | None = None) -> GateResult:
    return _result(spec.name, False, spec.kind, provider)


async def _unknown_runner(spec: GateSpec, provider: str | None = None) -> GateResult:
    return _result(spec.name, None, spec.kind, provider)


@pytest.mark.asyncio
async def test_all_required_pass_yields_certifiable():
    from backend.eval.harness import run_harness

    specs = [_spec("gate_a"), _spec("gate_b")]
    registry = {"gate_a": _pass_runner, "gate_b": _pass_runner}
    report = await run_harness(specs, registry, run_mode=RunMode.per_pr, providers=["ollama"],
                               commit_sha="abc123")
    assert report.verdict == FreezeVerdict.certifiable
    assert report.summary["passed"] == 2
    assert report.summary["failed"] == 0


@pytest.mark.asyncio
async def test_required_fail_yields_not_certifiable():
    from backend.eval.harness import run_harness

    specs = [_spec("gate_a"), _spec("gate_b")]
    registry = {"gate_a": _pass_runner, "gate_b": _fail_runner}
    report = await run_harness(specs, registry, run_mode=RunMode.per_pr, providers=["ollama"],
                               commit_sha="abc123")
    assert report.verdict == FreezeVerdict.not_certifiable
    assert report.summary["failed"] == 1


@pytest.mark.asyncio
async def test_required_unknown_at_freeze_yields_incomplete():
    from backend.eval.harness import run_harness

    specs = [_spec("gate_a")]
    registry = {"gate_a": _unknown_runner}
    report = await run_harness(specs, registry, run_mode=RunMode.freeze, providers=["ollama"],
                               commit_sha="abc123")
    assert report.verdict == FreezeVerdict.incomplete
    assert report.summary["unknown"] == 1


@pytest.mark.asyncio
async def test_reported_only_failure_does_not_block():
    from backend.eval.harness import run_harness

    specs = [_spec("gate_req"), _spec("gate_reported", kind=GateKind.reported_only)]
    registry = {"gate_req": _pass_runner, "gate_reported": _fail_runner}
    report = await run_harness(specs, registry, run_mode=RunMode.per_pr, providers=["ollama"],
                               commit_sha="abc123")
    assert report.verdict == FreezeVerdict.certifiable
    assert report.summary["reported"] == 1


@pytest.mark.asyncio
async def test_reported_only_unknown_does_not_block():
    from backend.eval.harness import run_harness

    specs = [_spec("gate_req"), _spec("gate_reported", kind=GateKind.reported_only)]
    registry = {"gate_req": _pass_runner, "gate_reported": _unknown_runner}
    report = await run_harness(specs, registry, run_mode=RunMode.per_pr, providers=["ollama"],
                               commit_sha="abc123")
    assert report.verdict == FreezeVerdict.certifiable


@pytest.mark.asyncio
async def test_report_has_correct_run_metadata():
    from backend.eval.harness import run_harness

    specs = [_spec("gate_a")]
    registry = {"gate_a": _pass_runner}
    report = await run_harness(specs, registry, run_mode=RunMode.freeze, providers=["gemini", "ollama"],
                               commit_sha="deadbeef", git_tag="v1.0.0")
    assert report.run_mode == RunMode.freeze
    assert report.commit_sha == "deadbeef"
    assert report.git_tag == "v1.0.0"
    assert set(report.providers) == {"gemini", "ollama"}
    assert report.schema_version == "1"


@pytest.mark.asyncio
async def test_per_provider_gate_runs_once_per_provider():
    """A per_provider gate must produce one GateResult per provider."""
    from backend.eval.harness import run_harness

    calls: list[str] = []

    async def counting_runner(spec: GateSpec, provider: str | None = None) -> GateResult:
        calls.append(provider or "none")
        return _result(spec.name, True, spec.kind, provider)

    specs = [_spec("triage_gate", dim=GateProviderDim.per_provider, providers=["gemini", "ollama"])]
    registry = {"triage_gate": counting_runner}
    report = await run_harness(specs, registry, run_mode=RunMode.freeze, providers=["gemini", "ollama"],
                               commit_sha="abc123")
    assert sorted(calls) == ["gemini", "ollama"]
    assert len([r for r in report.gate_results if r.gate == "triage_gate"]) == 2
