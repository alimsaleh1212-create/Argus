# Implementation Plan: Incident State Machine (Supervisor)

**Branch**: `005-incident-state-machine` | **Date**: 2026-06-08 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/005-incident-state-machine/spec.md`

## Summary

Fill the downstream-handoff seam #4 reserved (`services/pipeline.py:dispatch_to_pipeline`) with a
**deterministic supervisor** that drives a grounded Incident through an explicit lifecycle to a terminal
disposition. The supervisor is a **plain async state machine** — an enumerated transition table plus a
small loop — **not** an LLM and **not** (yet) LangGraph. It runs **inside the existing `worker` container**;
no new service, no new container, no new heavy dependency.

Three behaviours, kept deliberately small:

1. **Drive to disposition (the spine).** `grounded → … → {resolved | escalated | failed}`, persisting each
   transition to the Postgres `incidents` row (the source of truth). Agent stages (triage/enrichment/
   response) are invoked through one frozen **stage-handler contract** but their bodies stay stubs here —
   #8/#9/#10 fill them.
2. **Determinism-first routing.** A config-backed fast-path resolves the obvious cases with **zero agent
   calls** — an obvious-noise incident (`severity == low`) auto-resolves; an obvious-critical
   (`severity == critical`) routes straight to response; only the ambiguous middle (`medium`/`high`) pays
   for the full triage → (enrichment) → response depth, and enrichment runs only if triage didn't resolve
   it (adaptive depth).
3. **Bounded + graceful.** A hard, config-backed **step and token cap** per incident; transient
   `ToolError` retried a bounded number of times, anything else routed to a degraded terminal state. The
   worker never crashes.

A key simplicity decision: **stages are pure handlers** — given the incident (their bounded slice) they
return a `StageResult` (outcome + tokens) or raise a structured `ToolError`; the **supervisor is the single
writer** of incident status/disposition. That structurally enforces "the supervisor owns transitions, each
stage owns only its slice" (FR-003/FR-009) and means triage/enrichment never get DB-write or action
capability — the security boundary falls out of the design rather than being policed by prompt text.

The component is "done" when unit + integration + e2e are green: a grounded fixture reaches exactly one
terminal disposition; an obvious-noise alert resolves with no stage call; an injected stage error and an
injected cap-breach both land the incident in `escalated` with the worker still alive; and a re-delivered
incident is idempotent. It lands the **supervisor-routing** eval gate (#13). New surface area is small:
fill `pipeline.py`, add `services/supervisor.py` + `domain/pipeline.py`, minimal stub handlers in
`agents/`, one repository method, one `SupervisorSettings` section, and one tiny migration (`0004`) adding a
nullable `disposition` column. **No LLM is called anywhere in this component.**

## Technical Context

**Language/Version**: Python 3.12 (pinned `>=3.12,<3.13`); managed with `uv`.

**Primary Dependencies**: **none new.** Pure reuse — Pydantic v2 (the `StageResult`/`ToolError` domain
types and the extended `Incident` lifecycle), async SQLAlchemy (guarded status transitions on the existing
`incidents` table via `IncidentRepository`), and the **#2 observability seam** (`tracer.span()` per
step/stage, `bind_incident`, `get_logger`, the `Redactor` already applied at the `LOG`/`SNAPSHOT`
boundaries). The supervisor holds **no LLM client** (Constitution IV / SC-006) — it coordinates stages that
will later use `Depends(get_llm)`, but the orchestration layer itself never imports it. **LangGraph is
deliberately NOT added** here (see research SD1); the deterministic FSM is plain Python.

**Storage**: existing **Postgres `incidents` table**. Status stays `text`, so the new lifecycle values add
**no migration**. One small migration **`0004_incident_disposition`** adds a nullable `disposition text`
column (the fine-grained terminal reason the dashboard #12 reads beyond the coarse `status`). Step/token
counts are enforced **in-memory per run** and surface as **trace-span attributes** (#2) — not new columns —
so the dashboard's per-stage token/latency view comes from the existing `trace_spans` table, not the
incident row. No Redis, MinIO, or Neo4j use here.

**Testing**: `pytest` + `pytest-asyncio` (`asyncio_mode=auto`). **Unit** = transition-table legality,
the deterministic routing decision over labeled fixtures (noise/critical/ambiguous), step/token cap
enforcement, retry-then-degrade logic, and the idempotency/resume decision — all with the repository faked
and stage handlers as in-memory fakes (no DB, no LLM). **Integration** = the supervisor against **real
Postgres**: guarded transitions, `disposition` persistence, and resume from a persisted in-flight state.
**e2e** = POST a sample alert → worker grounds → supervisor drives it to a terminal disposition, across the
noise / critical / ambiguous fixtures, plus fault injection (stage `ToolError` → `escalated`; cap-breach →
`escalated`) asserting the worker process survives.

**Target Platform**: Linux containers under Docker Compose v2 (dev/CI). **No compose change** — the
supervisor executes in the already-active `worker` container (`python -m backend.worker`), invoked from the
grounding handoff. Same image, same command.

**Project Type**: Backend feature inside the existing modular-monolith `backend/` package. It *fills* the
#4-reserved `pipeline.py` seam and the #1-reserved `agents/` stubs, and adds one service module, one pure
domain module, one settings section, one repository method, and one migration. No restructuring.

**Performance Goals**: demo-scale (replayed alerts), single worker. The supervisor's own work is cheap
deterministic branching; cost lives inside the (stubbed) stages. The fast-path SC-003 target is **zero**
stage invocations for obvious-class alerts. Observability stays off the synchronous path (#2 batch
export), so per-step spans add negligible latency.

**Constraints**: **deterministic & reproducible** (same grounded incident + same stage outcomes ⇒ same
transition path — SC-001/SC-003, what makes the routing eval meaningful); **single-writer** state
(only the supervisor persists status/disposition); **idempotent & resumable** at-least-once (guarded
`UPDATE … WHERE status = :expected`, terminal/parked re-delivery is a no-op — SC-005); **hard step+token
cap** (terminal `escalated`, never an unbounded loop — SC-002); **graceful degradation** (retry transient
`ToolError` only, else `escalated`/`failed`; worker never 500s/crashes — SC-004); **no LLM in the
supervisor** (SC-006); typed `supervisor` settings section (`extra="forbid"`); structured logging +
correlation-id and **redaction before any log/trace** (no raw incident content — SC-007).

**Scale/Scope**: single-SOC, single-worker, replayed-alert scale. **In scope**: the deterministic
supervisor, the lifecycle states + transition table, fast-path/adaptive-depth routing, the step/token cap,
graceful degradation, the stage-handler contract (stubs), the `awaiting_approval` park + reserved resume
transitions, and the routing eval gate. **Out of scope** (seams only): triage/enrichment/response
intelligence and their tool sets (#8–#10); the approval **interrupt/resume mechanism**, **timeout**, and
**audit rows** (#10); temporal-memory reads/writes and the v2c feedback loop (#6); guardrails (#11).

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design — still passing.*

Derived from `.specify/memory/constitution.md` (v1.0.0).

- [x] **I. Spec-Driven Delivery**: `spec.md` precedes code; "done" = unit + integration + e2e green and
      pushed. Internal milestones keep the PR ≤ ~400 lines: **(a)** lifecycle states + transition table +
      `domain/pipeline.py` (`StageResult`/`ToolError`) + `services/supervisor.py` core loop + the guarded
      transition repo method + unit (the MVP — US1); **(b)** deterministic fast-path + adaptive depth +
      `SupervisorSettings` + stub stage handlers + `pipeline.py` delegation + integration (US2);
      **(c)** step/token cap + retry/degradation + `awaiting_approval` park + resume transitions + e2e +
      fault injection + the routing eval gate (US3).
- [x] **II. Test-First, Three-Tier, Eval-Gated (NON-NEGOTIABLE)**: three tiers planned, green daily,
      **≥80% on new code** (the supervisor is part of the safety boundary, so coverage is high here). This
      component **lands the supervisor-routing eval gate** (a deterministic labeled-fixture check: did each
      incident reach the correct next stage?). **No LLM is called**, so the both-providers gate is **N/A**
      here and the routing eval is provider-independent; the existing **smoke** + **redaction** gates still
      hold over the extended pipeline.
- [x] **III. Security Boundaries Are Structural, Not Prompted**: enforced *by construction* — stages are
      **pure handlers returning a `StageResult`**; the **supervisor is the single writer** of incident
      state, so the triage/enrichment handlers receive no DB-write and no action capability at all (the
      action tool set is injected only into the response handler by #10). A stage that returns an outcome
      implying an **illegal transition is rejected** by the transition table and routed to `escalated`
      (a hijacked/injected stage cannot drive the machine). Redaction before every log/trace is reused from
      #2; injection rails over alert text remain #11's seam.
- [x] **IV. Determinism First**: this component **is** the principle in code — the supervisor is a
      deterministic state machine with an explicit transition table, the obvious-noise/obvious-critical
      **fast-path makes no LLM call**, and a **hard step+token cap** is enforced. The supervisor holds no
      LLM client (verifiable: orchestration layer imports none — SC-006). Agents reason only over supplied
      evidence — but that is #8–#10; here they are stubs behind the contract.
- [x] **V. Human-in-the-Loop**: the lifecycle **defines `awaiting_approval`** and the park transition
      (response stage signals destructive → supervisor parks and stops). The resume transitions are owned
      here as pure state-machine edges; the **interrupt vehicle, approval timeout, and audit rows are
      #10** (a reserved seam, not a gap — #7 owns transitions, #10 owns the mechanism). The auto-vs-approval
      decision is **config-backed**, never hardcoded.
- [x] **VI. Temporal Memory & Graceful Degradation**: memory N/A (the supervisor consumes severity/evidence
      as given; tuning from memory is the #6 §v2c feedback loop). **Graceful degradation is central** —
      cap-breach ⇒ terminal `escalated`; transient `ToolError` ⇒ bounded retry; non-retryable/exception ⇒
      `escalated`/`failed`; **the worker process never crashes** (SC-002/SC-004).
- [x] **VII. Production Engineering Standards**: async throughout (async SQLAlchemy, async stage handlers);
      **DI** (the stage-handler registry, the repository, the tracer, and `SupervisorSettings` are injected;
      a fake registry/repo substitutes in tests); **lifespan singleton** (`SupervisorProvider` mirrors
      `QueueProvider`/`CacheProvider`); **Pydantic** at every boundary (`StageResult`, `ToolError`, the
      extended `Incident`); structured logging + correlation-id + off-path spans via #2; typed `supervisor`
      settings (`extra="forbid"`); **no new dependency** to pin.
- [x] **Scope & Tiers**: strictly v1 / T1; no ML detector / multi-tenancy / widget / live capture / LLM
      supervisor / 4th agent. Respects inward-only layering (`services → agents → repositories → infra`,
      `domain` isolated). **No new infra** — runs in the existing worker container.

**Result: PASS — no new dependency, no new service, no constitution deviation.** One small migration
(`0004`, a nullable column) and one settings section; the single notable choice — *not* adopting LangGraph
for the deterministic FSM and deferring it to #10's interrupt — is the simpler alternative, recorded in
research SD1 and `DECISIONS.md`. Complexity Tracking is therefore empty.

## Project Structure

### Documentation (this feature)

```text
specs/005-incident-state-machine/
├── plan.md              # This file (/speckit-plan output)
├── research.md          # Phase 0 — decisions & rationale (SD1–SD10)
├── data-model.md        # Phase 1 — lifecycle states, transition table, StageResult/ToolError, settings, migration
├── quickstart.md        # Phase 1 — drive a grounded incident; watch fast-path / ambiguous / cap / error
├── contracts/           # Phase 1 — the contracts later specs consume
│   ├── supervisor-state-machine.md  # states, the allowed-transition table, disposition reasons, caps
│   ├── stage-handler-contract.md    # the frozen StageHandler interface + StageResult/ToolError (seam to #8/#9/#10)
│   └── supervisor-routing-eval.md   # the routing eval gate: fixtures → expected next stage
├── checklists/
│   └── requirements.md  # (created by /speckit-specify)
└── tasks.md             # Phase 2 — created by /speckit-tasks (NOT here)
```

### Source Code (repository root)

> Fills reserved seams and adds the minimum new files; **no restructuring**. New files marked `+`.

```text
backend/
├── domain/
│   └── pipeline.py        + # NEW: pure types — StageName, StageOutcome, StageResult, ToolError, Disposition;
│                             #      no outward imports (domain-isolation contract). Imported by #8/#9/#10/#12.
│   └── incident.py          # EDIT: extend IncidentStatus with triaging/enriching/responding/awaiting_approval/
│                             #       resolved/escalated; add optional `disposition` field on Incident
├── services/
│   ├── supervisor.py      + # NEW: Supervisor — transition table, run_incident() loop, routing (fast-path +
│   │                         #      adaptive depth), step/token cap, retry/degradation, park + resume edges
│   └── pipeline.py          # FILL: dispatch_to_pipeline() delegates to Supervisor.run_incident (single writer)
├── agents/
│   ├── triage.py            # FILL (stub): run_triage(incident) -> StageResult — canned outcome, no LLM (#8 replaces)
│   ├── enrichment.py        # FILL (stub): run_enrichment(incident) -> StageResult — canned, no LLM (#9 replaces)
│   └── response.py          # FILL (stub): run_response(incident) -> StageResult — canned (may signal needs_approval) (#10 replaces)
├── repositories/
│   └── incidents.py         # EDIT: add advance_status(id, from_status→to_status, disposition?) guarded transition;
│                             #       set_grounded already exists; list_non_terminal reused for resume scan
├── infra/
│   ├── config.py            # EDIT: add SupervisorSettings; register on Settings; add "supervisor" to
│   │                         #       _KNOWN_SENTINEL_SECTIONS
│   └── container.py / lifespan.py  # EDIT: add SupervisorProvider singleton (mirrors QueueProvider); expose container.supervisor
├── dependencies.py          # EDIT: add get_supervisor() (request-path, for #12/tests); worker reads container.supervisor
└── worker.py                # EDIT: call container.supervisor via the pipeline seam, passing the session-bound repo

