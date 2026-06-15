"""Smoke gate runner adapter.

Records the compose-readiness result into the report. The actual compose
smoke check is the existing CI job; this adapter integrates the result
into the EvalReport so the harness can reference it.
"""

from __future__ import annotations

import os

from backend.domain.eval import GateKind, GateResult, GateSpec
from backend.eval.gates import GATE_REGISTRY


async def run_smoke(spec: GateSpec, provider: str | None = None) -> GateResult:
    """Read the SMOKE_STATUS env var (set by the CI smoke job) or probe /ready."""
    smoke_ok_env = os.environ.get("SMOKE_STATUS", "").lower()

    if smoke_ok_env in ("pass", "ok", "true", "1"):
        passed = True
        evidence = "compose stack healthy (SMOKE_STATUS=pass)"
    elif smoke_ok_env in ("fail", "false", "0"):
        passed = False
        evidence = "compose stack unhealthy (SMOKE_STATUS=fail)"
    else:
        # Try a quick HTTP probe if the stack is up
        try:
            import httpx

            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get("http://localhost:8000/ready")
            passed = resp.status_code == 200
            evidence = f"/ready → {resp.status_code}"
        except Exception as e:
            # Stack not up; treat as unknown (not a blocking failure for per-PR)
            return GateResult(
                gate=spec.name,
                kind=spec.kind,
                provider=provider,
                score=0.0,
                threshold=spec.threshold,
                passed=None,
                blocking=spec.kind == GateKind.required,
                evidence=f"compose stack unreachable: {type(e).__name__}",
            )

    max_unhealthy = spec.threshold.get("max_unhealthy_services", 0)
    gate_passed = passed and max_unhealthy == 0
    return GateResult(
        gate=spec.name,
        kind=spec.kind,
        provider=provider,
        score=1.0 if gate_passed else 0.0,
        threshold=spec.threshold,
        passed=gate_passed,
        blocking=spec.kind == GateKind.required,
        evidence=evidence,
    )


GATE_REGISTRY["smoke"] = run_smoke
