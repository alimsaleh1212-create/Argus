# Phase 0 — Research & Decisions: Knowledge Corpus (#5)

All Technical-Context unknowns are resolved here. Decisions are numbered `CD<n>` (Corpus Decision) and
carry forward into `DECISIONS.md`. The throughline is the user's standing steer: **make it simple, don't
overengineer** — reuse what runs, add no new service, keep no LLM in the retrieval path.

---

## CD1 — Split knowledge by access pattern: static reference docs in Postgres, temporal reputation in #6

**Decision**: Two kinds of knowledge, each to the store already running that fits it.
- **Static reference docs** — MITRE ATT&CK technique→mitigation mappings and runbooks — live in a new
  Postgres table `reference_corpus`. Retrieval is **deterministic keyed/lexical lookup** (by technique id,
  then tags/keywords), ranked. **No embeddings in v1** (an `embedding vector` column is reserved, left
  null).
- **Temporal reputation** — the seed IOC reputation set and on-demand intel verdicts — lives in the
  **existing memory store (#6)** as `TemporalFact`s, so `valid_from`/`valid_until`, invalidate-not-delete,
  and `as_of` time-scoping are **reused, not re-implemented**.

**Rationale**: Reference docs are static and non-temporal; a keyed lookup is exact, fast, deterministic
(Principle IV), and needs no model. Reputation is inherently temporal and is exactly what #6 was built
for. Both Postgres and Neo4j already run — **no new service**. This refines spec **FR-009** from "no second
persistence *substrate*" to "no second persistence *service*"; the intent (don't stand up new
infrastructure for knowledge) is honored.

**Rejected**: Put *everything* in Graphiti — running Graphiti's LLM entity/edge extraction over static
MITRE/runbook documents adds cost, latency, and nondeterminism for what is a keyed lookup; overengineering
and a Principle IV regression. A new dedicated corpus microservice — the very thing FR-009 forbids.

**Why no embeddings in v1**: the seeded set is small and structurally keyed (technique ids, tags). Keyed +
lexical retrieval meets the cold-start goal and the `retrieval` hit@k/MRR gate without an embedding
pipeline for the corpus. The `embedding` column is reserved so a later component can add vector recall
without a migration.

---

## CD2 — Minimal `MemoryStore.write_fact(TemporalFact)` addition (read via the existing `query_fact`)

**Decision**: Add one method to the `MemoryStore` Protocol (`domain/memory.py`):
`async def write_fact(self, fact: TemporalFact) -> None`. `NullMemory` no-ops it; `GraphitiMemory` writes
the fact as a time-bounded edge and **invalidates the prior fact** of the same `(entity, fact_type)` by
ending its validity (never deletes). Reads stay on the existing `query_fact(entity, fact_type, as_of=…)`.

**Rationale**: #6's FR-010 explicitly names the **knowledge corpus (#5) as a writer** of intel knowledge —
so an addition for #5 is *anticipated*, not scope creep. #6 shipped only `write_episode`, which is
incident-shaped (`incident_id`, `verdict`, `severity`, `disposition`); writing intel through it would
**pollute `search_similar`**, surfacing intel data as fake "prior incidents" to enrichment. `write_fact`
keeps incident similarity clean and reuses #6's temporal read path verbatim. Because `NullMemory` no-ops
the write, the FR-008 degradation guarantee (intel never blocks/crashes) is preserved for free.

**Rejected**: Synthesize an `IncidentEpisode` per intel datum (corrupts incident retrieval; shoehorns
intel into required incident fields). A parallel temporal table inside #5 (duplicates #6's temporal model
— DRY and Principle VI violation).

**Boundary note**: this is the *only* change to the #6 seam; `write_episode`/`search_similar`/`query_fact`
are untouched, and the pgvector fallback (`PgVectorMemory`, MD9) gains the same one-method addition when/if
it is built.

---

## CD3 — On-demand intel: optional, single-source, Redis-cached, fail-closed

**Decision**: `infra/intel.py` exposes a `ThreatIntelClient` behind an `IntelProvider` (DI, lifespan).
- **Single source**, configured by `IntelSettings` (`enabled` flag, `base_url`, timeout, cache TTL; API
  key from an **optional** Vault path).
- **Disabled** when `enabled=false` *or* the API key is absent → `lookup()` returns an "unknown" verdict
  and makes no call. **Missing creds disable; they do not fail boot** (unlike substrate creds).
- One async `httpx` GET per indicator, bounded by `timeout`. Any error/timeout → **"unknown"** (fail-closed).
- **Redis-cached** via the existing `CacheProvider` (key `intel:<indicator>`), with **negative caching**
  (unknown/negative verdicts cached too) so a repeat lookup within TTL issues no second external call and
  does not hammer the source.

**Rationale**: realizes FR-004/FR-005/FR-008 and the brief's "v1, optional." Reuses Redis already in the
stack; a single source is sufficient for v1 (the brief scopes failover/fusion to roadmap). Fail-closed +
negative caching protects both the pipeline (never blocks) and the upstream source (rate friendliness).

**Rejected**: Multiple/federated sources with failover (roadmap §v2/v3, out of v1 scope). Fail-boot on
missing intel creds (would make an *optional* capability a hard dependency — wrong for an enhancement).

