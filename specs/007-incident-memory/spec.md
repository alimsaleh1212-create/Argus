# Feature Specification: Incident Memory (Temporal)

**Feature Branch**: `007-incident-memory`

**Created**: 2026-06-09

**Status**: Draft

**Input**: User description: "The next spec by dependency order — the temporal incident-memory layer (Component #6, SPEC-memory). A store that gets more useful as it sees more incidents: it records each processed incident as a time-stamped episode, retrieves the closest prior incidents and their dispositions for a new incident, and preserves the time dimension so it can answer what was true *when* — invalidating superseded facts rather than deleting them. Realized as a Graphiti temporal graph on Neo4j, with a relational + vector-similarity fallback held in reserve. Make things simple, don't overengineer."

## Context & Boundary *(why this component, where it sits)*

The deterministic spine is built: ingestion (#4) → supervisor (#7) → triage (#8). Triage was deliberately scoped to reason **only over evidence supplied for the incident**, with no historical context — because the memory layer did not exist yet. This component builds that layer.

Incident memory is the capstone's **"gets smarter over time"** capability — and it is **memory and retrieval, not model learning**. Nothing retrains at runtime. The system accumulates what it has seen (incidents, the entities involved, the dispositions analysts and the pipeline reached) and retrieves it to inform reasoning about new incidents.

The distinguishing requirement is **time**. Security context is inherently temporal: an IP benign last month is flagged today; a host's role changes; an analyst's disposition on a pattern evolves. A flat similarity store collapses this into "what is true now" and loses "what was true when." This layer preserves the time dimension: facts are time-bounded, and a conflicting update **invalidates** the prior fact rather than deleting it, so both the current and the superseded state remain queryable.

**Where it sits.** This component owns the memory **substrate and its read/write contract** — not the knowledge it will later hold or the reasoning that consumes it. The reference corpus content and on-demand intel (#5) are written *into* this store but specified separately. The enrichment agent (#9) is the primary **reader** (internal correlation). The supervisor/pipeline is the **writer**, recording an incident as it reaches disposition. Triage is **not** retrofitted to read memory in v1; wiring memory back into early-stage scoring is the v2c feedback loop (roadmap, marked below).

**Realization (decided this run).** The temporal capability is realized as a **Graphiti temporal graph on Neo4j** — the headline architecture. Because that adds a service and framework on the path and makes reasoning calls during graph construction, a **day-1 integration spike** validates real latency and token cost before committing, with a **relational + vector-similarity fallback** (temporal validity as `valid-from`/`valid-until`) held in reserve. The requirements below are written against the *capability*, so they hold under either realization.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - The system remembers a similar prior incident and its disposition (Priority: P1)

As incidents are processed, each is recorded into memory as a time-stamped episode capturing its key entities, verdict/severity, and final disposition. When a new incident arrives that resembles one already seen, the system retrieves the closest prior incidents and how they were dispositioned, so downstream reasoning has historical context it could not get from the alert alone. This is the MVP — the minimal write→retrieve loop that makes the system "remember."

**Why this priority**: This is the core value of the whole component and the capstone's headline capability. Without the write+retrieve loop, memory holds nothing and surfaces nothing; with it, every new incident can be reasoned about in the context of past ones.

**Independent Test**: Process and dispose of incident A. Process a second incident B whose entities/pattern resemble A. Query memory for B's context and verify A is returned among the top-k results with its disposition and observed time, ranked by relevance. With an empty store, the same query returns an empty result, not an error.

**Acceptance Scenarios**:

1. **Given** an incident that has reached a disposition, **When** the pipeline records it, **Then** a time-stamped episode is written to memory capturing its key entities (e.g., source/destination addresses, host, user, indicators), its verdict/severity, its disposition, and the observed time.
2. **Given** a populated memory store and a new incident resembling a prior one, **When** the system queries memory for similar incidents, **Then** it returns the closest prior incidents (top-k) with their dispositions, observed times, and a relevance ranking.
3. **Given** an empty memory store (cold start) and a new incident, **When** the system queries for similar incidents, **Then** it returns an empty result set — this is a normal "no prior context" outcome, not an error.

---

### User Story 2 - The system answers "what was true when" (Priority: P2)

Facts about entities are stored with a validity time-range, not as a single current value. When a fact changes — an indicator's reputation flips from benign to malicious — the prior fact is invalidated (its validity is ended) and the new fact recorded, with **both retained**. A time-scoped query then returns the correct *time-valid* state: what was true as of a given moment, distinguished from what is true now. This is the differentiator a flat similarity store cannot provide.

**Why this priority**: This is what makes the layer *temporal* rather than just a vector store, and it is directly evaluated (the temporal-memory eval). It ranks below the basic remember loop because the system is demonstrable with retrieval alone, but it is the defensible core of the design.

**Independent Test**: Record a fact about an indicator as benign at time t1. Record a conflicting fact (malicious) at time t2. Query "what was this indicator's reputation as of t1?" and verify it returns benign (superseded). Query "what is it now?" and verify malicious (current). Verify the benign fact still exists, marked superseded — it was invalidated, not deleted.

**Acceptance Scenarios**:

1. **Given** a fact recorded with an observed/valid-from time, **When** a conflicting fact about the same entity is later recorded, **Then** the prior fact's validity is ended (it is marked superseded) and the new fact is recorded with its own valid-from time — the prior fact is **not** deleted or overwritten.
2. **Given** an entity whose fact changed over time, **When** a time-scoped query asks for the fact's state as of a past time, **Then** the system returns the state that was valid at that time, not merely the most recent or the most semantically similar value.
3. **Given** the same entity, **When** a query asks for the current state, **Then** the system returns the currently-valid fact and can also surface that a prior, now-superseded state exists.

---

### User Story 3 - Memory never blocks or breaks the pipeline (Priority: P3)

Memory is an enhancement to reasoning, never a dependency the pipeline can die on. If the memory store is unavailable or a write fails, the incident still reaches its disposition; a read miss returns empty rather than erroring; and memory work stays off the synchronous disposition-critical path where feasible. A documented relational + vector fallback exists so the temporal capability survives even without the graph store.

**Why this priority**: Robustness and cost-control. It protects the pipeline guarantees the supervisor depends on (the worker never crashes, dispositions always complete) and keeps the added service from becoming a single point of failure. The system is demonstrable without the failure paths, so this ranks below the core capabilities.

**Independent Test**: Inject a memory-store outage and, separately, a graph-construction reasoning failure. Verify in each case that incidents still reach a disposition, that no worker crashes, that a read during the outage returns an empty result, and that writes are retried or skipped without blocking. Verify the fallback path is specified and exercised.

**Acceptance Scenarios**:

1. **Given** the memory store is unavailable, **When** the pipeline records or queries an incident, **Then** the incident still completes its disposition, a query returns an empty result, and the worker does not crash — the failure is recorded, not raised into the disposition flow.
2. **Given** a reasoning call required to build the graph from an episode fails or times out, **When** an episode is written, **Then** the write degrades gracefully (retried within policy, then skipped) and never blocks the incident's disposition or crashes the worker.
3. **Given** the graph store is not available, **When** the system runs on its documented fallback, **Then** similar-incident retrieval and time-valid fact queries still function over a relational + vector-similarity store with validity columns.

---

### Edge Cases

- **Cold start (empty memory):** the very first incidents have no priors; retrieval returns empty and reasoning proceeds without historical context. (Seeding reference knowledge to soften cold start is Component #5's job, not this one.)
- **Conflicting facts:** benign→malicious (or any contradiction) invalidates the old fact and time-stamps the new one; both are retained and queryable. No destructive overwrite.
- **Duplicate / reprocessed incident:** writing the same incident again (e.g., a worker retry) is idempotent — it does not create duplicate episodes or double-count facts.
- **Semantically similar but stale match:** a fact that is similar but no longer valid must not be returned as current; time-scoped queries are bounded by validity, not similarity alone.
- **Sensitive values in the incident:** PII and secrets are redacted **before** anything is written to memory; nothing unredacted is ever persisted to or returned from the store (Component #2 boundary).
- **Graph-construction reasoning cost/latency spikes:** episode-construction reasoning is bounded and its tokens reported; it never runs unbounded and never blocks disposition.
- **Very large store / slow retrieval:** retrieval is bounded (top-k, latency budget); it adds no measurable latency to the synchronous disposition path.
- **Writer races:** the pipeline is the single writer of incident episodes; concurrent writes for distinct incidents do not corrupt the temporal record.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST record each processed incident into memory as a **time-stamped episode** capturing its key entities (e.g., source/destination addresses, host, user, indicators), its verdict and severity, its final disposition, and the time it was observed.
- **FR-002**: Given a new incident, the system MUST retrieve the most similar **prior incidents** (top-k) together with their dispositions, observed times, and a relevance ranking, to provide historical context to downstream reasoning.
- **FR-003**: The system MUST model facts and relationships about entities as **time-bounded** (valid-from / valid-until). On a conflicting update it MUST invalidate the prior fact (end its validity) and record the new fact with a new valid-from — it MUST NOT delete or destructively overwrite the prior fact.
- **FR-004**: The system MUST support a **time-scoped query** that returns the time-valid state of a fact (what was valid as of a given time) and distinguishes the currently-valid state from superseded states — temporal correctness, not similarity alone.
- **FR-005**: All incident and entity content MUST be **redacted before** being written to memory; no unredacted sensitive value (PII or secret) is ever persisted to, or returned from, the memory store (Component #2 redaction boundary).
- **FR-006**: Memory reads and writes MUST NOT block or crash the incident pipeline. If the store is unavailable or a write fails, the incident MUST still complete its disposition; a read miss MUST return an empty result, not an error. Memory operations MUST stay off the synchronous disposition-critical path where feasible.
- **FR-007**: Writing an incident episode MUST be **idempotent** for a given incident — reprocessing or a worker retry MUST NOT create duplicate episodes or double-count facts.
- **FR-008**: Any reasoning call required to construct the graph from an episode MUST go through the shared reasoning-provider adapter (Component #3), MUST report its token usage for accounting, and MUST fail closed — a construction failure degrades to a retried-then-skipped write, never a pipeline crash.
- **FR-009**: Retrieval MUST be **bounded** — a configurable top-k and a latency budget — and results MUST carry enough provenance (incident reference, disposition, observed time, relevance) for the dashboard and enrichment to display and use them.
- **FR-010**: The memory layer MUST expose a **stable read/write contract**: written to by the pipeline as incidents reach disposition and by the knowledge corpus (#5) as intel episodes; read by enrichment (#9). The set of writers and readers MUST be defined so that no stored field is unowned or written by two uncoordinated parties.
- **FR-011**: A **relational + vector-similarity fallback** (temporal validity expressed as valid-from / valid-until columns) MUST be specified so that similar-incident retrieval and time-valid fact queries still function without the graph store — the de-risking shrinkable slice.
- **FR-012**: Retrieval quality and temporal correctness MUST be **evaluable against committed labeled sets with CI threshold gates** — hit@k / MRR for similar-incident retrieval, and a temporal-validity eval (correct current-vs-superseded state) — runnable identically on both supported reasoning providers.
- **FR-013**: The memory layer's knobs (top-k, latency budget, store connection/credentials, fallback selection, validity behavior) MUST be **configuration-backed**; required values fail at startup if absent.

### Key Entities *(include if feature involves data)*

- **Incident Episode**: the unit of "what the system has seen" — a time-stamped record of one processed incident: its entities, verdict/severity, disposition, and observed time. Written by the pipeline at disposition; the primary thing retrieved for similarity.
- **Entity & Temporal Fact**: a tracked thing (address, host, user, indicator) and a **time-bounded** fact or relationship about it (reputation, role, disposition) carrying valid-from / valid-until. Conflicts invalidate rather than delete.
- **Retrieval Result**: the ranked set returned for a query — prior incidents and/or facts, each with relevance, disposition, and observed time; for time-scoped queries, the time-valid state plus an indicator that superseded states exist.
- **Memory Configuration**: the configuration-backed knobs — top-k, latency budget, store connection/credentials, fallback selection, validity behavior. Required values fail at startup.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: After processing a seeded set of incidents, a new incident that resembles a prior one surfaces that prior incident and its disposition within the top-k retrieved results in at least the committed **hit@k** threshold of cases.
- **SC-002**: For a fact that changed over time, a time-scoped query returns the **correct time-valid state** (current vs. superseded) in 100% of the committed temporal-validity eval cases — demonstrating temporal correctness distinct from semantic similarity.
- **SC-003**: No incident's disposition is blocked or failed by a memory problem — across a memory-unavailable failure-injection run, 100% of incidents still reach a disposition and the worker never crashes.
- **SC-004**: A fake secret or PII value injected into an incident **never** appears unredacted in the memory store or in any retrieval result (verified by the redaction eval).
- **SC-005**: Conflicting fact updates retain **both** the prior and new fact (zero destructive deletes); the prior fact remains queryable as superseded after the update.
- **SC-006**: Similar-incident retrieval returns within the configured latency budget for the demo dataset and adds no measurable latency to the synchronous disposition path.
- **SC-007**: The retrieval and temporal-validity evals pass **identically on both supported reasoning providers**, gating CI.

## Assumptions

- **Full Graphiti + Neo4j is the chosen realization** (decided this run) of the temporal-graph capability. A day-1 integration spike validates real latency and token cost before committing, with the relational + vector-similarity fallback (FR-011) ready if it bites. The requirements are written against the capability so they hold under either realization.
- **Redaction (Component #2) is applied before any memory write.** This layer builds episodes from already-redacted incident content and persists nothing raw.
- **This component owns the memory substrate and contract, not its contents.** The reference corpus and on-demand intel (#5) and the enrichment reasoning that reads memory (#9) are separate components that use this contract.
- **The pipeline is the single writer of incident episodes**, recording an incident when it reaches a meaningful disposition (resolved / escalated / responded). The read-only agents do not write episodes.
- **Triage (#8) is not retrofitted to read memory in v1.** Enrichment (#9) is the v1 reader; feeding memory back into early-stage scoring is the v2c feedback loop (roadmap, below).
- **LLM-backed graph construction is acceptable v1 cost**, given it is bounded and its tokens are reported into the existing accounting; it runs off the synchronous disposition path.
- **Labeled retrieval and temporal-validity eval sets are curated/available**, with thresholds committed so CI gates from the start.

## Out of Scope

- **The feedback loop** that feeds accumulated memory back into detection/triage scoring — specified as the marked roadmap section below (§v2c, Tier 2), not v1 core.
- **The reference corpus content and on-demand intel lookup** (Component #5).
- **Enrichment's cross-correlation reasoning** that consumes memory (Component #9).
- **Live / streaming external feed ingestion** (v2/v3 roadmap) — standing connections to volatile reputation/advisory sources.
- **Zero-day / novel-threat detection** — memory improves *response*, not detection; detecting the novel is a detection-layer concern (roadmap).
- **Any model training or retraining** — this is retrieval and temporal memory, never runtime learning.

## Roadmap — §v2c Feedback Loop *(Tier 2, not v1 core)*

Per the build plan, the feedback loop lives as a marked section inside this spec (and is cross-referenced by the response/state-machine spec). **Out of v1 scope; recorded here so the loop is not half-specified.**

When memory has accumulated, the disposition history it holds becomes an **input to future scoring** — e.g., an entity repeatedly dispositioned malicious raises the priority or shifts the triage/severity signal for a new incident touching it, closing the detection↔response loop that defines a mature SOC. The v1 contract (FR-002/FR-004 retrieval, with provenance and time-validity) is the seam this later consumes; no v1 behavior depends on it. A small eval proving behaviour changes after memory accumulates lands with Tier 2.
