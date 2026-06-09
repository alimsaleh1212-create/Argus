# Implementation Plan: Knowledge Corpus (Reference + On-Demand Intel)

**Branch**: `008-knowledge-corpus` | **Date**: 2026-06-09 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/008-knowledge-corpus/spec.md`

## Summary

Give the now-empty memory substrate (#6) **knowledge to reason over** — the component that satisfies
Constitution **Principle VI**'s "seeded reference corpus MUST make the agent competent on the very first
incident." Two deliberately small deliverables:

1. **Seeded reference corpus (P1, the MVP)** — a curated, bundled static snapshot of stable security
   knowledge (MITRE ATT&CK technique→mitigation mappings + a handful of runbooks) loaded at init, plus a
   small seed set of IOC reputations. Retrievable on the very first incident, closing cold-start.
2. **Optional on-demand intel (P2)** — a config-gated single-source threat-intel lookup for one indicator,
   Redis-cached, whose verdict is **also written into the temporal memory store (#6) as a time-stamped
   fact**, so the indicator's history is there next time. Disabled by config → the pipeline runs on the
   seeded corpus alone.

**Keep-it-simple posture (the user's standing steer).** Each knowledge kind goes to the store already
running that is *right* for it — no new service, no embeddings, no LLM in the retrieval path:

- **Static reference docs** (technique→mitigation, runbooks) → a new Postgres table `reference_corpus`
  (Postgres + pgvector are already in the image). v1 retrieval is **deterministic keyed/lexical lookup**
  (technique id + tags/keywords); a pgvector column is reserved but **not** populated in v1.
- **Temporal reputation** (seed IOC reputations + on-demand intel verdicts) → `TemporalFact`s in the
  existing memory store (#6), so invalidate-not-delete and `as_of` time-scoping are **reused, not
  re-implemented**. This needs one small, anticipated addition to the `MemoryStore` Protocol —
  `write_fact()` — read back through the existing `query_fact()`.

This component is a **writer of knowledge and a provider of retrieval**; it adds **no agent reasoning**
(that is enrichment, #9, the primary reader) and **no new persistence service**. On-demand intel is
**optional, fail-closed, and off the disposition path**: a missing credential disables it, an outage
returns "unknown," and neither ever blocks an incident. Externally-sourced (feed/intel) text is
**untrusted** — redacted before any write and routed through the reserved guardrail seam (#11), which
no-ops gracefully until #11 lands so #5 is not blocked on it.

Two internal milestones (commit at each): **(a)** seed → retrieve green (corpus competent on a cold
store) → **(b)** on-demand intel → temporal fact green (lookup cached + written, supersedes the seed).

## Technical Context

**Language/Version**: Python 3.12 (pinned, repo-wide `uv` project)

**Primary Dependencies**: **NEW** — `httpx` (already a dep) for the single async intel call; no new
third-party package required. **Existing reused** — the `MemoryStore` (#6: `query_fact`, + new
`write_fact`), the `Redactor` (#2, `Boundary.MEMORY_WRITE`), `CacheProvider` (#1/#4 Redis), async
SQLAlchemy + Alembic (#1), `pydantic-settings` (#1), the reserved `Guardrail` seam (#11). No Graphiti
LLM call is added by this component.

**Storage**: **NEW table — `reference_corpus`** in the existing Postgres (`pgvector/pgvector:pg16`) via
migration `0006_reference_corpus` (columns: `kind`, `key`, `title`, `content`, `tags`, reserved
`embedding vector` left null in v1). **Reused — the memory store (#6, Neo4j/Graphiti)** holds reputation
`TemporalFact`s. **No new service.** Bundled corpus data ships as repo files under `backend/data/corpus/`.

**Testing**: `pytest` — **unit** (corpus seed idempotency + keyed/lexical retrieval ranking; intel client
cache hit/miss + timeout→unknown + redaction-before-write; the `write_fact`/supersession mapping; store &
http mocked), **integration** (`CorpusRepository` against real Postgres via the existing harness: seed →
re-seed idempotent → retrieve; `write_fact`→`query_fact(as_of)` supersession against real Neo4j via the
existing `testcontainers[neo4j]`), **e2e** (seed a fresh stack → retrieve reference knowledge for an
incident's technique/indicators returns non-empty; an intel lookup writes a fact that supersedes a seed
reputation), **eval** (extend the existing provider-independent **retrieval** gate with a corpus fixture
set — cold-start improvement, hit@k/MRR).

**Target Platform**: Linux containers (same backend image). A new one-shot **`seed-corpus`**
(`python -m backend.seed_corpus`) mirrors `migrate`; the `worker`/`api` gain a read-only `CorpusRetriever`
+ optional `ThreatIntelClient` via DI. Enrichment (#9) is the consumer; #5 does not wire the dashboard/api.

**Performance Goals**: corpus retrieval is an indexed keyed/lexical query (sub-budget, no embeddings, no
LLM); intel lookups are bounded by a timeout and Redis-cached (negative caching) so a repeat within TTL
costs nothing; all knowledge work stays off the synchronous disposition path (FR-008).

**Constraints**: redaction before any write (FR-007, Constitution III); intel/feed text is untrusted →
guardrail seam (#11); on-demand intel optional & fail-closed — missing creds disable it, outage →
"unknown", never blocks disposition (FR-004/FR-008); seeding idempotent (FR-002); reputation supersession
via the #6 temporal model, invalidate-not-delete (FR-006); no new persistence **service** (FR-009, refined
— see Complexity Tracking).

**Scale/Scope**: a small curated snapshot (tens of techniques, a few runbooks, a small IOC seed set),
single intel source, replayed demo alerts; a labeled corpus-retrieval fixture set for the eval.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design — still passing; one refinement
(FR-009 "no second substrate" → "no new service") and one minor anticipated contract addition
(`write_fact`) are recorded in Complexity Tracking + `DECISIONS.md`.*

- [x] **I. Spec-Driven Delivery**: `spec.md` precedes code; "done" = unit + integration + e2e green and
      pushed. Small spec, but committed at two internal milestones **(a) seed→retrieve → (b)
      intel→temporal-fact** so work never goes dark; PRs ≤ ~400 lines.
- [x] **II. Test-First, Three-Tier, Eval-Gated (NON-NEGOTIABLE)**: unit/integration/e2e planned, ≥80% on
      new code. The corpus contributes a **fixture set to the existing `retrieval` gate** (cold-start
      improvement) rather than inventing a new gate — provider-independent (keyed/lexical retrieval is
      deterministic store logic, no chat-LLM judgment), consistent with the `smoke`/`supervisor_routing`
      precedent and #6's `retrieval`/`temporal_memory` gates.
- [x] **III. Security Boundaries Are Structural**: **redaction runs before every write** (reference and
      intel) via `Boundary.MEMORY_WRITE`; **feed/intel text is untrusted** and routed through the reserved
      `Guardrail` seam (#11) — Constitution VI's "all feed- and knowledge-sourced text passes the same
      guardrails as alert text." The seam no-ops gracefully until #11 lands (dependency ordering), so the
      structural boundary is honored without blocking #5. This component adds **no action tools** and **no
      write capability to any agent**.
- [x] **IV. Determinism First**: corpus retrieval is a **deterministic keyed/lexical lookup — no LLM**; the
      intel lookup is a deterministic external API call (not agent reasoning). No agentic step is added.
      This is the "determinism where it suffices" discipline the brief insists on.
- [N/A] **V. Human-in-the-Loop**: knowledge seeding/lookup executes no consequential action and raises no
      approval interrupt.
- [x] **VI. Temporal Memory & Graceful Degradation** *(this spec realizes the "seeded corpus" clause)*:
      the corpus **makes the agent competent on the first incident** (cold-start closed, US1); reputation
      uses the #6 temporal model so a contradicting intel verdict **invalidates, not deletes** the seed
      (US2/SC-004); graceful degradation throughout — intel disabled/unavailable → corpus-only, corpus miss
      → empty, never an error; the spine never moves.
- [x] **VII. Production Engineering Standards**: async (`httpx` async intel call, async SQLAlchemy corpus
      reads, async memory writes); DI via a `CorpusRetriever` provider + optional `ThreatIntelClient`
      provider (lifespan singletons, `Depends()`); Pydantic boundaries (`ReferenceHit`, `IntelVerdict`,
      `TemporalFact`, `CorpusSettings`, `IntelSettings`); typed `pydantic-settings` (`extra="forbid"`;
      intel API key from Vault, **optional** — absent → disabled, not fail-boot); structured logging with
      trace id; all work off the synchronous path; `uv` for deps. **No new external LLM path** (unlike #6).
- [x] **Scope & Tiers**: within v1 (T1) — no live/streaming feeds (roadmap §v2/v3, marked in the spec), a
      **single** intel source, **no embeddings** in v1, no ML, no 4th agent. Enrichment's cross-correlation
      reasoning (#9) and the memory substrate (#6) are out of scope. Respects the layering contract (#5 is
      on the dependency path between #6 and #9).

**Result: PASS** with one refinement + one minor anticipated addition (below).

## Project Structure

### Documentation (this feature)

```text
specs/008-knowledge-corpus/
├── plan.md              # This file
├── research.md          # Phase 0 — decisions CD1…CDn (store split, write_fact, intel client, seeding, guardrail ordering)
├── data-model.md        # Phase 1 — ReferenceCorpusEntry / ReferenceHit / IntelVerdict / TemporalFact (reused) + CorpusSettings/IntelSettings + reference_corpus schema
├── quickstart.md        # Phase 1 — seed the corpus, retrieve for an incident, run an intel lookup, verify supersession, run the gate
├── contracts/           # Phase 1
│   ├── corpus-retrieval-contract.md   # CorpusRetriever read API (search_reference) + ranking rules
│   ├── intel-lookup-contract.md       # ThreatIntelClient (lookup → IntelVerdict) + cache + fail-closed + write_fact
│   └── corpus-data-schema.md          # bundled corpus file shapes (techniques/runbooks/ioc_reputation) + the MemoryStore.write_fact addition
├── checklists/          # (pre-existing) requirements.md
└── tasks.md             # Phase 2 — created by /speckit-tasks (NOT here)
```

### Source Code (repository root)

```text
backend/
├── domain/
│   ├── corpus.py             # NEW — pure types: ReferenceKind, ReferenceCorpusEntry, ReferenceHit,
│   │                         #   ReferenceQuery, IntelVerdict + the CorpusRetriever Protocol (no outward imports)
│   └── memory.py             # EXTEND — add `write_fact(fact: TemporalFact) -> None` to the MemoryStore Protocol
├── infra/
│   ├── memory.py             # EXTEND — NullMemory.write_fact (no-op); GraphitiMemory.write_fact
│   │                         #   (write edge with valid_from; invalidate prior fact of same (entity, fact_type))
│   ├── intel.py              # NEW — ThreatIntelClient (httpx single source; Redis cache via CacheProvider;
│   │                         #   timeout→unknown; redact+guardrail before write_fact); IntelProvider (optional, DI)
│   └── config.py             # EXTEND — CorpusSettings + IntelSettings; register "corpus"/"intel";
│                             #   intel api key Vault path OPTIONAL (absent → disabled, not fail-boot)
├── repositories/
│   └── corpus.py             # NEW — CorpusRepository(CorpusRetriever): upsert_entries (idempotent seed),
│                             #   search_reference (keyed-by-technique + tag/lexical match, ranked)
├── services/
│   └── corpus.py             # NEW — seed_corpus(load bundled files → redact → upsert reference rows +
│                             #   write seed IOC reputation facts via store.write_fact); thin, unit-testable
├── data/
│   └── corpus/               # NEW — bundled curated snapshot (committed):
│       ├── techniques.json   #   MITRE technique→mitigation mappings
│       ├── runbooks.json     #   a handful of runbooks (tagged by technique)
│       └── ioc_reputation.json  # small seed IOC reputation set
└── seed_corpus.py            # NEW — `python -m backend.seed_corpus` one-shot (mirrors migrate): idempotent