backend/db/migrations/versions/
└── 0004_incident_disposition.py  + # NEW: add nullable `disposition text` column (reversible). Status stays text.

config/
└── eval_thresholds.yaml     # EDIT: activate the supervisor-routing gate (seeded as a placeholder day 1);
                             #       smoke/redaction gates unchanged in shape

tests/
├── unit/                    # transition legality, routing decisions, cap enforcement, retry/degradation, resume decision
├── integration/            # real postgres: guarded transitions, disposition persistence, resume from in-flight
├── e2e/                    # grounded fixture → terminal disposition; fast-path zero-stage; injected error/cap → escalated
└── fixtures/incidents/*.json  # labeled routing fixtures (noise / critical / ambiguous) + expected next stage
```

**Structure Decision**: Stay inside the modular-monolith `backend/` and **fill the reserved seams**. The
state-machine **types go in `domain/pipeline.py`** (no outward deps — domain-isolation `import-linter`
contract) so #8/#9/#10/#12 import one contract defined once; the **`Incident` lifecycle is extended**
(`domain/incident.py`), never re-declared. The **orchestration lives in `services/supervisor.py`** and may
import the **`agents/` stage handlers** (inward-only `services → agents → repositories → infra` holds);
`pipeline.py` is the thin seam the worker already calls. The **supervisor is a lifespan singleton** via a
`SupervisorProvider` (mirroring the existing cache/queue providers) and is **the single writer** of incident
state — stages return results, the supervisor persists, which is what makes the triage-has-no-action-tools
boundary structural. Every step/stage opens a span and binds the correlation id through the **#2 seam**; no
tracing or redaction is re-implemented. **No new infra service or dependency.**

## Complexity Tracking

> No constitution violations — this section is intentionally empty. The one design choice worth flagging
> (NOT adopting LangGraph for the deterministic FSM, deferring it to #10's interrupt) is the *simpler*
> alternative, not added complexity; its rationale and the rejected "use LangGraph now" option are recorded
> in research SD1 and `DECISIONS.md`. The single migration adds one nullable column; the single new settings
> section follows the established typed-settings pattern.
