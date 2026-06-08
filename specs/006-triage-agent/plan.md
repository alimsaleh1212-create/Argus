# Implementation Plan: Triage Agent

**Branch**: `006-triage-agent` | **Date**: 2026-06-08 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/006-triage-agent/spec.md`

## Summary

Replace the supervisor's `run_triage` **stub** with a real, reasoning-backed triage stage — the **first
and only LLM call in the pipeline so far**. It runs only on the ambiguous incidents the supervisor's
deterministic fast-path already routed to it (medium/high or severity-undetermined). Triage makes **exactly
one** structured-output call through the shared `LlmClient` (#3), parses the result into a validated
`TriageJudgment` (real / noise / uncertain + confidence + evidence-cited rationale), and maps it — through a
pure, config-threshold-gated function — to one `StageOutcome`: **ADVANCE** (real → enrichment), **RESOLVED**
(confident noise → auto-close), or **ESCALATE** (uncertain / low-confidence → human). It writes **no state**
and holds **no action tools** (Constitution III, structural): it returns a `StageResult` and the supervisor
persists everything, including merging triage's `evidence_patch` into the incident. Every failure mode —
provider down, timeout, malformed/out-of-vocabulary output — **fails closed to ESCALATE** and never crashes
the worker. Lands the **triage real-vs-noise eval gate** (committed labeled set, macro-F1 threshold, run
identically on both providers).

**Keep-it-simple posture (the user's standing steer):** no agentic loop, no tools, no memory dependency, no
new service or dependency, no migration. One call, one validated judgment, one pure mapping — and the single
supervisor/repo extension the spec already scopes (persist the `evidence_patch`).

## Technical Context

**Language/Version**: Python 3.12 (pinned, repo-wide `uv` project)

**Primary Dependencies**: existing only — `LlmClient` seam (`backend/infra/llm.py`, #3), pydantic v2,
`structlog`/OpenTelemetry via the #2 observability seam, the supervisor (#7). **No new dependency, no new
service, no new container** — triage runs inside the existing `worker`.

**Storage**: Postgres `incidents` via `IncidentRepository`. Triage writes nothing itself; the supervisor
merges triage's `evidence_patch` into the existing `evidence` JSONB column. **No migration** (the column
exists; #4 `0003`).

**Testing**: `pytest` — **unit** (judgment validation, the pure `decide_outcome` mapping, LLM-error → 
`ToolError` mapping, fail-closed paths; LLM mocked), **integration** (the triage handler against a real
`LlmClient` driving a real provider), **e2e** (one ambiguous incident through the worker→supervisor→triage
spine with the LLM faked at the driver boundary), **eval** (the committed triage F1 gate on both providers).

**Target Platform**: Linux `worker` container (same image as `api`).

**Project Type**: Web-service backend (layered modular monolith `backend/`); this component touches
`agents/`, `domain/`, `infra/`, `services/`, `repositories/`.

**Performance Goals**: exactly **one** LLM call per ambiguous incident (FR-009 / SC-006); span export and
token accounting stay off the synchronous path (#2). No fan-out, no retries inside triage beyond the
supervisor's existing `max_stage_retries`.

**Constraints**: fail-closed on every error (never auto-resolve/advance on unvalidated output); one call per
incident; reported `tokens_consumed` feeds the supervisor's per-incident cap; reasons only over supplied,
already-redacted evidence; no action tools and no DB write (structural).

**Scale/Scope**: single-worker, replayed sample alerts; only the ambiguous middle reaches triage.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design — still passing (no new
violations; the design adds no service, dependency, migration, or write capability to triage).*

- [x] **I. Spec-Driven Delivery**: `spec.md` precedes code; "done" = unit + integration + e2e green in CI
      and pushed behind a focused PR (≤ ~400 lines — triage is a small, self-contained stage). No internal
      milestone split needed (not a "big" spec).
- [x] **II. Test-First, Three-Tier, Eval-Gated (NON-NEGOTIABLE)**: unit/integration/e2e planned; ≥80% on
      new code (the safety-critical fail-closed paths covered explicitly). The **triage F1 gate** is added
      to `eval_thresholds.yaml` and runs identically on **both** providers (FR-013 / SC-002).
- [x] **III. Structural Security Boundaries**: triage holds **no** action tools and **no** DB-write
      capability — enforced by the frozen `StageHandler` signature (`Incident` in, `StageResult` out; no
      session, no action client is ever injected). Reads already-redacted evidence; the outbound prompt is
      credential-scrubbed and span previews redacted by the #3/#2 seams. Injection/jailbreak rails are
      deferred to #11 (the structural no-tools/no-write boundary is the v1 net — worst case is a wrong
      verdict, never an action; SC-004).
- [x] **IV. Determinism First; Agents Only for the Ambiguous Long Tail**: the supervisor stays a
      deterministic state machine and still resolves obvious cases with **no** LLM call; triage is the LLM
      reserved for the ambiguous tail, makes **one** call, reasons **only over supplied evidence** (never
      trained priors — FR-005), emits an **evidence-cited rationale**, and **abstains/escalates** below the
      configured confidence (FR-004). Token usage is reported into the supervisor's cap.
- [N/A] **V. Human-in-the-Loop**: triage executes no consequential action and raises no approval interrupt;
      its `ESCALATE` is abstention to a human, not an `awaiting_approval` park. The approval interrupt is
      owned by response (#10).
- [x] **VI. Temporal Memory & Graceful Degradation**: triage **deliberately does not depend on memory**
      (#6) — `retrieved_context` is typically empty and that is normal, not an error (memory-backed
      retrieval is enrichment's job, #9). The **graceful-degradation** half applies in full: transient
      provider failure → retryable `ToolError` (supervisor retries, then escalates); permanent/malformed →
      escalates; the worker never crashes (SC-005).
- [x] **VII. Production Engineering Standards**: async throughout; DI via a **handler-factory closure**
      (`make_triage_handler(llm, cfg)`) that injects the `LlmClient` and typed settings while preserving the
      frozen `StageHandler` signature — which is exactly what enforces Principle III and mocks the LLM in
      tests; Pydantic at the boundary (`TriageJudgment`, `TriageSettings`); structured logging with trace
      id; observability off the synchronous path; typed `pydantic-settings` (`extra="forbid"`); `uv` for
      deps.
- [x] **Scope & Tiers**: within v1 (T1) — no ML detector, no multi-tenancy, no 4th agent, no LLM
      supervisor; no memory/intel retrieval (out of scope here, deferred to #9/#6). Respects the layering
      contract.

**Result: PASS.** No entries in Complexity Tracking.

## Project Structure

### Documentation (this feature)

```text
specs/006-triage-agent/
├── plan.md              # This file
├── research.md          # Phase 0 — design decisions (TD1…TD8)
├── data-model.md        # Phase 1 — TriageJudgment / TriageSettings / evidence_patch / repo extension
├── quickstart.md        # Phase 1 — how to run & verify triage
├── contracts/           # Phase 1 — handler, judgment schema, eval gate
│   ├── triage-handler-contract.md
│   ├── triage-judgment-schema.md
│   └── triage-eval.md
├── checklists/          # (pre-existing)
└── tasks.md             # Phase 2 — created by /speckit-tasks (NOT here)
```

### Source Code (repository root)

```text
backend/
├── domain/
│   └── triage.py             # NEW — pure types: TriageVerdict, TriageJudgment (importable by #9/#12/eval)
├── agents/
│   └── triage.py             # REPLACE stub — make_triage_handler(llm, cfg), prompt build,
│                             #   one-call generate, validate → judgment, decide_outcome, error mapping
├── infra/
│   ├── config.py             # EXTEND — TriageSettings section (+ register "triage" section, + Settings field)
│   └── supervisor_provider.py# EXTEND — wire real triage handler from container.llm + settings.triage
├── services/
│   └── supervisor.py         # EXTEND (small) — pass StageResult.evidence_patch to advance_status
├── repositories/
│   └── incidents.py          # EXTEND (small) — advance_status(evidence_patch=…) JSONB-merges into evidence
└── worker.py                 # EXTEND — register_llm_provider() BEFORE SupervisorProvider

config/
└── eval_thresholds.yaml      # EXTEND — activate the `triage` gate (macro-F1, both providers)

tests/
├── unit/                     # test_triage_judgment / _decide / _errors / _no_state (LLM mocked)
├── integration/             # test_triage_provider — handler against a real LlmClient/provider
├── e2e/                      # extend the spine e2e: ambiguous incident → triage → enriching/resolved/escalated
├── eval/                     # test_triage_gate — labeled set, macro-F1 on both providers
└── fixtures/                # NEW labeled real/noise alert set for the triage eval
```

**Structure Decision**: Modular monolith `backend/` (Option 2, backend-only — no frontend work here). The
new pure types live in `domain/triage.py` (isolated, importable by the dashboard #12 and the eval without
pulling infra). All reasoning lives in `agents/triage.py`; the supervisor/repo gain only the single,
spec-scoped `evidence_patch` persistence extension. DI is by **closure factory**, so the frozen
`StageHandler` seam (#7 SD4) and the structural no-tools/no-write boundary are preserved unchanged.

## Complexity Tracking

> No Constitution Check violations — this table is intentionally empty. Triage adds no new service,
> dependency, migration, or write/action capability; it reuses the #3 adapter, the #7 handler seam, and the
> existing `evidence` column.
