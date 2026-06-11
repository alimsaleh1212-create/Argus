---
description: "Task list — Response & Remediation Agent (#10)"
---

# Tasks: Response & Remediation Agent

**Input**: Design documents from `specs/010-response-remediation/`
**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md),
[data-model.md](./data-model.md), [contracts/](./contracts/), [quickstart.md](./quickstart.md)

**Tests**: REQUIRED (Constitution II — Test-First, Three-Tier, Eval-Gated, NON-NEGOTIABLE). Coverage on the
action-execution + approval boundary is held **above** the 80% floor.

**Organization**: grouped by user story (US1/US2/US3) for independent implementation/testing. This is a **"big"
spec** — the three internal milestones (commit at each, Constitution I) map as: **(a) auto-path = US1**,
**(b) interrupt/park = US2 park tasks**, **(c) resume+timeout = US2 resume/timeout tasks**; US3 hardens both.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different files, no dependency on an incomplete task)
- **[Story]**: US1 / US2 / US3 (Setup/Foundational/Polish carry no story label)
- Exact file paths are in each task.

---

## Phase 1: Setup (Shared, component-level)

**Purpose**: typed config, pure types, and the playbook catalog the rest of the component builds on.

- [X] T00- [ ] T001 [P] Add `ResponseSettings` to [backend/infra/config.py](../../backend/infra/config.py) (fields per [data-model.md](./data-model.md) §6: `auto_execute_actions`, `select_min_confidence`, `approval_timeout_s`, `sweep_interval_s`, `catalog_dir`, `max_output_tokens`, `temperature`, `prompt_version`), add `"response"` to `_KNOWN_SENTINEL_SECTIONS`, and add the `response: ResponseSettings` field on `Settings`.
- [X] T00- [ ] T002 [P] Create pure types in `backend/domain/response.py` — enums (`ActionType`, `RiskClass`, `ActionStatus`, `VerificationVerdict` *(reserved §v2c)*, `ApprovalStatus`, `ApprovalDecision`) + models (`RemediationAction`, `ActionResult` incl. reserved `verification`, `RemediationPlan`) + the `ActionExecutor` Protocol, per [data-model.md](./data-model.md) §1–5. No outward imports (domain-isolation contract).
- [X] T00- [ ] T003 [P] Create the playbook catalog data file(s) under `backend/data/playbooks/` (criteria → ordered `ActionType`s + preconditions) and a `load_playbook_catalog(catalog_dir)` loader in `backend/agents/response.py`, per [research.md](./research.md) RD10.

---

## Phase 2: Foundational (Blocking prerequisites)

**Purpose**: persistence, executors, and the supervisor disposition surface that ALL stories depend on.

**⚠️ CRITICAL**: No user-story work begins until this phase is complete.

- [X] T00- [ ] T004 [P] Create Alembic migration `backend/db/migrations/versions/0006_response_remediation.py` (`revision="0006"`, `down_revision="0005"`) creating `approval_requests` + `audit_log` with indexes + partial-unique constraints, per [contracts/remediation-data-contract.md](./contracts/remediation-data-contract.md) and [data-model.md](./data-model.md) §8. Reversible `downgrade()`.
- [X] T00- [ ] T005 [P] Implement `AuditRepository` in `backend/repositories/audit.py` (`append` idempotent on the `applied` key → `bool`; `list_for_incident`), append-only, per [contracts/remediation-data-contract.md](./contracts/remediation-data-contract.md).
- [X] T00- [ ] T006 [P] Implement `ApprovalRepository` in `backend/repositories/approvals.py` (`create_pending`, `get`, `get_approved_pending_for`, guarded `resolve`, `list_pending_expired`), per [contracts/remediation-data-contract.md](./contracts/remediation-data-contract.md).
- [X] T00- [ ] T007 [P] Implement the mock `ActionExecutor` registry in `backend/infra/executors.py` (one executor per `ActionType` → `ActionResult(status=applied|failed)`), per [research.md](./research.md) RD9.
- [X] T00- [ ] T008 Extend the disposition surface in [backend/services/supervisor.py](../../backend/services/supervisor.py): add `DISP_REMEDIATED="remediated"`, `DISP_APPROVAL_EXPIRED="approval_expired"`, `DISP_REMEDIATION_UNVERIFIED="remediation_unverified"` *(reserved)*; change the `(RESPONDING, StageOutcome.RESOLVED)` table edge disposition to `None` so the handler-proposed disposition passes through, per [research.md](./research.md) RD8 / [data-model.md](./data-model.md) §7. (Same file as T020/T021 — keep sequential.)

**Checkpoint**: persistence + executors + disposition vocabulary ready.

---

