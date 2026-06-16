---
description: "Task list for SPEC-detector (#14) — deterministic rule/threshold detector"
---

# Tasks: Deterministic Rule/Threshold Detector (#14)

**Input**: Design documents from `specs/014-detector/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: INCLUDED — Constitution II is test-first/three-tier/eval-gated and FR-012 mandates the
`detection` precision/recall gate. Unit/integration/e2e + the eval gate are first-class tasks.

**Organization**: Tasks are grouped by user story (P1→P3) for independent implementation/testing, then
mapped to the two ≤~400-line milestone PRs (M-a, M-b) per Constitution I.

## Lean-structure guardrails (apply to every task)

This is a **backend-only additive extension** — keep it that way:

- **5 new files + 3 small edits only** (per plan.md). No new top-level layer, no new image, **no migration**.
- Reuse existing seams: emission via `services/intake.accept()`; severity↔level via `services/wazuh.py`;
  gate registry via `backend/eval/gates/`. Do **not** duplicate redact/dedup/persist/enqueue.
- **One shared labeled fixture set** (`tests/fixtures/detector/`) feeds unit, integration, e2e, and the
  `detection` gate — do not fork per-test copies.
- `backend/domain/detector.py` stays **pure** (no outward import except `Severity`); `evaluate()` stays
  pure (no I/O). The runner is the only I/O seam.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependency on an incomplete task)
- **[Story]**: US1 / US2 / US3 (setup, foundational, cross-cutting & polish carry no story label)

---

## Phase 1: Setup (Shared Infrastructure) — milestone M-a

**Purpose**: Create the (tiny) directory scaffolding; confirm no new dependency.

- [x] T001 [P] Create new directories `backend/data/detector/` and `tests/fixtures/detector/replay/` (the only new dirs this feature adds)
- [x] T002 [P] Confirm no new runtime dependency: `pyyaml` is already a direct dep (via #13) and no LLM/ML library is added — note in plan if any gap (Constitution IV)

---

## Phase 2: Foundational (Blocking Prerequisites) — milestone M-a

**Purpose**: Pure types, settings, rule-set loader, and the emission-seam param that EVERY story needs.

**⚠️ CRITICAL**: No user-story work can begin until this phase is complete.

- [x] T003 Create pure domain types in `backend/domain/detector.py`: `RawEvent`, `MatchRule` (`kind="match"`), `ThresholdRule` (`kind="threshold"`), the `DetectionRule` discriminated union, `RuleSet`, `FiredAlert` — Pydantic v2 `extra="forbid"`, `frozen` where natural, only outward import `Severity` from `domain/incident.py` (per data-model.md)
- [x] T004 Add `DetectorSettings(extra="forbid")` to `backend/infra/config.py` (`enabled`, `rules_path`, `replay_path`, `max_events` gt=0, `source_tag="detector"`) and register `detector: DetectorSettings` on the `Settings` aggregate (per contracts/detector-rules-contract.md §2)
- [x] T005 Implement the config-backed rule-set loader in `backend/services/detector.py` (`load_rules(path) -> RuleSet`): parse YAML, **fail-fast** on invalid `regex` / `in_list` missing `list_ref` / `value` missing for equals|contains|regex / unknown keys; empty or absent file → empty `RuleSet` (no crash) (FR-005, Edge Cases)
- [x] T006 Parameterize the emission seam in `backend/services/intake.py`: change `accept(...)` to take `source: str = "wazuh"` and set `Incident(source=source)`; verify the webhook caller in `backend/routers/ingest.py` is unchanged (backward-compatible) (research D1, FR-006/FR-013)

**Checkpoint**: Domain + settings + loader + emission seam ready — stories can begin.

---

## Phase 3: User Story 1 — Raw event detected, runs end to end (Priority: P1) 🎯 MVP — M-a (+e2e in M-b)

**Goal**: A replayed event with no pre-made alert matches a signature rule, fires through `intake.accept`
as `source="detector"`, and completes the existing triage→enrichment→response pipeline (SC-001).

**Independent Test**: Replay one matching event → exactly one `Incident(source="detector")` carrying the
rule's id/description/severity reaches a terminal disposition, with zero downstream code change.

### Tests for User Story 1 (write first; ensure they FAIL)

- [x] T007 [P] [US1] Unit tests in `tests/unit/test_detector_rules.py`: match operators (`equals`/`contains`/`regex`/`in_list`), malformed-event **skip** (FR-009), multi-match → single **highest-severity** alert with config-order tie-break (FR-011, D4)
- [x] T008 [P] [US1] Unit tests in `tests/unit/test_detector_mapping.py`: `FiredAlert → WazuhAlert` mapping (rule id/level/description/groups, `data` fields, `source_host→agent`) re-derives the same `Severity` through `intake.accept` (data-model.md mapping)
- [x] T009 [P] [US1] Integration test in `tests/integration/test_detector_emit.py`: runner → `intake.accept` → `Incident` persisted+enqueued with `source="detector"`; re-running the same replay creates **no duplicate** (FR-008 dedup)

### Implementation for User Story 1

- [x] T010 [US1] Implement the pure `evaluate(events, rules) -> list[FiredAlert]` **match path** in `backend/services/detector.py` (signature, single event; malformed skip; multi-match→highest severity per D4) — no I/O
- [x] T011 [US1] Implement `fired_alert_to_wazuh_alert(fired) -> WazuhAlert` in `backend/services/detector.py` (severity→level inverse using existing `services/wazuh.py` bands; `full_log` = deterministic summary)
- [x] T012 [US1] Implement the one-shot runner `backend/detector.py`: `make_detector_runner(...)` closure-factory DI + `python -m backend.detector` entrypoint (load rules+replay, `evaluate`, map, `intake.accept(source=settings.detector.source_tag)`, honor `max_events`; gated by `enabled`) — mirrors #8 `seed-corpus`
- [x] T013 [US1] Seed `backend/data/detector/rules.yaml` with the two `match` rules (`ioc-match` in_list, `malicious-cmd` regex) + the `lists` block (contracts/detector-rules-contract.md §1)
- [x] T014 [P] [US1] Create the shared fixtures `tests/fixtures/detector/rules.yaml` and `tests/fixtures/detector/replay/scenarios.json` with a labeled match scenario (one malicious matching event with `expected_rule`)
- [x] T015 [US1] e2e test `tests/e2e/test_detector_e2e.py`: replayed matching event → `Incident(source="detector")` runs the full pipeline to a terminal disposition (SC-001) — *lands in milestone M-b*

**Checkpoint**: US1 is independently demoable — Sentinel originates a detection end to end.

---

## Phase 4: User Story 2 — Benign events produce no alert (Priority: P2) — M-a

**Goal**: Replayed benign events that match no rule and cross no threshold produce zero alerts and zero
incidents (precision side; SC-003).

**Independent Test**: Replay benign-only events → zero alerts, zero incidents.

### Tests for User Story 2 (write first; ensure they FAIL)

- [x] T016 [P] [US2] Unit tests in `tests/unit/test_detector_rules.py` (extend): benign event matches no rule → **zero** `FiredAlert`s; empty/absent rule set → zero alerts, no crash (FR-003, Edge Cases)

### Implementation for User Story 2

- [x] T017 [US2] Confirm/guarantee suppression in `evaluate` (no default firing, below-threshold no fire); add the explicit benign/empty-ruleset guard only if missing from T010
- [x] T018 [US2] Extend `tests/fixtures/detector/replay/scenarios.json` with benign-labeled events (no `expected_rule`) and assert zero incidents in `tests/integration/test_detector_emit.py`

**Checkpoint**: US1 + US2 hold — the source fires precisely and stays quiet on benign traffic.

---

## Phase 5: User Story 3 — Threshold/aggregation fires one correlated alert (Priority: P3) — M-a

**Goal**: N qualifying events crossing a configured count within a window (over `event_time`) fire
**one** correlated alert per group, not one per event (SC-007).

**Independent Test**: Replay N qualifying events in window W → exactly one alert attributed to the
threshold rule; fewer than N → none.

### Tests for User Story 3 (write first; ensure they FAIL)

- [x] T019 [P] [US3] Unit tests in `tests/unit/test_detector_rules.py` (extend): threshold rule fires exactly **one** alert at the Nth qualifying event within W (grouped by `group_by`, windowed over `event_time`); `<N` → none (FR-004, SC-007)

### Implementation for User Story 3

- [x] T020 [US3] Implement the **threshold path** in `evaluate` (`backend/services/detector.py`): group qualifying events by `group_by`, slide the window over `event_time`, emit one `FiredAlert` at the Nth qualifying event per group (in-run state only; no wall-clock)
- [x] T021 [US3] Add the two `threshold` rules to `backend/data/detector/rules.yaml` (`failed-login-bruteforce`, `connection-fanout`) and grouped threshold scenarios to `tests/fixtures/detector/replay/scenarios.json`

**Checkpoint**: All three stories independently functional — M-a complete.

---

## Phase 6: Detection eval gate (cross-cutting, FR-012) — milestone M-b

**Purpose**: The committed precision/recall gate over the labeled replay set. Declared in yaml **and**
registered together (orphan/stale = hard error, exit 2, per #13).

- [x] T022 Add the `detection` gate block to `config/eval_thresholds.yaml` (`kind: deterministic`, `required: true`, `precision_min`/`recall_min`) (contracts/detection-eval.md)
- [x] T023 Implement `backend/eval/gates/detection.py`: `async def run_detection(spec, provider) -> GateResult` runs the labeled replay set through `evaluate()`, computes precision/recall (TP/FP/FN per contract), and registers `GATE_REGISTRY["detection"] = run_detection` in the **same change**
- [x] T024 Finalize the labeled fixture `tests/fixtures/detector/replay/scenarios.json` (`label` malicious/benign, `group`, `expected_rule`; threshold groups = one expected detection) so precision & recall ≥ committed thresholds
- [x] T025 Run `uv run python -m backend.eval --gate detection` and confirm registry validation passes and the gate is green (SC-002/SC-003)

**Checkpoint**: Detection quality is gated in CI — M-b complete.

---

## Phase 7: Polish & Cross-Cutting Concerns

- [x] T026 [P] Record the micro-decision in `DECISIONS.md`: "Detector emits via `intake.accept(source=...)`; `source` parameterized, default `wazuh`, backward-compatible" (research D1)
- [x] T027 [P] Run quickstart.md validation: detector run → verify `source="detector"` incidents, benign suppression, config-only new rule (SC-005)
- [x] T028 Lean/zero-change check: run `import-linter` (domain isolation holds) + the full existing test & eval suite via `scripts/run-tests.sh` / `scripts/run-evals.sh` to confirm **zero downstream change** and no orphan files / new layer (FR-013, SC-004)

---

## Dependencies & Execution Order

### Phase dependencies

- **Setup (P1)** → no deps.
- **Foundational (P2)** → depends on Setup; **blocks all stories**.
- **US1 (P3)** → depends on Foundational. **US2 (P4)** and **US3 (P5)** → depend on Foundational; both reuse `evaluate()` from US1's T010 (run after US1's `evaluate` skeleton exists, or coordinate the shared file).
- **Detection gate (P6)** → depends on `evaluate()` (US1+US3 paths) and the labeled fixtures.
- **Polish (P7)** → depends on everything desired being complete.

### Within each story

- Tests written first and FAIL → implementation → fixtures.
- Models (T003) before services (T005, T010, T011, T020) before the runner (T012).

### Milestone mapping (PR boundaries, Constitution I ≤~400 lines)

- **M-a** = T001–T014, T016–T021 (detector core + `intake` `source` param + unit/integration). Excludes the e2e test and the gate.
- **M-b** = T015 (e2e) + T022–T025 (`detection` gate + labeled fixture finalize). T026–T028 ride with M-b.

### Parallel opportunities

- Setup: T001, T002 in parallel.
- US1 tests: T007, T008, T009, plus fixture T014 — all `[P]` (distinct files).
- Polish: T026, T027 in parallel.
- Note: T010/T020 touch the **same** `services/detector.py` and T013/T021 + T018/T024 touch the **same** fixtures — these are sequential, not `[P]`.

---

## Parallel Example: User Story 1

```bash
# Write the failing tests + the shared fixture together (distinct files):
Task: "Unit tests in tests/unit/test_detector_rules.py (match ops, malformed-skip, multi-match)"
Task: "Unit tests in tests/unit/test_detector_mapping.py (FiredAlert -> WazuhAlert)"
Task: "Integration test in tests/integration/test_detector_emit.py (emit + dedup)"
Task: "Create fixtures tests/fixtures/detector/rules.yaml + replay/scenarios.json"
```

---

## Implementation Strategy

### MVP first (US1 only)

1. Phase 1 Setup → Phase 2 Foundational (blocks all).
2. Phase 3 US1 (signature event end to end).
3. **STOP & VALIDATE**: replay one match → `Incident(source="detector")` reaches terminal; brief demo #6.

### Incremental delivery

1. Foundational ready.
2. + US1 → MVP / demo (M-a in flight).
3. + US2 (suppression) → precision side proven.
4. + US3 (threshold) → realistic SOC pattern. **Ship M-a.**
5. + Detection gate + e2e + polish. **Ship M-b.**

### Notes

- `[P]` = different files, no incomplete-task dependency.
- Verify each test FAILS before implementing.
- Run tests via `scripts/run-tests.sh` / `make test-*` — never one big `pytest` (spaCy+graphiti OOM).
- Keep every PR ≤~400 lines (M-a / M-b split); reuse seams, add no new layer/image/migration.