---

## CD4 — Seeding as an idempotent one-shot (`python -m backend.seed_corpus`), mirroring `migrate`

**Decision**: A new one-shot module `backend/seed_corpus.py` run as its own compose service `seed-corpus`
(same backend image, different command — the established "one image, many containers" pattern, like
`migrate`/`vault-seed`). It loads the bundled files under `backend/data/corpus/`, redacts text at the
write boundary, **upserts** reference rows (idempotent on `(kind, key)`), and **writes seed IOC reputation
facts** via `store.write_fact` (idempotent — a re-seed of an unchanged seed fact is a no-op). It
`depends_on` `migrate` completed + `neo4j` healthy.

**Rationale**: matches the turnkey compose promise (no manual seed step) and Constitution VI's "seeded at
init." Idempotency (FR-002) means restart/redeploy never duplicates. Keeping it a one-shot (not a lifespan
hook) keeps `api`/`worker` startup clean and the seed observable as its own step.

**Rejected**: Seed inside the `api`/`worker` lifespan (adds startup cost and races across replicas). Fold
into Alembic `migrate` (mixes data seeding into schema migration — wrong layer; and reputation facts go to
Neo4j, not Postgres).

---

## CD5 — Untrusted-input boundary, and the #11 dependency ordering

**Decision**: All externally-sourced text (on-demand intel responses; feed text in the roadmap) is treated
as **untrusted**: it is **redacted** (`Boundary.MEMORY_WRITE`) before any write, and routed through the
reserved `Guardrail` seam (`infra/guardrails.py`, #11) before it is written or handed to reasoning. Because
#11 ships after #5, the call site uses the seam defensively: **if the guardrail is not yet configured it
no-ops (logs a debug) and proceeds** — it never raises `NotImplementedError` into the intel path. When #11
lands, the same call site enforces real rails with no #5 change. The **curated reference corpus is trusted
content** but still passes the redaction write-boundary for uniformity.

**Rationale**: honors Constitution III + VI ("all feed- and knowledge-sourced text passes the same
guardrails as alert text") and the redaction eval's `memory_write` boundary, while respecting build order
(#5 at Day 2, #11 at Day 8) so #5 is not blocked. The structural boundary is wired now; the library is
filled later.

**Rejected**: Block #5 on #11 (breaks dependency order; #11 depends only on #1/#2). Skip the guardrail call
entirely (would leave the seam un-wired and require a later #5 edit; and silently drops the Principle VI
requirement).

---

## CD6 — `CorpusRetriever` is a read service consumed by enrichment (#9); #5 ships + tests it standalone

**Decision**: #5 delivers `CorpusRepository(CorpusRetriever)` and the optional `ThreatIntelClient` as
DI-available services, plus its own seed→retrieve and intel→fact e2e. It does **not** wire enrichment,
the supervisor, the worker disposition path, or the dashboard. Enrichment (#9) imports `domain/corpus.py`'s
`CorpusRetriever` Protocol and consumes both later.

**Rationale**: keeps the layering contract clean (knowledge provision vs. cross-correlation reasoning are
separate components) and keeps this PR small. The Protocol in `domain/` lets #9 depend on the contract, not
the Postgres implementation.

**Rejected**: Wire enrichment here (that is #9's scope; would bloat the PR and couple two components).

---

## CD7 — Eval: extend the existing `retrieval` gate with a corpus fixture set (no new gate)

**Decision**: Add a small **corpus fixture set** (technique/indicator → expected reference entries) under
`tests/fixtures/` and score it through the existing provider-independent **`retrieval`** gate
(hit@k/MRR), demonstrating cold-start competence (SC-001/SC-007). No new gate is invented (per the eval
file's standing note).

**Rationale**: corpus retrieval is deterministic store logic, like #6's `retrieval`/`temporal_memory`
gates — provider-independent. Reusing the existing gate keeps the eval surface coherent and honors "don't
invent new gates."

**Rejected**: A brand-new `corpus_retrieval` gate (redundant with `retrieval`; more CI surface for no new
signal).

---

## Resolved unknowns

| Unknown | Resolution |
|---------|-----------|
| Where do reference docs vs. reputation live? | Reference docs → Postgres `reference_corpus`; reputation → #6 memory store (CD1). |
| How does intel write to memory without polluting incident search? | New `write_fact(TemporalFact)`; intel is a fact, not an episode (CD2). |
| Embeddings for the corpus? | No — keyed/lexical retrieval in v1; `embedding` column reserved (CD1). |
| Intel source count / failover? | Single source; failover is roadmap (CD3). |
| Missing intel credentials? | Disable the lookup, do **not** fail boot (CD3). |
| When/where does seeding run? | Idempotent one-shot `seed-corpus`, after migrate + neo4j healthy (CD4). |
| Guardrails not built yet (#11)? | Seam wired now, no-ops gracefully until #11 lands (CD5). |
| Who reads the corpus? | Enrichment (#9), via the `CorpusRetriever` Protocol; #5 ships + tests standalone (CD6). |
| New eval gate? | No — extend the existing `retrieval` gate with corpus fixtures (CD7). |
