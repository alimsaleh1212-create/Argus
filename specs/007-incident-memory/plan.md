# Implementation Plan: Incident Memory (Temporal)

**Branch**: `007-incident-memory` | **Date**: 2026-06-09 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/007-incident-memory/spec.md`

## Summary

Fill the reserved `infra/memory.py` seam with a real **temporal incident-memory layer** — the capstone's
"gets smarter over time" core and the component that satisfies Constitution **Principle VI** head-on. As each
incident reaches a terminal disposition, the worker writes a **redacted, time-stamped episode** into memory;
a new incident can then **retrieve the closest prior incidents and their dispositions**, and a **time-scoped
query** returns the correct *time-valid* state of a fact (current vs. superseded) — conflicts **invalidate,
never delete**.

The store is realized as **Graphiti on Neo4j 5.26** (`graphiti-core[google-genai]`), the headline
architecture the user confirmed, behind a small **`MemoryStore` Protocol** so the **pgvector + relational
fallback** (Constitution VI, decided at the day-1 spike) is a drop-in substitution. Graphiti's native
**Gemini** LLM + embedder do graph construction and similarity (reusing the Vault-resolved Gemini key already
in CI); this is the one place LLM calls do **not** route through the #3 `LlmClient` adapter — a justified,
documented deviation (see Complexity Tracking).

**Keep-it-simple posture (the user's standing steer):** one episode write per incident (idempotent), one
similarity read, one time-scoped fact query — no enrichment reasoning (that's #9), no corpus content (#5), no
live feeds (roadmap), no §v2c feedback loop (Tier 2, marked). Memory is wired into the **worker only** and
runs **off the synchronous disposition path**: a memory outage never blocks a disposition or crashes the
worker. The supervisor stays a pure deterministic state machine — **no memory dependency is added to it**.

This is a **"big" spec** (Constitution I): it commits at each internal milestone — **(0)** day-1 Graphiti
spike go/no-go → **(a)** write/ingest path green → **(b)** retrieve path green → **(c)** temporal-validity
green — so work never goes dark.

## Technical Context

**Language/Version**: Python 3.12 (pinned, repo-wide `uv` project)

**Primary Dependencies**: **NEW** — `graphiti-core[google-genai]` (Apache-2.0; bundles the `neo4j` async
driver, `GeminiClient`, `GeminiEmbedder`). **Existing reused** — `google-genai>=2.8.0` (already a dep), the
#2 `Redactor` (redaction-before-write), `structlog`/OpenTelemetry (#2), `pydantic-settings` (#1), the worker
(#4). Dev: `testcontainers[neo4j]` for the integration tier.

**Storage**: **NEW service — Neo4j 5.26 Community** (Graphiti backend; the reserved compose block, pinned to
the Graphiti-required 5.26+). Postgres `pgvector` (already in the image) backs the **decided fallback**
only. No change to the `incidents` table for the Graphiti path; the fallback adds a drafted `0005` migration
(`incident_episodes` + `entity_facts`), applied only if the spike selects pgvector.

**Testing**: `pytest` — **unit** (episode build + redaction-before-write, the time-scoping/`FactState` logic,
degrade-to-`NullMemory`, idempotency key; store mocked), **integration** (`GraphitiMemory` against a **real
Neo4j** via `testcontainers[neo4j]`: write → retrieve-similar → conflicting-fact invalidation → time-scoped
read), **e2e** (extend the spine: an incident reaching disposition writes a retrievable episode), **eval**
(the committed **retrieval** hit@k/MRR gate + **temporal-validity** gate).

**Target Platform**: Linux `worker` container (same image as `api`). The `api` is **not** made to depend on
Neo4j by this spec (dashboard/enrichment wiring is #12/#9).

**Performance Goals**: similarity retrieval within a configured latency budget (SC-006); episode writes and
their graph-construction LLM calls run **off the synchronous disposition path** (FR-006) — disposition is
persisted by the supervisor *before* the memory write is attempted.

**Constraints**: redaction before any memory write (FR-005, Constitution III); memory failure never blocks a
disposition or crashes the worker (FR-006); writes idempotent per incident (FR-007); time-validity preserved
by invalidation, not deletion (FR-003, Constitution VI); a decided, substitutable pgvector fallback
(FR-011). Graph-construction LLM/embedding cost is bounded and logged.

**Scale/Scope**: single-worker, replayed sample alerts; a committed labeled retrieval set + a changed-fact
temporal scenario for the evals.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design — still passing; the one deviation
(Graphiti's native LLM/embeddings vs. the #3 adapter) is justified and recorded below + in `DECISIONS.md`.*

- [x] **I. Spec-Driven Delivery**: `spec.md` precedes code; "done" = unit + integration + e2e green and
      pushed. This is a **big spec** → commits at the declared milestones **(0) spike → (a) write → (b)
      retrieve → (c) temporal** (Summary; mirrors the SOAR_Plan milestones for `SPEC-memory`). PRs kept
      focused (the spike + each milestone is its own ≤~400-line slice).
- [x] **II. Test-First, Three-Tier, Eval-Gated (NON-NEGOTIABLE)**: unit/integration/e2e planned; ≥80% on new
      code. Two gates land in `eval_thresholds.yaml` — **retrieval** (hit@k/MRR) and **temporal_memory**
      (correct current-vs-superseded state). These are **deterministic store-logic gates**, so — like the
      existing `smoke` and `supervisor_routing` gates — they are **provider-independent** (no chat-LLM
      judgment dimension). *Justification for no `check_per_provider`:* retrieval ranking is an embedding-
      similarity property and temporal validity is store logic; neither is an LLM-quality judgment, and the
      tiny Ollama fallback model cannot fairly perform graph extraction. Graphiti extraction quality is
      validated at the **spike**, not gated per-provider. Recorded in `DECISIONS.md`.
- [x] **III. Security Boundaries Are Structural**: **redaction runs before every memory write** — the
      `Redactor` (#2) is applied when the episode is assembled, so Graphiti's LLM/embedder only ever see
      redacted text. The `redaction` eval gate already reserves a **`memory_write`** boundary and asserts
      zero credential/PII leaks into the memory store (FR-006a) — this spec makes that boundary live. Memory
      holds **no action tools** and adds **no write capability** to triage/enrichment. Feed/knowledge-text
      guardrails apply to #5/#11 inputs, not to this substrate.
- [x] **IV. Determinism First**: the supervisor remains a **pure deterministic state machine with no LLM and
      no memory dependency** — the memory write is performed by the **worker after** `run_incident` returns,
      off the critical path. No agent reasoning is added here; Graphiti's construction LLM is *infrastructure*
      (entity/edge extraction), not a pipeline reasoning step, and is bounded + logged.
- [N/A] **V. Human-in-the-Loop**: memory executes no consequential action and raises no approval interrupt.
      (Episodes for `awaiting_approval` incidents are recorded when they terminalize via #10's resume path;
      out of scope here.)
- [x] **VI. Temporal Memory & Graceful Degradation** *(this spec IS Principle VI)*: institutional memory is
      **queryable, not retrained**; **time-validity preserved by invalidation, not deletion** (FR-003, US2);
      a **decided pgvector + `valid_from`/`valid_to` fallback** behind the `MemoryStore` Protocol, chosen at
      the **day-1 spike** (Milestone 0); the triage→enrichment→response spine and approval interrupt **do not
      move** when the slice shrinks (memory is additive, worker-only, off-path). Cold-start (empty memory)
      returns empty, not error; seeding the corpus to soften cold-start is #5's job, not this one.
- [x] **VII. Production Engineering Standards**: async throughout (async `neo4j` driver, async Graphiti API);
      DI via a **`MemoryProvider`** lifespan singleton (built once, disposed on shutdown) that **degrades to
      `NullMemory`** if Neo4j is unreachable rather than crashing the worker; Pydantic types at the boundary
      (`IncidentEpisode`, `MemoryHit`, `FactState`, `MemorySettings`); structured logging with trace id;
      memory work off the synchronous path; typed `pydantic-settings` (`extra="forbid"`, Neo4j creds from
      Vault, fail-boot if the required path is absent); `uv` for deps. **Deviation:** Graphiti's native
      Gemini LLM/embedder are *not* routed through the #3 `LlmClient` (which is `generate()`-only, no
      embeddings) — see Complexity Tracking.
- [x] **Scope & Tiers**: within v1 (T1) — no ML detector, no multi-tenancy, no 4th agent, no LLM supervisor.
      Enrichment reasoning (#9), corpus content (#5), live feeds (roadmap), and the §v2c feedback loop (T2)
      are explicitly out of scope; the loop is carried as a marked roadmap section in the spec. Respects the
      layering contract (memory is a T1 component on the dependency path to enrichment).

**Result: PASS** with one justified deviation (below).

## Project Structure

### Documentation (this feature)

```text
specs/007-incident-memory/
├── plan.md              # This file
├── research.md          # Phase 0 — design decisions (MD1…MDn) + the day-1 spike plan
├── data-model.md        # Phase 1 — IncidentEpisode / TemporalFact / FactState / MemoryHit / MemorySettings + fallback schema
├── quickstart.md        # Phase 1 — bring up Neo4j, run the spike, write/retrieve/verify, run the gates
├── contracts/           # Phase 1
│   ├── memory-store-contract.md   # the MemoryStore Protocol (write_episode / search_similar / query_fact)
│   ├── memory-episode-schema.md   # episode/entity/fact shapes + the redaction-before-write contract
│   └── memory-eval.md             # retrieval (hit@k/MRR) + temporal-validity gates
├── checklists/          # (pre-existing) requirements.md
└── tasks.md             # Phase 2 — created by /speckit-tasks (NOT here)
```

### Source Code (repository root)

```text
backend/
├── domain/
│   └── memory.py             # NEW — pure types: IncidentEpisode, EntityRef, TemporalFact, FactState,
│                             #   MemoryHit, EpisodeQuery + the MemoryStore Protocol (no outward imports)
├── infra/
│   ├── memory.py             # REPLACE stub — MemoryProvider (Graphiti+Neo4j singleton; degrade→NullMemory),
│   │                         #   GraphitiMemory(MemoryStore), NullMemory(MemoryStore)
│   └── config.py             # EXTEND — MemorySettings; register "memory"; Neo4j vault-path required validator
├── services/
│   └── memory.py             # NEW — record_episode(incident, store, redactor): redact → build IncidentEpisode
│                             #   → store.write_episode (best-effort orchestration; pure-ish, unit-testable)
└── worker.py                 # EXTEND — register MemoryProvider; after run_incident reaches terminal,
                              #   best-effort record_episode(...) wrapped so failure never blocks/raises

