---
description: "Task list for Incident State Machine (Supervisor) — Component #7"
---

# Tasks: Incident State Machine (Supervisor)

**Input**: Design documents from `/specs/005-incident-state-machine/`

**Prerequisites**: plan.md (required), spec.md (required), research.md, data-model.md, contracts/

**Tests**: REQUIRED (Sentinel constitution Principle II — Test-First, Three-Tier, Eval-Gated, NON-NEGOTIABLE).
Each user story carries the tiers it owns: US1 → unit; US2 → unit + integration; US3 → unit + e2e + eval.
Tests are written **before** the implementation in each story and must fail first.

**Organization**: Tasks are grouped by user story. The three stories map 1:1 to the plan's three PR
milestones (each ≤ ~400 lines): US1 = the spine, US2 = fast-path + adaptive depth + wiring, US3 = bounds +
degradation + park + eval gate.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: Which user story this task belongs to (US1, US2, US3)
- All paths are repository-relative

## Path Conventions

Backend modular-monolith: source under `backend/`, tests under `tests/{unit,integration,e2e,eval}/`,
fixtures under `tests/fixtures/`, migrations under `backend/db/migrations/versions/`, config under `config/`.
This component **fills reserved seams** (`services/pipeline.py`, `agents/*`) and adds the minimum new files.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Create the new test/fixture locations this component needs. No new dependency, no compose change.

