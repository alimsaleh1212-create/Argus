# Implementation Plan: Remediation Verification (Closed-Loop)

**Branch**: `015-remediation-verification` | **Date**: 2026-06-15 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/015-remediation-verification/spec.md`

## Summary

After any remediation reaches *applied* — on the auto path (`_pass_a`) or the human-approved path
(`_pass_b`) — the response stage runs a **deterministic verification step at its own tail** that compares
*observed* vs *expected* post-state and assigns a `VerificationVerdict`. `verified` keeps
`auto_remediated`/`remediated`; `unverified`/`regressed` activates the **reserved**
`remediation_unverified` disposition and **escalates** instead of resolving. The verdict combines two
signals: an **indicator re-check** (real path — `ThreatIntelClient.lookup` + `MemoryStore.query_fact`,
reusing the enrichment retrieval pattern) and an **executor status probe** (a new `probe()` method on the
`ActionExecutor` protocol — mock now, real-EDR-shaped later). Determinism-first (Constitution IV): no LLM
on the common path; an optional, config-gated LLM tiebreak fires *only* on a genuine signal conflict.

**M1** (probe + re-check) is the buildable scope and needs **no migration** (the verdict rides the existing
`incidents.evidence` JSONB via the response evidence-patch; reserved enums/disposition are merely
activated). **M2** (the `verifying` dwell-window monitoring loop) is **gated on the detector #14** and is
designed-but-deferred here. Ships as **three ≤400-line milestone PRs** under M1, then M2 later.

## Technical Context

**Language/Version**: Python 3.12 (pinned; `uv` at repo root)

**Primary Dependencies**: Pydantic v2, async SQLAlchemy, `httpx` (intel, confined to `infra/intel.py`),
existing `LlmClient` seam (optional tiebreak only); **no new dependency** for M1.

**Storage**: Postgres `incidents.evidence` JSONB (verdict persisted via the existing single-writer
evidence-patch); optional `audit_log` row (existing table) for the verification outcome. **No new table,
no migration for M1.** (M2 reuses the parked-state machinery; still text-status, no migration.)

**Testing**: pytest three-tier via `scripts/run-tests.sh` / `make test-*` (never one bare `pytest` —
spaCy/Graphiti OOM). Unit (pure verdict logic + probe, LLM mocked), integration (re-check against real
Redis/Postgres/memory), e2e (one full incident → verdict). New **`verification`** eval gate in
`backend/eval/` + `config/eval_thresholds.yaml`.

**Target Platform**: Linux container — the existing backend worker image (`python -m backend.worker`); no
new service, no new image.

**Project Type**: Backend-only extension of the response stage (peer to #9/#10 work). Dashboard touch is
read-only and largely free (disposition already surfaces in the queue; verdict rides `evidence`).

**Performance Goals**: No measurable regression to median time-to-disposition (SC-005). Re-check is one
cached `intel.lookup` (Redis) + one `query_fact` per applied target (≤ `max_indicators`, default 5); probe
is a local executor call. Verification runs inside the existing supervisor step/token cap.

**Constraints**: Read-only (Constitution III — no new write authority beyond #6's path, which #15 does not
even use; the re-check only *reads*). Best-effort + fail-closed (Constitution VI) — any signal gap →
`unverified`; a verification failure **never** blocks the incident from reaching a terminal/escalated
state. All verification text is redacted before egress.

**Scale/Scope**: Per-incident, a handful of applied actions/targets. Verdict logic is pure and O(actions).

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design.*

Derived from `.specify/memory/constitution.md` (v2.0.0).

- [x] **I. Spec-Driven Delivery**: `spec.md` precedes code; three M1 milestone PRs each ≤ ~400 lines
      (verdict-core → handler/FSM wiring → eval gate + dashboard surface); M2 a later milestone. No PR
      depends on a later one to be valid.
- [x] **II. Test-First, Three-Tier, Eval-Gated**: unit/integration/e2e planned; ≥80% on new code (higher
      on this remediation-adjacent path). New `verification` gate added to **both** the yaml *and* the
      registry in the same change (the declared⇔registered orphan check is a hard error — see #13). The
      verdict path is deterministic, so the gate is **provider-independent** like `supervisor_routing`;
      if the optional LLM tiebreak is enabled it MUST pass on both providers.
- [x] **III. Structural Security Boundaries**: verification is **read-only** — it adds **no** action tool
      and **no** new write authority; the re-check only reads (#5/#6) and the probe only observes. The
      verification record passes redaction before any log/trace/memory/dashboard egress. Triage-no-action
      and the guardrails-deferral (VD1) are untouched; no new untrusted feed is introduced.
- [x] **IV. Determinism First**: the verdict is a **pure deterministic** function; the common path makes
      **no LLM call**. An LLM tiebreak is config-gated, default-off, and fires only on a genuine
      signal conflict. The supervisor stays a deterministic FSM; one new outcome + one new edge.
- [x] **V. Human-in-the-Loop**: verification **escalates rather than false-resolves** on
      `unverified`/`regressed` (Constitution V); it executes nothing, so it needs no new approval. The
      auto/approval policy is unchanged; the verification outcome writes an audit row (actor=`verifier`).
- [x] **VI. Temporal Memory & Graceful Degradation**: the re-check uses time-valid `query_fact(as_of=…)`,
      honouring superseded-vs-current; best-effort/fail-closed on memory outage (→ `unverified`, never a
      block). Writing the verdict back to memory as a queryable fact is **#16's** job, not #15's.
- [x] **VII. Production Engineering Standards**: async; DI-by-closure (re-check retrievers + probe set
      injected into `make_response_handler`); Pydantic models for every new boundary; structured logging
      with trace IDs; typed settings on `ResponseSettings` (`extra="forbid"`); `uv`.
- [⚠] **Scope & Tiers**: in scope (no ML detector / multi-tenancy / 4th LLM agent — the verifier is a
      **deterministic helper**, not an agent / no live capture). **Layering-contract watch-item**: this is
      T2/v2 work. Per roadmap §6.1 the *design* may proceed ahead of the T1 tag (additive, low-risk), but
      **implementation code lands only after the T1 freeze (#12 dashboard, #13 eval green-and-tagged)** or
      under an explicit `DECISIONS.md` entry. Tracked below — not a principle violation, a sequencing gate.

## Project Structure

### Documentation (this feature)

```text
specs/015-remediation-verification/
├── plan.md              # This file
├── research.md          # Phase 0 — design decisions (D1–D10)
├── data-model.md        # Phase 1 — types, FSM delta, settings delta
├── quickstart.md        # Phase 1 — run/test/demo
├── contracts/
│   ├── verification-contract.md   # probe protocol, verdict fn, FSM edge, evidence shape
│   └── verification-eval.md       # the verification-accuracy gate spec
└── checklists/requirements.md     # (from /speckit-specify)
```

### Source Code (repository root)

```text
backend/
├── domain/
│   ├── response.py        # EXTEND: ProbeResult, ProbeState, VerificationSignals,
│   │                      #         VerificationRecord, decide_verdict(); probe() on ActionExecutor.
│   │                      #         (VerificationVerdict already present, reserved → activated)
│   └── pipeline.py        # EXTEND: StageOutcome.UNVERIFIED
├── infra/
│   ├── executors.py       # EXTEND: probe() on mock executors (expected post-state);
│   │                      #         build_regressed_executors(...) for tests
│   └── config.py          # EXTEND: ResponseSettings verification fields (no new section)
├── agents/
│   └── response.py        # EXTEND: verify_remediation(); wire into _pass_a (auto-only) + _pass_b;
│                          #         map verdict → RESOLVED | UNVERIFIED outcome
├── services/
│   └── supervisor.py      # EXTEND: transition (RESPONDING, UNVERIFIED) → (ESCALATED,
│                          #         DISP_REMEDIATION_UNVERIFIED)  [DISP constant already present]
└── eval/
    └── gates/
        └── verification.py  # NEW: deterministic verification-accuracy gate runner

