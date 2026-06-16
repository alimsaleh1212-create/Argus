"""T005 — unit tests for the threshold yaml loader.

Tests must FAIL before thresholds.py is implemented (TDD).
"""

from __future__ import annotations

import pathlib

import pytest

from backend.domain.eval import GateKind, GateProviderDim

YAML_PATH = pathlib.Path("config/eval_thresholds.yaml")


def test_load_specs_returns_all_declared_gates():
    from backend.eval.thresholds import load_specs

    specs = load_specs(YAML_PATH)
    names = {s.name for s in specs}
    # All seven seeded gates must be present
    assert {
        "smoke",
        "redaction",
        "supervisor_routing",
        "llm_provider",
        "triage",
        "retrieval",
        "temporal_memory",
    }.issubset(names), f"Missing gates in {names}"


def test_required_gates_have_kind_required():
    from backend.eval.thresholds import load_specs

    specs = load_specs(YAML_PATH)
    by_name = {s.name: s for s in specs}
    for name in (
        "smoke",
        "redaction",
        "supervisor_routing",
        "triage",
        "retrieval",
        "temporal_memory",
    ):
        assert by_name[name].kind == GateKind.required, f"{name} should be required"


def test_per_provider_gates_have_correct_dim():
    from backend.eval.thresholds import load_specs

    specs = load_specs(YAML_PATH)
    by_name = {s.name: s for s in specs}
    # llm_provider and triage have check_per_provider: true / providers list
    assert by_name["llm_provider"].provider_dim == GateProviderDim.per_provider
    assert by_name["triage"].provider_dim == GateProviderDim.per_provider


def test_provider_independent_gates_have_correct_dim():
    from backend.eval.thresholds import load_specs

    specs = load_specs(YAML_PATH)
    by_name = {s.name: s for s in specs}
    for name in ("smoke", "redaction", "supervisor_routing", "retrieval", "temporal_memory"):
        assert by_name[name].provider_dim == GateProviderDim.provider_independent, (
            f"{name} should be provider-independent"
        )


def test_rationale_gate_is_reported_only():
    """After T035 adds the rationale block, it must parse as reported_only."""
    from backend.eval.thresholds import load_specs

    specs = load_specs(YAML_PATH)
    by_name = {s.name: s for s in specs}
    if "rationale" not in by_name:
        pytest.skip("rationale gate not yet in yaml (added in T035)")
    assert by_name["rationale"].kind == GateKind.reported_only


def test_gate_spec_has_threshold_block():
    from backend.eval.thresholds import load_specs

    specs = load_specs(YAML_PATH)
    for spec in specs:
        assert isinstance(spec.threshold, dict), f"{spec.name} missing threshold block"


def test_unknown_shape_raises():
    """A yaml entry missing a threshold key should raise ValueError."""
    import io

    import yaml as pyyaml

    from backend.eval.thresholds import load_specs_from_dict

    bad = pyyaml.safe_load(
        io.StringIO("""
version: "1"
gates:
  bad_gate:
    description: "missing required fields"
""")
    )
    with pytest.raises((ValueError, KeyError)):
        load_specs_from_dict(bad)
