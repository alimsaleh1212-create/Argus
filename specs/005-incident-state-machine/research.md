# Phase 0 — Research & Decisions: Incident State Machine (Supervisor)

**Component**: #7 `SPEC-incident-state-machine` · **Date**: 2026-06-08

The spec had **no open `[NEEDS CLARIFICATION]`** markers, so Phase 0 records the design decisions that
turn the spec into a buildable plan. Each is biased toward the user's standing steer — *make it simple,
don't overengineer* — and toward the constitution's Principle IV (determinism first). Decisions are
labeled `SD1…SD10` and the non-obvious ones are mirrored into `DECISIONS.md`.

---

## SD1 — Plain async state machine; NOT LangGraph (defer it to #10)

**Decision**: Implement the supervisor as a **plain Python async loop over an explicit transition table**.
Do **not** add LangGraph (or any workflow engine) in this component. Revisit LangGraph only in #10, and
only if durable interrupt/resume genuinely needs a checkpointer beyond the Postgres-as-source-of-truth
status we already have.

**Rationale**: The brief and constitution are emphatic that the supervisor is "a deterministic state
machine, **not** an LLM freelancing about orchestration." A workflow engine on a fixed, enumerable
transition table is weight without benefit: it adds a dependency, a graph-construction concept, and a
debugging surface to model something a dict-of-allowed-transitions plus a `while` loop expresses exactly.
The incident's durable state already lives in the `incidents` row (the source of truth), so "resume after
crash" is a `SELECT status` away — no checkpointer required. This is precisely the over-engineering the
skeptic in the brief warns against.

**Alternatives considered**: *LangGraph now* — rejected: the memory note and brief reserve a "LangGraph
interrupt" for the **response** approval flow (#10); pulling it in here to wrap a deterministic FSM couples
#7 to a heavy framework for zero added capability and risks an LLM-graph mental model leaking into a layer
that must stay deterministic. *A generic third-party FSM library* — rejected: a ~30-line transition table
is clearer and fully under test. The LangGraph go/no-go for #10's interrupt is recorded as deferred.

---

## SD2 — Lifecycle states: extend `IncidentStatus`, no migration for status

