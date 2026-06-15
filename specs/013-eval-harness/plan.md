# Implementation Plan: Consolidated Evaluation Harness & CI Gates

**Branch**: `013-eval-harness` | **Date**: 2026-06-15 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/013-eval-harness/spec.md`

## Summary

Component **#13 `SPEC-eval`** — the **T1 day-9 freeze** spec. It *consolidates* eval work that has
been seeded component-by-component (seven gates already live in [config/eval_thresholds.yaml](../../config/eval_thresholds.yaml)
with tests in [tests/eval/](../../tests/eval/)) into the connective tissue the freeze requires:

1. **A single harness** (`python -m backend.eval`) that reads the thresholds file as its source of
   truth, runs every declared gate, scores each, and emits one structured `EvalReport`.
2. **CI enforcement** — today the gate tests are **not run by CI** ([ci.yml](../../.github/workflows/ci.yml)
   runs only unit/integration/e2e). A new per-PR `eval` job makes a required-gate regression fail the build.
3. **Both-providers freeze run** — a scheduled/dispatch workflow runs the LLM-dimension gates on **both**
   Gemini + Ollama, persists the report to the reserved **`eval-reports`** MinIO bucket under a
   per-commit/run key (history retained), and yields the certifiable/not-certifiable verdict.
4. **One net-new gate** — an LLM-judge **rationale-quality** evaluation over triage/enrichment/response,
   pinned judge (Gemini), validated against a small hand-labeled set, **reported-only** (catastrophic
   floor blocks).

**Out of scope (VD1):** the red-team / injection gate stays deferred to #11 (v3b). The harness reserves
its seam and makes no v1 injection-coverage claim. This is the one Constitution III deviation — tracked
below against the pending amendment.

## Technical Context

**Language/Version**: Python 3.12 (`uv` at repo root; the React `frontend/` is untouched — eval is
backend tooling only).

**Primary Dependencies**: `pytest` + `pytest-asyncio` (gate tests, unchanged), **`pyyaml`** (read the
thresholds file — promote from a transitive lock entry to a *direct* pinned dep), `aioboto3` via the
existing `infra/blob.py` `BlobClient` (report → MinIO), the existing `infra/llm.py` `LlmClient` seam
(#3) for the pinned rationale judge, and the existing `infra/redaction.py` `Redactor` (#2) on every
judge prompt + report. **No `scikit-learn`/`numpy`** — F1 and judge↔human agreement are hand-rolled
(consistent with the existing triage gate's hand-rolled macro-F1).

**Storage**: MinIO bucket **`eval-reports`** (already reserved in `MinioSettings.buckets`), keyed
`reports/{commit_sha}/{run_id}.json` (freeze copies under `freezes/{tag}/…`) — history retained, never
overwritten. Reads committed fixtures under `tests/fixtures/**`; thresholds in
`config/eval_thresholds.yaml`.

**Testing**: the gates **are** tests (`tests/eval/*.py`, pytest, `asyncio_mode=auto`); the harness/report
logic gets its own unit tests (`tests/unit/test_eval_*`), and the report→MinIO round-trip an integration
test (testcontainers, the `infra/blob.py` pattern).

**Target Platform**: GitHub Actions runners (per-PR + nightly/freeze) and local dev (`make eval`).

**Project Type**: Single backend project (layered `backend/`); no frontend, no new service.

**Performance Goals**: per-PR `eval` job adds only the deterministic gates + LLM gates on **Ollama**
(single provider) — target a few minutes, fast feedback. The both-providers + judge run is reserved for
nightly/freeze where wall-time is not gating.

**Constraints**: **memory-safe** — heavy gates (Presidio/spaCy redaction, graphiti) must run in
isolated subprocesses via the existing batched runner pattern ([scripts/run-tests.sh](../../scripts/run-tests.sh)),
so a full run does not OOM. Eval is entirely **off the incident hot path** (no runtime latency impact).

**Scale/Scope**: 8 gates total (7 existing consumed unchanged + 1 net-new rationale); small committed
golden sets (handful of labeled items per gate). Net-new code is the harness, the report model, the
rationale gate, config + CI wiring — sized for a *(big)* spec across 3 internal milestones.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design (below).*

Derived from `.specify/memory/constitution.md` (v2.0.0).

- [x] **I. Spec-Driven Delivery**: spec precedes code; this *(big)* spec commits at **3 internal
      milestones** (M1 harness+CI deterministic → M2 both-providers+MinIO → M3 rationale judge), each a
      focused PR ≤ ~400 lines, each leaving the suite green.
- [x] **II. Test-First, Three-Tier, Eval-Gated (NON-NEGOTIABLE)**: this spec *is* the fulfilment of II —
      it finally **wires the eval gates into CI** (the current gap) and runs both providers at the freeze.
      Harness logic is unit-tested first; report→MinIO is integration-tested; coverage stays ≥80%. The v1
      eval gate set (constitution v2.0.0) excludes red-team, so there is no shortfall.
- [x] **III. Structural Security Boundaries**: the **v1 non-negotiables** hold — redaction-before-write is
      honored (FR-014 — every judge prompt + report passes the `Redactor`) and triage-has-no-action-tools
      is unaffected (this spec acts on nothing). The injection/jailbreak **red-team gate** is **deferred to
      #11/v3b**, now **codified in constitution v2.0.0** (VD1) — not an exception. The harness reserves the
      seam and asserts no v1 injection-coverage claim (FR-015).
- [x] **IV. Determinism First**: the harness, the deterministic gates, and threshold comparison are fully
      deterministic; the **only** probabilistic element (the LLM judge) is **reported-only**, never a hard
      merge gate (FR-004). No LLM on the per-PR blocking path beyond the single-provider gate.
- [x] **V. Human-in-the-Loop**: **N/A** — the eval system reads and reports; it executes no action and
      mutates no incident/approval state.
- [x] **VI. Temporal Memory & Graceful Degradation**: consumes the existing `temporal_memory` and
      `retrieval` gates **read-only**; writes no memory, adds no de-redaction path. A provider/store
      outage degrades to "unknown" in the report without aborting the run (FR-016).
- [x] **VII. Production Engineering Standards**: async (`aioboto3`, `LlmClient`); a new typed
      `EvalSettings` (`pydantic-settings`, `extra="forbid"`) holds harness wiring; structured logging;
      thresholds read from the committed file (no hardcoded divergence, FR-011); `uv`-managed pinned deps.
- [x] **Scope & Tiers**: squarely the T1 freeze spec; no detector/ML/multi-tenancy/4th-agent. The red-team
      deferral is codified in v2.0.0 (VD1), not scope creep.

**Gate result:** PASS. The red-team deferral is codified in constitution v2.0.0 (VD1), so no exception is
required. No violations.

## Project Structure

### Documentation (this feature)

```text
specs/013-eval-harness/
├── plan.md              # This file
├── spec.md              # Feature spec (+ Clarifications)
├── research.md          # Phase 0 — config-value & design decisions resolved
├── data-model.md        # Phase 1 — EvalReport / GateResult / report DTOs
├── quickstart.md        # Phase 1 — run the suite, run a freeze, add a gate
├── contracts/           # Phase 1 — report schema, CLI, CI-gate, rationale-gate contracts
└── tasks.md             # Phase 2 (/speckit-tasks — NOT created here)
```

### Source Code (repository root)

```text
backend/
├── domain/
│   └── eval.py              # NEW — pure DTOs: GateSpec, GateResult, ProviderResult,
│                            #       RationaleScore, EvalReport, FreezeVerdict (no I/O; domain-isolated)
├── eval/                    # NEW — the harness package (top-level entrypoint, like worker.py/seed_corpus.py)
│   ├── __init__.py
│   ├── __main__.py          # `python -m backend.eval` — CLI (--freeze, --providers, --gate, --upload), exit codes
│   ├── harness.py           # load thresholds → run registry → aggregate EvalReport; orphan/stale detection (FR-002)
│   ├── thresholds.py        # read config/eval_thresholds.yaml into GateSpec[] (single source of truth, FR-011)
│   ├── report.py            # serialize EvalReport → JSON; upload via infra/blob BlobClient (FR-008/009)
│   ├── judge.py             # M3 — pinned-judge (Gemini) rationale scorer + judge↔hand-label agreement
│   └── gates/               # one runner per gate; existing scoring helpers shared with tests/eval/*
│       ├── __init__.py      # GATE_REGISTRY: name → async runner(provider) -> GateResult
│       ├── deterministic.py # supervisor_routing, retrieval, temporal_memory, redaction (provider-independent)
│       ├── llm.py           # triage, llm_provider (per-provider dimension)
│       ├── rationale.py     # M3 — rationale gate (reported-only)
│       └── smoke.py         # smoke gate adapter (compose readiness; freeze/CI-smoke context)
├── infra/
│   └── config.py            # EDIT — add EvalSettings (extra="forbid"): thresholds_path, report bucket/prefix,
│                            #         providers_per_pr / providers_freeze, rationale judge model + fixture dir
tests/
├── eval/                    # existing gate tests STAY; add test_rationale_gate.py; thresholds read from yaml
├── unit/                    # NEW test_eval_harness.py (orphan/stale, threshold read, report shape, exit codes)
├── integration/            # NEW test_eval_report_minio.py (report round-trip via testcontainers MinIO)
└── fixtures/
    └── rationale/           # NEW triage.json / enrichment.json / response.json (hand-labeled reference rationales)
config/
└── eval_thresholds.yaml     # EDIT — add `rationale` gate block (reported-only, catastrophic_floor, judge, fixtures)
.github/workflows/
├── ci.yml                   # EDIT — add per-PR `eval` job (deterministic + Ollama single-provider; required)
└── eval-freeze.yml          # NEW — schedule(nightly) + workflow_dispatch + tag push: both providers + judge + MinIO upload
scripts/
└── run-evals.sh             # NEW — memory-safe batched eval runner (mirrors run-tests.sh)
Makefile                     # EDIT — `eval` (local deterministic+single) and `eval-freeze` targets
pyproject.toml               # EDIT — add pyyaml as a direct pinned dep
```

**Structure Decision**: The harness is a **top-level `backend/eval/` entrypoint package**, peer to
`worker.py`/`seed_corpus.py` — it is a standalone runner, not on the request/incident path, so it does
not belong under `routers/services/agents/repositories`. Pure report DTOs live in `domain/eval.py`
(domain stays isolated and I/O-free per `import-linter`). Each gate runner shares its **scoring helpers**
with the corresponding `tests/eval/` test, so there is exactly one scoring implementation and one
threshold source (the yaml), satisfying FR-002/FR-011 without duplicating logic.

## Complexity Tracking

**No violations.** The only candidate — the absent red-team / injection gate — is **not** a deviation:
it is codified in **constitution v2.0.0** (Principle III re-tiered; VD1), which defers the rails + red-team
gate to **v3b** (before v3c live feeds). The harness reserves the seam and makes no v1 injection claim
(FR-015). No exception entry required.
