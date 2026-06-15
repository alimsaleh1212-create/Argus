"""Load config/eval_thresholds.yaml into GateSpec[] (single source of truth, FR-011)."""

from __future__ import annotations

import pathlib
from typing import Any

import yaml

from backend.domain.eval import GateKind, GateProviderDim, GateSpec


def load_specs(path: pathlib.Path | str = "config/eval_thresholds.yaml") -> list[GateSpec]:
    with pathlib.Path(path).open() as f:
        data = yaml.safe_load(f)
    return load_specs_from_dict(data)


def load_specs_from_dict(data: dict[str, Any]) -> list[GateSpec]:
    gates: dict[str, Any] = data.get("gates", {})
    if not gates:
        raise ValueError("eval_thresholds.yaml has no 'gates' section")

    specs: list[GateSpec] = []
    for name, cfg in gates.items():
        if not isinstance(cfg, dict):
            raise ValueError(f"gate '{name}': expected a mapping, got {type(cfg)}")
        if "threshold" not in cfg and name != "rationale":
            raise KeyError(f"gate '{name}': missing 'threshold' block")

        required: bool = cfg.get("required", True)
        kind = GateKind.required if required else GateKind.reported_only

        has_providers = bool(cfg.get("providers"))
        check_per = bool(cfg.get("check_per_provider", False))
        provider_dim = (
            GateProviderDim.per_provider
            if (has_providers or check_per)
            else GateProviderDim.provider_independent
        )

        threshold = cfg.get("threshold", {})
        if not isinstance(threshold, dict):
            threshold = {}

        # For rationale gate: threshold is the entire remaining config
        if name == "rationale" and not threshold:
            threshold = {
                k: v
                for k, v in cfg.items()
                if k
                not in (
                    "description",
                    "required",
                    "run_modes",
                    "judge_provider",
                    "stages",
                    "fixture_dir",
                )
            }

        specs.append(
            GateSpec(
                name=name,
                description=cfg.get("description", ""),
                kind=kind,
                provider_dim=provider_dim,
                threshold=threshold,
                providers=cfg.get("providers", []),
            )
        )
    return specs
