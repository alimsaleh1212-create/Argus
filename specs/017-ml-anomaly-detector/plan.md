# Implementation Plan: ML Anomaly Detection Layer (UEBA-style)

**Branch**: `017-ml-anomaly-detector` | **Date**: 2026-06-16 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/017-ml-anomaly-detector/spec.md`

## Summary

A **second, decoupled detection source** (UEBA-style) that **complements** the deterministic rule detector
(#14). It reads **replayed SIEM logs**, aggregates each entity's activity into a **per-entity time window**
(Q1), scores each window with an **Isolation Forest trained offline on the CERT Insider Threat dataset**
(Q4), derives severity from the anomaly score via **config-backed score→severity bands** (Q2), and **fires
alerts in the existing `#4` ingestion contract** so they flow through the unchanged
triage→enrichment→response pipeline. Emission reuses `services/intake.accept(source="anomaly-detector")` —
whose `source` parameter **already exists** (added by #14), so #17 needs **zero change to `intake`** and
**no migration**. The whole component mirrors #14's shape (one-shot runner + pure `services` core +
closure-factory DI), with the model injected behind a pure `AnomalyModel` Protocol so tests use a
`FakeAnomalyModel` and never load `scikit-learn`. A new **blocking** `anomaly_detection` eval gate (Q3 —
precision/recall + false-positive ceiling, scored deterministically against the committed artifact) is
declared in `eval_thresholds.yaml` **and** registered **and** imported together.

