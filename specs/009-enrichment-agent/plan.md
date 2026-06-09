# Implementation Plan: Enrichment Agent

**Branch**: `009-enrichment-agent` | **Date**: 2026-06-10 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/009-enrichment-agent/spec.md`

## Summary

Replace the supervisor's `run_enrichment` **stub** (which blindly ADVANCEs) with a real, retrieval-backed
**cross-correlation** stage — the **second** LLM stage in the pipeline, and the first that **retrieves**
context from outside the incident. It runs only on incidents triage marked `advance` (real-and-actionable),
which the supervisor already routes to `ENRICHING`.

The stage mirrors triage's shape exactly: a **bounded retrieval fan-out** (`asyncio.gather` — the canonical
"where enrichment fans out" of Principle VII) followed by **exactly one** structured-output call through the
shared `LlmClient` (#3). The fan-out reads **both directions** through contracts that **already exist**:

- **External** — the seeded reference corpus via `CorpusRetriever.search_reference` (#5), and an optional
  on-demand `IntelVerdict` for the incident's indicators via `ThreatIntelClient.lookup` (#5).
- **Internal** — similar prior incidents via `MemoryStore.search_similar`, and the **time-valid**
  reputation/role of the incident's entities via `MemoryStore.query_fact(as_of=…)` (#6, the temporal
  differentiator).

The one reasoning call **correlates** the two directions into a validated `EnrichmentReport` (a headline
correlation summary + the external/internal findings it rests on + an assessment with an evidence-citing
rationale), which a pure, config-threshold-gated function maps to one `StageOutcome`: **ADVANCE** (correlated
real → response, the common path), **RESOLVED** (correlation confidently exonerates → auto-close), or
**ESCALATE** (conflicting / low-confidence → human). Enrichment holds **retrieval tools only**, writes **no**
incident state, and returns a `StageResult`; the supervisor persists everything (single writer). Every
failure mode — a retrieval backend down/empty, intel disabled/`unknown`/timeout, the provider down/timeout,
malformed output — **degrades or fails closed** and never crashes the worker.

**Keep-it-simple posture (the user's standing steer).** This component is **almost entirely wiring +
reuse**:
- **No new service, no new dependency, no migration.** Enrichment consumes the `CorpusRetriever`,
  `ThreatIntelClient`, and `MemoryStore` that #5/#6 already built and exposed as DI singletons.
- **Zero change to `supervisor.py` or `repositories/incidents.py`.** #7 already defined all three
  `ENRICHING → {responding, resolved, escalated}` transitions and already merges `evidence_patch` — unlike
  triage, which needed a small supervisor/repo extension, enrichment needs **none**.
- **One LLM call, one validated report, one pure mapping** — no agentic loop, no tools, no write capability.
- The only non-trivial change is **worker DI wiring**: register the (worker-absent) `CorpusProvider` +
  `IntelProvider` and order `memory`/`corpus`/`intel` before `SupervisorProvider`, which then builds the real
  enrichment handler the same eager way it builds triage.

## Technical Context

**Language/Version**: Python 3.12 (pinned, repo-wide `uv` project)

**Primary Dependencies**: existing only — the `LlmClient` seam (`backend/infra/llm.py`, #3), the
`CorpusRetriever` + `ThreatIntelClient` (`backend/infra/corpus.py` / `backend/infra/intel.py`, #5), the
`MemoryStore` (`backend/infra/memory.py`, #6), pydantic v2, `structlog`/OpenTelemetry via the #2 seam, the
supervisor (#7). **No new third-party package, no new service, no new container** — enrichment runs inside
the existing `worker`.

**Storage**: none of its own. Enrichment **reads** Postgres `reference_corpus` (through the `CorpusRetriever`)
and Neo4j/Graphiti (through the `MemoryStore` read methods), and **writes nothing itself**. The supervisor
merges enrichment's `evidence_patch` into the existing `evidence` JSONB column. **No migration.** (The only
incidental persistence is the `ThreatIntelClient`'s own designed reputation accretion via `write_fact`,
internal to #5's client — off the incident-state path.)

**Testing**: `pytest` — **unit** (report validation, the pure `decide_outcome` mapping, the deterministic
query/entity builders, retrieval-degradation paths, LLM-error → `ToolError` mapping, no-state/no-action;
retrievers + LLM mocked), **integration** (the enrichment handler against a real `CorpusRetriever` (seeded
Postgres) + real `MemoryStore` (Neo4j) + a real `LlmClient` on **both** providers), **e2e** (one full-depth
incident through worker→supervisor→triage→**enrichment**→responding/resolved/escalated with the LLM faked at
the driver boundary), **eval** (extend the committed provider-independent **retrieval** gate with an
enrichment fixture set — no new gate).

**Target Platform**: Linux `worker` container (same image as `api`).

**Project Type**: Web-service backend (layered modular monolith `backend/`); this component touches
`agents/`, `domain/`, `infra/config.py`, the `supervisor_provider`, and `worker.py` wiring — **not**
`services/supervisor.py` or `repositories/`.

**Performance Goals**: exactly **one** LLM call per enriched incident (FR-011 / SC-006); the retrieval
fan-out runs concurrently (`asyncio.gather`) and is bounded by k (corpus/memory) and a capped indicator set
(intel); corpus retrieval is an indexed keyed/lexical query, intel is Redis-cached + timeout-bounded, and all
of it stays off the synchronous disposition budget. Reported `tokens_consumed` feeds the supervisor's
per-incident cap.

**Constraints**: retrieval is **best-effort** — any backend unavailable/empty, intel disabled/`unknown`/
timeout → degrade to "context not available," never fail the incident (FR-008). Fail-closed on every
reasoning error (never advance/auto-resolve on unvalidated output — FR-009). One reasoning call per incident.
Reasons only over supplied, already-redacted evidence + already-redacted retrieved context (no raw text
emitted; no redactor needed in-stage). **No action tools, no incident-state write** (structural). Uses only
the **read** methods of the `MemoryStore` (`search_similar`/`query_fact`); never `write_episode`.

**Scale/Scope**: single-worker, replayed sample alerts; a small curated corpus and a small prior-incident
set; only triage-`advance` incidents reach enrichment.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design — still passing (the design adds no
service, dependency, migration, or write/action capability to enrichment; it changes only DI wiring and adds
one stage handler + one pure report type).*

- [x] **I. Spec-Driven Delivery**: `spec.md` precedes code; "done" = unit + integration + e2e green in CI and
      pushed behind a focused PR (≤ ~400 lines — enrichment is a small, self-contained stage). Not a "big"
      spec; no internal-milestone split required.
- [x] **II. Test-First, Three-Tier, Eval-Gated (NON-NEGOTIABLE)**: unit/integration/e2e planned; ≥80% on new
      code (degradation + fail-closed paths covered explicitly). The eval extends the existing
      **`retrieval`** gate with an enrichment fixture set (hit@k/MRR); the retrieval gate is
      provider-independent (deterministic store logic), and the handler's correlation call is exercised on
      **both** providers in the integration tier (FR-015 / SC-002). The cross-correlation *quality* LLM-judge
      is SPEC-eval (#13) scope, not invented here.
- [x] **III. Structural Security Boundaries**: enrichment holds **retrieval-only** tools and **no** DB-write/
      action capability — enforced by the frozen `StageHandler` signature (`Incident` in, `StageResult` out;
      only read-only retrievers are injected; no session, no action client). It reads already-redacted
      evidence + already-redacted retrieved context and treats all retrieved/intel/feed text as **untrusted
      data**; the intel client already redacts + routes feed text through the reserved #11 guardrail seam
      before any write. Injection/jailbreak rails are deferred to #11 (the structural no-tools/no-write
      boundary is the v1 net — worst case is a wrong *assessment*, never an action; SC-004).
- [x] **IV. Determinism First; Agents Only for the Ambiguous Long Tail**: the supervisor stays a
      deterministic state machine; enrichment is the LLM reserved for the ambiguous correlation that no keyed
      lookup settles (the brief's "conflicting evidence needs a judgment call"). It makes **one** call,
      reasons **only over supplied + retrieved evidence** (never trained priors), emits an **evidence-cited**
      correlation rationale, and **abstains/escalates** below the configured confidence (FR-006). Retrieval
      itself is deterministic (keyed/lexical corpus + store search). Token usage is reported into the cap.
- [N/A] **V. Human-in-the-Loop**: enrichment executes no consequential action and raises no approval
      interrupt; its `ESCALATE` is abstention to a human, not an `awaiting_approval` park. The approval
      interrupt is owned by response (#10).
- [x] **VI. Temporal Memory & Graceful Degradation**: enrichment is the **primary reader** of temporal
      memory — it uses `query_fact(as_of=…)` for the **time-valid** state (benign-as-of-seed vs.
      malicious-as-of-update) and `search_similar` for priors, realizing the Graphiti differentiator at the
      reasoning layer. Graceful degradation in full — memory unavailable (`NullMemory` returns empty), corpus
      empty, intel disabled/`unknown` → proceed on partial context, never block (FR-008). The pgvector
      fallback is transparent behind the `MemoryStore` Protocol. The spine never moves.
- [x] **VII. Production Engineering Standards**: async throughout; **`asyncio.gather`** for the retrieval
      fan-out; DI via a **handler-factory closure** (`make_enrichment_handler(llm, corpus, memory, intel,
      cfg)`) that injects the read-only retrievers + typed settings while preserving the frozen `StageHandler`
      signature — which is exactly what enforces Principle III and mocks every dependency in tests; Pydantic at
      the boundary (`EnrichmentReport`, `EnrichmentSettings`); structured logging with trace id; observability
      off the synchronous path; typed `pydantic-settings` (`extra="forbid"`); `uv` for deps.
- [x] **Scope & Tiers**: within v1 (T1) — no live/streaming feeds (roadmap §v2/v3, marked in the spec), no ML
      detector, no multi-tenancy, no 4th agent, no LLM supervisor, no action tools. The §v2c feedback loop
      write-back is #6/#7-#10 scope, not this retrieval-only stage. Respects the layering contract (#9 depends
      on #5/#6/#3 — all done).

**Result: PASS.** No entries in Complexity Tracking.

## Project Structure

### Documentation (this feature)

```text
specs/009-enrichment-agent/
├── plan.md              # This file
├── research.md          # Phase 0 — design decisions (ED1…ED7)
├── data-model.md        # Phase 1 — EnrichmentReport / EnrichmentAssessment / EnrichmentSettings /
│                        #   evidence_patch shape / reused retrieval-input contracts
├── quickstart.md        # Phase 1 — how to run & verify enrichment (full-depth incident, degradation, gate)
├── contracts/           # Phase 1
│   ├── enrichment-handler-contract.md   # make_enrichment_handler + fan-out + decide_outcome + error mapping
│   ├── enrichment-report-schema.md      # the structured-output schema + EnrichmentReport validation
│   └── enrichment-eval.md               # the retrieval-gate extension (enrichment fixture set)
├── checklists/          # (pre-existing) requirements.md
└── tasks.md             # Phase 2 — created by /speckit-tasks (NOT here)
```

### Source Code (repository root)

```text
backend/
├── domain/
│   └── enrichment.py          # NEW — pure types: EnrichmentAssessment, EnrichmentReport
│                              #   (importable by #10/#12/eval; no outward imports)
├── agents/
│   └── enrichment.py          # REPLACE stub — make_enrichment_handler(llm, corpus, memory, intel, cfg):
│                              #   build_reference_query + extract_entities (deterministic, over redacted
│                              #   evidence), asyncio.gather fan-out (each guarded), one generate,
│                              #   validate → EnrichmentReport, decide_outcome, LlmError → ToolError mapping
├── infra/
│   └── config.py              # EXTEND — EnrichmentSettings section (+ register "enrichment", + Settings field)
├── supervisor_provider.py     # EXTEND — wire real enrichment handler from container.{llm,corpus,intel,memory}
│                              #   + settings.enrichment; fall back to the ADVANCE stub when no LLM
└── worker.py                  # EXTEND (wiring) — register CorpusProvider + IntelProvider; order
                               #   MemoryProvider/CorpusProvider/IntelProvider BEFORE SupervisorProvider

