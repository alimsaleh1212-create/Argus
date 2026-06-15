"""T007 — unit tests for the gate registry orphan/stale guard (FR-002)."""

from __future__ import annotations

import pytest

from backend.domain.eval import GateKind, GateProviderDim, GateSpec
from backend.eval.gates import GATE_REGISTRY, RegistryMismatchError, validate_registry


def _spec(name: str) -> GateSpec:
    return GateSpec(
        name=name,
        description="test",
        kind=GateKind.required,
        provider_dim=GateProviderDim.provider_independent,
        threshold={"pass_rate": 1.0},
    )


async def _fake_runner(spec: GateSpec, provider: str | None = None):
    raise NotImplementedError


def test_validate_registry_passes_when_equal():
    orig = dict(GATE_REGISTRY)
    try:
        GATE_REGISTRY.clear()
        GATE_REGISTRY["gate_a"] = _fake_runner
        specs = [_spec("gate_a")]
        validate_registry(specs)  # should not raise
    finally:
        GATE_REGISTRY.clear()
        GATE_REGISTRY.update(orig)


def test_orphan_gate_raises():
    """Gate declared in yaml but no runner → RegistryMismatchError."""
    orig = dict(GATE_REGISTRY)
    try:
        GATE_REGISTRY.clear()
        specs = [_spec("orphan_gate")]
        with pytest.raises(RegistryMismatchError, match="orphan"):
            validate_registry(specs)
    finally:
        GATE_REGISTRY.clear()
        GATE_REGISTRY.update(orig)


def test_stale_runner_raises():
    """Runner registered but not declared in yaml → RegistryMismatchError."""
    orig = dict(GATE_REGISTRY)
    try:
        GATE_REGISTRY.clear()
        GATE_REGISTRY["stale_runner"] = _fake_runner
        specs: list[GateSpec] = []
        with pytest.raises(RegistryMismatchError, match="stale"):
            validate_registry(specs)
    finally:
        GATE_REGISTRY.clear()
        GATE_REGISTRY.update(orig)


def test_mismatch_error_names_both():
    """When both orphan and stale exist, the error message names both."""
    orig = dict(GATE_REGISTRY)
    try:
        GATE_REGISTRY.clear()
        GATE_REGISTRY["stale_runner"] = _fake_runner
        specs = [_spec("orphan_gate")]
        with pytest.raises(RegistryMismatchError) as exc:
            validate_registry(specs)
        msg = str(exc.value)
        assert "orphan" in msg
        assert "stale" in msg
    finally:
        GATE_REGISTRY.clear()
        GATE_REGISTRY.update(orig)
