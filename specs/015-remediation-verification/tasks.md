# Tasks: Remediation Verification (Closed-Loop, #15)

**Input**: Design documents from `specs/015-remediation-verification/`

**Prerequisites**: plan.md ✓, spec.md ✓, research.md ✓, data-model.md ✓, contracts/verification-contract.md ✓, contracts/verification-eval.md ✓, quickstart.md ✓

**Tests**: For Sentinel, tests are REQUIRED, not optional. Per constitution Principle II
(Test-First, Three-Tier, Eval-Gated — NON-NEGOTIABLE), every component carries unit + integration +
e2e tasks that must be green in CI before the spec is "done", plus eval-threshold tasks that gate CI
on both LLM providers. (The generic "tests are optional" note from upstream Spec Kit does not apply.)

**Milestone map**: M1-a (verdict core → Phase 3, T009–T013) · M1-b (wiring → T014–T017) · M1-c (eval gate + dashboard surface → Phase 4) · M2 (deferred, gated on #14)

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1, US2, US3)
- Exact file paths in all descriptions

## Path Conventions

- Backend: `backend/`, tests: `tests/`, config: `config/`, fixtures: `tests/fixtures/`

---

## Phase 1: Setup

**Purpose**: Fixture directory creation — only new directory needed for this backend-only extension

- [x] T001 Create `tests/fixtures/verification/` directory and add a `.gitkeep` placeholder (7 labeled fixture files follow in Phase 4 / US2)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Shared enum and settings additions that block all US1 implementation

**⚠️ CRITICAL**: No user story implementation can begin until this phase is complete

- [x] T002 [P] Add `StageOutcome.UNVERIFIED = "unverified"` member to `StageOutcome` StrEnum in `backend/domain/pipeline.py` (blocks supervisor edge T014 and handler outcome mapping in T016)
- [x] T003 [P] Extend `ResponseSettings` in `backend/infra/config.py` with `verify_remediation: bool = True`, `verify_regressed_verdicts: list[str] = ["malicious", "suspicious"]`, `verify_llm_tiebreak: bool = False`, `dwell_window_s: int = 900` (M2-reserved); `extra="forbid"` already inherited

**Checkpoint**: Foundation ready — user story implementation can now begin in parallel

---

## Phase 3: User Story 1 — Honest post-remediation verdict (M1-a + M1-b) (Priority: P1) 🎯 MVP

**Goal**: After any applied remediation (auto or human-approved path), compute a deterministic
`verified`/`unverified`/`regressed` verdict via executor probe + indicator re-check; map to the
correct terminal disposition; fail-closed and idempotent on re-run.

**Independent Test**: Drive `verified_clean`, `regressed_malicious`, and `unverified_inconclusive`
states through the full response handler in e2e and assert the correct verdict and terminal
disposition for each: clean → `verified` → `auto_remediated`; still-malicious → `regressed` →
`remediation_unverified` + escalated; inconclusive → `unverified` → `remediation_unverified` +
escalated. No detector or live environment required.

### Tests for User Story 1

> **Write these tests FIRST — ensure they FAIL before implementing the functions they call**

- [x] T004 [P] [US1] Write failing unit tests for `decide_action_verdict` and `decide_verdict` pure functions (all rule-table rows, worst-case aggregate, genuine-conflict case, empty-action list) in `tests/unit/test_verification_verdict.py`
- [x] T005 [P] [US1] Write failing unit tests for `ProbeState`, `ProbeResult`, `VerificationSignals`, and `VerificationRecord` Pydantic models (`extra="forbid"`, `frozen=True`, required fields, default values) in `tests/unit/test_verification_models.py`
- [x] T006 [P] [US1] Write failing unit tests for `probe()` on mock executors: default returns `ProbeState.EXPECTED`, `build_regressed_executors(*types)` returns `UNEXPECTED`, `build_inconclusive_executors(*types)` returns `INCONCLUSIVE` in `tests/unit/test_executor_probe.py`
- [x] T007 [US1] Write failing integration tests for `verify_remediation` on both auto (`_pass_a`) and approved (`_pass_b`) handler branches against real Redis/Postgres/memory, including fail-closed case (outage → `unverified`, never blocks disposition) in `tests/integration/test_verification_handler.py`
- [x] T008 [US1] Write failing e2e test for full incident → applied remediation → `verify_remediation` → verdict → terminal disposition (one test per verdict class) in `tests/e2e/test_verification_e2e.py`

