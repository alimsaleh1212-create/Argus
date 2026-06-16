# Phase 0 Research: Deterministic Rule/Threshold Detector (#14)

All decisions below resolve the spec's deferred items and the user's two explicitly-named questions
(emission mechanism; concrete rule/threshold-set scope). No `NEEDS CLARIFICATION` remain.

---

## D1 — Emission mechanism: in-process `intake.accept()` vs the existing webhook

**Decision.** The detector emits **in-process** by calling the existing
`backend/services/intake.py::accept(...)` once per fired alert, with a **new backward-compatible
parameter `source: str = "wazuh"`** so detector-originated incidents are tagged `source="detector"`.

**Rationale.**
- `accept()` already performs the full **redact → dedup → persist → enqueue → `IngestResult`** path. The
  detector should *reuse* it, not duplicate it. This keeps emission to the existing contract and gives
  detector alerts redaction (Constitution III) for free via the SNAPSHOT boundary.
- The HTTP webhook (`POST /ingest/wazuh`) is itself just *auth → size guard → validate → `intake.accept`*.
  Routing the detector through HTTP would add a Vault webhook token, a running API, and a network
  round-trip **for zero benefit** in a replay tool, and it would still need a way to set `source` to
  distinguish detector alerts (FR-006).
- The only code touch is **parameterizing `source`** (today hardcoded `source="wazuh"` at
  `intake.py:85`). This is additive and backward-compatible: the webhook caller passes nothing and keeps
  `"wazuh"`. The `Incident.source` column is already free-text — **no schema change, no migration**, and
  no behavior change for existing callers. This satisfies FR-013 ("zero downstream change") in spirit:
  the change is an *additive seam extension*, not a pipeline/schema/eval-gate alteration.

**Alternatives considered.**
- *HTTP POST to `/ingest/wazuh`* — rejected: needs token + running API + round-trip; still needs a
  `source` mechanism; truer "decoupling" but a replay tool gains nothing from the network hop.
- *Detector replicates redact/dedup/persist/enqueue* — rejected: duplicates load-bearing logic, drifts
  from the single ingestion contract, and risks a second dedup authority (violates FR-008).

**Recorded as a micro-decision** for `DECISIONS.md`: "Detector emits via `intake.accept(source=...)`;
`source` parameterized, default `wazuh`, backward-compatible."

---

## D2 — Concrete rule/threshold-set scope (seed set)

**Decision.** A small, config-backed seed set in `backend/data/detector/rules.yaml` with **two rule
kinds** and **~4–6 seed rules** — enough to exercise both signature and threshold paths, label a replay
set, and run the demo. Each rule carries `id`, `description`, `severity` (mapped to the existing
`Severity` enum), and an optional MITRE `technique`.

**Rule grammar (two discriminated kinds).**
- **`match` (signature, single event).** `field` (dotted path into the event), `op`
  (`equals`/`contains`/`regex`/`in_list`), and `value` (or `list_ref` for `in_list`, referencing a
  config-backed list such as an IOC list). Fires one alert per matching event.
- **`threshold` (aggregation, windowed).** A qualifying `match` condition + `group_by` field (e.g.
  `src_ip`) + `count` N + `window_seconds` W (evaluated over `event_time`, **not** wall-clock). Fires
  **one** alert when the Nth qualifying event within W lands for a group.

**Seed rules.**
| id | kind | detects | severity | MITRE |
|---|---|---|---|---|
| `ioc-match` | match `in_list` | event indicator (ip/domain/hash) in config IOC list | high | — |
| `malicious-cmd` | match `regex` | process command-line matches a known-bad pattern (e.g. `mimikatz`, encoded PowerShell) | high | T1059 |
| `failed-login-bruteforce` | threshold | ≥ N auth-failure events from one `src_ip` within W | high | T1110 |
| `connection-fanout` | threshold | ≥ M distinct `dest`/port from one `src_ip` within W (scan) | medium | T1046 |

**Rationale.** Covers the two scenarios the spec prioritizes (US1 signature single-event; US3 threshold
aggregation) plus the suppression case (US2 — benign events match nothing). Small enough to keep the PR
under budget and the replay set hand-labelable; config-backed so a new rule is a YAML edit (SC-005). The
window uses `event_time` to keep the eval **reproducible** (deterministic, no clock dependence).

**Alternatives considered.** Importing the full Wazuh ruleset / Sigma corpus — rejected: massive scope,
needs a rule compiler, and the eval/demo don't need it. Hardcoding rules in Python — rejected (FR-005
requires config-backed).

---

## D3 — Component shape: one-shot command + pure core (mirrors #8/#9/#15)

**Decision.** `python -m backend.detector` is a **one-shot runner** (like #8 `seed-corpus`) built with a
**closure-factory** `make_detector_runner(...)` (like #9/#15). It loads the rule set + replay event
source, calls the **pure** `services/detector.evaluate(events, rules) -> list[FiredAlert]`, maps each
`FiredAlert -> WazuhAlert`, and calls `intake.accept(..., source="detector")`. Domain types live in a
**pure** `backend/domain/detector.py` (no outward imports — domain-isolation import-linter contract).

**Rationale.** Determinism + testability: `evaluate()` is pure and unit-testable with no I/O; the runner
is the only I/O seam and is integration/e2e-tested. Reuses established Argus patterns; no new layer.

---

## D4 — Severity & multi-match resolution

**Decision.** Severity is taken from the matched rule's configured `severity`. When one event matches
multiple rules, the detector emits **one** alert attributed to the **highest-severity** matched rule
(FR-011); ties break by rule order in config. Reuses `Severity` from `domain/incident.py`.

**Rationale.** One event → ≤1 alert keeps precision/recall labeling clean (spec assumption) and avoids
flooding; the existing dedup absorbs genuine repeats.

---

## D5 — Replay source & determinism

**Decision.** Replay events are read from a config-backed JSON file (`detector.replay_path`), a list of
structured event records (`event_time`, fields). Malformed/partial records are **skipped with a logged
warning**, the run continues (FR-009). The detector is a **one-shot batch**, not a standing process
(FR-010); threshold state lives only within a single run (spec assumption).

**Rationale.** Matches the brief's "replayed sample alerts" model and the "no live capture" scope;
file-backed + `event_time`-driven windows make the detection gate fully reproducible.

---

## D6 — `detection` eval gate

**Decision.** A new **deterministic, provider-independent** `detection` gate in
`backend/eval/gates/detection.py`: it runs the labeled replay set through `evaluate()` and computes
**precision and recall** (an event is a true positive when a malicious-labeled event/group produces an
alert; false positive when a benign one does). The gate is **declared in `config/eval_thresholds.yaml`
AND registered in `GATE_REGISTRY` in the same change** (orphan/stale mismatch is a hard error → exit 2,
per #13). Thresholds (precision ≥, recall ≥) committed in the yaml.

**Rationale.** Directly measures SC-002/SC-003; provider-independent like the `feedback`/`temporal_memory`
gates (the detector has no LLM). Extends the suite without duplicating existing gates.

---

## D7 — Out-of-scope confirmations (unchanged from spec)

- No ML/anomaly scoring (that is **#17**, designed-but-deferred).
- No live capture / standing feeds (v3c).
- No `016-M2` feed-to-detector tuning (gated on this + memory).
- No change to `Incident` schema, supervisor FSM, agent stages, or existing eval gates (beyond the
  additive `source` param + the new `detection` gate).
