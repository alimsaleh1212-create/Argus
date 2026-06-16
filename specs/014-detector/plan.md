# Implementation Plan: Deterministic Rule/Threshold Detector

**Branch**: `014-detector` | **Date**: 2026-06-16 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/014-detector/spec.md`

## Summary

A **deterministic, decoupled detection source** (Constitution IV — no LLM, no ML) that reads replayed
raw events, applies a config-backed rule/threshold set, and **fires alerts in the existing `#4`
ingestion contract** so they flow through the unchanged triage→enrichment→response pipeline. Emission is
**in-process via the existing `services/intake.accept()`** seam (which already does
redact→dedup→persist→enqueue) with one backward-compatible change: `accept()` gains a `source: str =
"wazuh"` parameter so detector-originated incidents are tagged `source="detector"` (FR-006). A new
deterministic **`detection`** eval gate (precision/recall on a labeled replay set) is declared in
`eval_thresholds.yaml` **and** registered in the gate registry together. Backend-only, **no migration**
(the `Incident` schema is unchanged; `source` is already free text), mirroring the closure-factory DI +
pure-domain patterns of #9/#15 and the one-shot command pattern of #8 (`seed-corpus`).

## Technical Context

**Language/Version**: Python 3.12 (`uv`-managed)

**Primary Dependencies**: Pydantic v2 (domain types + `DetectorSettings`), `pyyaml` (rule set + labeled
replay fixtures — already a direct dep via #13), async SQLAlchemy + Redis via the existing
`intake.accept()` path. **No new runtime dependency. No LLM/ML libraries.**

**Storage**: None new. Reuses Postgres `incidents` (via `IncidentRepository`) and Redis (dedup/queue)
**through `intake.accept()`** — no schema change, no migration.

**Testing**: `pytest` three-tier (unit/integration/e2e) via `scripts/run-tests.sh` / `make test-*`
(never one big `pytest` — spaCy+graphiti OOM); eval via `python -m backend.eval --gate detection`.

**Target Platform**: Linux container (the existing one-image-many-containers backend; the detector is a
one-shot command `python -m backend.detector`, same venv as API/worker/migrate).

**Project Type**: Backend-only extension (no `frontend/` change).

**Performance Goals**: Detection is sub-millisecond per rule per event (in-memory matching); a replay run
is a one-shot batch, not on the synchronous incident path. No latency budget concern.

**Constraints**: Zero downstream change (FR-013); deterministic/reproducible (no LLM, no wall-clock
nondeterminism — threshold windows use `event_time`, not now()); graceful skip on malformed events
(FR-009); replay-safe via the existing dedup fingerprint (FR-008).

**Scale/Scope**: A small seed rule set (~4–6 rules: signature + threshold) and a labeled replay fixture
(tens of events) sufficient to gate precision/recall and run the demo. Not a production rule corpus.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|---|---|---|
| **I — Spec-Driven Delivery (≤400-line PRs)** | ✅ (milestone split) | Ships as **M-a** (detector core + `intake` `source` param + unit/integration) then **M-b** (`detection` eval gate + fixtures + e2e) to keep each PR ≤~400 lines. |
| **II — Test-First, Three-Tier, Eval-Gated** | ✅ | New deterministic `detection` gate declared in yaml **and** registered as a runner *together* (orphan check is a hard error, #13). Provider-independent (no LLM in the detector); the e2e pipeline it feeds still runs on both providers. |
| **III — Security Boundaries** | ✅ | No change to the triage-no-action-tools DI boundary. Detector-originated alerts are **redacted by the reused `intake.accept()` SNAPSHOT boundary**. Detector input (raw events) is attacker-influenceable like alert text, but guardrails are **v3b-deferred (VD1)**; **no injection-coverage claim is made**. No new write authority beyond creating incidents — exactly what an alert source does. |
| **IV — Determinism First; No LLM** | ✅ (strongly aligned) | Pure rule/threshold matching, no LLM, no ML. This *is* the enumerable core the constitution reserves for determinism. |
| **V — Human-in-the-Loop** | ✅ (N/A) | Detector does not act; it only fires alerts. |
| **VI — Temporal Memory & Graceful Degradation** | ✅ (N/A here) | Detector writes no facts. (`016-M2` will later tune the detector — out of scope.) Graceful skip on malformed events. |
| **VII — Production Engineering Standards** | ✅ | Async (`intake.accept` is async), closure-factory DI (`make_detector_runner`), Pydantic at every boundary, `DetectorSettings` with `extra="forbid"`, `uv`. |

**Result: PASS — no violations.** Complexity Tracking left empty.

**Scope-discipline note (constitution lines 149–165).** The constitution lists "an ML anomaly detector"
as out of scope and places the **rule/threshold detector at T3 (Days 11–12)** — this component *is* that
T3 item. No constitution amendment is needed for `014` (it is deterministic). The ML anomaly detector
(now planned as **#17**) is the item that will need a `DECISIONS.md` entry + constitution note — not this.

## Project Structure

### Documentation (this feature)

```text
specs/014-detector/
├── plan.md              # This file
├── research.md          # Phase 0 — resolves the 2 deferred decisions + others
├── data-model.md        # Phase 1 — DetectionRule / RawEvent / FiredAlert
├── quickstart.md        # Phase 1 — run the detector + the detection gate
├── contracts/
│   ├── detector-rules-contract.md     # rule grammar + emission contract
│   └── detection-eval.md              # precision/recall gate contract
└── tasks.md             # Phase 2 (/speckit-tasks — NOT created here)
```

### Source Code (repository root)

```text
backend/
├── domain/
│   └── detector.py            # NEW — pure types: DetectionRule (Match|Threshold), RawEvent, FiredAlert
├── services/
│   ├── detector.py            # NEW — pure evaluate(events, rules) -> list[FiredAlert]; map FiredAlert -> WazuhAlert
│   └── intake.py              # EDIT (backward-compatible) — accept(..., source: str = "wazuh")
├── detector.py                # NEW — one-shot runner: python -m backend.detector (closure-factory DI)
├── infra/
│   └── config.py              # EDIT — add DetectorSettings(extra="forbid") + register on Settings
├── data/
│   └── detector/
│       └── rules.yaml         # NEW — config-backed seed rule set (signature + threshold)
└── eval/
    └── gates/
        └── detection.py       # NEW — `detection` precision/recall gate + GATE_REGISTRY registration

config/
└── eval_thresholds.yaml       # EDIT — add `detection` gate block (declared + registered together)

tests/
├── unit/
│   ├── test_detector_rules.py        # NEW — match/threshold logic, malformed-skip, multi-match→highest sev
│   └── test_detector_mapping.py      # NEW — FiredAlert -> WazuhAlert contract
├── integration/
│   └── test_detector_emit.py         # NEW — detector -> intake.accept -> incident persisted/enqueued; source="detector"; replay-safe dedup
├── e2e/
│   └── test_detector_e2e.py          # NEW — replayed event detected -> full pipeline terminal
└── fixtures/
    └── detector/
        ├── rules.yaml                # test rule set
        └── replay/scenarios.json     # labeled replay events (malicious/benign + threshold groups)
```

**Structure Decision**: Backend-only extension. The detector is a **one-shot command**
(`python -m backend.detector`) — the same shape as #8's `seed-corpus` — built with closure-factory DI
(`make_detector_runner(...)`, mirroring #9/#15). Detection logic is a **pure** `services/detector.py`
function (testable without I/O); emission reuses `services/intake.accept()`. No new image, no migration,
no new top-level layer.

## Complexity Tracking

> No Constitution Check violations — section intentionally empty.
