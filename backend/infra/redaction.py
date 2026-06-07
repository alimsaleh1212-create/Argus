"""Redaction seam — sanitize sensitive data before it leaves a trust boundary.

RESERVED SEAM. Interface only; implementation lands in SPEC-observability (#2)
and is reused by SPEC-ingestion (#4) on the alert intake path.

Redaction runs at three boundaries, all behind this one interface:
  1. logs            — secrets / PII never written to structured logs
  2. LLM prompts     — packet payloads / Wazuh fields scrubbed before the model
  3. stored snapshots — incident snapshots scrubbed before MinIO/Postgres

The implementation composes TWO strategies (Wazuh/packet data carries both):
  - a deterministic secret/credential scrubber (regex + entropy; always on) for
    API keys, JWTs, bearer tokens, private keys — NOT covered by PII tooling;
  - Microsoft Presidio for PII entities (IP, email, credit card, IBAN, phone…),
    run in-process by default (hot path); a Presidio sidecar is the escape hatch.

Determinism (Constitution IV): Presidio NER may be disabled for deterministic
paths, leaving pattern-only detection; this is a #2 configuration concern.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Redactor(Protocol):
    """Sanitizes text/mappings; returns a redacted copy. Never mutates input."""

    def redact_text(self, text: str) -> str: ...

    def redact_mapping(self, data: dict) -> dict: ...


def get_redactor() -> Redactor:
    """Return the configured Redactor. Implemented in SPEC-observability (#2)."""
    raise NotImplementedError(
        "Redaction is a reserved seam; implemented in SPEC-observability (#2)."
    )