backend/db/migrations/versions/
└── 0006_reference_corpus.py  # NEW — reference_corpus table (kind,key,title,content,tags, reserved embedding)

config/
├── eval_thresholds.yaml      # EXTEND — add a corpus fixture set to the existing `retrieval` gate (cold-start)
└── (compose.yaml at root)    # EXTEND — add the seed-corpus one-shot (depends_on migrate+neo4j healthy);
                              #   optional intel key seeded by vault-seed (secret/intel) when present in .env

tests/
├── unit/                     # test_corpus_retrieval (ranking), test_corpus_seed_idempotent,
│                             #   test_intel_client (cache/timeout/unknown/redact), test_write_fact_supersede
├── integration/             # test_corpus_repo (real Postgres: seed→re-seed→retrieve),
│                             #   test_write_fact (real Neo4j: write_fact→query_fact as_of supersession)
├── e2e/                     # test_corpus_e2e: fresh seed → retrieve non-empty; intel lookup → fact supersedes seed
├── eval/                    # extend test_retrieval_gate with the corpus fixture set
└── fixtures/                # corpus retrieval labels (technique/indicator → expected reference entries)
```

**Structure Decision**: Modular monolith `backend/` (backend-only; no frontend here). Pure types + the
`CorpusRetriever` Protocol live in `domain/corpus.py` (importable by #9 enrichment and the eval without
pulling `infra`), mirroring `domain/memory.py`/`domain/llm.py`. Static reference docs get a thin
`repositories/corpus.py` over Postgres (the layer the project already uses for `incidents`); the temporal
reputation path reuses `infra/memory.py` via the small `write_fact` addition. Seeding is a one-shot module
(one image, many containers — like `migrate`), and the optional intel client is its own `infra/intel.py`
behind a provider so it can be disabled by config. The supervisor, worker, and `incidents` repo are
**untouched**; enrichment (#9) consumes the new `CorpusRetriever`/intel DI later.

## Complexity Tracking

> One spec-refinement and one minor anticipated contract addition. Both recorded here and in `DECISIONS.md`.

| Item | Why Needed | Simpler Alternative Rejected Because |
|------|------------|-------------------------------------|
| **Two stores: a new Postgres `reference_corpus` table alongside the #6 memory store** (refines spec FR-009 "MUST NOT introduce a second persistence substrate" → "MUST NOT introduce a second persistence *service*") | Static reference docs (MITRE→mitigation, runbooks) are **not** temporal facts and **not** incident episodes; forcing them through Graphiti would run its **LLM extraction over reference documents** — added cost, latency, and nondeterminism for a deterministic keyed lookup. Reputation **is** temporal and belongs in #6. Each knowledge kind goes to the store that fits it; **both stores already run** (Postgres + Neo4j) — no new service, honoring FR-009's intent and "don't overengineer." | **Everything in Graphiti** — rejected: LLM extraction over static docs is overengineering and nondeterministic, contradicting Principle IV and the user's keep-it-simple steer. **A new dedicated corpus service** — rejected: a new service is exactly what FR-009 forbids. |
| **Add `write_fact(TemporalFact)` to the `MemoryStore` Protocol** (a touch on the #6 seam) | #6's FR-010 explicitly names the **knowledge corpus (#5) as a writer** of intel knowledge, but #6 shipped only `write_episode` (incident-shaped). Writing intel as an `IncidentEpisode` would **pollute `search_similar`** (intel data surfacing as fake prior incidents). A minimal `write_fact` keeps incident similarity clean and reuses the existing `query_fact(as_of)`/invalidate-not-delete read path. NullMemory no-ops it (degradation preserved). | **Synthesize an `IncidentEpisode` per intel datum** — rejected: corrupts incident retrieval and shoehorns intel into incident-required fields (verdict/severity/disposition). **Re-implement a parallel temporal table in #5** — rejected: duplicates #6's temporal model (DRY/Principle VI). |

> Neo4j, pgvector, and `httpx` are **not** logged as violations — all are pre-existing stack elements;
> this component adds **no new service and no new external LLM path**.