### Implementation (M1-a — verdict core)

- [x] T009 [P] [US1] Add `ProbeState` StrEnum (`expected`/`unexpected`/`inconclusive`) and `ProbeResult` Pydantic model (`type: ActionType`, `target: str`, `state: ProbeState`, `detail: str = ""`) to `backend/domain/response.py`
- [x] T010 [US1] Extend `ActionExecutor` Protocol with `async def probe(self, action: RemediationAction) -> ProbeResult` (read-only; never raises — any error → `INCONCLUSIVE`) in `backend/domain/response.py` (depends on T009)
- [x] T011 [US1] Add `IndicatorRecheck` (`target`, `intel_verdict`, `fact_value`, `fact_is_current`), `VerificationSignals` (`probe: ProbeResult`, `recheck: IndicatorRecheck | None`), and `VerificationRecord` (`verdict`, `per_action`, `signals`, `used_llm_tiebreak`, `rationale`) Pydantic models to `backend/domain/response.py` (depends on T003, T009)
- [x] T012 [US1] Implement `decide_action_verdict(signals: VerificationSignals, cfg: ResponseSettings) -> VerificationVerdict` and `decide_verdict(per_action: list[VerificationSignals], cfg: ResponseSettings) -> VerificationVerdict` pure functions with worst-case aggregate (`REGRESSED > UNVERIFIED > VERIFIED`) and config-backed `verify_regressed_verdicts` set in `backend/domain/response.py` (depends on T003, T011)
- [x] T013 [US1] Extend all mock executor classes in `backend/infra/executors.py` with `async def probe(self, action: RemediationAction) -> ProbeResult` returning `ProbeState.EXPECTED` by default; add `build_regressed_executors(*action_types)` and `build_inconclusive_executors(*action_types)` test-helper factory functions (depends on T010)

### Implementation (M1-b — wiring)

- [x] T014 [US1] Add FSM transition `(IncidentStatus.RESPONDING, StageOutcome.UNVERIFIED): (IncidentStatus.ESCALATED, DISP_REMEDIATION_UNVERIFIED)` to the supervisor transition table in `backend/services/supervisor.py` (depends on T002)
- [x] T015 [US1] Implement `async def verify_remediation(applied_actions, executors, intel, memory, settings) -> VerificationRecord` in `backend/agents/response.py`: fan-out `_safe(intel.lookup)` + `_safe(memory.query_fact(as_of=None))` via `asyncio.gather` for each applied-action target, call `probe()` on each executor, call `decide_verdict`; idempotency guard returns existing record if `evidence["response"]["verification"]` already present (depends on T012, T013)
- [x] T016 [US1] Wire `verify_remediation()` into the terminal branches of `_pass_a` (auto) and `_pass_b` (approved) in `backend/agents/response.py`: merge `VerificationRecord.model_dump(mode="json")` into `evidence_patch["response"]["verification"]`, stamp each `ActionResult.verification`, return `StageOutcome.RESOLVED` on `verified` or `StageOutcome.UNVERIFIED` on `unverified`/`regressed` (depends on T014, T015)
- [x] T017 [US1] Extend `make_response_handler` closure signature in `backend/agents/response.py` to accept optional `intel: ThreatIntelClient | None` and `memory: MemoryStore | None` retrievers; on `unverified`/`regressed` append one `audit_log` row (`actor="verifier"`, `action="verification"`, `outcome=<verdict>`) via the existing audit repo (depends on T016)

