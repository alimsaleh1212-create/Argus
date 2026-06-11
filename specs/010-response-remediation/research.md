# Phase 0 — Research & Design Decisions: Response & Remediation Agent (#10)

Decisions that resolve the spec into an implementation, grounded in the **existing** supervisor seam
(`services/supervisor.py`), the stage-handler contract (`domain/pipeline.py`), and the five clarifications
(Session 2026-06-10). Format: **Decision / Rationale / Alternatives**. No NEEDS CLARIFICATION remain.

---

## RD1 — Forward shape: determinism-first selection → pure policy → execute/park

**Decision.** The forward handler is: `select_playbook` (deterministic catalog match first; **one** structured
`LlmClient` call **only** when ambiguous) → build a `RemediationPlan` → **pure** `classify` (config-backed
default-deny) → execute auto actions (mock executors + audit) and/or park destructive ones. At most **one** LLM
call per incident; **zero** when the match is unambiguous; **zero** on the resume execution path.

**Rationale.** Clarification Q1 + Constitution IV ("using AI where determinism suffices is overengineering").
The existing `StageResult.tokens_consumed=0` default and the cap machinery already accommodate a zero-token
stage. Mirrors triage's `decide_outcome` purity and enrichment's "bounded reasoning + pure mapping."

**Alternatives.** *Always one LLM call* (clarification option B) — rejected: violates determinism-first; a
known technique → single playbook is a lookup. *Multi-step tool-calling agent* — rejected (spec Out of Scope;
overengineering).

---

## RD2 — The approval interrupt rides the existing supervisor seam (not LangGraph)