## Phase 3: User Story 1 — Auto-remediation with an audit trail (Priority: P1) 🎯 MVP · milestone (a)

**Goal**: a confirmed incident whose playbook yields only low-risk actions auto-executes against the mock
environment, writes an audit row per action, and resolves `auto_remediated` — determinism-first selection
(no LLM when unambiguous).

**Independent Test**: replay a confirmed, deterministically-matched, auto-only incident → `responding → resolved`
(`auto_remediated`), one `audit_log` row per action, `evidence["response"]` populated, zero LLM tokens.

### Tests for User Story 1 (write first; ensure they fail)

- [X] T00- [ ] T009 [P] [US1] Unit: deterministic `select_playbook` (no LLM) + ambiguous → one LLM call, in `tests/unit/test_response_select.py`.
- [X] T0- [ ] T010 [P] [US1] Unit: pure default-deny `classify()` (allowlist → AUTO, else APPROVAL_REQUIRED; unknown → non-executable) in `tests/unit/test_response_policy.py`.
- [X] T0- [ ] T011 [P] [US1] Unit: `RemediationPlan` / `RemediationAction` / `ActionResult` validation (incl. honest `status`, reserved `verification=None`) in `tests/unit/test_response_plan.py`.

### Implementation for User Story 1

- [X] T0- [ ] T012 [US1] Implement `select_playbook(incident, catalog, llm, cfg)` in `backend/agents/response.py` — deterministic match first; one structured `LlmClient` call only for the ambiguous tail; below `select_min_confidence` / no fit → fail-closed signal, per [contracts/response-handler-contract.md](./contracts/response-handler-contract.md) Pass A step 1.
- [X] T0- [ ] T013 [US1] Implement the pure `classify(plan, cfg)` default-deny policy in `backend/agents/response.py`, per [research.md](./research.md) RD10. (Same file as T012 — sequential.)
- [X] T0- [ ] T014 [US1] Implement `make_response_handler(...)` forward Pass-A **auto branch** in `backend/agents/response.py`: execute auto actions via injected `executors` + `AuditRepository.append`, return `StageResult(RESOLVED, disposition="auto_remediated", evidence_patch={"response": …})`, per [contracts/response-handler-contract.md](./contracts/response-handler-contract.md). (Same file — sequential.)
- [X] T0- [ ] T015 [US1] Wire the real handler in [backend/supervisor_provider.py](../../backend/supervisor_provider.py): build from `container.llm` + a `session_factory` from `container.db_engine` + the mock `executors` + the loaded catalog + `settings.response`; fall back to the existing `run_response` stub when `llm` **or** `db_engine` is absent.
- [X] T0- [ ] T016 [P] [US1] Integration: auto-path handler against real Postgres (audit rows written) + a real `LlmClient` on **both** providers (ambiguous selection), in `tests/integration/test_response_provider.py`.
- [X] T0- [ ] T017 [P] [US1] e2e (auto-path portion): confirmed auto-only incident driven worker → … → `RESPONDING` → `resolved`/`auto_remediated` with audit rows, LLM faked at the driver, in `tests/e2e/test_response_e2e.py`.

**Checkpoint** (milestone a): auto-remediation + audit fully functional and testable. **Commit.**

---

## Phase 4: User Story 2 — Destructive actions park for approval & resume on decision (Priority: P1) · milestones (b)+(c)

**Goal**: destructive actions park in `awaiting_approval` (nothing executed); a human approve/reject through the
#10-owned endpoint resumes the pipeline (approve re-runs the response stage to execute → `remediated`; reject →
`rejected_by_human`); a timeout sweeper expires past-deadline approvals → `approval_expired`.

**Independent Test**: a destructive incident → `awaiting_approval`, nothing executed; approve → `remediated`;
reject → `rejected_by_human`; no decision before the deadline → `approval_expired`. None execute pre-approval.

### Tests for User Story 2 (write first; ensure they fail)

- [X] T0- [ ] T018 [P] [US2] Unit: park branch (writes `pending` approval + returns `NEEDS_APPROVAL`, nothing destructive executed) and Pass-B resume execution (executes the approved plan, **no** LLM call) in `tests/unit/test_response_park_resume.py`.

### Implementation for User Story 2