config/
└── eval_thresholds.yaml       # EXTEND — add an enrichment fixture set to the existing `retrieval` gate

tests/
├── unit/                      # test_enrichment_report / _decide / _builders / _degrade / _errors / _no_state
│                              #   (retrievers + LLM mocked)
├── integration/              # test_enrichment_provider — handler against real corpus+memory+LlmClient (both providers)
├── e2e/                      # extend the spine e2e: full-depth incident → triage → enrichment → responding/resolved/escalated
├── eval/                     # extend test_retrieval_gate with the enrichment fixture set
└── fixtures/                 # enrichment retrieval labels (incident → expected prior + expected corpus mapping)
```

**Structure Decision**: Modular monolith `backend/` (backend-only; no frontend here). Pure types live in
`domain/enrichment.py` (isolated, importable by response #10, the dashboard #12, and the eval without pulling
`infra`), mirroring `domain/triage.py`. All reasoning + the deterministic query/entity builders + the pure
`decide_outcome` live in `agents/enrichment.py`, which imports only `domain.*` (layering-clean: agents never
import `services`/`infra`; the retrievers arrive by closure injection). `supervisor_provider.py` gains the
real handler wiring (same eager pattern as triage), and `worker.py` gains the DI registration the API already
has for corpus. **`services/supervisor.py`, `repositories/incidents.py`, and the DB schema are untouched** —
#7 already wired the `ENRICHING` transitions and `evidence_patch` merge, so enrichment is a pure drop-in.

## Complexity Tracking

> No Constitution Check violations — this table is intentionally empty. Enrichment adds no new service,
> dependency, migration, or write/action capability; it reuses the #3 adapter, the #5 corpus/intel
> retrievers, the #6 memory read methods, and the #7 handler seam + transition table. The only structural
> change is DI wiring in `worker.py`/`supervisor_provider.py` (registering the worker-absent corpus/intel
> providers and ordering them before the supervisor), which is wiring, not new complexity.