**Checkpoint**: US1 done — `scripts/run-tests.sh unit integration e2e` all green; drive auto and approved incidents to each verdict class and assert disposition + audit row; confirm verification failure never blocks terminal state

---

## Phase 4: User Story 2 — Analyst sees the verdict (read-only dashboard surface + eval gate, M1-c) (Priority: P2)

**Goal**: Verification verdict and redacted evidence visible in the incident trace; `remediation_unverified`
distinguishable in the queue; a verdict breakdown KPI available; `verification` eval gate green; all
three existing gate extensions (temporal_memory, redaction, supervisor_routing) still pass.

**Independent Test**: Drive an incident to each verdict, then assert `GET /incidents/{id}` trace exposes
the `VerificationRecord` from `evidence["response"]["verification"]` in redacted form; `GET /incidents`
queue marks `remediation_unverified` distinctly from `remediated`; `GET /incidents/kpis` includes a
verdict-class breakdown; no secret value appears unredacted in any view.

### Tests for User Story 2

> **Write these tests FIRST — ensure they FAIL before implementing**

- [x] T018 [P] [US2] Write failing unit tests asserting `IncidentDetailView.evidence["response"]["verification"]` serialises the `VerificationRecord` with redacted `target` and `detail` fields (no raw indicator values) in `tests/unit/test_verification_dashboard_dtos.py`
- [x] T019 [US2] Write failing integration test asserting `remediation_unverified` disposition is distinguishable in the queue read response and no secret value appears unredacted in any verification-related read-endpoint view in `tests/integration/test_verification_redaction.py`

### Implementation (M1-c — eval gate + gate extensions + dashboard surface)

- [x] T020 [P] [US2] Create 7 labeled fixture files in `tests/fixtures/verification/` (one per case from `contracts/verification-eval.md`): `verified_clean_indicator.json`, `regressed_indicator_still_malicious.json`, `regressed_probe_unexpected.json`, `unverified_intel_unknown.json`, `unverified_probe_inconclusive.json`, `conflict_probe_ok_indicator_malicious.json`, `multi_action_worst_case.json`; each fixture carries `signals` input + `expected_verdict` label
- [x] T021 [P] [US2] Implement `backend/eval/gates/verification.py`: `async def run_verification(spec, provider=None) -> GateResult` loads labeled fixtures from `tests/fixtures/verification/`, drives each through `decide_verdict`, scores `classification_accuracy` and `false_verified_rate` against committed thresholds; register under `"verification"` in `GATE_REGISTRY` mirroring `run_supervisor_routing` (depends on T012)
- [x] T022 [US2] Add `verification` gate block to `config/eval_thresholds.yaml` with `required: true`, `min_accuracy: 0.95`, `max_false_verified_rate: 0.0`, and all 7 fixture names; **must land in the same commit as T021** — the harness enforces a declared⇔registered orphan/stale check as a hard error (depends on T021)
- [x] T023 [P] [US2] Extend `backend/eval/gates/temporal_memory.py` with a verification-specific case asserting `query_fact(as_of=None)` returns only the current-valid fact and a superseded fact (`is_current=False`) is treated as absent in the indicator re-check path
- [x] T024 [P] [US2] Extend `backend/eval/gates/redaction.py` to add seeded-secret coverage for verification record `target`/`detail` fields and the dashboard trace view of the `VerificationRecord`; no new boundary — uses existing `memory_write`, `dashboard`, `operational` boundary set
- [x] T025 [P] [US2] Extend `backend/eval/gates/supervisor_routing.py` with `verified_resolves` fixture (RESPONDING + RESOLVED outcome → `auto_remediated`/`remediated`) and `unverified_escalates` fixture (RESPONDING + UNVERIFIED outcome → ESCALATED + `remediation_unverified`) for the new FSM edge

**Checkpoint**: US2 done — verdict visible in trace (redacted), `remediation_unverified` in queue, `verification` gate green, all gate extensions pass; `scripts/run-evals.sh verification temporal_memory redaction supervisor_routing` all pass

---

