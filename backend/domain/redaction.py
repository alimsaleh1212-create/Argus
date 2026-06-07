"""Redaction domain types — pure, no outward dependencies.

SensitiveClass × Boundary matrix encodes the FR-006a/006b policy decision:
  - CREDENTIAL: scrubbed at every boundary (no downstream use for raw secrets).
  - PII + OPERATIONAL_IDENTIFIER: redacted at output boundaries only; raw values
    retained in OPERATIONAL/MEMORY_WRITE so the pipeline can correlate on them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class SensitiveClass(StrEnum):
    CREDENTIAL = "credential"
    PII = "pii"
    OPERATIONAL_IDENTIFIER = "operational_identifier"


class Boundary(StrEnum):
    # Output boundaries — redact CREDENTIAL + PII + OPERATIONAL_IDENTIFIER
    LOG = "log"
    TRACE = "trace"
    PROMPT = "prompt"
    SNAPSHOT = "snapshot"
    DASHBOARD = "dashboard"
    # Internal boundaries — redact CREDENTIAL only (FR-006a/b)
    MEMORY_WRITE = "memory_write"
    OPERATIONAL = "operational"


_OUTPUT_BOUNDARIES: frozenset[Boundary] = frozenset(
    {Boundary.LOG, Boundary.TRACE, Boundary.PROMPT, Boundary.SNAPSHOT, Boundary.DASHBOARD}
)
_ALL_BOUNDARIES: frozenset[Boundary] = frozenset(Boundary)


@dataclass(frozen=True)
class RedactionPolicy:
    """Centralized class × boundary matrix (FR-006).

    ``rules``: maps each SensitiveClass to the set of Boundaries at which it is
    redacted.  The default encodes the spec decision (FR-006a/b):
      - CREDENTIAL everywhere (no raw credential is ever legitimate downstream).
      - PII and OPERATIONAL_IDENTIFIER only at output boundaries; raw values
        may remain in OPERATIONAL/MEMORY_WRITE for correlation.
    """

    rules: dict[SensitiveClass, frozenset[Boundary]] = field(
        default_factory=lambda: {
            SensitiveClass.CREDENTIAL: _ALL_BOUNDARIES,
            SensitiveClass.PII: _OUTPUT_BOUNDARIES,
            SensitiveClass.OPERATIONAL_IDENTIFIER: _OUTPUT_BOUNDARIES,
        }
    )
    default_placeholder: str = "[REDACTED:{cls}]"
    fail_closed_placeholder: str = "[REDACTION-FAILED]"

    def should_redact(self, cls: SensitiveClass, boundary: Boundary) -> bool:
        return boundary in self.rules.get(cls, frozenset())

    def placeholder_for(self, cls: SensitiveClass) -> str:
        return self.default_placeholder.format(cls=cls.value.upper())


DEFAULT_POLICY: RedactionPolicy = RedactionPolicy()
