# Feature Specification: Incident State Machine (Supervisor)

**Feature Branch**: `005-incident-state-machine`

**Created**: 2026-06-08

**Status**: Draft

**Input**: User description: "depending on docs/resources/SOAR_brief.md and docs/resources/SOAR_Plan.md make things simple and don't overengineer while working" — Component #7 (`SPEC-incident-state-machine`): deterministic supervisor — states, transitions, routing (adaptive-depth investigation; determinism-first action), step/token cap.

## Overview

This component is the **spine** of Argus's incident pipeline. It picks up where ingestion (#4) left
off: the worker grounds an Incident and hands it to a frozen seam (`dispatch_to_pipeline`), which today
just logs. This component **fills that seam** with a **deterministic supervisor** — a state machine that
drives each grounded Incident through an explicit lifecycle to a terminal disposition, deciding *which*
stages run and stopping when it must.

Two ideas from the brief shape the scope and keep it simple:

- **The supervisor is deterministic, not an LLM.** It owns the loop, the transitions, and a hard cap on
  steps and tokens per incident. The intelligence lives *inside* the bounded agent stages it coordinates,
  not in the orchestration. Using an LLM to decide "what to do next" would be the overengineering the
  brief explicitly warns against.
- **Determinism owns the enumerable core; agents own the ambiguous tail.** Obvious false-positives and
  obvious criticals resolve on a deterministic fast-path with **no agent (LLM) call at all**. Only
  ambiguous incidents pay for the full triage → enrichment → response depth, and even then only as deep as
  needed (adaptive depth).

Per scope discipline, this slice wires the **structure**, not the agents. The triage, enrichment, and
response stages are invoked through a single frozen contract but their bodies remain stubs here — they are
filled by Components #8, #9, and #10. The value this component delivers on its own is a pipeline that
**flows end-to-end and never gets stuck**: every grounded incident reaches a defined terminal disposition,
within bounded cost, degrading gracefully on failure.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Supervisor drives a grounded incident to disposition (Priority: P1)

The worker grounds an Incident and hands it to the supervisor. The supervisor advances the Incident
through an explicit lifecycle — recording each transition — and lands it in a terminal disposition
(resolved, escalated, or failed). Nothing is left stuck in an in-flight state.

**Why this priority**: This is the irreducible MVP and the contract every later component plugs into.
Without it, a grounded incident has nowhere to go — the pipeline dead-ends at the #4 logging stub. On its
own it delivers a visible end-to-end spine (the trace tree the dashboard later renders), even while the
agent stages are stubs.

**Independent Test**: Hand a grounded Incident to the supervisor with the agent stages stubbed to return
canned stage results, and assert the Incident moves through the expected lifecycle states, each transition
is persisted, and it ends in exactly one terminal disposition (never an in-flight state).

**Acceptance Scenarios**:

1. **Given** a grounded Incident, **When** the supervisor processes it, **Then** the Incident advances
   through allowed lifecycle transitions and ends in exactly one terminal disposition, with every
   transition persisted to the source-of-truth incident record.
2. **Given** a stage that reports its outcome, **When** the supervisor receives it, **Then** the next
   transition is one of the *allowed* transitions for the current state; an outcome implying an illegal
   transition is rejected and the Incident is routed to a defined degraded terminal state.
3. **Given** the supervisor has finished an Incident, **When** the full run is inspected, **Then** it is
   reconstructable as a trace tree (the ordered stages it ran) with the correlation id propagated and no
   unredacted incident content present.

---

### User Story 2 - Deterministic fast-path and adaptive depth (Priority: P2)

Most alerts are unambiguous. The supervisor resolves the obvious ones with **no agent call**: an obvious
false-positive/noise incident closes directly; an obvious critical routes straight to the response stage.
Only ambiguous incidents go through the full triage → enrichment → response depth, and enrichment runs
only when triage has not already resolved the incident.

**Why this priority**: This is the cost-and-safety payoff the brief promises — "most alerts never touch an
agent." It makes the pipeline cheap and fast on the common case and reserves expensive reasoning for the
long tail. It is built on top of P1's state machine.