## Phase 5: User Story 3 — Monitoring loop reopens recurrences (M2, gated on #14) (Priority: P3) ⛔ DEFERRED

> **DEFERRED** — M2 is explicitly gated on the detector component (#14) and built only once #14 lands.
> No implementation tasks are included here. When #14 is complete, M2 work will be tracked in a
> separate spec update.
>
> M2 design is fully documented: `research.md D7`, `contracts/verification-contract.md C7`,
> `data-model.md §8 (M2-reserved)`. Reserved seam: `IncidentStatus.VERIFYING` + park/resume
> machinery (same as `awaiting_approval`) + dwell-window sweeper analogous to `expire_incident`.
> The `dwell_window_s` setting is already added in T003 (M2-reserved, default 900 s).

---

## Phase 6: Polish & Cross-Cutting Validation

**Purpose**: Full three-tier test run + eval gate confirmation + quickstart criteria verification

- [ ] T026 [P] Run `scripts/run-tests.sh unit` and ensure all verdict, model, and probe unit test suites pass (`tests/unit/test_verification_verdict.py`, `test_verification_models.py`, `test_executor_probe.py`, `test_verification_dashboard_dtos.py`)
- [ ] T027 [P] Run `scripts/run-tests.sh integration` and confirm handler verdict path tests pass against real Redis/Postgres/memory (`tests/integration/test_verification_handler.py`, `test_verification_redaction.py`)
- [ ] T028 Run `scripts/run-tests.sh e2e` and confirm full incident → applied remediation → verdict → terminal disposition e2e test passes (`tests/e2e/test_verification_e2e.py`)
- [ ] T029 [P] Run `scripts/run-evals.sh verification` and confirm `verification` gate green with `classification_accuracy ≥ 0.95` and `false_verified_rate = 0.0` (operationalises SC-003 and SC-004)
- [ ] T030 [P] Run `scripts/run-evals.sh temporal_memory redaction supervisor_routing` and confirm all three extended gates still pass (regression guard on T023–T025 extensions)
- [ ] T031 Validate all 7 quickstart.md "done" criteria: three verdict classes correct on auto + approved paths; fail-closed on outage; idempotent re-run; verdict visible (redacted) in dashboard trace; `remediation_unverified` in queue; `verification` gate green

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — start immediately
- **Foundational (Phase 2)**: Depends on Phase 1 — BLOCKS all user story implementation
- **US1 (Phase 3)**: Depends on Phase 2 completion; tests written and failing before implementation
- **US2 (Phase 4)**: `run_verification` (T021) depends on `decide_verdict` (T012) from US1; gate extensions (T023–T025) are independent
- **US3 (Phase 5)**: DEFERRED — gated on detector #14
- **Polish (Phase 6)**: Depends on US1 + US2 completion

### User Story Dependencies

- **US1 (P1)**: Starts after Foundational (Phase 2) — no dependency on other stories
- **US2 (P2)**: T021 (`run_verification`) depends on T012 (`decide_verdict`) from US1; T022 must land in the same commit as T021; T020, T023, T024, T025 are independent [P]
- **US3 (P3)**: Deferred — gated on #14

### Within User Story 1

1. Tests (T004–T008) MUST be written and FAIL before implementation
2. M1-a models (T009–T013): T009 first; T010 and T011 can follow in parallel; T012 after T011; T013 after T010
3. M1-b wiring (T014–T017): T014 depends only on T002 (can start early); T015 after T012 + T013; T016 after T014 + T015; T017 after T016

### Within User Story 2

1. Tests (T018–T019) MUST be written and FAIL before implementation
2. T020, T023, T024, T025 are all independent [P] — launch together
3. T021 depends on T012 (US1 M1-a done)
4. T022 MUST land in the same commit as T021 (orphan/stale hard error)

### Parallel Opportunities

Within Phase 2: T002, T003 (different files)

Within Phase 3 tests: T004, T005, T006 (different test files)

Within Phase 3 M1-a: T009 then T010 + T011 in parallel; T012 after T011; T013 after T010

Within Phase 4: T018, T020, T023, T024, T025 (all independent files); T021 → T022 sequential (same commit)

Within Phase 6: T026, T027, T029, T030 (different test suites)

---

## Parallel Example: User Story 1

```bash
# Write all three M1-a unit test suites together (must FAIL):
Task T004: "decide_action_verdict + decide_verdict unit tests in tests/unit/test_verification_verdict.py"
Task T005: "ProbeState/ProbeResult/VerificationSignals/VerificationRecord model tests in tests/unit/test_verification_models.py"
Task T006: "probe() on mock executors + build_regressed_executors tests in tests/unit/test_executor_probe.py"

# After tests FAIL, implement M1-a verdict core (T009 → then parallel):
Task T009: "ProbeState enum + ProbeResult model in backend/domain/response.py"
# Then in parallel after T009:
Task T010: "extend ActionExecutor protocol with probe() in backend/domain/response.py"
Task T011: "IndicatorRecheck, VerificationSignals, VerificationRecord models in backend/domain/response.py"
```

---

## Parallel Example: User Story 2

```bash
# After US1 M1-a complete (T012 done), launch M1-c tasks together:
Task T020: "7 labeled fixture files in tests/fixtures/verification/"
Task T021 → T022 (sequential, same commit): "run_verification gate runner + yaml block"
Task T023: "temporal_memory gate extension in backend/eval/gates/temporal_memory.py"
Task T024: "redaction gate extension in backend/eval/gates/redaction.py"
Task T025: "supervisor_routing gate extension in backend/eval/gates/supervisor_routing.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup (T001)
2. Complete Phase 2: Foundational (T002–T003)
3. Write US1 tests first (T004–T008) — ensure FAIL
4. Implement M1-a verdict core (T009–T013)
5. Implement M1-b wiring (T014–T017)
6. **STOP and VALIDATE**: `scripts/run-tests.sh unit integration e2e` — all green

### Incremental Delivery

1. Setup + Foundational → M1-a verdict core → unit tests green
2. M1-b wiring → integration + e2e green → **M1-a + M1-b PR demoable**
3. M1-c eval gate + gate extensions + dashboard surface → **all three tiers + gate green → M1 complete**
4. M2 (US3) after #14 lands — tracked separately

### Milestone PR Slices (from quickstart.md, each ≤ ~400 lines)

| PR | Tasks | Scope |
|----|-------|-------|
| M1-a | T001–T003, T004–T006, T009–T013 | domain types + `decide_verdict` + probe contract + mock `probe()` + unit tests |
| M1-b | T007–T008, T014–T017 | `verify_remediation` wiring + supervisor edge + integration + e2e |
| M1-c | T018–T025 (US2), T026–T031 | eval gate + gate extensions + dashboard surface + validation |
| M2 | (deferred, gated on #14) | `verifying` monitoring loop + dwell sweeper |

---

## Notes

- **[P]** tasks: different files, no inter-dependencies — safe to parallelize
- **[Story]** label maps each task to its user story for traceability and PR slicing
- Tests MUST be written first and FAIL before implementation begins
- **T021 and T022 (gate registration + yaml block) MUST land in the same commit** — the harness enforces a declared⇔registered orphan/stale check as a hard error
- **US3 (M2) is gated on #14** — do not implement `IncidentStatus.VERIFYING`, the monitoring loop, or the dwell sweeper until the detector lands; `dwell_window_s` setting is already reserved in T003
- Memory write-back is **#16's job** — #15 produces and records the verdict on the incident; writing it back to temporal memory is out of scope
- Run tests via `scripts/run-tests.sh` / `make test-*`, **never bare `pytest`** (spaCy/Graphiti OOM)
- `verify_remediation` is **read-only** — it adds no write authority; the re-check only reads #5/#6
- Idempotency guard (T015): if `evidence["response"]["verification"]` already present, skip re-running — no double probe, no duplicate audit row
