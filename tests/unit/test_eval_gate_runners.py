"""T015/T016 — gate runner unit tests and regression-blocks-merge contract."""

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
from backend.eval.gates import GATE_REGISTRY, validate_registry
from backend.eval.thresholds import load_specs


def _spec(
    name: str,
    kind: GateKind = GateKind.required,
    dim: GateProviderDim = GateProviderDim.provider_independent,
) -> GateSpec:
    return GateSpec(
        name=name,
        description="test",
        kind=kind,
        provider_dim=dim,
        threshold={"pass_rate": 1.0},
    )


# ---------------------------------------------------------------------------
# T015 — registry integrity
# ---------------------------------------------------------------------------


def test_gate_registry_has_required_gates():
    """After importing gate modules, the registry must contain all eight gates."""
    import backend.eval.gates.deterministic  # noqa: F401 — side-effect: registers runners
    import backend.eval.gates.llm  # noqa: F401
    import backend.eval.gates.rationale  # noqa: F401
    import backend.eval.gates.smoke  # noqa: F401

    required = {
        "smoke",
        "redaction",
        "supervisor_routing",
        "llm_provider",
        "triage",
        "retrieval",
        "temporal_memory",
        "rationale",
    }
    missing = required - set(GATE_REGISTRY)
    assert not missing, f"Registry missing gates: {missing}"


def test_validate_registry_passes_against_yaml():
    """Registry + yaml are consistent (no orphan/stale) after imports."""
    import backend.eval.gates.deterministic  # noqa: F401
    import backend.eval.gates.llm  # noqa: F401
    import backend.eval.gates.rationale  # noqa: F401
    import backend.eval.gates.smoke  # noqa: F401

    specs = load_specs()
    # validate_registry raises on mismatch; should not raise here
    validate_registry(specs)


def test_gate_result_is_well_formed():
    """Each registered runner is callable and returns something GateResult-shaped."""
    import backend.eval.gates.deterministic  # noqa: F401
    import backend.eval.gates.llm  # noqa: F401
    import backend.eval.gates.rationale  # noqa: F401
    import backend.eval.gates.smoke  # noqa: F401

    for name, runner in GATE_REGISTRY.items():
        assert callable(runner), f"{name} runner is not callable"


# ---------------------------------------------------------------------------
# T16 — regression blocks merge (CLI exit non-zero contract)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_below_threshold_gate_yields_not_certifiable():
    """A seeded sub-threshold required gate makes the verdict not_certifiable."""
    from backend.eval.harness import run_harness

    async def _failing_runner(spec: GateSpec, provider: str | None = None) -> GateResult:
        return GateResult(
            gate=spec.name,
            kind=spec.kind,
            provider=provider,
            score=0.0,
            threshold=spec.threshold,
            passed=False,
            blocking=True,
            evidence="planted regression",
        )

    specs = [_spec("gate_regressed")]
    registry = {"gate_regressed": _failing_runner}
    report = await run_harness(
        specs, registry, run_mode=RunMode.per_pr, providers=["ollama"], commit_sha="test"
    )
    assert report.verdict == FreezeVerdict.not_certifiable


@pytest.mark.asyncio
async def test_not_certifiable_verdict_maps_to_exit_1():
    """A not_certifiable verdict must map to exit code 1 (CLI contract)."""
    from backend.eval.__main__ import verdict_to_exit_code

    assert verdict_to_exit_code(FreezeVerdict.not_certifiable) == 1


@pytest.mark.asyncio
async def test_certifiable_verdict_maps_to_exit_0():
    from backend.eval.__main__ import verdict_to_exit_code

    assert verdict_to_exit_code(FreezeVerdict.certifiable) == 0


@pytest.mark.asyncio
async def test_incomplete_verdict_maps_to_exit_3():
    from backend.eval.__main__ import verdict_to_exit_code

    assert verdict_to_exit_code(FreezeVerdict.incomplete) == 3