**Independent Test**: Feed the supervisor a labeled fixture set (obvious-noise, obvious-critical,
ambiguous). Assert obvious-class incidents reach their terminal/next stage with zero agent-stage
invocations, and ambiguous incidents invoke triage and only then enrichment.

**Acceptance Scenarios**:

1. **Given** an incident whose grounded evidence/severity marks it an obvious false-positive, **When** the
   supervisor routes it, **Then** it is auto-resolved/closed deterministically with no agent (LLM) call.
2. **Given** an incident whose severity marks it an obvious critical, **When** the supervisor routes it,
   **Then** it is routed directly to the response stage, skipping the ambiguity path.
3. **Given** an ambiguous incident, **When** the supervisor routes it, **Then** triage runs first and
   enrichment runs only if triage did not resolve it (adaptive depth), governed by config — never
   hardcoded thresholds.

---

### User Story 3 - Bounded execution and graceful degradation (Priority: P3)

Each incident has a hard ceiling on how much work it may consume, and a stage failure must never crash the
worker. The supervisor caps total steps and tokens per incident, retries only transient stage failures a
bounded number of times, and routes anything it cannot complete to a defined degraded terminal state.

**Why this priority**: This is the cost ceiling and the reliability/safety net. It is essential for a
production-shaped pipeline but builds on the P1 spine and P2 routing.

**Independent Test**: Inject (a) a stage that loops/over-consumes past the step or token cap, and (b) a
stage that returns a non-retryable error. Assert the incident reaches a defined terminal/escalated state
in both cases, the worker process stays alive, and the cap/error reason is recorded.

**Acceptance Scenarios**:

1. **Given** an incident whose processing would exceed the configured step or token cap, **When** the cap
   is hit, **Then** the supervisor stops advancing it and lands it in a defined terminal state with the
   cap recorded as the reason — it never loops unbounded.
2. **Given** a stage that returns a retryable `ToolError`, **When** the supervisor handles it, **Then** the
   stage is retried a bounded number of times (transient only); on exhaustion the incident is routed to a
   degraded terminal state (escalated/failed) and the worker does not crash.
3. **Given** a stage that returns a non-retryable `ToolError` or raises, **When** the supervisor handles
   it, **Then** the incident is immediately routed to a degraded terminal state with the reason recorded,
   without retry and without crashing the worker.

---

### Edge Cases

- **Indeterminate severity** (no usable severity signal on the grounded incident): the incident is *not*
  fast-pathed; it is routed through the full triage path rather than dropped or mis-closed.
- **Illegal transition requested by a stage**: a (stubbed, or later prompt-injected) stage that returns an
  outcome implying a transition not in the allowed set cannot drive it — the supervisor rejects it and
  routes to a defined degraded terminal state. The transition table is a structural guardrail.
- **Re-delivery of an already-terminal incident** (at-least-once queue): the supervisor is a no-op and the
  job is acknowledged — no double-processing, no duplicate side-effects.
- **Crash between two transitions**: on re-delivery the incident resumes from its last persisted state, not
  from the beginning; no stage's side-effect is repeated.
- **Response stage signals a destructive action requiring approval**: the supervisor parks the incident in
  a non-terminal `awaiting_approval` state and stops auto-advancing it. The interrupt/resume mechanism,
  timeout, and audit are Component #10; only the parked state and the reserved resume entry point exist
  here.
