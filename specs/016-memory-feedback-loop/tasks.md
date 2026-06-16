# Tasks: Memory Feedback Loop (Gets Smarter Over Time, #16)

**Input**: Design documents from `specs/016-memory-feedback-loop/`

**Prerequisites**: plan.md ✓, spec.md ✓, research.md ✓, data-model.md ✓, contracts/feedback-write-contract.md ✓, contracts/feedback-consumption-contract.md ✓, contracts/feedback-eval.md ✓, quickstart.md ✓

**Tests**: For Sentinel, tests are REQUIRED, not optional. Per constitution Principle II
(Test-First, Three-Tier, Eval-Gated — NON-NEGOTIABLE), every component carries unit + integration +
e2e tasks that must be green in CI before the spec is "done", plus eval-threshold tasks that gate CI.
(The generic "tests are optional" note from upstream Spec Kit does not apply.)

**Milestone map**: M1-a (write-back → Phase 3, US1) · M1-b (consumption/bias → Phase 4, US2 impl) · M1-c (feedback eval gate + supervisor_routing extension + read-only dashboard surface → Phase 4 eval + Phase 5, US3) · M2 (feed-to-detector, deferred, gated on #14 → Phase 6, US4)

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1, US2, US3, US4)
- Exact file paths in all descriptions

## Path Conventions

- Backend: `backend/`, tests: `tests/`, config: `config/`, fixtures: `tests/fixtures/`

---

## Phase 1: Setup

**Purpose**: Fixture directory creation — the only new directory for this backend-only extension

- [x] T001 Create `tests/fixtures/feedback/` directory and add a `.gitkeep` placeholder (5 labeled baseline-vs-repeat fixture files follow in Phase 4 / US2)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Shared domain types and settings that block both the write (US1) and the read/bias (US2)

**⚠️ CRITICAL**: No user story implementation can begin until this phase is complete

- [x] T002 [P] Create `backend/domain/feedback.py` with `RemediationOutcome` StrEnum (`verified`/`unverified`/`regressed`, values identical to `VerificationVerdict`), `FAILURE_CLASS = frozenset({UNVERIFIED, REGRESSED})`, and the `FeedbackSignal` Pydantic model (`indicator: str`, `outcome: RemediationOutcome`, `is_current: bool`, `observed_at: datetime | None = None`; `extra="forbid"`, `frozen=True`) — domain→domain imports only (blocks US1 write mapping and US2 read/bias)
- [x] T003 [P] Add a new `FeedbackSettings` section to `backend/infra/config.py` (`enabled: bool = True`, `escalate_on: list[str] = ["regressed","unverified"]`, `severity_bias: Literal["bump_one","to_critical","none"] = "bump_one"`, `prefer_stronger_playbook: bool = True`, `max_indicators: int = 5` (`gt=0`), `outcome_fact_type: str = "remediation_outcome"`; `extra="forbid"`) and register it as the `feedback` field on the `Settings` aggregate

**Checkpoint**: Foundation ready — user story implementation can now begin

---

## Phase 3: User Story 1 — The system records what actually worked (M1-a, write-back) (Priority: P1) 🎯 MVP