config/
└── eval_thresholds.yaml   # EXTEND: `verification` gate block (+ supervisor_routing fixtures)

tests/
├── unit/                  # verdict logic, probe contract, settings
├── integration/           # re-check against real intel/memory; handler verdict paths
├── e2e/                   # full incident → verdict → disposition
└── fixtures/verification/ # NEW: labeled post-remediation states (verified/unverified/regressed)
```

**Structure Decision**: Backend-only, mirroring `009-enrichment-agent` (zero schema change, closure-factory
DI, pure domain types). The verifier lives at the **tail of the existing response stage**, not as a new
stage or agent — honouring "no new agent" (roadmap/Constitution IV) and the single-writer supervisor.

## Complexity Tracking

| Item | Why | Note / mitigation |
|------|-----|-------------------|
| New `StageOutcome.UNVERIFIED` + one FSM edge | The `(RESPONDING, ESCALATE)` edge hardcodes `escalated_response`; a distinct outcome keeps "no playbook" vs "unverified remediation" auditable (Constitution IV) and mirrors the `NEEDS_APPROVAL` precedent | Rejected: reusing `ESCALATE` with table-disp pass-through (latent `None`-disposition risk). Blast radius is one enum member emitted only by the response tail. |
| Response handler gains re-check retrievers (intel/memory) | The indicator re-check is a real retrieval path | Injected by closure like enrichment; **read-only**, no new write authority (Constitution III preserved). |
| Planning ahead of the T1 tag | Value-early v2 sequence (roadmap §2) | **Sequencing gate, not a violation**: design now, code after the #12/#13 freeze or under a recorded `DECISIONS.md` entry. Surfaced in the Constitution Check. |

## Phase 0 — Research

See [research.md](research.md): D1 verifier placement (response tail, no new stage); D2 FSM disposition
(new `UNVERIFIED` outcome + edge); D3 executor `probe()` contract; D4 indicator re-check (reuse enrichment
retrieval); D5 deterministic verdict + worst-case aggregation + optional conflict-only LLM tiebreak; D6
settings on `ResponseSettings`; D7 M1/M2 split + M2 `verifying` design; D8 verification eval gate (declared
⇔registered together) + temporal/redaction gate extension; D9 memory write-back boundary (#16, not #15);
D10 idempotency via terminal-state no-op + evidence presence check.

## Phase 1 — Design & Contracts

Outputs: [data-model.md](data-model.md), [contracts/verification-contract.md](contracts/verification-contract.md),
[contracts/verification-eval.md](contracts/verification-eval.md), [quickstart.md](quickstart.md). The
agent-context pointer in `CLAUDE.md` (between the SPECKIT markers) is updated to this plan.

## Phase 2 — (next) `/speckit-tasks`

Task breakdown is produced by `/speckit-tasks`, not here. Expected milestone slices:
**M1-a** verdict-core (domain types + `decide_verdict` + probe contract + mock probe + unit) →
**M1-b** handler/FSM wiring (`verify_remediation`, `StageOutcome.UNVERIFIED`, supervisor edge, settings,
integration/e2e) → **M1-c** eval gate + temporal/redaction extension + read-only dashboard surface.
**M2** (`verifying` monitoring loop) is deferred until #14 lands.
