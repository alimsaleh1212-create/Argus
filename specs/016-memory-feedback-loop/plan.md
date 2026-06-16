# Implementation Plan: Memory Feedback Loop (Gets Smarter Over Time)

**Branch**: `016-memory-feedback-loop` | **Date**: 2026-06-16 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/016-memory-feedback-loop/spec.md`

## Summary

When an incident reaches a terminal disposition carrying a verification verdict (produced by #15,
`evidence["response"]["verification"]`), a **best-effort, off-path** step writes the verdict + remediation
outcome back to temporal memory as a **time-valid `TemporalFact`** (`fact_type="remediation_outcome"`,
`value=<verdict>`), keyed to each applied action's target — the **same write path and worker seam the episode
write already uses** (`MemoryStore.write_fact`, invalidate-not-delete). Future incidents then consume it
through a **deterministic feedback lookup** (`query_fact(as_of=None)` — current/time-valid only) that tunes two
config-backed **inputs**: (1) it **raises effective severity / biases routing toward escalation** at the
grounding-evidence boundary when an indicator carries a current failure-class outcome (`regressed`/`unverified`),
and (2) it makes playbook selection **prefer a stronger playbook** on a known-failed remediation. The result is
the brief's demo #5 — *"the same alert handled differently after memory accumulates"* — and it is
**memory/retrieval, not retraining** (Constitution VI), **deterministic** (Constitution IV), and adds **no second
writer of incident state** (the supervisor stays the single writer; feedback tunes inputs — Constitution III).

**M1** (within-SOAR tuning) is the buildable scope and needs **no migration** (the outcome fact rides the
existing memory store; the bias rides the existing grounding-evidence write and the deterministic router /
playbook selector; reserved nothing-new on the incident schema). **M2** (feed-to-detector export) is **gated on
the detector #14** and is designed-but-deferred here. Ships as **three ≤400-line milestone PRs** under M1, then
M2 later.

## Technical Context

**Language/Version**: Python 3.12 (pinned; `uv` at repo root)

**Primary Dependencies**: Pydantic v2, async SQLAlchemy, existing `MemoryStore` Protocol
(`domain/memory.py`: `write_fact`/`query_fact`), existing redaction (`infra/redaction.py`), existing playbook
catalog loader (`agents/response/catalog.py`). **No new runtime dependency** for M1.

**Storage**: Temporal memory (Graphiti/Neo4j with the decided pgvector fallback) via the existing
`MemoryStore.write_fact` — the **same off-path, best-effort, post-terminal write the worker already performs for
episodes**. The bias reads the in-flight `incidents.evidence` JSONB (grounding slice) and writes the
prior-outcome slice through the existing grounding-evidence write. **No new table, no migration for M1.**

**Testing**: pytest three-tier via `scripts/run-tests.sh` / `make test-*` (never one bare `pytest` —
spaCy/Graphiti OOM). Unit (pure bias rules + fact builder, memory mocked), integration (write→`query_fact`
round-trip against real memory; bias against a seeded fact), e2e (1st vs 2nd occurrence handled differently).
New **`feedback`** eval gate in `backend/eval/gates/` + `config/eval_thresholds.yaml`; **extends**
`temporal_memory` (outcome-fact time-validity) and `supervisor_routing` (a prior-`regressed`-escalates fixture).

**Target Platform**: Linux container — the existing backend worker image (`python -m backend.worker`); no new
service, no new image.

**Project Type**: Backend-only extension (mirrors #9/#15 — closure-factory DI, pure domain types). Dashboard
touch is read-only and largely free (the `MemoryHit` KPI surface already exists).

**Performance Goals**: No measurable regression to median time-to-disposition (SC-005). Write-back is fire-and-
forget off-path (like the episode write). The feedback lookup is one cached/`query_fact` per indicator
(≤ `feedback.max_indicators`, default 5), best-effort and concurrent (`asyncio.gather`), and runs once at the
grounded boundary.

**Constraints**: **Memory/retrieval, not retraining** (Constitution VI). **Deterministic** bias, config-backed
(Constitution IV — no LLM added on the feedback path). **No second writer** of incident state (Constitution III
— the supervisor remains the only writer of status/disposition; feedback tunes *inputs*). **Best-effort +
graceful degradation** — a memory outage means *no bias* (fail-open to baseline v1 behavior) and *no write*,
never a block on disposition. **Redaction before egress** on every memory write (Constitution III).

**Scale/Scope**: Per-incident, a handful of indicators/applied targets. Bias logic is pure and O(indicators);
the write is O(applied targets).

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design.*

Derived from `.specify/memory/constitution.md` (v2.0.0).

- [x] **I. Spec-Driven Delivery**: `spec.md` precedes code; three M1 milestone PRs each ≤ ~400 lines
      (write-back → consumption/bias → eval gate + dashboard surface); M2 a later milestone. No PR depends on a
      later one to be valid; M1 stands alone without #14.
- [x] **II. Test-First, Three-Tier, Eval-Gated**: unit/integration/e2e planned; ≥80% on new code. New
      **`feedback`** gate added to **both** the yaml *and* the registry in the same change (the
      declared⇔registered orphan check is a hard error — #13). The bias is deterministic, so the gate is
      **provider-independent** like `supervisor_routing`/`verification`. Extends `temporal_memory` (outcome-fact
      time-validity) + `supervisor_routing` (prior-regressed-escalates) + `redaction` (outcome fact + KPI view).
- [x] **III. Structural Security Boundaries**: consumption is **read-only**; the write reuses the existing
      memory write path and passes the **memory-write redaction boundary** before egress. **No second writer of
      incident state** — feedback tunes the grounding-evidence *inputs* the supervisor routes on; the supervisor
      remains the single writer of status/disposition and approve/reject remains the only human write path.
      Triage-no-action and the guardrails deferral (VD1) are untouched; **no new untrusted feed** in M1 (M2's
      memory→detector export passes the same guardrails per Constitution III tiering, before any v3c feed).
- [x] **IV. Determinism First**: the bias rules are a **pure, config-backed** function; the feedback path makes
      **no LLM call**. The supervisor stays a deterministic FSM (M1 adds **no new FSM edge** — the escalation
      bias rides the existing severity→`route_grounded` path; the stronger-playbook preference rides existing
      deterministic selection).
- [x] **V. Human-in-the-Loop**: feedback **escalates** (routes toward human attention) and **prefers stronger
      playbooks** — it executes nothing new and adds no new auto action, so it needs no new approval. The
      auto/approval allowlist is unchanged; a stronger playbook with destructive actions still parks for approval
      via the existing #10 path.
- [x] **VI. Temporal Memory & Graceful Degradation**: the outcome fact is **time-valid** (invalidate-not-delete
      via `write_fact`); consumption reads **current** state via `query_fact(as_of=None)` and ignores superseded
      facts; best-effort/fail-open on memory outage (→ no bias, baseline behavior; → no write, never a block).
- [x] **VII. Production Engineering Standards**: async; DI-by-closure (feedback retriever + memory injected like
      enrichment); Pydantic models for every new boundary; structured logging with trace IDs; new typed
      `FeedbackSettings` section (`extra="forbid"`); `uv`.
- [⚠] **Scope & Tiers**: in scope (no ML detector / multi-tenancy / 4th LLM agent — the feedback lookup is a
      **deterministic helper**, not an agent / no live capture / supervisor stays deterministic). **Layering-
      contract watch-item** (same as #15): this is T2/v2 work. Per roadmap §6.1 the *design* may proceed ahead of
      the T1 tag (additive, low-risk), but **implementation code lands only after the T1 freeze (#12 dashboard,
      #13 eval green-and-tagged)** or under an explicit `DECISIONS.md` entry. Tracked below — a sequencing gate,
      not a principle violation.

## Project Structure

### Documentation (this feature)

```text
specs/016-memory-feedback-loop/
├── plan.md              # This file
├── research.md          # Phase 0 — design decisions (D1–D11)
├── data-model.md        # Phase 1 — types, fact shape, settings delta, bias rules
├── quickstart.md        # Phase 1 — run/test/demo
├── contracts/
│   ├── feedback-write-contract.md        # outcome-fact shape, write seam, idempotency, redaction
│   ├── feedback-consumption-contract.md  # feedback lookup + deterministic bias rules (severity/routing/playbook)
│   └── feedback-eval.md                  # the feedback-effectiveness gate spec
└── checklists/requirements.md            # (from /speckit-specify)
```

### Source Code (repository root)

```text
backend/
├── domain/
│   └── feedback.py        # NEW: pure types + bias rules — RemediationOutcome (fact mapping),
│                          #      FeedbackSignal, decide_severity_bias(), prefer_stronger_playbook()
├── services/
│   ├── memory.py          # EXTEND: record_outcome_facts(incident, store, redactor) — write the
│   │                      #         time-valid remediation_outcome fact(s) per applied target
│   └── feedback.py        # NEW: gather_feedback(memory, entities, cfg) — read-only current-outcome
│                          #      lookup (mirrors enrichment context fan-out, best-effort)
├── worker.py              # EXTEND: _maybe_record_episode also records outcome facts (off-path);
│                          #         grounding path runs gather_feedback → augments Evidence + severity bias
├── agents/
│   └── response/
│       └── selection.py   # EXTEND: stronger-playbook preference when target has a current failure-class fact
├── services/
│   └── supervisor.py      # EXTEND (minimal): route_grounded honours the prior-failure escalation flag
│                          #         (no new FSM edge — biases the existing severity→route path)
├── infra/
│   └── config.py          # NEW: FeedbackSettings section; catalog `strength` consumed by selection
└── eval/
    └── gates/
        └── feedback.py    # NEW: deterministic feedback-effectiveness gate runner

