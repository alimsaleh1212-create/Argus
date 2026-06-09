# Phase 0 — Research & Decisions: Incident Memory (Temporal)

**Component**: #6 `SPEC-memory` (branch `007-incident-memory`) · **Date**: 2026-06-09

The spec had **no open `[NEEDS CLARIFICATION]`** markers (the one fork — Graphiti vs. pgvector — was resolved
with the user before the spec was written: **full Graphiti + Neo4j**). Phase 0 records the design decisions
that turn the spec into a buildable plan. Decisions are labeled `MD1…MD9`; every one is biased toward the
user's standing steer — *make it simple, don't overengineer* — and toward Constitution **Principle VI**
(temporal memory, time-validity preserved, decided fallback, never a single point of failure). The non-obvious
ones (MD2, MD9) are mirrored into `DECISIONS.md`.

---

## MD0 — Day-1 Graphiti spike is Milestone 0 (the go/no-go gate)

**Decision**: Before any production code, stand Neo4j 5.26 up in compose and run Graphiti's quickstart against
a handful of sample incidents: write a few episodes, retrieve a similar one, and force a fact conflict to see
native invalidation. **Measure** end-to-end write latency, retrieval latency, and **graph-construction token
cost per episode**. Record a **go/no-go vs. the pgvector fallback** in `DECISIONS.md`. Only on "go" do
milestones (a)/(b)/(c) build against Graphiti.

**Rationale**: Constitution VI and the SOAR plan both mandate this spike; Graphiti adds a new service +
framework + LLM calls on graph construction, and the honest way to de-risk that is to measure it once,
early, not discover it at milestone (c). The spike is cheap and the fallback (MD9) is ready.

**Alternatives considered**: *Commit to Graphiti without the spike* — rejected: violates the constitution's
explicit de-risk clause and risks discovering a latency/cost problem deep into the build.

---

## MD1 — Graphiti on Neo4j 5.26 behind a `MemoryStore` Protocol; pgvector fallback is a drop-in

