"""Guardrails seam — reserved.

RESERVED SEAM. Implemented in SPEC-safety (#11). The *structural* security
boundary (triage holds no action tools; response tools gated via the provider
seam + human approval) is already enforced architecturally and does NOT depend
on this module.

This seam is the optional LLM-rail layer on top of that: highest-value piece is
prompt-injection / jailbreak INPUT rails on attacker-controlled incident data.
Implementation choice is deliberately deferred (lightweight by preference):
Llama-Guard-via-LLM-provider, Guardrails AI, a custom input rail, or NeMo
Guardrails as a sidecar. Only the interface is fixed here.

Note: deterministic PII/secret redaction is a SEPARATE, always-on concern — see
``backend.infra.redaction`` (Presidio + secret scrubber). Guardrails ≠ redaction.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class Guardrail(Protocol):
    """Validates/filters model input or output; raises or returns a verdict."""

    async def check_input(self, text: str) -> Any: ...

    async def check_output(self, text: str) -> Any: ...


def get_guardrail() -> Guardrail:
    """Return the configured guardrail. Implemented in SPEC-safety (#11)."""
    raise NotImplementedError("Guardrails are a reserved seam; implemented in SPEC-safety (#11).")
