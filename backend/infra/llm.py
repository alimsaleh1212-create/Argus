"""LLM provider seam — reserved.

RESERVED SEAM. Implemented in SPEC-llm-provider (#3). Wraps the model client
behind a provider-agnostic interface so the "passes on both providers" eval
dimension (Constitution II) and structural tool-gating (Constitution III) are
enforced here — triage is handed a client with no action tools.
"""

from __future__ import annotations

from typing import Any


class LlmProvider:
    """Provider-agnostic LLM client provider. Implemented in SPEC-llm-provider (#3)."""

    name = "llm"

    def build(self, settings: Any) -> Any:
        raise NotImplementedError(
            "LLM client is a reserved seam; implemented in SPEC-llm-provider (#3)."
        )