**The one material difference from #14:** this is the project's first **ML at the detection layer**, an
explicit, recorded exception to Constitution IV (and to the v1 Scope-Discipline line) — bounded
(response path stays deterministic), decoupled (no second writer, no new FSM edge), complementary (does not
replace #14). It requires a `DECISIONS.md` entry + a constitution note **before implementation lands**
(research R10; Complexity Tracking below).

## Technical Context

**Language/Version**: Python 3.12 (`uv`-managed)

**Primary Dependencies**: Pydantic v2 (domain types + `AnomalySettings`), **`scikit-learn>=1.5` + `numpy>=1.26`
(NEW runtime deps — Isolation Forest inference)**, `joblib` (artifact load/save, ships with sklearn),
`pyyaml`, async SQLAlchemy + Redis via the existing `intake.accept()` path. **`pandas>=2.2` added as a
dev/training-only group** (offline CERT wrangling in `anomaly_train`; never on the serve path). **No LLM in
the detector.**

**Storage**: None new. Reuses Postgres `incidents` (via `IncidentRepository`) and Redis (dedup/queue)
**through `intake.accept()`** — no schema change, no migration. One committed file artifact
(`backend/data/anomaly/model.joblib`).

**Testing**: `pytest` three-tier (unit/integration/e2e) via `scripts/run-tests.sh` / `make test-*` (never
one big `pytest` — spaCy+graphiti OOM); eval via `python -m backend.eval --gate anomaly_detection`.
Unit/integration inject a `FakeAnomalyModel` (no sklearn load).

**Target Platform**: Linux container — the existing one-image-many-containers backend. Two one-shot
commands (`python -m backend.anomaly_train` offline; `python -m backend.anomaly_detector` replay), same venv
as API/worker/migrate/detector (research R8 — no separate image).

**Project Type**: Backend-only extension (no `frontend/` change; the `source="anomaly-detector"` tag
already surfaces read-side in the existing dashboard incident views — no dashboard code change required).

**Performance Goals**: Inference is a one-shot batch over replayed logs, off the synchronous incident path;
Isolation Forest scoring is milliseconds per window. No latency budget concern.

**Constraints**: Zero downstream change (FR-014); **reproducible** — training pins `random_state`, and the
eval scores the committed artifact deterministically (no retraining in CI, FR-010/SC-008); windows binned by
`event_time` (no wall-clock); fail-closed on a missing model (FR-012); graceful skip on malformed records
(FR-011); replay-safe via the existing dedup fingerprint (FR-013).

**Scale/Scope**: A small Isolation Forest (~KB–low-MB artifact), ~10–20 per-user-day features, and a
downsampled labeled CERT-derived replay fixture (tens of windows) sufficient to gate precision/recall/FP
and run the demo. The full CERT dataset is offline-only and **not committed**.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|---|---|---|
| **I — Spec-Driven Delivery (≤400-line PRs)** | ✅ (milestone split) | Ships as **M-a** (offline training + feature pipeline + model artifact + `AnomalyModel`/infra), **M-b** (replay runner + emission + integration/e2e), **M-c** (`anomaly_detection` eval gate + labeled fixture). Each ≤~400 lines. **M-0** precondition: record the DECISIONS.md + constitution note (below). |
| **II — Test-First, Three-Tier, Eval-Gated** | ✅ | New deterministic `anomaly_detection` gate declared in yaml **and** registered **and** imported together (orphan/stale = hard error, #13). **Blocking** (Q3), justified by deterministic scoring of the saved artifact. Provider-independent (no LLM in the detector); the e2e pipeline it feeds still runs on both providers. |
| **III — Security Boundaries** | ✅ | No change to the triage-no-action-tools DI boundary. Anomaly alerts are **redacted by the reused `intake.accept()` SNAPSHOT boundary**. Detector input (replayed logs) is attacker-influenceable like alert text, but guardrails are **v3b-deferred (VD1)**; **no injection-coverage claim is made**. No new write authority beyond creating `received` incidents — exactly what an alert source does. |
| **IV — Determinism First; No LLM** | ⚠️ **RECORDED EXCEPTION** | This is ML at the **detection** layer — the one principled exception (research R10). Determinism-first is **preserved on the response path** (supervisor stays a deterministic FSM; agents reason only over supplied evidence). The detector is **decoupled** (no second writer, no new FSM edge) and **complements** #14. Requires a `DECISIONS.md` entry + constitution note **before implementation** (see Complexity Tracking). |
| **V — Human-in-the-Loop** | ✅ (N/A) | Detector does not act; it only fires alerts. |
| **VI — Temporal Memory & Graceful Degradation** | ✅ (N/A here) | Detector writes no facts. (A future `016-M2`-analog could tune it — out of scope.) Fail-closed on missing model; graceful skip on malformed records. |
| **VII — Production Engineering Standards** | ✅ | Async (`intake.accept` is async), closure-factory DI (`make_anomaly_runner`), Pydantic at every boundary, `AnomalySettings` with `extra="forbid"`, `uv`, pinned deps. `scikit-learn`/`joblib` confined to `infra` + the train/eval entrypoints (extends the no-bypass import guard). |

**Result: PASS — with one recorded, justified exception (Principle IV / Scope Discipline).** The exception
is explicitly anticipated and authorized by the brief's 2026-06-16 *Detection Strategy Update*; Governance
requires it be recorded (Complexity Tracking), not that it block the plan. All other principles are clean,
and the exception is bounded and decoupled.

**Scope-discipline note (constitution lines 149–165).** The constitution lists "an ML anomaly detector
(roadmap v2a/v3)" as v1-out-of-scope and places it at **T4 (stretch / v3, only with surplus)**. Building it
in-project as **#17** (after #14, per the 2026-06-16 update) is the scope decision the same DECISIONS.md
entry + constitution note must record. This is the item #14's plan flagged would need exactly this
treatment.

## Project Structure

### Documentation (this feature)

```text
specs/017-ml-anomaly-detector/
├── plan.md              # This file
├── research.md          # Phase 0 — R1–R11 (model, dataset, granularity, bands, gate, runtime, exception)
├── data-model.md        # Phase 1 — EntityActivityWindow / FeatureVector / AnomalyModel / AnomalyFinding
├── quickstart.md        # Phase 1 — train, run the detector, run the gate, tests
├── contracts/
│   ├── anomaly-model-contract.md     # AnomalyModel Protocol + AnomalySettings + emission
│   └── anomaly-eval.md               # anomaly_detection precision/recall/FP gate contract
├── checklists/
│   └── requirements.md  # spec quality checklist (from /speckit-specify)
└── tasks.md             # Phase 2 (/speckit-tasks — NOT created here)
```

### Source Code (repository root)

```text
backend/
├── domain/
│   └── anomaly.py              # NEW — pure types: EntityActivityWindow, FeatureVector, AnomalyFinding,
│                               #       ScoreBands, AnomalyModel (Protocol). No outward imports but Severity.
├── services/
│   └── anomaly.py              # NEW — pure: build_windows(events), featurize(window, spec),
│                               #       score_to_severity(score, bands), finding_to_wazuh_alert(finding),
│                               #       load_replay_events(path). NO model object, NO I/O.
├── infra/
│   └── anomaly_model.py        # NEW — SklearnAnomalyModel(AnomalyModel): joblib load + score_samples →
│                               #       [0,1]; fail-closed on missing/unloadable artifact. Owns sklearn.
├── anomaly_train.py            # NEW — offline one-shot: python -m backend.anomaly_train (CERT → features →
│                               #       fit IsolationForest(seed) → save artifact). pandas (dev group).
├── anomaly_detector.py         # NEW — replay one-shot: python -m backend.anomaly_detector
│                               #       (make_anomaly_runner closure-factory DI; mirrors backend/detector.py)
├── infra/
│   └── config.py               # EDIT — add AnomalySettings(extra="forbid") + register `anomaly` on Settings
├── data/
│   └── anomaly/
│       └── model.joblib        # NEW (committed) — trained artifact (IsolationForest + feature_spec + norm)
└── eval/
    ├── gates/
    │   └── anomaly_detection.py  # NEW — gate runner + GATE_REGISTRY registration
    └── __main__.py             # EDIT — import backend.eval.gates.anomaly_detection (register side-effect)

config/
└── eval_thresholds.yaml        # EDIT — add `anomaly_detection` gate block (declared + registered together)

pyproject.toml                  # EDIT — add scikit-learn + numpy (runtime); pandas (dev group);
                                #        extend the import-linter no-bypass guard for scikit-learn

tests/
├── unit/
│   ├── test_anomaly_features.py     # NEW — build_windows / featurize determinism; missing/extra feature handling
│   ├── test_anomaly_bands.py        # NEW — score_to_severity bands + fire_threshold; fail-closed (FakeAnomalyModel)
│   └── test_anomaly_mapping.py      # NEW — AnomalyFinding -> WazuhAlert contract
├── integration/
│   └── test_anomaly_emit.py         # NEW — runner -> intake.accept -> incident persisted/enqueued;
│                                    #       source="anomaly-detector"; replay-safe dedup (FakeAnomalyModel)
├── e2e/
│   └── test_anomaly_e2e.py          # NEW — replayed anomalous window detected -> full pipeline terminal
└── fixtures/
    └── anomaly/
        └── replay/scenarios.jsonl   # NEW — labeled per-entity windows (malicious/normal), CERT-derived slice
```

**Structure Decision**: Backend-only extension, **mirroring #14**. The detector is two **one-shot commands**
(`anomaly_train` offline, `anomaly_detector` replay) — the same shape as #8's `seed-corpus` and #14's
`detector` — built with closure-factory DI (`make_anomaly_runner`). Scoring/feature logic is a **pure**
`services/anomaly.py`; the sklearn model lives behind the `AnomalyModel` Protocol in `infra/anomaly_model.py`
(injected, faked in tests); emission reuses `services/intake.accept()` **unchanged**. No new image, no
migration, no new top-level layer, no new import-linter contract (sklearn confined to `infra`/entrypoints).

## Complexity Tracking

> One Constitution Check item requires a justified, recorded exception (Governance).

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| **Principle IV — ML on the detection layer** (and the Scope-Discipline "ML anomaly detector = v1-out-of-scope / T4-stretch" line) | Catching **novel behavior** (compromised credentials, lateral movement, insider exfil) is exactly where deterministic rules structurally cannot reach; the brief's central detection thesis is that signature + anomaly **layer** to cover each other's blind spots. The 2026-06-16 *Detection Strategy Update* explicitly brings this in-project as #17, after #14. | A purely deterministic detector (#14) was already built and is kept — it is the high-precision baseline. It **cannot** be the simpler alternative *for this capability* because by construction it only fires on enumerated known-bad patterns; novel behavior produces no rule match. The exception is bounded: response path stays deterministic, detector is decoupled (no second writer / no FSM edge), and it complements rather than replaces #14. **Mandatory before implementation: a `DECISIONS.md` entry + a constitution note** recording this time-bound exception (research R10; M-0 precondition task). |
| **New runtime deps: `scikit-learn` + `numpy`** | Isolation Forest inference requires them. | No lighter way to run a real ML model; kept CPU-only/GPU-free (Isolation Forest, not an autoencoder), confined to `infra` + train/eval entrypoints, with `pandas` pushed to a dev-only training group so the serve image stays lean. |
