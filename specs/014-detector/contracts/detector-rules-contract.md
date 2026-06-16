# Contract: Detector Rule Set + Emission

**Owner**: `014-detector`. **Consumers**: the existing ingestion path (`#4`), unchanged.

## 1. Rule-set config (`backend/data/detector/rules.yaml`)

Config-backed (FR-005). Path resolved from `DetectorSettings.rules_path`. Shape:

```yaml
lists:
  ioc_ips: ["203.0.113.10", "198.51.100.7"]
  ioc_hashes: ["e3b0c44298fc1c149afbf4c8996fb924..."]
rules:
  - id: ioc-match
    kind: match
    description: "Indicator on known-bad IOC list"
    field: data.src_ip
    op: in_list
    list_ref: ioc_ips
    severity: high
  - id: malicious-cmd
    kind: match
    description: "Process command-line matches known-malicious pattern"
    field: data.command_line
    op: regex
    value: "(?i)(mimikatz|-enc\\s+[A-Za-z0-9+/=]{40,})"
    severity: high
    technique: T1059
  - id: failed-login-bruteforce
    kind: threshold
    description: "Repeated authentication failures from one source"
    match: { field: data.event_type, op: equals, value: auth_failure }
    group_by: data.src_ip
    count: 5
    window_seconds: 60
    severity: high
    technique: T1110
  - id: connection-fanout
    kind: threshold
    description: "Connection fan-out / port scan from one source"
    match: { field: data.event_type, op: equals, value: connection }
    group_by: data.src_ip
    count: 20
    window_seconds: 30
    severity: medium
    technique: T1046
```

**Invariants.**
- Unknown top-level keys → `ValidationError` (settings `extra="forbid"` discipline).
- `op: in_list` ⇒ `list_ref` present and defined under `lists`; `op` in {equals,contains,regex} ⇒
  `value` present. Invalid `regex` fails at load (fail-fast), not per event.
- Empty `rules` ⇒ detector runs and emits nothing (no crash) — Edge Case.

## 2. `DetectorSettings` (new section, `extra="forbid"`)

Added to `backend/infra/config.py` and registered as `detector` on the `Settings` aggregate.

| Field | Type / default | Purpose |
|---|---|---|
| `enabled` | `bool = True` | gate the one-shot runner |
| `rules_path` | `str = "backend/data/detector/rules.yaml"` | rule set source |
| `replay_path` | `str \| None = None` | labeled/raw replay event source (JSON list) |
| `max_events` | `int = 10_000` (`gt=0`) | safety cap on a replay run |
| `source_tag` | `str = "detector"` | value passed to `intake.accept(source=...)` |

## 3. Emission contract (the load-bearing "zero downstream change" rule)

- The detector emits **only** through `services/intake.accept(*, alert: WazuhAlert, source: str, ...)`.
- `intake.accept` gains `source: str = "wazuh"` (backward-compatible; webhook caller unchanged). Detector
  passes `source=settings.detector.source_tag` (`"detector"`) ⇒ `Incident.source == "detector"` (FR-006),
  distinguishable in audit/trace from `"wazuh"`.
- Emitted alerts are **redacted** by the existing SNAPSHOT boundary inside `accept()` (Constitution III).
- **Replay-safety / idempotency** is the existing dedup fingerprint — the detector adds no second dedup
  authority (FR-008).
- The detector creates `Incident(received)` rows only; it performs **no** status transitions (supervisor
  stays single writer).

## 4. What this contract does NOT touch

- No change to `WazuhAlert`/`NormalizedEvent`/`Incident` schemas (no migration).
- No new router/endpoint (in-process emission; see research D1).
- No change to the supervisor, agent stages, or any existing eval gate (FR-013).