- [X] T0- [ ] T019 [US2] Extend `make_response_handler` in `backend/agents/response.py`: **park** destructive actions (`ApprovalRepository.create_pending` with `deadline_at` + return `NEEDS_APPROVAL`; co-proposed auto actions still execute+audit) **and** **Pass-B** resume (`get_approved_pending_for` → execute → `audit_log` actor=human → `RESOLVED`/`remediated`), per [contracts/response-handler-contract.md](./contracts/response-handler-contract.md) Pass A step 4 / Pass B. *(milestone b = park; milestone c = Pass-B)*
- [X] T0- [ ] T020 [US2] Complete `resume_incident` in [backend/services/supervisor.py](../../backend/services/supervisor.py): approve → advance `AWAITING_APPROVAL → RESPONDING` then re-drive `run_incident` to execute; reject → `RESOLVED`/`rejected_by_human` + audit row, per [research.md](./research.md) RD3. (Same file as T008/T021 — sequential.)
- [X] T0- [ ] T021 [US2] Add `expire_incident` to [backend/services/supervisor.py](../../backend/services/supervisor.py): `AWAITING_APPROVAL → ESCALATED` (`approval_expired`), per [research.md](./research.md) RD7. (Same file — sequential.)
- [X] T0- [ ] T022 [US2] Implement the approvals endpoints in [backend/routers/approvals.py](../../backend/routers/approvals.py): `GET /approvals` (pending queue) + `POST /approvals/{id}/decision` (resolve guarded → `supervisor.resume_incident`; 404/409/422), per [contracts/approvals-api-contract.md](./contracts/approvals-api-contract.md).
- [X] T0- [ ] T023 [US2] Add `get_approval_repo` + `get_audit_repo` session-scoped FastAPI providers in [backend/dependencies.py](../../backend/dependencies.py) (mirror `get_incident_repo`).
- [X] T0- [ ] T024 [US2] Register `SupervisorProvider` in [backend/main.py](../../backend/main.py) `_bootstrap_providers` so the API can drive the approval-resume path, per [research.md](./research.md) RD4.
- [X] T0- [ ] T025 [US2] Implement the periodic approval-timeout sweeper task in [backend/worker.py](../../backend/worker.py) (every `sweep_interval_s`: `list_pending_expired` → `expire_incident` + approval `status=expired` + audit `not_executed`), spawned alongside `_run`, per [research.md](./research.md) RD7.
- [X] T0- [ ] T026 [P] [US2] Integration: approvals API approve + reject against a real session (records + resumes) in `tests/integration/test_approvals_api.py`.
- [X] T0- [ ] T027 [P] [US2] Integration: timeout sweeper expires past-deadline pending approvals in `tests/integration/test_timeout_sweeper.py`.
- [X] T0- [ ] T028 [P] [US2] e2e (interrupt portion): destructive incident → `awaiting_approval` → approve→`remediated` / reject→`rejected_by_human` / timeout→`approval_expired`, in `tests/e2e/test_response_e2e.py`.

**Checkpoint** (milestones b+c): interrupt, resume, and timeout fully functional. **Commit at b, then at c.**

---

## Phase 5: User Story 3 — Bounded, fail-closed, structural boundary, idempotent (Priority: P2)

**Goal**: only the response stage holds action tools; unknown/unmapped actions never execute; an injected
"isolate every host" still parks; malformed/timeout reasoning fails closed; executor failures degrade
gracefully; execution is idempotent; at most one LLM call with token reporting.

**Independent Test**: structural assertion (triage/enrichment have no executors); injected-instruction →
parked not executed; catalog-miss → escalate; LLM malformed/timeout → escalate, worker survives; duplicate
resume → one execution / one audit row.

### Tests for User Story 3 (write first; ensure they fail)

- [X] T0- [ ] T029 [P] [US3] Unit: structural boundary — only the response stage is injected executors; triage/enrichment have none, in `tests/unit/test_response_boundary.py`.
- [X] T030 [P] [US3] Unit: idempotency — duplicate execute / duplicate resume → exactly one action + one audit row, in `tests/unit/test_response_idempotency.py`.
- [X] T031 [P] [US3] Unit: fail-closed + error mapping (malformed/timeout LLM → `ToolError`; executor failure → retryable; catalog-miss/precondition-fail → ESCALATE; injected action stays parked), in `tests/unit/test_response_errors.py`.

### Implementation for User Story 3

- [X] T032 [US3] Add idempotency guards in `backend/agents/response.py` (check `audit_log` for an existing `applied` row by `idempotency_key` before executing; guard duplicate resume), per [research.md](./research.md) RD6. (Same file — sequential.)
- [X] T033 [US3] Add fail-closed handling in `backend/agents/response.py`: `LlmError`/malformed → `ToolError` (reuse the triage mapping), executor failure → retryable/persistent → ESCALATE with partial-failure recorded, catalog-miss/failed-precondition → ESCALATE (`escalated_response`), per [contracts/response-handler-contract.md](./contracts/response-handler-contract.md). (Same file — sequential.)
- [X] T034 [US3] Enforce allowlisted-catalog containment + at-most-one-LLM + token reporting in `backend/agents/response.py` (an action outside the catalog never reaches an executor; report `tokens_consumed`), per FR-005/FR-003. (Same file — sequential.)
- [X] T035 [P] [US3] Integration: degradation/failure suite (executor failure, LLM timeout/malformed, duplicate resume) extending `tests/integration/test_response_provider.py`.

