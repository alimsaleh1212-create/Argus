# Implementation Plan: Response & Remediation Agent

**Branch**: `010-response-remediation` | **Date**: 2026-06-10 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/010-response-remediation/spec.md`

## Summary

Replace the `run_response` **stub** with the pipeline's only **acting** stage — selecting a playbook,
auto-executing low-risk reversible actions, and raising the **human-in-the-loop approval interrupt** for
destructive ones — and **complete the supervisor's reserved interrupt/resume seam** with the timeout and audit
that Component #5 explicitly deferred to #10.

The forward path mirrors triage/enrichment in shape but is **determinism-first** (clarification Q1): for a
confirmed incident (only enrichment-`advance` incidents reach `RESPONDING`), a deterministic catalog match
selects the playbook with **no** LLM call; only the ambiguous long tail (multiple candidate playbooks, failed
preconditions, conflicting evidence) makes **at most one** structured-output `LlmClient` call (#3). A **pure,
config-backed, default-deny policy** then classifies each proposed action:

- **auto-execute** (low-risk, reversible — add-to-watchlist, open-ticket, enrich-and-tag) → execute now against
  the **mock** executors, writing an **audit row** per execution; if the plan is auto-only → `RESOLVED`
  (`auto_remediated`).
- **approval-required** (destructive — isolate-host, disable-user, block-IP) → return `NEEDS_APPROVAL`; the
  supervisor parks the incident in `AWAITING_APPROVAL` (this edge already exists). A `pending_approval` record
  is written with the proposed action(s), rationale, and a timeout deadline.

A human approve/reject arrives through the **backend approvals endpoint this component owns** (clarification
Q3). On **approve**, the incident resumes `AWAITING_APPROVAL → RESPONDING` and the **response stage re-runs to
execute** the approved plan — because only the response stage holds action tools (Constitution III), execution
**must** go through it; the re-run makes **no** LLM call, executes, audits (actor = the deciding human), and
resolves `remediated`. On **reject** → `RESOLVED` (`rejected_by_human`), nothing executed. A **timeout
sweeper** in the worker expires pending approvals past their deadline → `ESCALATED` (`approval_expired`),
nothing executed (fail-safe). Every executed action is **idempotent** (no double-execution / duplicate audit
row on retry or duplicate resume).

**Honest semantics (clarification Q4/Q5).** Every audit `outcome` and the `auto_remediated`/`remediated`
dispositions denote the action was **applied** — never "threat eliminated"; v1 cannot observe efficacy against
a mock environment. The `ActionResult` contract reserves a `verification` field and a `remediation_unverified`
disposition (unused in v1); the **post-remediation verification + feedback loop is the designed §v2c section**,
implemented at the T2 checkpoint — the layering contract is not traded.

**What makes this a "big" spec (≈2 days, three internal milestones — Constitution I).** Unlike enrichment
(pure drop-in), this component genuinely **writes** and **completes the supervisor seam**:
- **New persistence** — `approval_requests` + `audit_log` tables (migration **0006**) and two repositories;
  the **incidents table is unchanged** (status/disposition are already text — the new disposition values need
  no migration), so the supervisor stays the single writer of incident state.
- **Bounded supervisor completion** (the reserved #10 surface, *not* a redesign of #5/#7 routing): finish
  `resume_incident` to re-drive execution on approve; add the `expire_incident` edge; let the
  `(RESPONDING, RESOLVED)` edge pass the handler-proposed disposition through so auto vs approved are
  distinguishable; add two disposition constants.
- **Runs in two processes** — the forward path + timeout sweeper in the `worker`, the approval resume in the
  `api` (which must now register `SupervisorProvider`).
- **Milestones:** (a) auto-path green (low-risk → executed + audited → resolved); (b) interrupt green
  (destructive → parked, nothing executed); (c) resume green (approve executes, reject abandons, timeout
  expires — all idempotent).

## Technical Context

**Language/Version**: Python 3.12 (pinned, repo-wide `uv` project)

**Primary Dependencies**: existing only — the `LlmClient` seam (`backend/infra/llm.py`, #3), the `Supervisor`
+ stage-handler seam + reserved `resume_incident` (`backend/services/supervisor.py`, #7/#5), the
`IncidentRepository` (#4), FastAPI (`routers/approvals.py`, reserved in #1), async SQLAlchemy + **Alembic**
(new migration), pydantic v2, `structlog`/OpenTelemetry via the #2 seam. **New first-party modules only** —
mock action executors (`backend/infra/executors.py`), two repositories, pure types (`domain/response.py`). **No
new third-party package, no new container/service.**

**Storage**: **NEW (the one schema change of this component)** — Postgres `approval_requests` (the parked
pending-approval record + its lifecycle) and `audit_log` (append-only accountability ledger), both FK →
`incidents(id)` (migration **0006**, revises `0005`). The **`incidents` table is unchanged** — new disposition
*values* (`remediated`, `approval_expired`) are plain text in the existing `disposition` column. The supervisor
remains the single writer of `incidents`; the two new tables are owned exclusively by this component's
repositories.

**Testing**: `pytest` — **unit** (deterministic playbook match; the pure default-deny policy classification;
`RemediationPlan`/`ActionResult` validation; idempotency guard; fail-closed mapping; LLM-error → `ToolError`;
the structural "only response holds executors" assertion — executors + LLM + session mocked), **integration**
(the response handler against a real Postgres for audit/approval writes + a real `LlmClient` on **both**
providers for the ambiguous-selection path; the approvals endpoint against a real session; the timeout
sweeper), **e2e** (full-depth incident: worker → … → `RESPONDING` → auto-resolved **and** a destructive
incident → `AWAITING_APPROVAL` → approve-resume → `remediated` / reject → `rejected_by_human` / timeout →
`approval_expired`, LLM faked at the driver boundary), **eval** (extend the committed provider-independent
**supervisor-routing** gate with response/remediation fixtures — no new gate). Coverage on the
action-execution + approval boundary is held **above** the 80% floor (Constitution II).

**Target Platform**: Linux. Runs in **both** the `worker` container (forward stage + timeout sweeper task) and
the `api` container (approval-resume endpoint) — same image.

**Project Type**: Web-service backend (layered modular monolith `backend/`). Touches `agents/response.py`,
`domain/response.py` (NEW), `services/supervisor.py` (bounded completion of the reserved seam),
`repositories/` (two new), `db/migrations/versions/0006_*` (NEW), `routers/approvals.py`, `infra/config.py`
(+`ResponseSettings`), `infra/executors.py` (NEW mock executors), `supervisor_provider.py` (wire real
handler), `worker.py` (timeout sweeper), `main.py` (register `SupervisorProvider` in the API).

**Performance Goals**: **at most one** LLM call per incident, **zero** when the playbook match is
deterministic (clarification Q1 / FR-003); the resume execution path makes **zero** LLM calls; mock executors
are in-process and fast; the timeout sweeper is periodic and off the synchronous path. Reported
`tokens_consumed` feeds the supervisor's per-incident cap (SC-005).

**Constraints**: only the response stage holds action tools (DI, structural — Constitution III / FR-002); the
auto/approval boundary is **config-backed default-deny** (FR-004) and the action set is an **allowlisted
catalog** (FR-005); **HITL** for every destructive action (FR-007); execution is **idempotent** (FR-011);
**fail-closed** on malformed/unvalidated reasoning (FR-016); redaction (#2) on every log/trace/dashboard-bound
view; audit rows persist the real target for accountability and are redacted only when surfaced. v1 records
**applied**, never **eliminated** (FR-020).

**Scale/Scope**: single-org, replayed sample alerts; a small playbook catalog; a single `admin` actor; mock
remediation targets.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design — still passing (the design adds
exactly the persistence + reserved-seam completion the spec scopes, no new service/dependency, and keeps every
structural boundary).*

- [x] **I. Spec-Driven Delivery**: `spec.md` precedes code; "done" = unit + integration + e2e green in CI and
      pushed. This is a **"big" spec** → it commits at each internal milestone (a) auto-path, (b) interrupt,
      (c) resume+timeout, never going dark. PRs stay focused (the milestones map to ≤~400-line slices).
- [x] **II. Test-First, Three-Tier, Eval-Gated (NON-NEGOTIABLE)**: unit/integration/e2e planned; coverage
      **above** the 80% floor on the remediation + safety boundary (the action executors, the default-deny
      policy, idempotency, the HITL park/resume). The eval **extends** the committed provider-independent
      **supervisor-routing** gate with response fixtures (auto→resolved, destructive→awaiting_approval,
      approve→remediated, reject→rejected_by_human, timeout→approval_expired); the ambiguous-selection LLM call
      is exercised on **both** providers in integration. No new gate (FR-019).
- [x] **III. Structural Security Boundaries**: response is the **only** stage injected action executors —
      enforced by the frozen `StageHandler` signature + closure DI (`make_response_handler(...)`); triage and
      enrichment are built without executors (asserted structurally, SC-004). The action set is an
      **allowlisted catalog** and the LLM cannot cause execution outside it (FR-005); a destructive action can
      **never** auto-execute (default-deny → HITL), so an injected "isolate every host" is at worst a parked
      proposal (SC-002). All inputs are untrusted data; logs/traces/audit views are redacted (#2). Dedicated
      injection rails are #11 — the structural boundary is the v1 net.
- [x] **IV. Determinism First; Agents Only for the Ambiguous Long Tail**: the supervisor stays a deterministic
      state machine; playbook selection is **determinism-first** — unambiguous incidents are matched with
      **no** LLM call, the LLM is reserved for the ambiguous tail (multiple candidates / failed preconditions /
      conflict), and the auto/approval classification is a **pure config-backed** function (never the LLM's
      say-so). The handler reasons only over supplied evidence (incident + enrichment report + catalog +
      policy), emits an **evidence-cited** rationale, and **escalates** when it cannot confidently select
      (FR-013). Token usage is reported into the cap.
- [x] **V. Human-in-the-Loop for Consequential Action** *(the centerpiece)*: destructive actions raise the
      approval interrupt → `AWAITING_APPROVAL`; the auto/approval boundary is a **config-backed policy**
      (default-deny), defended in `DECISIONS.md`, never hardcoded in agent logic; pending approvals have a
      **configured timeout** with an explicit terminal state (`approval_expired`, nothing executed); **every
      executed action writes an audit row** (actor / action / target / timestamp / outcome). Approve/reject
      resume on an explicit human decision only; the agent never overrides a rejection.
- [x] **VI. Temporal Memory & Graceful Degradation**: response does **not** read/write temporal memory in v1
      (the terminal-state episode write stays the #6 worker step; the §v2c verification write-back is T2).
      Graceful degradation in full: an executor failure returns a structured retryable `ToolError`
      (transient→retry, persistent→escalate), a partial multi-action failure is **recorded not swallowed**, and
      the worker never crashes (FR-014). No single point of failure introduced.
- [x] **VII. Production Engineering Standards**: async throughout; **DI via a handler-factory closure**
      (`make_response_handler(llm, session_factory, executors, policy, catalog, cfg)`) — which is what enforces
      Principle III and mocks every dependency; Pydantic at the boundary (`RemediationPlan`, `RemediationAction`,
      `ActionResult`, `ResponseSettings`); structured logging with trace id; the timeout sweeper + span export
      off the synchronous path; typed `pydantic-settings` (`extra="forbid"`, new `"response"` section);
      `uv` for deps. New tables via Alembic; new repositories isolate all SQL (layering-clean).
- [x] **Scope & Tiers**: within v1 (T1) — mock environment only, single `admin`, no rollback, no per-action
      granularity, no live feeds, no ML detector, no 4th agent, no LLM supervisor. The **§v2c** verification +
      feedback loop is **designed, not implemented** (gated behind the day-9 T1 tag). Respects the layering
      contract (#10 depends on #7/#3 — both done).

**Result: PASS.** No entries in Complexity Tracking. (The new migration and the bounded supervisor changes are
**in scope** — they realize the reserved `resume_incident`/`awaiting_approval`/timeout/audit surface Component
#5 explicitly assigned to #10 — not constitution violations. See `research.md` RD2/RD5/RD8 for the
reconciliation with the spec's "don't modify #5/#7 routing" boundary.)

## Project Structure

### Documentation (this feature)

```text
specs/010-response-remediation/
├── plan.md              # This file
├── research.md          # Phase 0 — design decisions (RD1…RD12)
├── data-model.md        # Phase 1 — domain types, the two new tables, disposition map, evidence_patch
├── quickstart.md        # Phase 1 — run & verify (auto-path, interrupt, approve/reject/timeout, eval)
├── contracts/           # Phase 1
│   ├── response-handler-contract.md   # make_response_handler: select → classify → execute/park → resume
│   ├── approvals-api-contract.md      # GET /approvals, POST /approvals/{id}/decision (approve|reject)
│   ├── remediation-data-contract.md   # approval_requests + audit_log tables (migration 0006) + repos
│   └── response-eval.md               # the supervisor-routing-gate extension (response fixtures)
├── checklists/          # (pre-existing) requirements.md
└── tasks.md             # Phase 2 — created by /speckit-tasks (NOT here)
```

### Source Code (repository root)

```text
backend/
├── domain/
│   └── response.py             # NEW — pure types: ActionType, RiskClass, RemediationAction,
│                               #   RemediationPlan, ActionResult (incl. reserved `verification`),
│                               #   ActionExecutor Protocol, ApprovalDecision/ApprovalStatus enums
│                               #   (importable by #12/eval; no outward imports)
├── agents/
│   └── response.py             # REPLACE stub — make_response_handler(llm, session_factory, executors,
│                               #   policy, catalog, cfg): select_playbook (deterministic-first; LLM only
│                               #   for ambiguous), classify (pure default-deny), execute auto + audit,
│                               #   park destructive (write approval_request) OR — on resume — execute the
│                               #   approved plan (no LLM); idempotency guard; LlmError → ToolError
├── services/
│   └── supervisor.py           # EXTEND (reserved-seam completion only): finish resume_incident (approve
│                               #   re-drives run_incident to execute); add expire_incident (→ ESCALATED,
│                               #   approval_expired); (RESPONDING,RESOLVED) edge → disposition passthrough;
│                               #   add DISP_REMEDIATED, DISP_APPROVAL_EXPIRED
├── repositories/
│   ├── approvals.py            # NEW — ApprovalRepository: create_pending, get, resolve (guarded
│   │                           #   pending→approved/rejected/expired), list_pending_expired(now)
│   └── audit.py                # NEW — AuditRepository: append(row), list_for_incident (append-only)
├── infra/
│   ├── config.py               # EXTEND — ResponseSettings (+ register "response" section + Settings field)
│   └── executors.py            # NEW — mock ActionExecutor registry (one per catalog action type) →
│                               #   ActionResult(applied/failed); real executors are a drop-in later
├── routers/
│   └── approvals.py            # REPLACE reserved stub — GET /approvals (pending queue for #12),
│                               #   POST /approvals/{id}/decision {approve|reject} → records + resumes
├── supervisor_provider.py      # EXTEND — build the real response handler from container.{llm,db_engine}
│                               #   + settings.response (fall back to the existing stub when no LLM/DB)
├── worker.py                   # EXTEND — spawn the periodic approval-timeout sweeper task alongside _run
├── main.py                     # EXTEND — _bootstrap_providers registers SupervisorProvider (API needs the
│                               #   supervisor for the approval-resume path)
└── data/
    └── playbooks/              # NEW — config-backed playbook catalog (criteria → ordered actions)