config/
├── eval_thresholds.yaml      # EXTEND — activate `retrieval` (hit@k/MRR) + `temporal_memory` gates
└── (compose.yaml at root)    # EXTEND — uncomment+configure neo4j:5.26 (auth, healthcheck, volume);
                              #   vault-seed writes secret/memory; worker depends_on neo4j + REQUIRED_PATHS

# Decided fallback — built ONLY if the Milestone-0 spike rejects Graphiti:
backend/repositories/memory.py # (fallback) PgVectorMemory(MemoryStore) over Postgres
backend/db/migrations/versions/0005_memory_fallback.py  # (fallback) incident_episodes + entity_facts (+vector)

tests/
├── unit/                     # test_memory_episode (build+redaction), test_memory_factstate (time-scoping),
│                             #   test_memory_degrade (NullMemory), test_memory_idempotent
├── integration/             # test_graphiti_memory — real Neo4j (testcontainers): write→retrieve→invalidate→as_of
├── e2e/                     # extend spine e2e: disposition → episode written → search_similar finds it
├── eval/                    # test_retrieval_gate (hit@k/MRR), test_temporal_gate (current vs superseded)
└── fixtures/                # labeled prior-incident retrieval set + a changed-fact temporal scenario
```

**Structure Decision**: Modular monolith `backend/` (backend-only; no frontend here). Pure types + the
`MemoryStore` Protocol live in `domain/memory.py` (importable by #9 enrichment, #12 dashboard, and the eval
without pulling `infra`). The Graphiti/Neo4j client and its `MemoryStore` implementation live in
`infra/memory.py` (mirroring how `infra/llm.py` wraps an external service with pure types in
`domain/llm.py`). Episode assembly is a thin `services/memory.py` so redaction + shaping are unit-testable
with the store mocked. The worker gains the **one** wiring change real memory needs: register the provider
and best-effort-record after disposition. The supervisor and `incidents` repo are **untouched** by the
Graphiti path (no new write capability, no migration).

## Complexity Tracking

> One justified deviation from Constitution VII. Recorded here and in `DECISIONS.md` as a time-bound,
> spike-validated decision.

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| **Graphiti's native Gemini LLM + embedder are used directly, not via the #3 `LlmClient` adapter** (Principle VII "LLM only through the adapter") | Graphiti drives entity/edge extraction through its own `LLMClient`/`Embedder` classes with internal prompt libraries and pydantic response models; and the #3 adapter is **`generate()`-only with no embeddings** and a dict-schema contract incompatible with Graphiti's extraction. Using `graphiti-core[google-genai]` with the Vault-resolved Gemini key is the smallest thing that works. **Mitigations:** redaction runs *before* Graphiti sees any text (III preserved); construction is off the synchronous path and bounded; tokens logged on a span; it reuses the same Gemini provider/key already in CI. Validated at the day-1 spike. | **Wrapping our adapter as a Graphiti `LLMClient`+`Embedder` shim** — rejected as disproportionate ("don't overengineer"): it requires adding an embeddings method to the #3 seam (scope creep into a frozen, shipped component) and a fragile re-implementation of Graphiti's structured-extraction contract that breaks across Graphiti versions, for no v1 capability gain. The deviation is confined to *infrastructure* graph-construction, never to an agent's reasoning call. |

> Neo4j (new service) and `graphiti-core` (new dependency) are **not** logged as violations: Constitution VI
> explicitly names the Graphiti/Neo4j memory layer (with the pgvector fallback) as the sanctioned design for
> this component.