- [x] T001 Create the new test/fixture directories `tests/eval/` and `tests/fixtures/incidents/` (with `__init__.py` where needed); `tests/unit/`, `tests/integration/`, `tests/e2e/`, and `tests/fixtures/wazuh_alerts/` already exist

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The shared contract, schema, persistence primitive, and typed settings that **all** stories
depend on. These are the pure types (#8/#9/#10/#12 import them), the extended lifecycle, the migration, the
guarded transition, and the `supervisor` settings section.

**⚠️ CRITICAL**: No user-story work can begin until this phase is complete.

- [x] T002 [P] Define the pure state-machine types in `backend/domain/pipeline.py` — `StageName`, `StageOutcome`, `StageResult` (Pydantic `extra="forbid"`: stage, outcome, tokens_consumed=0, disposition, evidence_patch, note), `ToolError(Exception)` (retryable/kind/detail), and the `StageHandler = Callable[[Incident], Awaitable[StageResult]]` alias; no outward imports (domain-isolation contract) per data-model.md §3
- [x] T003 Extend `IncidentStatus` in `backend/domain/incident.py` with `triaging`, `enriching`, `responding`, `awaiting_approval`, `resolved`, `escalated` (keep existing `received`/`grounding`/`grounded`/`failed`), and add the optional `disposition: str | None = None` field to `Incident` per data-model.md §1–§2
- [x] T004 [P] Add migration `backend/db/migrations/versions/0004_incident_disposition.py` (revision `0004`, down_revision `0003`) adding a nullable `disposition text` column to `incidents`; reversible `downgrade` drops it; no status DDL (status stays `text`) per data-model.md §9
- [x] T005 Add the guarded transition `advance_status(incident_id, *, expected: IncidentStatus, target: IncidentStatus, disposition: str | None = None) -> bool` to `backend/repositories/incidents.py` (atomic `UPDATE … SET status=:target[, disposition=:disp] WHERE id=:id AND status=:expected RETURNING id`, returns True iff applied) and map the new `disposition` column in `_row_to_incident` (depends on T003) per data-model.md §8
- [x] T006 [P] Add `SupervisorSettings` to `backend/infra/config.py` (`extra="forbid"`: `max_steps=8`, `max_tokens=40_000`, `max_stage_retries=2`, `fast_path_autoclose_severities=["low"]`, `fast_path_critical_severities=["critical"]`), register it on `Settings` as `supervisor`, and add `"supervisor"` to `_KNOWN_SENTINEL_SECTIONS` per data-model.md §7

**Checkpoint**: Contract types, lifecycle, migration, guarded transition, and settings exist — the supervisor and stages can now be built against a stable seam.

---

## Phase 3: User Story 1 - Supervisor drives a grounded incident to disposition (Priority: P1) 🎯 MVP

**Goal**: The spine — a deterministic state machine that takes a grounded incident, advances it through the
enumerated transition table (persisting every edge as the single writer), and lands it in exactly one
terminal disposition. Agent stages are exercised through an injected handler registry (faked in tests).

**Independent Test**: Hand a grounded `Incident` to `Supervisor.run_incident` with a fake repo and a fake
stage registry returning canned `StageResult`s; assert it walks allowed lifecycle states, each transition
is persisted via `advance_status`, and it ends in exactly one terminal disposition (never stuck in-flight).

### Tests for User Story 1 (write first; must fail before implementation) ⚠️

- [x] T007 [P] [US1] Unit test the transition table legality in `tests/unit/test_supervisor_transitions.py`: every enumerated `(state, trigger) → state` edge from data-model.md §4 is allowed, and any `(state, outcome)` pair **not** in the table routes to `escalated` with `disposition = escalated_illegal_transition`
- [x] T008 [P] [US1] Unit test the run loop in `tests/unit/test_supervisor_loop.py`: with a fake repo + fake stage registry, a grounded incident advances through the lifecycle to exactly one terminal disposition, every transition is persisted, and nothing is left in-flight (SC-001)
- [x] T009 [P] [US1] Unit test entry/idempotency by state class in `tests/unit/test_supervisor_entry.py`: `grounded` starts, `triaging`/`enriching`/`responding` resume, `awaiting_approval` and the terminal states (`resolved`/`escalated`/`failed`) are no-ops; a `False` from `advance_status` (lost the guard race) ends the run cleanly (SC-005)

### Implementation for User Story 1

- [x] T010 [US1] Define the transition table (the allowed-edge map keyed on `(IncidentStatus, StageOutcome | route)` → next status + terminal `disposition`) in `backend/services/supervisor.py` per data-model.md §4
- [x] T011 [US1] Implement the `Supervisor` class and `run_incident(incident_id, repo)` loop in `backend/services/supervisor.py`: read persisted status → state-class dispatch (entry/resume/no-op) → invoke the next stage via the injected `StageHandler` registry → apply the outcome through the transition table → persist via `repo.advance_status` (single writer) until terminal/parked; reject any illegal transition to `escalated` (depends on T010)
- [x] T012 [US1] Open a parent span per run and a child span per step/stage via the #2 observability seam (`tracer.span()`), bind the correlation id (`bind_incident`), and ensure span/log attributes carry only redacted content (reuse `Redactor`) — no raw incident content (SC-007) — in `backend/services/supervisor.py` (depends on T011)

**Checkpoint**: A grounded incident is driven end-to-end to a terminal disposition through stubbed/faked stages, every transition persisted. US1 is independently testable.

---

## Phase 4: User Story 2 - Deterministic fast-path and adaptive depth (Priority: P2)

**Goal**: Resolve the obvious cases with **zero** agent calls (config-backed severity bands), route only
ambiguous incidents through full depth, and run enrichment only when triage `ADVANCE`d. Land the real stub
handlers and the worker→pipeline→supervisor wiring so the spine is demoable.

**Independent Test**: Feed the supervisor the labeled fixture set (obvious-noise, obvious-critical,
ambiguous); assert obvious-class incidents reach their terminal/next stage with **0** stage invocations, and
ambiguous incidents invoke triage and only then enrichment.

### Tests for User Story 2 (write first; must fail before implementation) ⚠️

- [x] T013 [P] [US2] Unit tests for routing + adaptive depth in `tests/unit/test_supervisor_routing.py`: `route_grounded` sends `low→resolved` (0 calls), `critical→responding`, `medium`/`high`→`triaging`, and `severity_defaulted`→`triaging`; adaptive depth — triage `RESOLVED` skips enrichment, triage `ADVANCE`→enrichment, enrichment `ADVANCE`→response (SC-003, FR-006/FR-007)
- [x] T014 [P] [US2] Integration test against **real Postgres** in `tests/integration/test_supervisor_pg.py`: guarded `advance_status` transitions, `disposition` persistence, and resume from a persisted in-flight state

### Fixtures for User Story 2

- [x] T015 [P] [US2] Add the labeled routing fixtures under `tests/fixtures/incidents/`: `noise_low`, `critical_high`, `ambiguous_resolved_at_triage`, `ambiguous_full_depth`, `indeterminate_severity` (each a grounded `Incident` + expected routing) per contracts/supervisor-routing-eval.md

### Implementation for User Story 2

- [x] T016 [US2] Implement `route_grounded(incident, cfg)` (pure, config-backed per data-model.md §6) and wire it into the `grounded` entry of `run_incident` — fast-path autoclose/critical, ambiguous default, and `severity_defaulted`→`triaging` — in `backend/services/supervisor.py` (depends on T011)
- [x] T017 [P] [US2] Fill the stub stage handlers (no LLM, read-only): `run_triage`→`ADVANCE`, `run_enrichment`→`ADVANCE`, `run_response`→`RESOLVED` (`auto_remediated`) in `backend/agents/triage.py`, `backend/agents/enrichment.py`, `backend/agents/response.py` per contracts/stage-handler-contract.md
- [x] T018 [P] [US2] Fill the seam `backend/services/pipeline.py`: `dispatch_to_pipeline(incident, repo=None)` (backward-compatible one-arg) delegates to `container.supervisor.run_incident(incident.id, repo)` per data-model.md §10
- [x] T019 [P] [US2] Add `SupervisorProvider` (lifespan singleton mirroring `QueueProvider`, `name="supervisor"`) in `backend/infra/supervisor_provider.py`, building the `Supervisor` with the stage-handler registry `{TRIAGE: run_triage, ENRICHMENT: run_enrichment, RESPONSE: run_response}`, `SupervisorSettings`, and the tracer; exposed as `container.supervisor` (depends on T011, T017)
- [x] T020 [US2] Register `SupervisorProvider` in `backend/worker.py` bootstrap and pass the session-bound `repo` through `dispatch_to_pipeline(incident, repo)`; add `get_supervisor()` (request-path, for #12/tests) to `backend/dependencies.py` (depends on T018, T019)

**Checkpoint**: Obvious-class incidents resolve with zero stage calls; ambiguous ones walk the full adaptive depth; the worker drives incidents through the supervisor end-to-end. US1 + US2 both work.

---

## Phase 5: User Story 3 - Bounded execution and graceful degradation (Priority: P3)

**Goal**: A hard step+token cap, bounded retry of transient errors with graceful degradation (worker never
crashes), the `awaiting_approval` park + reserved resume edges, and the activated supervisor-routing eval
gate.

**Independent Test**: Inject (a) a stage that over-consumes past the step/token cap and (b) a stage that
raises a non-retryable `ToolError`; assert the incident reaches `escalated` with the reason recorded and the
worker process stays alive in both cases.

### Tests for User Story 3 (write first; must fail before implementation) ⚠️

- [x] T021 [P] [US3] Unit test bounds in `tests/unit/test_supervisor_bounds.py`: step cap → `escalated` (`escalated_step_cap`), token cap → `escalated` (`escalated_token_cap`), asserting the loop never runs unbounded (SC-002)
- [x] T022 [P] [US3] Unit test retry/degradation in `tests/unit/test_supervisor_errors.py`: a retryable `ToolError` is retried ≤ `max_stage_retries` then `escalated`; a non-retryable `ToolError` or an unexpected exception → `escalated` (`escalated_stage_error`) immediately; the loop never propagates an exception out (worker survives) (SC-004)
- [x] T023 [P] [US3] Unit test park + resume in `tests/unit/test_supervisor_approval.py`: `responding` + `NEEDS_APPROVAL` → `awaiting_approval` (parked, loop stops); `resume_incident` approve→`responding`, reject→`resolved` (`rejected_by_human`) (FR-013)
- [x] T024 [P] [US3] e2e test in `tests/e2e/test_pipeline_dispositions.py`: POST a sample alert → worker grounds → supervisor reaches a terminal disposition across noise/critical/ambiguous fixtures, plus fault injection (stage `ToolError` → `escalated`; cap-breach → `escalated`) asserting the worker process stays alive (SC-001/SC-004)
- [x] T025 [P] [US3] Eval-gate test in `tests/eval/test_supervisor_routing_gate.py`: 100% of the labeled routing fixtures reach the expected next stage / terminal disposition (provider-independent, unit tier) per contracts/supervisor-routing-eval.md

### Fixtures for User Story 3

- [x] T026 [P] [US3] Add the fault + park fixtures under `tests/fixtures/incidents/` (`destructive_parks`, `stage_error_escalates`, `cap_breach_escalates`) and the Wazuh alert fixtures under `tests/fixtures/wazuh_alerts/` (`low_noise.json`, `medium_ambiguous.json`, `critical_destructive.json`) per quickstart.md and contracts/supervisor-routing-eval.md

### Implementation for User Story 3

- [x] T027 [US3] Enforce the hard step + token caps before each stage call in `run_incident` (step counter; accumulate `StageResult.tokens_consumed`; on breach → `escalated` with `escalated_step_cap`/`escalated_token_cap`, never an unbounded loop) in `backend/services/supervisor.py` per data-model.md §4/SD7
- [x] T028 [US3] Implement bounded retry + graceful degradation in `run_incident`: retry retryable `ToolError` ≤ `max_stage_retries`; non-retryable / exhausted / unexpected exception → `escalated` (`escalated_stage_error`); never let an exception escape the loop (the worker never crashes or 500s) in `backend/services/supervisor.py` (depends on T027) per SD4/SD7
- [x] T029 [US3] Implement the `awaiting_approval` park (`responding` + `NEEDS_APPROVAL` → `awaiting_approval`, stop the loop) and the reserved `resume_incident(incident_id, decision, repo)` seam (approve→`responding`, reject→`resolved`/`rejected_by_human`) in `backend/services/supervisor.py`, and have `run_response` return `NEEDS_APPROVAL` for a destructive-flagged fixture in `backend/agents/response.py` (depends on T028) per SD9
- [x] T030 [US3] Activate the `supervisor_routing` gate in `config/eval_thresholds.yaml` (`threshold: 1.0`, `required: true`, unit-tier, provider-independent), alongside the existing `smoke`/`redaction`/`llm_provider` gates per SD10

**Checkpoint**: Cap breaches and stage errors land in `escalated` without crashing the worker; destructive actions park; the routing eval gate is green. All three stories are independently functional.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Verify the cross-cutting guarantees and close out the docs.

- [x] T031 [P] Add an SC-006 guard test in `tests/unit/test_supervisor_no_llm.py` asserting the orchestration layer (`backend/services/supervisor.py`) imports no LLM client, and confirm the `import-linter` layering contract (`services → agents → repositories → infra`, `domain` isolated) still passes with the new modules
- [x] T032 [P] Add an SC-007 assertion that supervisor logs/spans for an incident with planted secret/PII contain no unredacted values (reusing the #2 redaction seam) in `tests/unit/test_supervisor_redaction.py`
- [x] T033 [P] Record SD1 (plain async FSM; LangGraph deferred to #10), the `0004` migration, and the `SupervisorSettings` choice in `DECISIONS.md`
- [x] T034 Run the `quickstart.md` validation across all four scenarios (fast-path noise, ambiguous full depth, critical-parks, fault paths) and confirm ≥80% coverage on the new code (depends on all prior phases)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — start immediately.
- **Foundational (Phase 2)**: Depends on Setup — **BLOCKS all user stories**.
- **User Stories (Phase 3–5)**: All depend on Foundational. US2 builds on US1's loop; US3 builds on US1's
  loop and US2's wiring. They are layered (P1 → P2 → P3), not fully parallel, because they edit the same
  `services/supervisor.py` spine.
- **Polish (Phase 6)**: Depends on all desired user stories.

### User Story Dependencies

- **US1 (P1)**: Depends only on Foundational. The irreducible MVP.
- **US2 (P2)**: Depends on US1 (extends `run_incident`'s `grounded` entry with routing). Independently testable.
- **US3 (P3)**: Depends on US1 (loop) and US2 (wiring for e2e). Independently testable.

### Within Each User Story

- Tests are written and **fail** before implementation.
- Foundational types/migration/settings before the supervisor.
- Transition table before the loop; loop before routing; routing before bounds/degradation/park.
- Fixtures before the tests that consume them.

### Parallel Opportunities

- Foundational: **T002, T004, T006** are different files with no interdeps → parallel. (T003 before T005.)
- US1 tests **T007, T008, T009** are different files → parallel. Implementation T010→T011→T012 is the same
  file (sequential).
- US2: tests **T013, T014** and fixtures **T015** parallel; implementation **T017, T018, T019** are
  different files → parallel; **T016** edits the supervisor (after T011), **T020** after T018+T019.
- US3: tests **T021–T025** and fixtures **T026** are different files → parallel; implementation
  T027→T028→T029 is the same file (sequential), **T030** (yaml) parallel.
- Polish: **T031, T032, T033** parallel; **T034** last.

---

## Parallel Example: User Story 1

```bash
# Launch all US1 unit tests together (write first, expect FAIL):
Task: "Unit test transition-table legality in tests/unit/test_supervisor_transitions.py"
Task: "Unit test the run loop to terminal disposition in tests/unit/test_supervisor_loop.py"
Task: "Unit test entry/idempotency by state class in tests/unit/test_supervisor_entry.py"

# Then implement the supervisor spine sequentially (same file):
#   T010 transition table → T011 run_incident loop → T012 spans + correlation id
```

## Parallel Example: Foundational

```bash
# Independent files — run together:
Task: "Define pure types in backend/domain/pipeline.py"            # T002
Task: "Add migration 0004_incident_disposition.py"                 # T004
Task: "Add SupervisorSettings to backend/infra/config.py"          # T006
# Then: T003 (domain/incident.py) → T005 (repositories/incidents.py)
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup.
2. Complete Phase 2: Foundational (CRITICAL — blocks all stories).
3. Complete Phase 3: US1 — the spine.
4. **STOP and VALIDATE**: a grounded incident reaches exactly one terminal disposition through faked stages,
   every transition persisted; nothing stuck in-flight.

This is milestone (a) in plan.md — a self-contained PR (≤ ~400 lines).

### Incremental Delivery

1. Setup + Foundational → contract/schema/settings ready.
2. US1 → spine green (unit) → **MVP**.
3. US2 → fast-path + adaptive depth + wiring green (unit + integration) → demoable via quickstart.
4. US3 → bounds + degradation + park + eval gate green (unit + e2e + eval) → production-shaped.

Each story is a green-in-CI increment and a separate ≤ ~400-line PR (the three plan milestones).

---

## Notes

- [P] = different files, no dependency on an incomplete task.
- The supervisor is the **single writer** of `status`/`disposition`; stages are pure handlers (no DB, no
  action capability) — the security boundary is structural, not prompted (Constitution III).
- The supervisor holds **no LLM client** (SC-006) and adds **no new dependency / service / container**.
- "Done" = unit + integration + e2e + the supervisor-routing eval gate all green in CI, ≥80% coverage on new
  code, redaction holding (SC-007).
- Commit after each task or logical group; stop at any checkpoint to validate a story independently.