config/
└── eval_thresholds.yaml        # EXTEND — response fixtures on the existing `supervisor-routing` gate

db/migrations/versions/
└── 0006_response_remediation.py  # NEW — approval_requests + audit_log (revises 0005)

tests/
├── unit/                       # test_response_select / _policy / _plan / _idempotency / _errors / _boundary
├── integration/               # test_response_provider (real PG audit/approval + LlmClient both providers),
│                              #   test_approvals_api, test_timeout_sweeper
├── e2e/                       # extend the spine e2e: auto-resolved + park→approve/reject/timeout
├── eval/                      # extend test_supervisor_routing_gate with response fixtures
└── fixtures/                  # response routing labels (incident → expected next state + disposition)
```

**Structure Decision**: Modular monolith `backend/`. Pure types live in `domain/response.py` (isolated, like
`domain/triage.py`/`domain/enrichment.py`), importable by the dashboard (#12) and eval without pulling `infra`.
All selection/classification/execution + the pure default-deny policy live in `agents/response.py`, which
imports only `domain.*` (layering-clean; executors, the session factory, catalog, and policy arrive by closure
injection — the same mechanism that keeps action tools out of triage/enrichment). The two new tables get their
**own repositories** (`repositories/approvals.py`, `repositories/audit.py`) so all SQL stays in the repository
layer and the `incidents` table keeps its single writer. The **only** edits to `services/supervisor.py` are the
completion of the reserved `resume_incident`/`expire_incident` mechanism and the response-owned disposition
passthrough — **not** new routing/cap logic (RD2/RD8). The `api` now registers `SupervisorProvider` so the
approval endpoint can drive resume synchronously through the supervisor singleton (RD4).

## Complexity Tracking

> No Constitution Check violations — this table is intentionally empty. This component adds the persistence
> (`approval_requests` + `audit_log`) and completes the `resume_incident`/`awaiting_approval`/timeout/audit
> surface that Component #5 **reserved for #10** — both are in declared scope for this "big" spec, not
> unjustified complexity. It introduces no new third-party dependency, service, or container, and preserves
> every structural boundary (action tools only in response; supervisor the single writer of `incidents`).