**Goal**: On a terminal incident carrying a verification verdict (from #15), write one time-valid
`remediation_outcome` `TemporalFact` per applied target — off-path, best-effort, redacted, idempotent —
keyed identically to the reputation fact so it is retrievable later (write-key == read-key).

**Independent Test**: Drive incidents to terminal with each verdict and assert a time-valid outcome fact
appears in memory for the affected indicator; drive a later contradicting outcome and assert the prior fact
is preserved as superseded (`query_fact(as_of=earlier)` = old, `query_fact(now)` = new). With memory down,
assert no write and no block on disposition.

### Tests for User Story 1

> **Write these tests FIRST — ensure they FAIL before implementing the functions they call**

- [x] T004 [P] [US1] Write failing unit tests for the outcome-fact mapping in `tests/unit/test_feedback_write.py`: verdict + applied targets (from `evidence["response"]["verification"]` + `["results"]`) → one `TemporalFact` per applied target with `fact_type="remediation_outcome"`, `value=<verdict>`, `valid_from=incident.updated_at`, entity keyed like the reputation fact; absent verification → no fact; no applied targets → no fact
- [x] T005 [US1] Write failing integration tests for `record_outcome_facts` against real Postgres/memory in `tests/integration/test_feedback_write.py`: write→`query_fact(as_of=None)` round-trip returns the outcome; a later contradicting outcome supersedes (invalidate-not-delete, time-valid); memory outage → no write, no raise; re-finalize → no duplicate/conflicting fact (idempotent)

### Implementation (M1-a — write-back)

- [x] T006 [US1] Implement `async def record_outcome_facts(incident, store, redactor)` in `backend/services/memory.py`: read the incident-level verdict from `evidence["response"]["verification"]["verdict"]` and applied targets from `evidence["response"]["results"]` (status `applied`); build one `TemporalFact` per target keyed like `infra/intel.py::_persist_fact` (so write-key == read-key, research D2); redact via `Boundary.MEMORY_WRITE`; `await store.write_fact(fact)` (depends on T002, T003)
- [x] T007 [US1] Extend `backend/worker.py` `_maybe_record_episode` (or add a sibling `_maybe_record_feedback`) to call `record_outcome_facts` off-path alongside `record_episode` — same fire-and-forget task, same terminal-status guard, same redactor, same try/except (errors logged + swallowed, never block disposition) (depends on T006)
- [x] T008 [P] [US1] Extend `backend/eval/gates/temporal_memory.py` + `config/eval_thresholds.yaml` `temporal_memory.cases` with a `remediation_outcome_flip` case: write `verified`@t1, `regressed`@t2 for one indicator → assert `query_fact(as_of=t1)=verified` (superseded), `query_fact(now)=regressed` (current), and the `verified` fact is RETAINED (invalidated, not deleted)

**Checkpoint**: US1 done — `scripts/run-tests.sh unit integration` green; outcome facts written for each verdict; time-validity preserved; outage never blocks; `temporal_memory` gate (incl. new case) passes

---

## Phase 4: User Story 2 — The same alert is handled differently after memory accumulates (M1-b impl + M1-c eval) (Priority: P1)

**Goal**: A second incident on an indicator with a current failure-class outcome is handled differently —
deterministically escalated sooner (severity/routing bias) and/or matched to a stronger playbook — driven
entirely by retrieved, current, time-valid memory facts; no second writer of incident state.

**Independent Test**: Seed a current `regressed` outcome fact for indicator X; run a fresh incident on X vs a
control with no prior fact; assert the X incident's effective severity is bumped / it escalates sooner and that
response prefers the stronger playbook — reproducible across runs (deterministic), difference attributable to
the retrieved fact. Superseded/`verified` priors apply no change.

### Tests for User Story 2

> **Write these tests FIRST — ensure they FAIL before implementing**

- [x] T009 [P] [US2] Write failing unit tests for the pure bias rules in `tests/unit/test_feedback_rules.py`: `has_prior_failure` (failure-class vs `verified` vs empty), `decide_severity_bias` (`bump_one`/`to_critical`/`none`; idempotent; `verified` → no change), `prefer_stronger_playbook` (highest-`strength` candidate on failure-class; `None`/no-change otherwise)
- [x] T010 [US2] Write failing integration tests for `gather_feedback` against real memory in `tests/integration/test_feedback_consume.py`: returns a current `FeedbackSignal` from a seeded `remediation_outcome` fact; a superseded fact (`is_current=False`) is dropped; memory outage → `[]` (no bias); bounded by `max_indicators`
- [x] T011 [US2] Write failing e2e test in `tests/e2e/test_feedback_e2e.py`: 1st occurrence (no prior) vs 2nd occurrence (seeded current `regressed`) on the same indicator — assert the 2nd escalates sooner (biased severity / route) and/or selects the stronger playbook; assert the supervisor remains the sole writer of status/disposition

### Implementation (M1-b — consumption / bias)

- [x] T012 [US2] Implement the pure bias rules in `backend/domain/feedback.py`: `has_prior_failure(signals, cfg)`, `decide_severity_bias(severity, signals, cfg) -> Severity`, `prefer_stronger_playbook(candidates, signals, cfg) -> PlaybookRef | None` — pure, no I/O, fully unit-testable, `verified` never biases (depends on T002)
- [x] T013 [US2] Implement `backend/services/feedback.py` `async def gather_feedback(*, memory, entities, cfg) -> list[FeedbackSignal]`: concurrent `_safe(memory.query_fact(entity, cfg.outcome_fact_type, as_of=None))` for indicators bounded by `cfg.max_indicators`; keep only `is_current`; best-effort (outage → `[]`); read-key MUST equal the write-key (depends on T002, T003)
- [x] T014 [US2] Wire `gather_feedback` at the grounded boundary in `backend/worker.py` — after `ground()`, before `repo.set_grounded(...)`: augment `Evidence` with a redacted `prior_outcome` slice, apply `decide_severity_bias` to `evidence.severity`, and append `prior_failure` to `evidence["flags"]` when `has_prior_failure` (depends on T012, T013)
- [x] T015 [US2] Add config-backed playbook `strength` (optional `int`, default `0`) to `PlaybookEntry` + loader in `backend/agents/response/catalog.py` and the playbook yaml under `backend/data/playbooks/`; extend `select_playbook` in `backend/agents/response/selection.py` to call `prefer_stronger_playbook` (gated by `cfg.feedback.prefer_stronger_playbook`) when the target has a current failure-class signal — deterministic, before any ambiguous-tail LLM call (depends on T012)
- [x] T016 [US2] Confirm/extend `route_grounded` in `backend/services/supervisor.py` to honour the biased severity + `prior_failure` flag through the **existing** severity→route path — **no new `StageOutcome`, no new FSM edge** (depends on T014)

### Implementation (M1-c — feedback-effectiveness eval gate)

- [x] T017 [P] [US2] Create 5 labeled fixtures in `tests/fixtures/feedback/` (per `contracts/feedback-eval.md`): `prior_regressed_escalates.json`, `prior_unverified_escalates.json`, `prior_failure_picks_stronger_playbook.json`, `verified_prior_no_change.json`, `superseded_prior_no_change.json` (each: seed outcome + baseline-vs-repeat expected behavior); implement `backend/eval/gates/feedback.py` `async def run_feedback(spec, provider=None) -> GateResult` driving the pure bias rules + `route_grounded`, register under `"feedback"` in `GATE_REGISTRY` (depends on T012, T015)
- [x] T018 [US2] Add the `feedback` gate block to `config/eval_thresholds.yaml` (`required: true`, `pass_rate: 1.0`, all 5 fixture names); **must land in the same commit as T017** — the declared⇔registered orphan/stale check is a hard error (depends on T017)
- [x] T019 [P] [US2] Extend `backend/eval/gates/supervisor_routing.py` + the `supervisor_routing.fixtures` list in `config/eval_thresholds.yaml` with a `prior_regressed_escalates` fixture (grounded incident + seeded prior failure → escalation route via the biased input)

**Checkpoint**: US2 done — the demo works (2nd occurrence handled differently); `scripts/run-tests.sh unit integration e2e` green; `feedback` + `supervisor_routing` gates green; supervisor remains single writer

---

## Phase 5: User Story 3 — The analyst sees the loop working (read-only dashboard surface, M1-c) (Priority: P2)

**Goal**: A feedback / memory-hit KPI and a redacted indication in the incident trace that a prior outcome
informed handling — read-only, no new write authority.

**Independent Test**: Drive a population including repeat indicators that triggered feedback bias; assert the
KPI read DTO exposes a feedback/memory-hit breakdown and the incident trace surfaces the redacted
`prior_outcome`; assert no secret appears unredacted in any feedback view.

### Tests for User Story 3

> **Write these tests FIRST — ensure they FAIL before implementing**

- [x] T020 [US3] Write failing unit/integration tests in `tests/integration/test_feedback_dashboard.py`: the feedback/memory-hit KPI counts incidents whose handling was informed by a current `prior_outcome`; `GET /incidents/{id}` trace exposes the redacted `prior_outcome` slice; no seeded secret appears unredacted in the KPI or trace views

### Implementation (M1-c — read-only dashboard surface)

- [x] T021 [P] [US3] Extend the `MemoryHit` KPI surface (`backend/domain/dashboard.py`) + `kpi_enriched_and_hit_counts` in `backend/repositories/incidents.py` + `backend/services/kpis.py` with a feedback counter (incidents carrying a current `prior_outcome`) — read-only aggregate, supervisor stays single writer
- [x] T022 [US3] Surface the redacted `prior_outcome` evidence slice in the incident detail trace DTO (`IncidentDetailView`, `backend/domain/dashboard.py`) so the analyst sees that a prior disposition/verdict informed handling (read-only)
- [x] T023 [P] [US3] Extend `backend/eval/gates/redaction.py` coverage for the `remediation_outcome` fact and the feedback KPI/trace view — no new boundary (uses the existing `memory_write`, `dashboard`, `operational` boundary set)

**Checkpoint**: US3 done — feedback/memory-hit KPI present, `prior_outcome` visible (redacted) in the trace, `redaction` gate green

---

## Phase 6: User Story 4 — Memory feeds the detector (M2, gated on #14) (Priority: P3) ⛔ DEFERRED

> **DEFERRED** — M2 (feed-to-detector export) is explicitly gated on the detector component (#14) and built
> only once #14 lands. No implementation tasks are included here. When #14 is complete, M2 work will be
> tracked in a separate spec update.
>
> M2 design is fully documented: `research.md D11`, `data-model.md §11`. Boundary: a defined export contract
> memory → #14 config (current/time-valid snapshot of confirmed-malicious indicators + recurrence/held-
> remediation signals); the detector still emits the **existing ingestion schema** (zero downstream change);
> exported text passes the **same guardrails as alert text** (Constitution III tiering — guardrails land by
> v3b, before any v3c live feed).

---

## Phase 7: Polish & Cross-Cutting Validation

**Purpose**: Full three-tier test run + eval gate confirmation + quickstart criteria verification

- [x] T024 [P] Run `scripts/run-tests.sh unit` and ensure all feedback unit suites pass (`tests/unit/test_feedback_write.py`, `test_feedback_rules.py`)
- [x] T025 [P] Run `scripts/run-tests.sh integration` and confirm write/consume/dashboard integration tests pass against real Postgres/memory (`tests/integration/test_feedback_write.py`, `test_feedback_consume.py`, `test_feedback_dashboard.py`)
- [x] T026 Run `scripts/run-tests.sh e2e` and confirm the 1st-vs-2nd-occurrence e2e test passes (`tests/e2e/test_feedback_e2e.py`)
- [x] T027 [P] Run `scripts/run-evals.sh feedback` and confirm the `feedback` gate is green at `pass_rate = 1.0` (operationalises SC-001/SC-004)
- [x] T028 [P] Run `scripts/run-evals.sh temporal_memory supervisor_routing redaction` and confirm all three extended gates still pass (regression guard on T008, T019, T023)
- [x] T029 Validate all quickstart.md "done" criteria: outcome fact written off-path on terminal; 2nd occurrence handled differently (escalates sooner / stronger playbook); time-validity preserved; feedback/memory-hit KPI + redacted `prior_outcome` in the trace; memory outage → no bias + no write + no block; supervisor remains single writer

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — start immediately
- **Foundational (Phase 2)**: Depends on Phase 1 — BLOCKS all user story implementation
- **US1 (Phase 3)**: Depends on Phase 2; tests written and failing before implementation
- **US2 (Phase 4)**: Depends on Phase 2; `gather_feedback`/bias read the fact US1 writes, but US2 is independently testable with a **seeded** fact (no hard dependency on US1 code)
- **US3 (Phase 5)**: Depends on Phase 4 (the `prior_outcome` evidence slice it surfaces)
- **US4 (Phase 6)**: DEFERRED — gated on detector #14
- **Polish (Phase 7)**: Depends on US1 + US2 + US3 completion

### User Story Dependencies

- **US1 (P1)**: Starts after Foundational — no dependency on other stories
- **US2 (P1)**: Starts after Foundational — testable with a seeded fact; T018 must land in the same commit as T017 (orphan/stale hard error)
- **US3 (P2)**: Depends on US2's `prior_outcome` evidence slice
- **US4 (P3)**: Deferred — gated on #14

### Within User Story 1

1. Tests (T004–T005) MUST be written and FAIL before implementation
2. T006 (`record_outcome_facts`) after T002 + T003; T007 (worker hook) after T006; T008 (temporal_memory case) independent [P]

### Within User Story 2

1. Tests (T009–T011) MUST be written and FAIL before implementation
2. T012 (bias rules) after T002; T013 (`gather_feedback`) after T002 + T003 — both can run in parallel [P]
3. T014 (worker wiring) after T012 + T013; T015 (selection + catalog `strength`) after T012; T016 (route_grounded) after T014
4. T017 (gate runner + fixtures) after T012 + T015; T018 (yaml) same commit as T017; T019 (supervisor_routing) independent [P]

### Parallel Opportunities

- Phase 2: T002, T003 (different files)
- Phase 3 tests: T004 (T005 is integration, sequential by DB); T008 independent [P]
- Phase 4: T009 [P]; T012 + T013 in parallel after T002/T003; T017→T018 sequential (same commit); T019 [P]
- Phase 5: T021, T023 independent [P]
- Phase 7: T024, T025, T027, T028 (different suites)

---

## Parallel Example: User Story 1

```bash
# Write US1 tests first (must FAIL):
Task T004: "outcome-fact mapping unit tests in tests/unit/test_feedback_write.py"
Task T005: "record_outcome_facts integration tests in tests/integration/test_feedback_write.py"

# Implement write-back, then the temporal_memory case in parallel:
Task T006: "record_outcome_facts in backend/services/memory.py"
Task T007: "worker off-path hook in backend/worker.py"
Task T008: "remediation_outcome_flip case in backend/eval/gates/temporal_memory.py"  # [P]
```

## Parallel Example: User Story 2

```bash
# After Foundational, write US2 tests (must FAIL), then implement core in parallel:
Task T012: "pure bias rules in backend/domain/feedback.py"
Task T013: "gather_feedback in backend/services/feedback.py"   # [P] with T012
# Then wire:
Task T014: "grounded-boundary wiring in backend/worker.py"
Task T015: "stronger-playbook + catalog strength in backend/agents/response/{selection,catalog}.py"
# Eval (T017 → T018 sequential, same commit; T019 [P]):
Task T017: "feedback fixtures + run_feedback gate + register"
Task T019: "supervisor_routing prior_regressed_escalates extension"   # [P]
```

---

## Implementation Strategy

### MVP First (User Stories 1 + 2)

1. Complete Phase 1: Setup (T001)
2. Complete Phase 2: Foundational (T002–T003)
3. US1 write-back (T004–T008) — the loop *records* outcomes
4. US2 consumption/bias (T009–T019) — the loop *closes* (same alert handled differently)
5. **STOP and VALIDATE**: `scripts/run-tests.sh unit integration e2e` + `scripts/run-evals.sh feedback` green — this is the brief's demo #5

### Incremental Delivery

1. Setup + Foundational → US1 write-back → unit/integration green → **M1-a PR**
2. US2 consumption/bias → integration/e2e green → **M1-b PR (the demo)**
3. US2 eval gate + supervisor_routing extension + US3 dashboard surface → all tiers + gates green → **M1-c PR → M1 complete**
4. M2 (US4) after #14 lands — tracked separately

### Milestone PR Slices (each ≤ ~400 lines)

| PR | Tasks | Scope |
|----|-------|-------|
| M1-a | T001–T003, T004–T008 | `domain/feedback.py` types + `FeedbackSettings` + `record_outcome_facts` + worker hook + temporal_memory case + tests |
| M1-b | T009–T016 | pure bias rules + `gather_feedback` + grounded-boundary wiring + stronger-playbook + e2e |
| M1-c | T017–T023, T024–T029 | `feedback` eval gate + supervisor_routing/redaction extensions + read-only dashboard KPI/trace surface + validation |
| M2 | (deferred, gated on #14) | feed-to-detector export contract |

---

## Notes

- **[P]** tasks: different files, no inter-dependencies — safe to parallelize
- **[Story]** label maps each task to its user story for traceability and PR slicing
- Tests MUST be written first and FAIL before implementation begins
- **T017 and T018 (gate runner + yaml block) MUST land in the same commit** — the harness enforces a declared⇔registered orphan/stale check as a hard error
- **Write-key MUST equal read-key** (research D2): `record_outcome_facts` (T006) and `gather_feedback` (T013) must construct the entity identically (mirroring `infra/intel.py::_persist_fact`) — the `feedback` eval (T017) and e2e (T011) are the end-to-end proof
- **No second writer of incident state** (Constitution III): feedback writes only to the memory store (T006) and the grounding `Evidence` input (T014); the supervisor remains the sole writer of status/disposition
- **No new FSM edge** for M1 (research D4): escalation rides the existing severity→`route_grounded` path (T016)
- **US4 (M2) is gated on #14** — do not implement the feed-to-detector export until the detector lands
- Run tests via `scripts/run-tests.sh` / `make test-*`, **never bare `pytest`** (spaCy/Graphiti OOM)
- Best-effort + graceful degradation: memory outage → no bias (baseline v1) + no write, never a block on disposition