config/
├── eval_thresholds.yaml   # EXTEND: `feedback` gate block; supervisor_routing + temporal_memory fixtures
└── (playbooks)            # EXTEND: optional `strength` rank on catalog entries (config-backed ordering)

tests/
├── unit/                  # bias rules, fact builder/mapping, settings
├── integration/           # write→query_fact round-trip; bias against a seeded fact
├── e2e/                   # 1st vs 2nd occurrence handled differently (the demo)
└── fixtures/feedback/     # NEW: labeled baseline-vs-repeat scenarios
```

**Structure Decision**: Backend-only, mirroring `009-enrichment-agent` / `015-remediation-verification` (zero
schema change, closure-factory DI, pure domain types in a new `domain/feedback.py`). The **write** extends the
existing off-path worker→`services/memory` seam; the **read/bias** is a deterministic helper at the existing
grounded boundary plus the existing deterministic playbook selector — **no new agent, no new pipeline stage, no
new FSM edge** for M1, and the supervisor remains the single writer.

## Complexity Tracking

| Item | Why | Note / mitigation |
|------|-----|-------------------|
| New `domain/feedback.py` + `services/feedback.py` | The feedback concern (read outcome facts → deterministic bias) is genuinely cross-cutting (grounding + routing + response) | Pure domain types + one read-only service; mirrors the enrichment retrieval split. Keeps bias rules unit-testable and out of stage handlers. |
| New `FeedbackSettings` config section | The feature spans grounding/routing/response, so its switches don't belong to any single existing section | Rejected: scattering `feedback_*` fields across `Supervisor`/`Response`/`Enrichment` settings — would fragment one config concern across three sections. One typed section (`extra="forbid"`) is the honest home. |
| Catalog `strength` rank (config-backed) | Stronger-playbook preference needs an ordering | Rejected: hardcoding an escalation map in `selection.py` (violates Constitution VII config-backed). An optional integer `strength` on catalog entries keeps the ordering in config. |
| Feedback lookup adds memory I/O at the grounded boundary | Severity/routing bias **must precede** routing, so it cannot wait for the enrichment stage | Best-effort/fail-open (memory outage → baseline), concurrent (`asyncio.gather`), bounded by `max_indicators`; mirrors the enrichment `_safe(...)` pattern. Off the LLM path. |
| Planning ahead of the T1 tag | Value-early v2 sequence (roadmap §2) | **Sequencing gate, not a violation**: design now, code after the #12/#13 freeze or under a recorded `DECISIONS.md` entry. Surfaced in the Constitution Check. |

## Phase 0 — Research

See [research.md](research.md): D1 write-back location (extend the worker off-path seam, no supervisor change);
D2 outcome-fact shape + **key-consistency invariant** (key identically to the reputation fact so write-key ==
read-key); D3 feedback lookup placement (deterministic, at the grounded boundary, before routing); D4 severity/
routing escalation bias (tune the grounding-evidence input, no new FSM edge); D5 stronger-playbook preference
(config-backed `strength`); D6 settings home (`FeedbackSettings`); D7 single-writer honesty (feedback tunes
inputs, supervisor stays sole writer); D8 best-effort/graceful degradation (fail-open to baseline); D9 the
`feedback` eval gate (declared⇔registered together) + `temporal_memory`/`supervisor_routing` extension; D10
idempotency (off-path write keyed by target+verdict, terminal no-op); D11 M1/M2 split + M2 feed-to-detector
export boundary (gated on #14).

## Phase 1 — Design & Contracts

Outputs: [data-model.md](data-model.md),
[contracts/feedback-write-contract.md](contracts/feedback-write-contract.md),
[contracts/feedback-consumption-contract.md](contracts/feedback-consumption-contract.md),
[contracts/feedback-eval.md](contracts/feedback-eval.md), [quickstart.md](quickstart.md). The agent-context
pointer in `CLAUDE.md` (between the SPECKIT markers) is updated to this plan.

## Phase 2 — (next) `/speckit-tasks`

Task breakdown is produced by `/speckit-tasks`, not here. Expected milestone slices:
**M1-a** write-back (`domain/feedback.py` outcome mapping + `services/memory.record_outcome_facts` + worker
off-path hook + redaction + idempotency + `temporal_memory` `remediation_outcome_flip` case + unit/integration)
→ **M1-b** consumption/bias (`services/feedback.gather_feedback`, severity/routing bias at the grounded
boundary, stronger-playbook preference in `selection.py`, `FeedbackSettings`, integration/e2e) → **M1-c**
`feedback` eval gate + `supervisor_routing` fixture extension + read-only dashboard KPI surface. **M2**
(feed-to-detector export) is deferred until #14 lands.