- **All stages are still stubs** (agents #8–#10 not yet implemented): the incident still reaches a terminal
  disposition via the fast-path and the stub stage outcomes — the spine is demoable before the agents land.

## Requirements *(mandatory)*

### Functional Requirements

**Supervisor & lifecycle**

- **FR-001**: System MUST provide a deterministic supervisor that, on receiving a grounded Incident through
  the reserved pipeline handoff seam, drives it through an explicit lifecycle to a terminal disposition.
  The supervisor MUST be a state machine with enumerated transitions — never an LLM deciding the next step.
- **FR-002**: System MUST define the incident lifecycle states the supervisor manages — the in-flight
  stages (triaging, enriching, responding), the parked `awaiting_approval` state, and the terminal
  dispositions (auto-resolved/closed, escalated, failed) — extending the existing `received/grounding/
  grounded` ingestion states. Transitions not in the allowed set MUST be rejected.
- **FR-003**: System MUST persist every state transition to the source-of-truth incident record so the
  current disposition of every incident is durably recorded and visible to later components (dashboard
  #12). The supervisor MUST own transitions; no stage advances the lifecycle on its own.
- **FR-004**: The supervisor itself MUST make no LLM call; all model reasoning happens inside the agent
  stages it coordinates.

**Routing — determinism-first, adaptive depth**

- **FR-005**: System MUST resolve obvious cases on a deterministic fast-path with no agent invocation: an
  obvious false-positive/noise incident MUST move directly to a closed/auto-resolved terminal state, and an
  obvious critical MUST route directly to the response stage — decided from the grounded evidence and
  severity, with no LLM call.
- **FR-006**: System MUST route only ambiguous incidents through the full triage → enrichment → response
  depth, and the depth MUST be adaptive: enrichment runs only when triage has not already resolved the
  incident.
- **FR-007**: The routing policy (what counts as obvious vs. ambiguous, and the auto-vs-escalate boundary)
  MUST be config-backed, not hardcoded in stage logic.

**Stage invocation contract (the seam to #8/#9/#10)**

- **FR-008**: System MUST invoke each agent stage (triage, enrichment, response) through a single frozen
  stage-handler contract that receives a bounded slice of incident state and returns either a structured
  stage result (its outcome and the tokens it consumed) or a structured `ToolError`. Stage bodies are stubs
  at this component.
- **FR-009**: The incident-state slices the supervisor passes to the stages MUST partition the incident
  contract such that no field is written by two stages (the no-gap seam rule); the supervisor owns
  transitions, each stage owns only its declared slice.

**Bounded execution & graceful degradation**

- **FR-010**: The supervisor MUST enforce a hard, config-backed cap on both the number of steps (stage
  transitions) and the total tokens consumed per incident; when either cap is exceeded the incident MUST
  end in a defined terminal/escalated state with the cap recorded as the reason — never an unbounded loop.
- **FR-011**: A stage returning a retryable `ToolError` MUST be retried a bounded number of times (transient
  failures only); a non-retryable error, an exception, or exhausted retries MUST route the incident to a
  defined degraded terminal state (escalated/failed). The worker process MUST NOT crash or surface a 500.
- **FR-012**: Supervisor execution MUST be idempotent and resumable: re-delivery of the same incident
  (at-least-once queue) MUST NOT repeat a stage's side-effects nor skip stages, and an incident interrupted
  mid-pipeline MUST resume from its last persisted state.

**Approval park (reserved seam for #10)**

- **FR-013**: When the response stage signals a destructive action requiring human approval, the supervisor
  MUST park the incident in the non-terminal `awaiting_approval` state and stop auto-advancing it. The
  interrupt/resume mechanism, approval timeout, and audit rows are out of scope here (Component #10); only
  the parked state and a reserved resume entry point are provided.

**Cross-cutting**

- **FR-014**: Every supervisor step and stage invocation MUST emit a trace span and propagate the incident
  correlation id, and MUST never emit unredacted incident content (reusing the #2 observability/redaction
  seam). A full incident run MUST be reconstructable as a trace tree for the dashboard.

### Key Entities *(include if feature involves data)*

- **Incident** (reused from #4, lifecycle extended): the canonical object and downstream contract. This
  component adds the in-flight stage states, the `awaiting_approval` parked state, the terminal
  dispositions, and per-incident step/token accounting. The schema is *extended*, never re-declared.
- **Supervisor / State Machine**: the deterministic coordinator. Owns the set of states, the allowed
  transition table, the routing rules (fast-path + adaptive depth), and the per-incident step/token budget.
  Makes no LLM call.
- **Stage Handler**: the single frozen interface each agent stage (triage/enrichment/response) implements;
  takes a bounded slice of incident state, returns a structured outcome or a `ToolError`. Stubs at this
  component.
- **Stage Result / ToolError**: the structured value a stage returns — the resolved-or-next-stage outcome
  and tokens consumed; or, on failure, a `ToolError` carrying a `retryable` flag that governs whether the
  supervisor retries.
- **Step/Token Budget**: the per-incident counters (steps taken, tokens consumed) the supervisor checks
  against the configured caps before each transition.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Over a replay of the sample alert set, 100% of grounded incidents reach exactly one terminal
  disposition; none is ever left stuck in an in-flight state.
- **SC-002**: 100% of incidents stay within the configured step and token caps; any that would exceed a cap
  end in a defined terminal/escalated state — zero unbounded loops, zero worker crashes.
- **SC-003**: On a labeled routing fixture set, obvious-noise and obvious-critical incidents are resolved
  by the deterministic fast-path with **zero** agent-stage invocations, and ambiguous incidents invoke the
  agent stages — 100% routed to the expected next stage.
- **SC-004**: Under injected stage failures (retryable, non-retryable, and exception), 100% of affected
  incidents reach a defined terminal/escalated state and the worker process stays alive throughout.
- **SC-005**: Re-delivering the same incident produces no duplicate side-effects and the same terminal
  disposition (idempotent); an incident interrupted mid-pipeline resumes from its persisted state.
- **SC-006**: The supervisor makes zero LLM calls (verifiable: the orchestration layer holds no LLM client
  dependency).
- **SC-007**: A planted secret/PII value never appears unredacted in any persisted incident field, log
  line, or trace span emitted by the supervisor.

## Assumptions

- **The agents are stubs here.** Triage (#8), enrichment (#9), and response (#10) are invoked through the
  frozen stage-handler contract but return canned outcomes in this component's tests. The supervisor's
  correctness does not depend on real agent logic — it depends only on the contract.
- **The supervisor fills the frozen `dispatch_to_pipeline(incident)` seam** reserved by #4; the worker
  handoff, the Postgres-as-source-of-truth incident record, and the Incident schema are reused, not
  redefined. The Incident schema's status set is *extended* with the lifecycle states above.
- **The approval interrupt/resume mechanism, timeout, and audit rows are Component #10.** This component
  defines only the `awaiting_approval` parked state and the park/resume transitions as reserved seams. The
  concrete checkpoint/interrupt vehicle (e.g., a LangGraph checkpointer) is decided at plan time / in #10.
- **Step/token accounting reuses the #2 observability token metric.** Each stage reports the tokens it
  consumed in its stage result; the supervisor aggregates per incident and enforces the cap. The supervisor
  does not itself meter an LLM because it makes no LLM call.
- **Caps and routing thresholds are config-backed** via a typed settings section (consistent with the
  project's `pydantic-settings`, `extra="forbid"` pattern), with sensible small defaults (a low double-
  digit step cap; a per-incident token cap on the order of tens of thousands). Tuning values is not part of
  this spec.
- **Behavior is deterministic and reproducible**: the same grounded incident plus the same (mocked) stage
  outcomes always produces the same transition path — which is what makes the supervisor-routing eval (#13)
  meaningful.

## Out of Scope

- The triage / enrichment / response **intelligence and tool sets** (Components #8, #9, #10) — this
  component only invokes them through a contract and treats them as stubs.
- The **approval interrupt/resume** mechanism, the approval **timeout** and its terminal state, and the
  **audit rows** for executed actions (Component #10).
- **Temporal memory** reads/writes and the **v2c feedback loop** that would tune routing/severity from past
  dispositions (Component #6) — the supervisor consumes severity/evidence as given; tuning comes later.
- The **eval gates** themselves (Component #13) — though this component is built to be eval-able (it exposes
  a deterministic routing decision against labeled fixtures).
- **Guardrails / injection rails** (Component #11) — the structural transition-table guardrail here is
  independent of the guardrails library.