**Decision**: Implement the memory layer as **Graphiti** (`graphiti-core[google-genai]`) over **Neo4j 5.26
Community** (Graphiti's required minimum; the compose block reserved `neo4j:5`, now pinned `5.26`). All
consumers (the worker writer, #9 enrichment later, the eval) talk only to a small **`MemoryStore` Protocol**
(`write_episode`, `search_similar`, `query_fact`) defined in `domain/memory.py`. `GraphitiMemory` is the v1
implementation; `PgVectorMemory` (MD9) satisfies the same Protocol.

**Rationale**: The Protocol is what makes Constitution VI's "decided fallback" real and *cheap* — swapping
backends is a config toggle, not a rewrite — and it keeps the headline Graphiti choice from leaking into
callers. Mirrors the proven `infra/llm.py` (external client) + `domain/llm.py` (pure types) split.

**Alternatives considered**: *Call Graphiti directly from the worker/enrichment* — rejected: hard-wires the
framework into callers and kills the fallback substitution. *Skip the Protocol, build only Graphiti* —
rejected: violates VI's "decided fallback" and makes the eval backend-specific.

---

## MD2 — Graphiti uses its **native Gemini** LLM + embedder; redaction runs before any write *(VII deviation)*

**Decision**: Configure Graphiti with `GeminiClient` + `GeminiEmbedder` using the **Vault-resolved Gemini
key** (the same `secret/llm` key the #3 adapter and the triage eval already use). Graphiti's construction
LLM/embedding calls therefore **do not** route through the #3 `LlmClient`. To hold Constitution III, the
episode text is **redacted by the #2 `Redactor` before** it is handed to `write_episode`, so Graphiti's
model only ever sees redacted content. Construction runs off the synchronous path; its tokens are recorded on
a span (best-effort) for visibility.

**Rationale**: The #3 adapter is **`generate()`-only with no embeddings**, and Graphiti drives extraction
through its own `LLMClient`/`Embedder` classes with internal pydantic response models. A faithful adapter
shim would mean adding embeddings to a frozen, shipped seam (#3) *and* re-implementing Graphiti's extraction
contract — disproportionate for v1 and fragile across Graphiti versions. Using `graphiti-core[google-genai]`
is the smallest thing that works, and it reuses Gemini-in-CI. This is a **justified, time-bound deviation**
recorded in `DECISIONS.md` and Complexity Tracking; it is confined to *infrastructure* graph-construction,
never an agent reasoning call.

**Alternatives considered**: *Wrap `LlmClient` as a Graphiti `LLMClient`+`Embedder`* — rejected as
overengineering (scope-creeps #3, fragile). *Use Ollama for extraction to honor "both providers"* — rejected:
the pinned tiny fallback model (`qwen2:0.5b`) cannot do faithful entity/edge extraction; forcing it would
make the eval dishonest (see MD7).

---

## MD3 — The **worker** writes one episode per incident, after disposition, off-path and best-effort

**Decision**: Episode writes happen in `worker._run`, **after** `dispatch_to_pipeline`/`run_incident`
returns and the supervisor has already persisted the terminal disposition. The worker reloads the incident,
calls `services.memory.record_episode(incident, store, redactor)`, and wraps it so **any failure is logged
and swallowed** — never re-raised into the disposition/ack flow. The write is **idempotent on
`incident_id`** (FR-007). The **supervisor is not touched** (no memory dependency, stays pure — Constitution
IV).

**Rationale**: This is the literal reading of FR-006 ("off the synchronous disposition-critical path") and
keeps the single-writer determinism intact — disposition is durable *before* memory is attempted, so a memory
outage degrades to a missing episode, not a lost disposition. Putting the write in the worker (not the
supervisor, not a new queue/consumer) is the simplest place that satisfies the contract without new
infrastructure. (`awaiting_approval` parked incidents are recorded when they terminalize via #10's resume
path — out of scope here.)

**Alternatives considered**: *Write inside the supervisor transition* — rejected: pollutes the pure state
machine with an external dep + LLM cost and risks coupling disposition durability to memory. *A dedicated
memory queue + consumer* — rejected: real async decoupling is nice but is more moving parts than v1 needs
("don't overengineer"); best-effort in-worker write already meets FR-006.

---

## MD4 — Episode/entity/fact model; rely on Graphiti's **native** temporal invalidation

**Decision**: An **`IncidentEpisode`** carries the redacted summary + selected normalized fields, the
verdict/severity, the final disposition, the extracted **entity refs** (source/destination address, host,
user, indicators — pulled from `normalized_event`), and `observed_at`. `write_episode` hands this to
Graphiti's `add_episode`; **Graphiti natively extracts entities/relationships and manages time-validity** —
on a contradicting episode it sets the prior edge's `invalid_at` and time-stamps the new one (FR-003) without
deletion. Retrieval uses Graphiti's similarity search; the time-scoped `query_fact(entity, type, as_of=…)`
maps to Graphiti's `valid_at`/`invalid_at` edge attributes, returning a **`FactState`** (value + validity
window + `is_current`).

**Rationale**: Temporal invalidation-not-deletion is *exactly* what Graphiti exists to do — reusing it is the
opposite of overengineering, and it directly delivers US2 / FR-003 / FR-004. We keep our own model thin
(episode in, hits/fact-state out) and let the framework own the graph mechanics.

**Alternatives considered**: *Hand-roll entity extraction before Graphiti* — rejected: duplicates the
framework. *Store only flat episodes (no entity facts)* — rejected: loses the temporal-validity differentiator
that justifies Graphiti at all.

---

## MD5 — `MemorySettings` config section (typed, `extra="forbid"`, creds from Vault)

**Decision**: Add a `MemorySettings` block to `config.py` (env `SENTINEL__MEMORY__*`), register `"memory"` in
`_KNOWN_SENTINEL_SECTIONS`, add the `memory: MemorySettings` field to `Settings`, and add a `model_validator`
that ensures the Neo4j credential path is in `vault.required_paths` (fail-boot if absent — mirrors the
existing llm/ingest validators). Fields: `backend: Literal["graphiti","pgvector"] = "graphiti"`,
`neo4j_uri: str = "bolt://neo4j:7687"`, `neo4j_vault_path: str = "secret/memory"`,
`retrieval_k: int = 5`, `retrieval_timeout_s: float = 5.0`,
`embedding_model: str = "text-embedding-004"` (Gemini), `enabled: bool = True`.

**Rationale**: One typed settings object per Constitution VII; `extra="forbid"` catches typos at boot; the
`backend` toggle is the MD1/MD9 fallback switch; Neo4j creds come from Vault (never inline), fail-boot if the
path is unseeded — consistent with how the Gemini key is handled. `enabled=False` cleanly disables memory in
environments that don't run Neo4j (e.g. unit-only CI), degrading to `NullMemory`.

**Alternatives considered**: *Reuse `LlmSettings`/`PostgresSettings`* — rejected: memory knobs change for
independent reasons and own the backend toggle. *Inline Neo4j password in compose env* — rejected: violates
the secrets-from-Vault rule (Constitution VII).

---

## MD6 — `MemoryProvider` lifespan singleton that **degrades to `NullMemory`**; wired into the **worker** only

**Decision**: Implement `MemoryProvider.build` (currently a `NotImplementedError` stub) as an async context
manager: resolve Neo4j creds from Vault, construct the async Graphiti client (and run its one-time index
setup), `yield` a `GraphitiMemory`, and dispose the driver on shutdown. If `enabled=False` or the connection
fails at startup, **log and yield `NullMemory`** (no-op writes, empty reads) instead of crashing — Constitution
VI / FR-006. Register `MemoryProvider()` in `worker._main_async`; the **api is not** made to depend on Neo4j
in this spec (dashboard read wiring is #12).

**Rationale**: Lifespan-singleton DI (Constitution VII) built once, disposed once. Degrading to `NullMemory`
rather than failing boot is what keeps memory from being a single point of failure for the worker. Worker-only
wiring keeps the api's hard-dependency surface unchanged (the api needn't gain a Neo4j boot dependency for a
write-side feature).

**Alternatives considered**: *Fail boot if Neo4j is down* — rejected: turns memory into a SPOF, violating VI.
*Build the client lazily per write* — rejected: defeats the singleton + re-resolves Vault per write.

---

## MD7 — Two **provider-independent** eval gates: retrieval (hit@k/MRR) + temporal-validity

**Decision**: Add two gates to `eval_thresholds.yaml`: **`retrieval`** (run a labeled set of new incidents
against a memory pre-seeded with priors; score **hit@k** and **MRR** that the correct prior surfaces) and
**`temporal_memory`** (feed a changed-fact scenario; assert `query_fact(as_of=t1)`=old/superseded,
`query_fact(now)`=new/current, and the old fact is **retained**). Both are **deterministic store-logic
gates** with **no `check_per_provider`** dimension — exactly like the existing `smoke` and
`supervisor_routing` gates.

**Rationale**: Retrieval ranking is an **embedding-similarity** property and temporal validity is **store
logic** — neither is a chat-LLM *judgment*, so the Constitution II "both providers" rule (which targets evals
with an LLM-judgment dimension, e.g. triage F1) does not meaningfully apply, and the tiny Ollama fallback
cannot fairly perform Graphiti extraction. Pinning the embedder (Gemini `text-embedding-004`) keeps the gates
deterministic and CI-reproducible. The justification is recorded in `DECISIONS.md` so the "both providers"
exemption is explicit, not silent.

**Alternatives considered**: *Force both providers* — rejected: dishonest (qwen2:0.5b extraction) and adds CI
cost for no signal. *Skip the temporal gate* — rejected: it is the differentiator the whole component exists
to prove (SC-002, the brief's temporal-memory eval).

---

## MD8 — Compose: Neo4j 5.26 Community, auth via `vault-seed`, healthcheck + volume, worker `depends_on`

**Decision**: Replace the reserved `neo4j:` block with a configured service: `image: neo4j:5.26`,
`NEO4J_AUTH=neo4j/<dev-password>`, ports `7474`/`7687`, a named volume, and a bolt healthcheck. `vault-seed`
writes `secret/memory` (`username`, `password`, `uri`); the `worker` gains
`depends_on: neo4j: service_healthy` and `secret/memory` in `SENTINEL__VAULT__REQUIRED_PATHS`. The smoke gate's
service set grows by one (Neo4j).

**Rationale**: Keeps the turnkey "fresh-clone `docker compose up` comes up clean" promise (Constitution
workflow) — Neo4j self-configures with seeded creds and a healthcheck gating the worker, no manual step. Dev
password lives in Vault, not inline (VII).

**Alternatives considered**: *Neo4j Enterprise* — rejected: Community 5.26 is free, GPLv3, and the
best-documented Graphiti backend (per the brief). *No healthcheck/`depends_on`* — rejected: races the worker
against an unready DB and flakes the smoke gate.

---

## MD9 — Decided pgvector + relational fallback (specified now, built only on spike "no-go")

**Decision**: The fallback is `PgVectorMemory(MemoryStore)` over Postgres with a `0005_memory_fallback`
migration: an **`incident_episodes`** table (`incident_id`, redacted `summary`, `disposition`, `severity`,
`observed_at`, `embedding vector`) for similarity (pgvector cosine, IVFFlat index) and an **`entity_facts`**
table (`entity`, `fact_type`, `value`, `valid_from`, `valid_until NULL`) for temporal validity — invalidation
= `UPDATE … SET valid_until = now()` then `INSERT` the new fact; time-scoped read =
`WHERE valid_from <= :as_of AND (valid_until IS NULL OR valid_until > :as_of)`. Embeddings reuse the same
Gemini embedder (MD2). It is **fully specified here** (data-model + this decision) but **implemented only if
the Milestone-0 spike returns "no-go"** — at which point the `backend` toggle (MD5) flips and the same evals
(MD7) run against it unchanged.

**Rationale**: This is Constitution VI's "decided pgvector + `valid_from`/`valid_to` fallback, chosen at the
day-1 spike." Specifying it now (so it's a flip, not a scramble) while not *building* an unused second store
is the disciplined "don't overengineer" middle — the Protocol (MD1) guarantees it drops in.

**Alternatives considered**: *Build both stores in v1* — rejected: dead code unless the spike fails. *Leave
the fallback undecided* — rejected: violates VI's "decided at the spike" requirement.

---

## Resolved unknowns summary

| Question | Resolution |
|----------|------------|
| Graphiti or pgvector? | **Graphiti + Neo4j 5.26** (user decision), behind a `MemoryStore` Protocol with pgvector as the decided fallback (MD1/MD9). |
| What LLM/embedder does graph construction use? | Graphiti's **native Gemini** client + embedder, Vault key; **not** the #3 adapter (justified VII deviation, MD2). Embeddings gap in #3 thus avoided. |
| Who writes episodes, and when? | The **worker**, after `run_incident` reaches terminal, **off-path + best-effort + idempotent**; supervisor untouched (MD3). |
| How is "what was true when" delivered? | Graphiti's native `valid_at`/`invalid_at` edge invalidation; `query_fact(..., as_of=…)` → `FactState` (MD4). |
| Config + secrets? | `MemorySettings` (`backend` toggle, `neo4j_uri`, `neo4j_vault_path`, `retrieval_k`, timeout, `embedding_model`), Neo4j creds from Vault, fail-boot if unseeded (MD5). |
| How is the client built/disposed; api or worker? | `MemoryProvider` lifespan singleton, **degrade→NullMemory** on outage; **worker-only** wiring (MD6). |
| Eval gates + "both providers"? | **retrieval (hit@k/MRR)** + **temporal_memory** gates; **provider-independent** store-logic gates (like smoke/routing), justification in `DECISIONS.md` (MD7). |
| Compose / turnkey bring-up? | `neo4j:5.26` Community, creds via `vault-seed` → `secret/memory`, healthcheck + `depends_on`, smoke set +1 (MD8). |
| Fallback? | `PgVectorMemory` + `0005` migration, **specified now, built only if the spike says no-go** (MD9). |
| New dependency / service / migration? | **+`graphiti-core[google-genai]`**, **+Neo4j service**, **+`testcontainers[neo4j]`** (dev). Migration only on the fallback path. |
