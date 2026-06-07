# Contract — Redaction Interface & Policy

**Feature**: `002-observability-redaction` | Consumed by: ingestion (#4, alert intake), memory (#6,
episode writes), the agents (#8/#9/#10, prompts + recorded I/O), dashboard (#12), eval (#13, the gate).

Implements the reserved `backend/infra/redaction.py` Protocol. The binding rules are FR-001–FR-007 and
the FR-006a/b scope decision resolved during `/speckit-specify`.

---

## Interface

```python
class Redactor(Protocol):
    def redact_text(self, text: str, boundary: Boundary) -> str: ...
    def redact_mapping(self, data: dict, boundary: Boundary) -> dict: ...
```

- **Idempotent** (FR-004): redacting already-redacted content (placeholders) is a no-op.
- **Recursive** (FR-004): traverses nested mappings/lists at any depth; preserves structure so output
  stays parseable; never mutates the input (returns a copy).
- **Fail-closed** (FR-003): if detection/anonymization raises, return the
  `fail_closed_placeholder` for the affected field — **never** the raw value.

## Class × Boundary policy (the decision, FR-006a/b)

| Class | LOG | TRACE | PROMPT | SNAPSHOT | DASHBOARD | MEMORY_WRITE | OPERATIONAL |
|-------|:---:|:-----:|:------:|:--------:|:---------:|:------------:|:-----------:|
| `CREDENTIAL` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `PII` | ✅ | ✅ | ✅ | ✅ | ✅ | — | — |
| `OPERATIONAL_IDENTIFIER` | ✅ | ✅ | ✅ | ✅ | ✅ | — | — |

- **Credentials are scrubbed everywhere**, including the operational object and memory writes —
  nothing downstream needs a raw credential (FR-006a).
- **Operational identifiers** (IP/host/user) are **kept raw internally** (`MEMORY_WRITE`,
  `OPERATIONAL`) so enrichment (#9) can correlate, and **redacted at every output boundary** (FR-006b).
- Invariant (tested): the `CREDENTIAL` row is fully ✅; the two internal columns are empty for non-credentials.

## Detection strategies (FR-001, FR-005)

| Strategy | Catches | Determinism |
|----------|---------|-------------|
| **Secret scrubber** (always on) | regex pattern set (AWS keys, bearer/JWT, PEM blocks, `secret=`/`token=` kv) **+** Shannon-entropy heuristic for unmatched high-entropy tokens | deterministic |
| **Presidio** (toggle via settings) | PII entities — email, IP, credit card, IBAN, phone, person (NER) | recognizers deterministic; NER may be disabled on deterministic paths |

## Contract tests (must exist)

- Each seeded fake **credential** is redacted at **all** boundaries incl. `MEMORY_WRITE`/`OPERATIONAL` (unit).
- Each seeded **PII** value is redacted at output boundaries but a raw **IP/hostname** survives at
  `OPERATIONAL`/`MEMORY_WRITE` and is redacted at `LOG`/`PROMPT`/etc. (unit).
- Nested payload: a secret three levels deep is redacted; structure preserved; re-redaction is a no-op (unit).
- A high-entropy token matching no explicit pattern is still flagged by the entropy heuristic (unit).
- Redactor raising on a field yields `[REDACTION-FAILED]`, not the raw value (unit).
- The redaction **eval gate** in `config/eval_thresholds.yaml` fails CI if any seeded secret/PII appears
  unredacted in a captured log/trace/snapshot (integration; the gate's harness is #13).