**Decision**: Extend the existing `IncidentStatus` enum with the minimal set the pipeline needs:
in-flight `triaging`, `enriching`, `responding`; parked `awaiting_approval`; terminal `resolved`,
`escalated` (`failed` already exists from #4). `status` is stored as `text`, so **adding values needs no
migration** (the #4 data-model explicitly anticipated this).

**Rationale**: This is the smallest state set that expresses the spine, the park, and the three dashboard
KPI buckets (auto-resolved / escalated / awaiting-approval map 1:1 to `resolved` / `escalated` /
`awaiting_approval`). No `auto_resolved` vs `closed` vs `remediated` proliferation — the coarse outcome is
`status`; the fine-grained reason is `disposition` (SD3).

**Alternatives considered**: A richer state set (separate `closed`, `auto_remediated`, `parked_timeout`,
…) — rejected as premature; the reasons live in `disposition` without multiplying terminal states. A
single boolean `is_open` — rejected: loses the KPI buckets and the resume target.

---

## SD3 — One nullable `disposition` column; step/token counts stay in spans

**Decision**: Add migration **`0004_incident_disposition`** with a single nullable `disposition text`
column carrying the fine-grained terminal reason (`auto_resolved_noise`, `auto_remediated`,
`escalated_ambiguous`, `escalated_step_cap`, `escalated_token_cap`, `escalated_stage_error`,
`awaiting_approval_destructive`, …). Do **not** add `steps`/`tokens_used` columns — the cap is enforced
**in-memory per run**, and per-stage token/latency telemetry already lands in the `trace_spans` table (#2),
which is where the dashboard's drill-down reads it.

**Rationale**: The dashboard (#12) needs the disposition reason to render "why" and to compute
mean-time-to-disposition; a queryable column beats burying it in `evidence` JSONB (which is semantically
*grounding inputs*, not an outcome). But step/token totals are **trace data**, not incident facts — #2
already records them off-path, so duplicating them as columns is redundant write amplification. One small
reversible column is the whole schema change.

**Alternatives considered**: Persist `steps`/`tokens_used` on the row — rejected (duplicates span data;
only needed for resume budget, which we accept resets per run — SD8). Store disposition in `evidence.flags`
— rejected (wrong home; not first-class queryable for KPIs).

---

## SD4 — Stages are pure handlers; the supervisor is the single writer

**Decision**: A stage handler has the signature `async def run(incident: Incident) -> StageResult` (raising
`ToolError` on failure). Stages **do not touch the database or execute actions directly**; they return a
`StageResult` describing their outcome (+ any evidence/disposition they propose, + tokens consumed). The
**supervisor applies every persisted change** — it is the sole writer of `status`/`disposition`.

**Rationale**: This makes Constitution III **structural, not prompted**: the triage and enrichment handlers
are handed no DB-write capability and no action client, so a prompt-injected alert that hijacks triage
*cannot* mutate state or remediate — the capability is simply absent. It also keeps each stage trivially
unit-testable (pure in → out) and gives the supervisor one obvious place to enforce the transition table.
Response's action *execution* (#10) is the lone exception, and even there the action client is injected
only into the response handler, never the others.

**Alternatives considered**: Stages write their own slice to the DB — rejected: scatters the writer,
weakens the single-writer invariant, and would hand write capability to triage/enrichment. A shared mutable
incident object passed by reference and mutated in place — rejected: non-obvious ownership, harder to test,
easy to violate the slice rule.

---

## SD5 — Deterministic routing keyed on the grounded severity band

**Decision**: The fast-path routes on the already-computed `severity` (the #4 deterministic Wazuh
`rule.level` band), governed by config:
`fast_path_autoclose_severities` (default `["low"]`) ⇒ `resolved` with no stage call;
`fast_path_critical_severities` (default `["critical"]`) ⇒ straight to `responding`;
everything else (`medium`/`high`) ⇒ `triaging` (the ambiguous full-depth path). Indeterminate/defaulted
severity is treated as **ambiguous** (never fast-pathed), matching the spec edge case.

**Rationale**: Severity is the one trustworthy deterministic signal already on the grounded incident, and
the band table is config-tunable. This is an honest **coarse proxy** for "obvious noise / obvious critical"
that the triage agent (#8) later refines with real real-vs-noise judgment — exactly the "determinism owns
the enumerable core, agents own the ambiguous tail" split. No thresholds are hardcoded in stage logic
(FR-007).

**Alternatives considered**: Route on `evidence.verdict` / rule groups / a rules DSL — rejected as
premature; #8 owns nuanced triage and the v2c feedback loop (#6) owns learned tuning. Hardcode the bands —
rejected (violates FR-007's config-backed requirement).

---

## SD6 — `StageResult` outcome vocabulary drives adaptive depth

**Decision**: `StageResult.outcome` is a small enum: `RESOLVED` (close the incident),
`ADVANCE` (go to the next stage — i.e. triage→enrichment, or enrichment→response),
`NEEDS_APPROVAL` (response only — park), `ESCALATE` (hand to a human). The supervisor maps the current
state + outcome onto the next state through the transition table. Adaptive depth is just: triage returning
`RESOLVED` skips enrichment and response; triage returning `ADVANCE` runs enrichment; enrichment `ADVANCE`
runs response.

**Rationale**: Four outcomes cover every edge in the table without encoding stage-specific knowledge into
the supervisor. The supervisor stays generic ("apply outcome to state"); the *meaning* of each outcome
lives in the stage. This keeps the adaptive-depth rule (FR-006) declarative and the eval (SD10) a pure
function of (state, outcome).

**Alternatives considered**: Let stages name their explicit next state — rejected: lets a (hijacked) stage
request an illegal jump and pushes routing policy into stages. A boolean `resolved?` only — rejected:
can't express park vs escalate vs advance.

---

## SD7 — Hard step + token cap, enforced before each transition

**Decision**: `SupervisorSettings` carries `max_steps` (default `8`) and `max_tokens` (default `40_000`).
The loop increments a step counter per transition and accumulates `StageResult.tokens_consumed`; **before**
each stage call it checks both caps and, on breach, transitions to `escalated`
(`disposition = escalated_step_cap | escalated_token_cap`) and stops. This is a cost **and** safety control
(Constitution IV).

**Rationale**: A fixed flow of ≤ ~4 stages can only loop if something misbehaves (a stage repeatedly
`ADVANCE`-ing, a future bug); the cap turns "runaway" into a clean terminal escalation rather than an
infinite worker spin or a token blowout. Defaults are generous enough never to trip on the legitimate
triage→enrichment→response path yet tight enough to bound a pathological one.

**Alternatives considered**: Step cap only — rejected: the brief explicitly wants a **token** cap (the real
cost lever once stages call the LLM). No cap, rely on stage timeouts — rejected: timeouts bound a single
call, not the per-incident aggregate the brief specifies.

---

## SD8 — Idempotent & resumable via guarded transitions

**Decision**: Every transition is a **guarded** `UPDATE incidents SET status=:to … WHERE id=:id AND
status=:from` (the existing `claim_for_grounding` pattern), exposed as
`IncidentRepository.advance_status(id, expected, target, disposition=None)`. On entry the supervisor reads
the current status: a **terminal** (`resolved`/`escalated`/`failed`) or **parked** (`awaiting_approval`)
incident is a **no-op** (idempotent under at-least-once re-delivery); a `grounded` incident **starts**; an
in-flight status (`triaging`/`enriching`/`responding`) **resumes** from that point. The per-run step/token
budget restarts on resume (accepted — SD3).

**Rationale**: Guarded transitions give optimistic concurrency for free (two workers can't double-advance)
and make re-delivery safe without a distributed lock. Reusing the proven `claim_for_grounding` idiom keeps
the repository uniform. Resetting the budget on the rare crash-resume is a deliberate simplicity trade: the
alternative (persisting counters) buys little and costs a column (SD3).

**Alternatives considered**: An advisory/Redis lock per incident — rejected (heavier; guarded UPDATE
suffices at single-worker scale). A monotonic `version` column with CAS — rejected (the status guard is the
CAS we need).

---

## SD9 — `awaiting_approval` park here; interrupt mechanism / timeout / audit in #10

**Decision**: #7 owns the **state-machine edges**: `responding → awaiting_approval` (when the response
stage returns `NEEDS_APPROVAL`) and the **resume edges** `awaiting_approval → responding|resolved`
(approve) / `awaiting_approval → escalated|resolved` (reject), exposed as a reserved
`Supervisor.resume_incident(incident_id, decision)` entry point. #7 implements the **park** (persist
`awaiting_approval`, stop the loop) and the resume **transitions**; it does **not** implement the interrupt
vehicle (LangGraph/HTTP), the approval **timeout**, the **audit rows**, or the actual action execution —
those are #10.

**Rationale**: The supervisor is the transition owner (Constitution V says the boundary is config-backed
policy, not agent logic), so the edges belong here; the *mechanism* (how a human is prompted, how a
timeout fires, what gets audited) is the response-remediation spec's job. Splitting it this way lets #7's
state machine be complete and testable now while leaving #10 a clean, well-named seam to fill.

**Alternatives considered**: Defer the whole park to #10 — rejected: would leave the transition table
incomplete and untestable, and force #10 to edit #7's core. Implement timeout/audit now — rejected: scope
creep into #10; needs the audit table and action client that #10 owns.

---

## SD10 — Lands the supervisor-routing eval as a deterministic fixture gate

**Decision**: This component activates the **supervisor-routing** gate in `eval_thresholds.yaml` (seeded as
a placeholder on day 1). The eval is a **deterministic** check: a small labeled fixture set of grounded
incidents (noise / critical / ambiguous-resolved-at-triage / ambiguous-full-depth) each asserts the
expected **next stage / terminal disposition**. Because the supervisor makes no LLM call, the gate is
provider-independent and needs no both-providers run.

**Rationale**: "Did each incident reach the correct next stage?" (the brief's routing eval) is a pure
function of the routing rules (SD5/SD6), so it's cheap, fast, flake-free, and a real regression guard for
the determinism contract. Seeding it now keeps CI gating from the start (Constitution II).

**Alternatives considered**: An LLM-judged routing eval — rejected (unnecessary and non-deterministic for a
deterministic component). Folding routing assertions into e2e only — rejected: the committed eval gate is
the regression contract the constitution requires per component.

---

## Resolved unknowns summary

| Question | Resolution |
|----------|------------|
| Workflow engine vs plain FSM? | Plain async FSM; LangGraph deferred to #10 (SD1). |
| New lifecycle states / migration? | Extend the text `status` enum (no migration); one nullable `disposition` column (SD2/SD3). |
| Who writes incident state? | The supervisor only; stages are pure handlers (SD4). |
| How is the fast-path decided? | Config-backed severity bands; ambiguous = full depth (SD5/SD6). |
| Cap shape? | In-memory step + token cap → `escalated` on breach (SD7). |
| At-least-once safety? | Guarded status transitions; terminal/parked re-delivery is a no-op; resume from in-flight (SD8). |
| Approval boundary split? | #7 owns the park + resume edges; #10 owns the mechanism/timeout/audit (SD9). |
| Eval gate? | Deterministic supervisor-routing fixture gate, provider-independent (SD10). |
| New dependency / service / container? | None — runs in the existing worker; no LLM in the supervisor. |
