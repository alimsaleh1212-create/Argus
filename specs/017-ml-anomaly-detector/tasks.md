---
description: "Task list for SPEC-ml-anomaly-detector (#17) — UEBA-style ML anomaly detection layer"
---

# Tasks: ML Anomaly Detection Layer (#17)

**Input**: Design documents from `specs/017-ml-anomaly-detector/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: INCLUDED — Constitution II is test-first/three-tier/eval-gated and FR-009 mandates the
**blocking** `anomaly_detection` precision/recall/FP gate. Unit/integration/e2e + the eval gate are
first-class tasks.

**Organization**: Tasks are grouped by user story (P1→P3) for independent implementation/testing, then
mapped to the milestone PRs (M-0 governance precondition → M-a model → M-b fires-into-pipeline → M-c gate),
each ≤~400 lines per Constitution I.

## Lean-structure guardrails (apply to every task)

This is a **backend-only additive extension that mirrors #14** — keep it that way:

- **New files only, NO migration, NO new image, NO new top-level layer / import-linter contract** (per
  plan.md). Code lives in `backend/` across existing layers (DECISIONS.md **AD1**); a standalone `ml/` dir
  is explicitly rejected.
- Reuse existing seams: emission via `services/intake.accept(source=...)` **unchanged** (the `source` param
  already exists from #14); severity↔level via the shared mapping `services/detector.py`/`services/wazuh.py`
  uses; gate registry via `backend/eval/gates/`. Do **not** duplicate redact/dedup/persist/enqueue.
- **One shared labeled fixture set** (`tests/fixtures/anomaly/`) feeds unit, integration, e2e, and the
  `anomaly_detection` gate — do not fork per-test copies.
- `backend/domain/anomaly.py` stays **pure** (no outward import except `Severity`); `services/anomaly.py`
  stays pure (no model object, no I/O). `scikit-learn`/`joblib` are confined to `backend/infra/anomaly_model.py`
  + the `anomaly_train`/eval entrypoints. Unit/integration/e2e inject a `FakeAnomalyModel` — they never load
  sklearn or the real artifact.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependency on an incomplete task)
- **[Story]**: US1 / US2 / US3 (setup, foundational, governance, cross-cutting & polish carry no story label)

---

## Phase 1: Setup & Governance precondition — milestone M-0 / M-a

**Purpose**: Record the constitutional exception (a hard precondition), create the tiny scaffolding, and add
the ML dependencies.

**⚠️ BLOCKING (Governance)**: T001 MUST land before any implementation code — this component is the project's
first ML at the detection layer (an explicit, recorded exception to Principle IV).

- [x] T001 **Governance (BLOCKING)**: record the Constitution IV detection-layer ML exception in `DECISIONS.md` (new entry **AD2** — bounded: response path stays deterministic, detector decoupled with no second writer / no FSM edge, complements #14) **and** add the corresponding note/amendment to `.specify/memory/constitution.md` (research R10, plan Complexity Tracking). *(AD1 structure entry + docs §10 already recorded.)*
- [x] T002 [P] Create new directories `backend/data/anomaly/` and `tests/fixtures/anomaly/replay/` (the only new dirs this feature adds)
- [x] T003 [P] Add runtime deps `scikit-learn>=1.5` + `numpy>=1.26` to `pyproject.toml` `[project.dependencies]`; add `pandas>=2.2` to the dev/training dependency group (offline training only); run `uv lock` (research R8)
- [x] T004 Extend the import-linter no-bypass guard (the one that fences `opentelemetry`/`presidio`) so `scikit-learn`/`joblib` are confined to `backend/infra/` + the `anomaly_train`/eval entrypoints; confirm `domain` + `services` stay pure (Constitution VII, plan Structure)

---

## Phase 2: Foundational (Blocking Prerequisites) — milestone M-a

**Purpose**: Pure types, the model Protocol, settings, the pure window/feature skeleton, and the test fake
that EVERY story needs.

**⚠️ CRITICAL**: No user-story work can begin until this phase is complete.

- [x] T005 Create pure domain types in `backend/domain/anomaly.py`: `EntityActivityWindow`, `FeatureVector`, `AnomalyFinding`, `ScoreBands`, and the `AnomalyModel` Protocol — Pydantic v2 `extra="forbid"`, `frozen` where natural, only outward import `Severity` from `domain/incident.py` (per data-model.md)
- [x] T006 Add `AnomalySettings(extra="forbid")` to `backend/infra/config.py` (`enabled`, `model_path`, `replay_path`, `window`, `fire_threshold` 0..1, `band_medium`/`band_high`/`band_critical`, `max_events` gt=0, `source_tag="anomaly-detector"`) and register `anomaly: AnomalySettings` on the `Settings` aggregate (contracts/anomaly-model-contract.md §3)
- [x] T007 Implement pure `build_windows(events, window) -> list[EntityActivityWindow]` and `load_replay_events(path) -> list[RawLogEvent]` in `backend/services/anomaly.py`: group records per entity, bin by `event_time` (no wall-clock), skip malformed records (FR-011) — no model object, no I/O surface beyond file parsing
- [x] T008 [P] Add a deterministic `FakeAnomalyModel` (scores derived reproducibly from window features) under `tests/helpers/anomaly.py` (or `tests/conftest.py`) so unit/integration/e2e never load `scikit-learn` or the real artifact

**Checkpoint**: Domain + Protocol + settings + window builder + test fake ready — stories can begin.

---

## Phase 3: User Story 1 — Anomalous behavior detected, runs end to end (Priority: P1) 🎯 MVP — M-a (+ integration/e2e in M-b)

**Goal**: A replayed entity-window deviating from the learned baseline (matching no signature rule) scores
over `fire_threshold`, fires through `intake.accept` as `source="anomaly-detector"`, and completes the
existing triage→enrichment→response pipeline (SC-001).

**Independent Test**: Replay one anomalous user-window → exactly one `Incident(source="anomaly-detector")`
carrying the anomaly score + contributing features + a score-derived severity reaches a terminal
disposition, with zero downstream code change.

### Tests for User Story 1 (write first; ensure they FAIL)

- [x] T009 [P] [US1] Unit tests in `tests/unit/test_anomaly_features.py`: `build_windows` grouping + `event_time` binning determinism; `featurize` feature-order stability; missing feature → 0.0 / extra feature → dropped (both logged); malformed-record **skip** (FR-011)
- [x] T010 [P] [US1] Unit tests in `tests/unit/test_anomaly_mapping.py`: `AnomalyFinding → WazuhAlert` mapping (`anomaly-ueba` rule id, score-derived `level` via the shared severity→level inverse, `score`+`top_features`+`entity_id` in `data`, `entity→agent`) re-derives the same `Severity` through `intake.accept` (data-model.md)
- [x] T011 [P] [US1] Integration test in `tests/integration/test_anomaly_emit.py`: runner (with `FakeAnomalyModel`) → `intake.accept` → `Incident` persisted+enqueued with `source="anomaly-detector"`; re-running the same replay creates **no duplicate** (FR-013 dedup) — *lands in milestone M-b*

### Implementation for User Story 1

- [x] T012 [US1] Implement pure `featurize(window, feature_spec) -> FeatureVector` and `score_to_severity(score, bands) -> Severity` (+ the `fire_threshold` gate) in `backend/services/anomaly.py` (FR-002, FR-004a, FR-005) — identical featurization path is reused by the trainer (structural zero train/serve skew)
- [x] T013 [US1] Implement `finding_to_wazuh_alert(finding) -> WazuhAlert` in `backend/services/anomaly.py`: reuse the severity→level inverse (lift the shared `_SEVERITY_TO_LEVEL` into `services/wazuh.py` if needed so it stays single-source), deterministic `full_log`, and carry `score`+`top_features` as evidence — an anomaly score, NOT a rule identity (FR-003, FR-015)
- [x] T014 [US1] Implement `backend/infra/anomaly_model.py`: `SklearnAnomalyModel(AnomalyModel)` — `joblib` load, `score_samples` → negate + min-max-normalize via the **saved** params → `[0,1]`; **fail-closed** (emit nothing, clear error) if the artifact is missing/unloadable (FR-012, research R3/R9). Owns the sklearn/joblib imports.
- [x] T015 [US1] Implement the offline trainer `backend/anomaly_train.py` (`python -m backend.anomaly_train --cert-dir --out --seed`): read CERT r6.2, build per-user-day features via `services/anomaly.featurize`, fit `IsolationForest(random_state=seed)`, persist an artifact embedding model + ordered `feature_spec` + normalization params (FR-001, research R3/R9; uses pandas from the dev group)
- [x] T016 [US1] Train and **commit** the artifact `backend/data/anomaly/model.joblib` (small; reproducible via the pinned seed) — required before the `anomaly_detection` gate (M-c) can pass; the full CERT dataset is NOT committed
- [x] T017 [US1] Implement the replay runner `backend/anomaly_detector.py`: `make_anomaly_runner(*, settings, session_factory, queue, cache, redactor, model)` closure-factory DI + `python -m backend.anomaly_detector` entrypoint (load model, load replay, `build_windows`, `model.score`, `score_to_severity` over `fire_threshold`, `finding_to_wazuh_alert`, `intake.accept(source=settings.anomaly.source_tag)`; honor `max_events` + `enabled`; fail-closed on missing model) — mirrors `backend/detector.py` — *lands in milestone M-b*
- [x] T018 [P] [US1] Create the shared fixture `tests/fixtures/anomaly/replay/scenarios.jsonl` with one labeled **malicious** anomalous user-window scenario (+ the raw records that aggregate into it)
- [x] T019 [US1] e2e test `tests/e2e/test_anomaly_e2e.py`: replayed anomalous window → `Incident(source="anomaly-detector")` runs the full pipeline to a terminal disposition (SC-001) — *lands in milestone M-b*

**Checkpoint**: US1 is independently demoable — Argus detects novel behavior end to end (real ML, mock env).

---

## Phase 4: User Story 2 — Normal behavior produces no alert (Priority: P2) — M-a (unit) / M-b (integration)

**Goal**: Replayed entity-windows within the learned baseline (score below `fire_threshold`) produce zero
alerts and zero incidents (precision / false-positive side; SC-003).

**Independent Test**: Replay normal-only windows → false-positive rate at or below the committed ceiling
(ideally zero alerts on clearly-normal entities).

### Tests for User Story 2 (write first; ensure they FAIL)

- [x] T020 [P] [US2] Unit tests in `tests/unit/test_anomaly_bands.py`: `score_to_severity` band breakpoints + `fire_threshold` (below threshold → no fire); fail-closed when the model is missing (FR-005, FR-012) — *M-a*

### Implementation for User Story 2

- [x] T021 [US2] Extend `tests/fixtures/anomaly/replay/scenarios.jsonl` with **normal**-labeled user-windows; assert zero incidents + bounded FP in `tests/integration/test_anomaly_emit.py` (SC-003) — *M-b*

**Checkpoint**: US1 + US2 hold — the source fires on deviation and stays quiet on normal behavior.

---

## Phase 5: User Story 3 — Complements the rule detector, not replaces it (Priority: P3) — M-b

**Goal**: The deterministic rule detector (#14) and the ML anomaly detector (#17) run over the same replayed
source; each fired incident is attributable to its source and neither interferes with the other (SC-005).

**Independent Test**: Replay a mixed source where one entity trips a #14 rule and a different entity trips
the #17 threshold → two incidents, one `source="detector"` and one `source="anomaly-detector"`.

### Tests for User Story 3 (write first; ensure they FAIL)

- [x] T022 [P] [US3] Integration test in `tests/integration/test_anomaly_emit.py` (extend): run #14 detector + #17 anomaly runner over the same replayed source; assert two incidents with distinct `source` tags, no interference, and **no new FSM edge / no second writer** (#14 behavior unchanged) (SC-005, FR-008/FR-014)

**Checkpoint**: All three stories independently functional — layering proven (signature + anomaly).

---

## Phase 6: `anomaly_detection` eval gate (cross-cutting, FR-009) — milestone M-c

**Purpose**: The committed, **blocking** precision/recall + FP-ceiling gate, scored deterministically
against the committed artifact. Declared in yaml **and** registered **and** imported together (orphan/stale =
hard error, exit 2, per #13).

- [x] T023 Add the `anomaly_detection` gate block to `config/eval_thresholds.yaml` (`required: true`, `precision_min`/`recall_min`/`max_false_positive_rate`) (contracts/anomaly-eval.md)
- [x] T024 Implement `backend/eval/gates/anomaly_detection.py`: `async def run_anomaly_detection(spec, provider) -> GateResult` loads the committed artifact via `SklearnAnomalyModel`, scores the labeled fixture (`build_windows`→`featurize`→`score`→`fire_threshold`), computes precision/recall/FP-rate per contract; missing artifact/fixture → `passed=None`; register `GATE_REGISTRY["anomaly_detection"] = run_anomaly_detection` in the **same change**
- [x] T025 Import `backend.eval.gates.anomaly_detection` in `backend/eval/__main__.py` (register side-effect) so `validate_registry()` sees `anomaly_detection` declared (yaml) ⇔ registered (code)
- [x] T026 Finalize the labeled fixture `tests/fixtures/anomaly/replay/scenarios.jsonl` (malicious/normal windows) so precision & recall ≥ committed thresholds and FP-rate ≤ the ceiling against the committed artifact (SC-002/SC-003)
- [x] T027 Run `uv run python -m backend.eval --gate anomaly_detection` and confirm registry validation passes and the gate is green (deterministic against the saved artifact — SC-008)

**Checkpoint**: Detection quality is gated in CI — M-c complete.

---

## Phase 7: Polish & Cross-Cutting Concerns

- [x] T028 [P] Run quickstart.md validation: detector run over the committed artifact → verify `source="anomaly-detector"` incidents, normal-behavior suppression, config-only `fire_threshold`/band change (SC-006), and #14 + #17 coexisting over one source
- [x] T029 [P] Confirm docs current: `DECISIONS.md` **AD1** (structure) + **AD2** (Constitution IV exception, from T001) recorded; `docs/siem-ml-detector.md` §10 (structure) present; honesty note (offline-trained, replayed inference, no real-time efficacy claim) in quickstart
- [x] T030 Lean / zero-change check: run `import-linter` (domain + services pure; sklearn confined to infra + entrypoints) + the full existing test & eval suite via `scripts/run-tests.sh` / `scripts/run-evals.sh` to confirm **zero downstream change**, no new layer/migration, `intake.accept` unchanged (FR-014, SC-004)

---

## Dependencies & Execution Order

### Phase dependencies

- **Governance/Setup (P1)** → T001 **blocks all implementation**; T002–T004 have no further deps.
- **Foundational (P2)** → depends on Setup; **blocks all stories**.
- **US1 (P3)** → depends on Foundational. **US2 (P4)** and **US3 (P5)** → depend on Foundational; both reuse `services/anomaly.py` (US1's T012/T013) and the runner (US1's T017).
- **Anomaly gate (P6)** → depends on the committed artifact (T016), the pure scoring path (T012–T014), and the labeled fixture.
- **Polish (P7)** → depends on everything desired being complete.

### Within each story

- Tests written first and FAIL → implementation → fixtures.
- Domain (T005) before services (T007, T012, T013) before infra model (T014) before the trainer (T015)/artifact (T016) before the runner (T017).

### Milestone mapping (PR boundaries, Constitution I ≤~400 lines)

- **M-0** = T001 (governance precondition — may ride at the head of M-a's PR or land first).
- **M-a** (model exists + unit-tested) = T002–T010, T012–T016, T018, T020. The trained artifact + pure
  scoring + sklearn wrapper + unit tests; does not yet emit.
- **M-b** (fires into the pipeline) = T011 (integration), T017 (runner), T019 (e2e), T021 (US2 fixtures +
  FP assertions), T022 (US3 coexistence).
- **M-c** (gated in CI) = T023–T027. T028–T030 ride with M-c.

### Parallel opportunities

- Setup: T002, T003 in parallel (T004 after T003).
- Foundational: T008 `[P]` alongside T005–T007.
- US1 tests: T009, T010, T011, plus fixture T018 — all `[P]` (distinct files).
- Polish: T028, T029 in parallel.
- Note: T012/T013 touch the **same** `services/anomaly.py`; T018/T021/T026 touch the **same** fixture file;
  T011/T021/T022 touch the **same** `test_anomaly_emit.py` — these are sequential, not `[P]`.

---

## Parallel Example: User Story 1

```bash
# Write the failing tests + the shared fixture together (distinct files):
Task: "Unit tests in tests/unit/test_anomaly_features.py (build_windows, featurize, malformed-skip)"
Task: "Unit tests in tests/unit/test_anomaly_mapping.py (AnomalyFinding -> WazuhAlert)"
Task: "Integration test in tests/integration/test_anomaly_emit.py (emit + dedup, FakeAnomalyModel)"
Task: "Create fixture tests/fixtures/anomaly/replay/scenarios.jsonl (one malicious window)"
```

---

## Implementation Strategy

### MVP first (US1 only)

1. T001 governance precondition → Phase 1 Setup → Phase 2 Foundational (blocks all).
2. Phase 3 US1 (train model → score → runner → emit → e2e).
3. **STOP & VALIDATE**: replay one anomalous window → `Incident(source="anomaly-detector")` reaches
   terminal; the headline "detects novel behavior" demo.

### Incremental delivery

1. Governance + Foundational ready.
2. + US1 model & unit tests → **ship M-a** (real ML model exists, unit-gated).
3. + runner + integration + e2e + US2 (suppression) + US3 (coexistence) → **ship M-b** (fires into pipeline).
4. + `anomaly_detection` gate + labeled fixture + polish → **ship M-c** (gated in CI).

### Notes

- `[P]` = different files, no incomplete-task dependency.
- Verify each test FAILS before implementing.
- Unit/integration/e2e use the `FakeAnomalyModel`; **only the `anomaly_detection` gate loads the real model**.
- Run tests via `scripts/run-tests.sh` / `make test-*` — never one big `pytest` (spaCy+graphiti OOM).
- Keep every PR ≤~400 lines (M-a / M-b / M-c split); reuse seams, add no new layer/image/migration; the
  `source` param on `intake.accept` already exists (#14) — do not change `intake`.
- **T001 is non-negotiably first**: no implementation code lands before the Constitution IV exception is
  recorded in `DECISIONS.md` + the constitution note (Governance).