**Decision.** Realize the interrupt entirely on what #5/#7 already built: `StageOutcome.NEEDS_APPROVAL` and the
`(RESPONDING, NEEDS_APPROVAL) → (AWAITING_APPROVAL, awaiting_approval_destructive)` transition **already
exist**; `resume_incident(incident_id, decision, repo)` is the **reserved seam** (its docstring: "Interrupt
vehicle, timeout, audit rows, and action execution are Component #10"). #10 fills it. No LangGraph, no
checkpointer.

**Rationale.** Supersedes the brief's "LangGraph interrupt" (the supervisor is a plain async state machine —
Component #5 decision, already in the spec's Assumptions). Reuses tested transitions; minimal churn.

**Reconciliation with the spec's Out of Scope** ("don't modify #5/#7 routing"): completing the reserved
`resume_incident`/`awaiting_approval`/timeout/audit surface is exactly the mechanism #5 *reserved for #10*
("#10 owns mechanism/timeout/audit") — not new routing or cap logic. The routing table for GROUNDED/TRIAGING/
ENRICHING and the step/token cap are untouched.

**Alternatives.** A new LangGraph subgraph — rejected (contradicts the deterministic-supervisor decision, adds
a dependency). A separate parking table outside the state machine — rejected (the status already models it).

---

## RD3 — Approve **re-runs the response stage** to execute (preserves the action-tool boundary)

**Decision.** On approve the incident resumes `AWAITING_APPROVAL → RESPONDING` and the supervisor **re-runs the
response stage**, which detects the now-approved pending plan and **executes** it (no LLM call), audits (actor =
deciding human), and returns `RESOLVED` (`remediated`). Execution **never** happens inside `resume_incident` or
any non-response code.

**Rationale.** Constitution III: only the response stage holds action tools. If `resume_incident` (a supervisor
method) executed the action, action capability would leak outside the response stage. Re-entering `RESPONDING`
routes execution back through the only tool-holding stage — the structural boundary holds even on resume. The
handler distinguishes "first pass" vs "post-approval" by reading the `ApprovalRepository` for an
`approved`-status pending record for this incident (first pass: none → select+classify; post-approval: present
→ execute the recorded plan).

**Alternatives.** Execute in `resume_incident` — rejected (leaks action tools out of the response stage).
Persist the plan only in `incidents.evidence` and re-derive — rejected (the approval record is the
authoritative, queryable lifecycle; evidence is a denormalized mirror for the dashboard).

---

## RD4 — Approval-resume orchestration runs synchronously in the **API** via the supervisor singleton

**Decision.** The `POST /approvals/{id}/decision` endpoint (API process) validates the decision, records it on
the approval record, and calls `supervisor.resume_incident(...)`; for `approve` the supervisor re-drives
`run_incident` synchronously and the endpoint returns the resulting disposition. This requires the **API to
register `SupervisorProvider`** (it currently does not — `main.py._bootstrap_providers` stops at
vault/db/blob/obs/llm/cache/queue/corpus).

**Rationale.** v1 executors are **mock + in-process + fast**, so synchronous resume is safe and gives the
dashboard immediate feedback ("approve → resolved" in one request). The supervisor is the single source of
transition truth; reusing it avoids duplicating the state machine. Building `SupervisorProvider` in the API
also builds triage/enrichment handlers, but those tolerate missing `memory`/`intel` (best-effort, #9) and are
**never invoked** on the resume path (the incident is already `RESPONDING`).

**Alternatives.** *Option B — enqueue a resume job; the worker re-drives* — rejected for v1 as overengineering
(needs a resume queue/marker + a worker branch, since the worker loop is grounding-claim-specific). **Recorded
as the roadmap path** for a real, slow/remote executor (move execution off the request). *A bespoke
"approval-resume service" that bypasses the supervisor* — rejected (would re-implement transition guards).

---

## RD5 — New persistence: `approval_requests` + `audit_log` (migration 0006); `incidents` unchanged

**Decision.** Two new Postgres tables (migration **0006**, `down_revision="0005"`), each FK → `incidents(id)`:
- `approval_requests` — one row per parked incident: the proposed action(s) (JSONB plan), rationale, `status`
  (`pending`/`approved`/`rejected`/`expired`), `deadline_at`, and on resolution `decided_by`/`decided_at`.
- `audit_log` — append-only: `actor`, `action`, `target`, `outcome`, `created_at` (+ incident FK + an
  idempotency key). Owned exclusively by `repositories/audit.py` / `repositories/approvals.py`.

The **`incidents` table is not altered** — the new disposition *values* (`remediated`, `approval_expired`) are
plain text in the existing `disposition` column.

**Rationale.** The "single writer" principle is about **incident state**; the audit ledger and approval
lifecycle are this component's own stores (Constitution V mandates both). Separate tables keep `incidents`
clean and let the supervisor remain the sole `incidents` writer. Action tools (executors) write only to
`audit_log`; the response stage is the only writer of these tables.

**Alternatives.** Store approvals/audit inside `incidents.evidence` JSONB — rejected (no queryable lifecycle,
no append-only guarantee, no FK integrity, awkward for the dashboard queue). A single combined table —
rejected (different shapes/lifecycles; audit is append-only, approvals mutate).

---

## RD6 — Idempotency (FR-011 / SC-006)

**Decision.** Three guards: **(a) execution** — each action carries a deterministic idempotency key
`(incident_id, plan_id, action_type, target)`; before executing, the handler checks `audit_log` for an existing
`applied` row with that key (unique constraint backs it) and skips re-execution. **(b) resume** — the
supervisor's guarded `advance_status(expected=AWAITING_APPROVAL, …)` ensures only the first decision transitions
the incident; the approval record's `status` guard (`pending → …`) backs it at the data layer. **(c) duplicate/
late decision** — first explicit decision wins; subsequent or post-expiry decisions are recorded no-ops.

**Rationale.** A retry, duplicate resume, or re-delivered decision must never double-isolate a host or double
the audit trail (SC-006). The existing `advance_status` already returns `False` when the guard is lost — reuse
that pattern; add the data-layer status/idempotency constraints for defense in depth.

**Alternatives.** Best-effort/no guard — rejected (double remediation is a safety defect). Distributed lock —
rejected (overengineering; the DB guard + unique key suffice for a single-org single-worker deployment).

---

## RD7 — Timeout enforced by a periodic sweeper task in the worker

**Decision.** The `worker` spawns a periodic async task (alongside `_run`) that every
`response.sweep_interval_s` calls `ApprovalRepository.list_pending_expired(now)` and, for each, invokes
`supervisor.expire_incident` → `AWAITING_APPROVAL → ESCALATED` (`approval_expired`), sets the approval
`status=expired`, and writes an `audit_log` row (`actor=timeout`, `outcome=not_executed`). `deadline_at` is set
on the approval record at park time = `created_at + response.approval_timeout_s`.

**Rationale.** A deterministic terminal state requires something to *fire* the timeout (FR-009); lazy
expiry-on-access could leave an incident parked forever if never viewed. The worker is the natural long-running
async home; the sweep is off the synchronous path (Constitution VII). Both `approval_timeout_s` and
`sweep_interval_s` are config-backed.

**Alternatives.** Lazy expiry on dashboard read — rejected (non-deterministic terminal; demo-fragile). An
external cron/scheduler — rejected (adds an operational moving part; the in-worker task is simpler and
testable). `expire_incident` is a new supervisor method, parallel to `resume_incident` (RD2 reconciliation).

---

## RD8 — Disposition vocabulary: map spec names → canonical constants; one table-edge tweak

**Decision.** Concrete disposition constants in `services/supervisor.py`:

| Spec term (clarification Q2) | Canonical constant / value | Status |
|------------------------------|----------------------------|--------|
| auto path | `DISP_AUTO_REMEDIATED = "auto_remediated"` | **exists** |
| approved | `DISP_REMEDIATED = "remediated"` | **NEW** |
| rejected | `DISP_REJECTED_BY_HUMAN = "rejected_by_human"` | **exists** (resume reject path) |
| timeout | `DISP_APPROVAL_EXPIRED = "approval_expired"` | **NEW** |
| fail-closed | `DISP_ESCALATED_RESPONSE = "escalated_response"` | **exists** |

To distinguish *auto* vs *approved* on the shared `RESOLVED` outcome, change the
`(RESPONDING, StageOutcome.RESOLVED)` edge's table disposition from the hardcoded `auto_remediated` to **`None`**
so the handler-proposed `result.disposition` passes through (`final_disp = table_disp or result.disposition`,
already the supervisor's rule). The forward auto-path proposes `auto_remediated`; the resume path proposes
`remediated`.

**Rationale.** Clarification Q2 chose distinct dispositions; the existing code already half-models them. The
spec's shorthand `rejected` is the existing `rejected_by_human` constant (don't rename a tested constant). The
edge tweak is the minimal way to let the response stage own its own dispositions (RD2 reconciliation — this is
a response disposition, not #5/#7 routing).

**Alternatives.** Add new `StageOutcome` values for approved/expired — rejected (the handler only ever returns
`RESOLVED`/`NEEDS_APPROVAL`/`ESCALATE`; expiry is a supervisor transition, not a stage outcome). A dedicated
`EXPIRED` `IncidentStatus` — rejected (reusing `ESCALATED` + the distinct `approval_expired` disposition keeps
the status enum stable while the dashboard separates by disposition).

---

## RD9 — Action executors: mock infra behind a Protocol

**Decision.** `domain/response.py` defines an `ActionExecutor` Protocol (`async def execute(action) ->
ActionResult`). `infra/executors.py` provides a mock registry — one executor per catalog `ActionType` —
returning `ActionResult(status=applied|failed, …)`. The handler is injected the registry by closure; **no other
stage receives it**. A real executor (SOAR connector) is a later drop-in with no change to the policy/audit/
interrupt logic.

**Rationale.** "Actions run against a mock environment" (spec) while the *abstraction* is real and exercised
(integration tests assert executors are invoked / audited). Keeping executors in `infra/` behind a domain
Protocol preserves layering and the DI boundary that enforces Constitution III.

**Alternatives.** Inline mock returns in the handler — rejected (untestable as a tool boundary; couples
selection to execution). A real connector now — rejected (spec Out of Scope; no real targets in the demo).

---

## RD10 — Playbook catalog + auto/approval policy are config-backed (default-deny)

**Decision.** The **catalog** is a small data file under `backend/data/playbooks/` (criteria — e.g. technique
id / rule group — → an ordered list of `ActionType`s with optional preconditions), loaded at startup like the
corpus `data_dir`. The **policy** is `ResponseSettings.auto_execute_actions: list[ActionType]` (the
allowlist); **every action not on it is approval-required (default-deny)**, and any action **not in the
catalog** is non-executable (dropped/escalated). Both are defended in `DECISIONS.md`.

**Rationale.** FR-004/FR-005 + Constitution V (config-backed, never hardcoded). Default-deny is the safe
posture: a new/unknown destructive action requires approval by construction. Mirrors the corpus `data_dir`
config pattern already in the codebase.

**Alternatives.** Hardcoded allow/deny lists in agent code — rejected (Constitution V violation). LLM decides
auto vs approval — rejected (the boundary must be deterministic and auditable, never model-influenced).

---

## RD11 — Verification verdict is contract-reserved, unused in v1 (§v2c readiness)

**Decision.** `ActionResult` carries `verification: VerificationVerdict | None = None` (enum reserved:
`verified`/`unverified`/`regressed`); `services/supervisor.py` reserves `DISP_REMEDIATION_UNVERIFIED =
"remediation_unverified"` (defined, unused). v1 always records **applied/failed** (execution result), never an
efficacy claim (FR-020). The §v2c section of the spec designs how T2 populates `verification` and writes it back
to memory.

**Rationale.** Clarification Q4/Q5: keep v1 honest and scoped; shape the contract so T2 adds *behavior*, not
schema churn. The layering contract gates implementation behind the day-9 T1 tag.

**Alternatives.** Implement verification now — rejected (clarification Q5 chose defer; unobservable against a
mock; T2 scope). Omit the reserved field — rejected (would force a schema change at T2).

---

## RD12 — Eval: extend the supervisor-routing gate (no new gate)

**Decision.** Add response/remediation fixtures to the committed **supervisor-routing** gate
(`config/eval_thresholds.yaml` + `tests/eval/test_supervisor_routing_gate.py`): a low-risk incident →
`resolved`/`auto_remediated`; a destructive incident → `awaiting_approval`; approve → `remediated`; reject →
`rejected_by_human`; timeout → `approval_expired`. The fixtures are deterministic (routing/policy logic), run
identically on both providers, and the ambiguous-selection LLM call is exercised in the integration tier.

**Rationale.** FR-019 + Constitution II ("gates land green as their component does"); the routing gate already
checks "did each incident reach the correct next stage" — response transitions are exactly that. The
remediation-rationale LLM-judge is SPEC-eval (#13), not invented here.

**Alternatives.** A new "remediation" eval gate — rejected (the spec mandates extending the existing gate; a new
gate is unjustified surface).

---

## Open items / explicit non-decisions (deferred to planning-adjacent or later specs)

- **Approval timeout default value** — a `ResponseSettings.approval_timeout_s` config value; tests use a short
  override. Default chosen at implementation (a demo-friendly value, e.g. minutes); it changes no architecture.
- **Auth on the approvals endpoint** — single `admin`; the auth layer is Component #12. v1 records the
  `decided_by` actor from whatever the (future) auth context supplies; for now a configured/stub admin
  identity. The endpoint shape does not change when #12 adds real auth.
- **Dashboard rendering** of the approval queue / audit trail — Component #12 (this component only exposes
  `GET /approvals` + the data).