**Checkpoint**: the safety + robustness boundary is verified above the coverage floor.

---

## Phase 6: Polish & Cross-Cutting Concerns

- [X] T036 [P] Extend the **supervisor-routing** eval gate with response fixtures in [config/eval_thresholds.yaml](../../config/eval_thresholds.yaml) + `tests/eval/test_supervisor_routing_gate.py` + `tests/fixtures/` (auto→resolved/auto_remediated, destructive→awaiting_approval, approve→remediated, reject→rejected_by_human, timeout→approval_expired, no-playbook→escalated_response), runnable on both providers, per [contracts/response-eval.md](./contracts/response-eval.md).
- [X] T037 [P] Record `DECISIONS.md` entries: the auto/approval allowlist (Constitution V), the resume re-run boundary (RD3), the synchronous API resume + Option-B roadmap (RD4), and the bounded supervisor edits (RD2/RD8).
- [X] T038 [P] Confirm redaction (#2) on audit/log/dashboard-bound views and update [quickstart.md](./quickstart.md) verification + docstrings; verify coverage on the action/approval boundary exceeds the floor.
- [X] T039 Run the full three-tier suite + the eval gate on **both** LLM providers; confirm green before each milestone commit (Constitution II).

---

## Dependencies & Execution Order

### Phase dependencies

- **Setup (P1)** → **Foundational (P2)** → **US1 (P3)** → **US2 (P4)** → **US3 (P5)** → **Polish (P6)**.
- Foundational blocks all stories.

### Story dependencies (honest — not fully independent)

- **US1 (P1)**: depends on Setup + Foundational. The MVP.
- **US2 (P1)**: depends on **US1** (reuses the execute+audit machinery on the resume path) + Foundational
  (`ApprovalRepository`, supervisor disposition surface). Spec sequences US1 first for exactly this reason.
- **US3 (P2)**: depends on **US1 + US2** (hardens the handler + boundary they create).

### Same-file sequencing (cannot be parallel)

- `backend/agents/response.py`: T012 → T013 → T014 (US1) → T019 (US2) → T032 → T033 → T034 (US3).
- `backend/services/supervisor.py`: T008 (Foundational) → T020 → T021 (US2).
- `tests/integration/test_response_provider.py`: T016 (US1) → T035 (US3 extends).
- `tests/e2e/test_response_e2e.py`: T017 (US1) → T028 (US2 extends).

### Parallel opportunities

- **Setup**: T001, T002, T003 in parallel.
- **Foundational**: T004, T005, T006, T007, T008 in parallel (all different files).
- **Per story tests**: T009/T010/T011 (US1); T029/T030/T031 (US3) in parallel.
- **US2 integration/e2e**: T026, T027, T028 in parallel (different files).
- **Polish**: T036, T037, T038 in parallel.

---

## Parallel Example: Foundational (Phase 2)

```bash
Task: "Create migration 0006_response_remediation.py (approval_requests + audit_log)"
Task: "Implement AuditRepository in backend/repositories/audit.py"
Task: "Implement ApprovalRepository in backend/repositories/approvals.py"
Task: "Implement mock ActionExecutor registry in backend/infra/executors.py"
Task: "Extend disposition surface in backend/services/supervisor.py"
```

---

## Implementation Strategy

### MVP first (milestone a)

1. Phase 1 Setup → 2. Phase 2 Foundational → 3. Phase 3 US1 → **STOP & validate** the auto-path independently
(`auto_remediated` + audit). This alone is a complete, demoable response stage. **Commit (milestone a).**

### Incremental delivery (the three "big"-spec milestones)

- **(a)** US1 — auto-remediation + audit → commit.
- **(b)** US2 park tasks (T019 park branch, T028 park assertions) — destructive parks, nothing executed → commit.
- **(c)** US2 resume/timeout (T019 Pass-B, T020/T021/T022/T024/T025) — approve/reject/timeout resume correctly,
  idempotent → commit.
- Then **US3** hardening + **Polish** (eval gate, DECISIONS, both-provider run) before the spec is "done".

### Notes

- [P] = different files, no incomplete dependency. Verify each test fails before implementing.
- Commit at each milestone (Constitution I — never go dark inside a big spec).
- The structural boundary (executors injected to the response stage only) and idempotency (no double
  remediation) carry the **highest coverage bar** in the component.
