# Feature Specification: Knowledge Corpus (Reference + On-Demand Intel)

**Feature Branch**: `008-knowledge-corpus`

**Created**: 2026-06-09

**Status**: Draft

**Input**: User description: "The next spec by dependency order — the knowledge layer (Component #5, SPEC-knowledge-corpus). Two things, kept deliberately small: (1) a seeded reference corpus — a curated point-in-time snapshot of stable security knowledge (MITRE ATT&CK technique→mitigation mappings, a sample IOC/reputation set, a handful of runbooks) loaded at init so the agent is competent on the very first incident; and (2) an optional, config-gated on-demand intel lookup — the enrichment agent may ask an external threat-intel source for a verdict on a specific indicator, cached briefly, and the result is also written into the temporal memory store as a time-stamped episode so the second time that indicator appears its history is already there. Live/streaming feed ingestion is roadmap, not v1. Make things simple, don't overengineer."

## Context & Boundary *(why this component, where it sits)*

The temporal memory substrate (#6) is built: a store that records processed incidents and answers "what was true when," with a stable read/write contract — but it ships **empty**. This component is what gives that store, and the agents that reason over it, **knowledge to work with**. It owns the *content* of knowledge and how knowledge enters the system; it does **not** own the substrate (that is #6) or the reasoning that consumes it (that is enrichment, #9).

The capstone's reasoning quality has a **cold-start problem**: an empty system on day one has nothing to compare an incident against, so the first incidents would be reasoned about with no domain context at all. The fix is the same move a profile-bootstrapping system makes — seed the system with curated public knowledge at init so it is competent before it has seen anything. That seeded **reference corpus** is the primary deliverable.

The corpus is deliberately a **curated, point-in-time snapshot**, not a live feed. It holds **stable, slow-decaying** knowledge — primarily structural mappings like MITRE ATT&CK technique→mitigation, plus a sample IOC/reputation set and a handful of runbooks. A snapshot stays useful for months, which is exactly the property that makes seeding-at-init the right, simple choice for v1.

The second, smaller deliverable is an **optional on-demand intel lookup**: when the enrichment agent needs a verdict on a *specific* indicator it does not already have, it may call an external threat-intel source for that one indicator. The result is cached briefly (cost/latency), and — the part that matters — it is **also written into the temporal memory store as a time-stamped episode**, so the *next* time that indicator appears, its history (with the time dimension) is already there. This is what distinguishes it from a throwaway lookup. It is config-gated and the whole pipeline runs correctly with it disabled.

**Where it sits.** This component is a **writer into the memory store (#6)** — seeding corpus knowledge at init and writing intel episodes on demand — and a **provider of knowledge retrieval** consumed by the enrichment agent (#9). Feed- and intel-sourced text is **attacker-influenceable, untrusted input**: it passes the same redaction (#2) and injection-guardrail (#11 seam) boundary as alert text before it is written or used. **Live/streaming ingestion** of volatile sources is explicitly out of v1 (roadmap §v2/v3 below); the boundary is about *consumption mode* (static snapshot vs. live stream), not the source.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - The system is competent on the very first incident (Priority: P1)

At initialization, a curated reference corpus — MITRE ATT&CK technique→mitigation mappings, a sample IOC/reputation set, and a handful of runbooks — is seeded into the knowledge store. From then on, a reasoning component can retrieve relevant reference knowledge for an incident (the mitigations for a detected technique, a matching runbook, the known reputation of an indicator) **even before any incident has ever been processed**. This is the MVP: it closes the cold-start problem and is the whole point of the component.

**Why this priority**: Without the seeded corpus, the system has no domain knowledge on day one and the enrichment agent has nothing to correlate against until incidents slowly accumulate. The seed is what makes the system useful from the first alert; everything else in this component is an optional enhancement on top of it.

**Independent Test**: Initialize a fresh system with no incidents processed. Query the knowledge layer for reference knowledge relevant to a known technique and indicator. Verify relevant corpus entries (e.g., the technique's mitigations, a matching runbook, the indicator's seeded reputation) are returned, ranked by relevance. Re-run initialization and verify the corpus is unchanged (no duplicate entries).

**Acceptance Scenarios**:

1. **Given** a freshly initialized system with no incidents processed, **When** the knowledge layer is queried for reference knowledge relevant to an incident's technique and indicators, **Then** it returns relevant curated corpus entries (technique→mitigation mappings, matching runbooks, known IOC reputation) ranked by relevance — a non-empty, useful result on the first incident.
2. **Given** an already-seeded corpus, **When** initialization runs again (e.g., on a restart or redeploy), **Then** seeding is idempotent — the corpus content is unchanged and no duplicate entries are created.
3. **Given** a query whose technique/indicators have no curated match, **When** the knowledge layer is queried, **Then** it returns an empty result for that slice — a normal "no reference knowledge" outcome, not an error.

---

### User Story 2 - On-demand intel for a specific indicator, remembered next time (Priority: P2)

When the enrichment agent needs a verdict on a *specific* indicator it does not already have, it can ask an external threat-intel source for that one indicator. The verdict is returned for the current incident, cached briefly so a repeat lookup within a short window costs nothing, and **written into the temporal memory store as a time-stamped episode**. The second time that indicator appears, its history is already present — the lookup is not discarded after use. This capability is optional and config-gated: with it disabled, the pipeline runs correctly using only the seeded corpus.

**Why this priority**: It is the "gets smarter" enhancement that turns a one-shot external lookup into accumulated, temporal institutional memory — the behaviour that distinguishes Argus's knowledge layer from a stateless intel proxy. It ranks below the corpus because the system is fully demonstrable with the seeded corpus alone, and the brief marks it optional for v1.

**Independent Test**: With on-demand intel enabled, request a verdict for a previously-unseen indicator; verify a verdict is returned within the configured timeout and that a time-stamped episode for that indicator is written to memory. Request the same indicator again within the cache window; verify it is served without a second external call. With on-demand intel disabled by config, verify the same incident still completes using only the seeded corpus.

**Acceptance Scenarios**:

1. **Given** on-demand intel is enabled and an indicator with no known verdict, **When** the enrichment agent requests intel for that indicator, **Then** the system queries the external source, returns the verdict within the configured timeout, and records the result as a time-stamped episode in the temporal memory store (so the indicator's history exists for next time).
2. **Given** an indicator whose intel was just looked up, **When** the same indicator is requested again within the cache window, **Then** the result is served from cache (or from memory) without issuing a second external call.
3. **Given** an intel result that contradicts the seeded snapshot (e.g., an indicator seeded benign now returns malicious), **When** it is written, **Then** it is recorded as a new time-stamped fact that supersedes the prior via the temporal model — the seeded "benign as of the seed" state is invalidated, not deleted, and both remain queryable.
4. **Given** on-demand intel is disabled by configuration, **When** an incident is processed, **Then** the pipeline completes correctly using only the seeded reference corpus, issuing no external intel calls.

---

### User Story 3 - Knowledge is untrusted input and never breaks the pipeline (Priority: P3)

Feed- and intel-sourced text is attacker-influenceable: it is redacted and passes the injection-guardrail boundary before it is written to memory or handed to any reasoning step, exactly as alert text is. Knowledge operations are also best-effort and bounded: a corpus miss returns empty, an external intel source that is slow, erroring, or unreachable degrades gracefully (timeout → unknown, cached briefly), and neither blocks or crashes the incident pipeline.

**Why this priority**: It protects the safety boundary (untrusted external content is a real injection vector) and the pipeline guarantees the supervisor depends on (the worker never crashes, dispositions always complete). The system is demonstrable without exercising these failure and adversarial paths, so it ranks below the core capabilities — but it is non-negotiable for correctness.

**Independent Test**: Inject a redaction probe (fake secret/PII) and a known injection payload into intel/feed text and verify neither the secret appears unredacted nor the injection succeeds anywhere downstream (log, memory store, reasoning input). Separately, inject an intel-source outage and a timeout and verify each returns an empty/unknown result, is not retried unboundedly, and never blocks the incident's disposition or crashes the worker.

**Acceptance Scenarios**:

1. **Given** intel/feed text containing a sensitive value or an injection payload, **When** it is ingested, **Then** the sensitive value is redacted before any write and the injection payload is caught by the guardrail boundary — nothing unredacted and no successful injection reaches a log, the memory store, or a reasoning input.
2. **Given** the external intel source is unreachable, slow, or erroring, **When** an intel lookup is attempted, **Then** it times out to an "unknown" result (cached briefly to avoid hammering the source), the incident still completes its disposition, and the worker does not crash.
3. **Given** the corpus has no match for a query, **When** the knowledge layer is read, **Then** it returns an empty result rather than raising — a corpus miss is a normal outcome, not a failure.

---

### Edge Cases

- **Cold start handled, not just tolerated:** unlike the bare memory store, this layer makes the *first* incident competent — the corpus is seeded before any incident exists.
- **Re-seed / redeploy:** seeding is idempotent — restarting or redeploying does not duplicate corpus entries or double-count knowledge.
- **Snapshot vs. live update conflict:** an on-demand intel verdict that contradicts the seeded snapshot is written as a new time-stamped episode that supersedes the prior via the temporal model (#6) — preserving "benign as of the seed, malicious as of the lookup." No destructive overwrite of the snapshot.
- **Untrusted feed/intel text:** all externally-sourced content passes redaction (#2) and the injection-guardrail boundary (#11 seam) before any write or reasoning use — the same treatment as alert text.
- **Intel source outage / timeout / rate limit:** a lookup that is slow, erroring, unreachable, or rate-limited degrades to a bounded "unknown" result, cached briefly, and never blocks disposition.
- **Cache hit within TTL:** a repeat lookup of the same indicator inside the cache window is served without a second external call (cost/latency control); unknown/negative verdicts are cached too.
- **Intel disabled:** with on-demand intel turned off by config, the pipeline runs correctly on the seeded corpus alone and makes no external calls.
- **Missing intel credentials:** absent threat-intel credentials simply disable the optional lookup — they do not fail startup or block the rest of the pipeline (unlike substrate credentials, which do).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST seed a **curated reference corpus** at initialization — MITRE ATT&CK technique→mitigation mappings, a sample IOC/reputation set, and a handful of runbooks — into the knowledge store, so that reference knowledge is retrievable on the very first incident (cold-start competence).
- **FR-002**: Corpus seeding MUST be **idempotent** — re-running initialization (restart, redeploy) MUST NOT create duplicate entries or change the corpus content.
- **FR-003**: The system MUST expose **bounded retrieval** over the corpus so a reasoning component (enrichment, #9) can fetch the reference knowledge relevant to an incident — the mitigations for a detected technique, matching runbooks, and the known reputation of given indicators — ranked by relevance, with a configurable top-k; a query with no match returns an empty result, not an error.
- **FR-004**: The system MUST provide an **optional, config-gated on-demand intel lookup**: given a specific indicator, it MAY query a configured external threat-intel source for a verdict, bounded by a timeout. With the lookup disabled, the pipeline MUST run correctly using only the seeded corpus and issue no external intel calls.
- **FR-005**: Intel lookup results MUST be **cached briefly** (configurable TTL) so a repeat lookup of the same indicator within the window issues no second external call; unknown/negative verdicts MUST be cached as well to bound cost and protect the source.
- **FR-006**: An intel lookup result MUST be **persisted into the temporal memory store (#6) as a time-stamped episode** so the indicator's history is available on its next appearance. A verdict that contradicts the seeded snapshot MUST **supersede** the prior via the temporal model (invalidate-not-delete), never destructively overwrite it.
- **FR-007**: All feed- and intel-sourced text is **untrusted input**: it MUST be redacted (#2) before any write and MUST pass the injection-guardrail boundary (#11 seam) before being written to memory or handed to any reasoning step — the same treatment applied to alert text.
- **FR-008**: Corpus reads and intel lookups MUST NOT block or crash the incident pipeline. A corpus miss MUST return empty; an intel source that is slow, erroring, unreachable, or rate-limited MUST degrade to a bounded "unknown" result (retried only within policy, then given up) and MUST NOT block an incident's disposition or crash the worker — fail closed.
- **FR-009**: This component MUST write **only through the memory layer's existing read/write contract (#6)** for intel episodes and corpus knowledge — it MUST NOT introduce a second persistence substrate, and no stored field may be written by two uncoordinated parties.
- **FR-010**: The knowledge layer's knobs — corpus source/content, the on-demand-intel enable flag, the external source connection and credentials, cache TTL, and retrieval top-k — MUST be **configuration-backed**. Required substrate values fail at startup if absent; **missing optional intel credentials disable the lookup rather than failing startup**.
- **FR-011**: Retrieval that draws on the corpus MUST be **evaluable** — its contribution to enrichment/retrieval quality (hit@k / MRR over a committed labeled set) gated in CI consistently with the memory retrieval eval — so seeding measurably improves the cold-start case.

### Key Entities *(include if feature involves data)*

- **Reference Corpus Entry**: a curated, point-in-time knowledge item — a MITRE ATT&CK technique→mitigation mapping, an IOC/reputation record, or a runbook. Seeded at init, slow-decaying, retrieved to give an incident domain context. The unit of cold-start competence.
- **Intel Lookup Result**: a verdict on a *specific* indicator from an external threat-intel source at a point in time. Returned for the current incident, cached briefly, and recorded as a time-stamped temporal episode so it accumulates into history. Untrusted until redacted and guardrail-checked.
- **Knowledge Configuration**: the configuration-backed knobs — corpus source/content, intel enable flag, external source connection/credentials, cache TTL, retrieval top-k. Required substrate values fail at startup; missing intel credentials disable the optional lookup.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: On a freshly initialized system with **zero incidents processed**, a query for reference knowledge relevant to a known technique and its indicators returns relevant corpus entries (mitigations, runbooks, seeded reputation) — cold-start competence demonstrated on the first incident.
- **SC-002**: Re-running initialization is **idempotent** — the corpus entry count and content are unchanged after a second seed (no duplicates).
- **SC-003**: With on-demand intel enabled, a lookup for a previously-unseen indicator returns a verdict within the configured timeout, and a repeat lookup of the same indicator within the cache window issues **no second external call**.
- **SC-004**: An intel verdict that contradicts the seeded snapshot is retained as a **new time-stamped fact that supersedes the prior without deleting it** — both the seeded and the updated state remain queryable (verified via the temporal model).
- **SC-005**: No corpus miss and no intel-source outage blocks or fails an incident's disposition — **100%** of incidents reach a disposition in a failure-injection run, and the pipeline runs correctly with on-demand intel disabled by config.
- **SC-006**: No unredacted sensitive value and **no successful injection payload** originating in feed/intel text ever reaches a log, the memory store, or a reasoning input (verified by the redaction and red-team evals).
- **SC-007**: Seeding the corpus **measurably improves** the cold-start retrieval case — the corpus-backed retrieval eval (hit@k / MRR) meets its committed threshold where an unseeded system would return nothing.

## Assumptions

- **The temporal memory store (#6) and its read/write contract exist** and are the single persistence substrate for both seeded corpus knowledge and intel episodes. This component writes *through* that contract; it introduces no new store.
- **Redaction (#2) and the guardrail boundary (#11 seam) apply to feed/intel text as untrusted input.** The curated corpus is trusted content, but it still passes the redaction write-boundary for uniformity; the on-demand intel path is the adversarial-input case the guardrail protects.
- **The reference corpus is a small curated snapshot bundled with the repo** — MITRE technique→mitigation mappings, a sample IOC/reputation set, and a few runbooks — seeded at init. Freshness is explicitly not a v1 concern (slow decay); it is refreshed manually, if at all.
- **On-demand intel is optional, config-gated, and single-source for v1.** A single configured external threat-intel source suffices; absent credentials simply disable the lookup, leaving the rest of the pipeline unaffected. Source failover and multi-source fusion are out of scope.
- **Redis (from #1/#4) is the brief cache** for intel results (cost/latency control), with a configurable TTL.
- **Enrichment (#9) is the primary reader** of this knowledge; this component provides the knowledge and the lookup, not the cross-correlation reasoning that consumes them.
- **A labeled retrieval eval set covering corpus knowledge is curated/available**, with thresholds committed so CI gates the cold-start improvement.

## Out of Scope

- **The temporal memory substrate itself (#6)** — this component writes into it; it does not build it.
- **Enrichment's cross-correlation reasoning (#9)** that consumes corpus knowledge and intel episodes.
- **Live / streaming feed ingestion** — standing scheduled connections to volatile reputation/advisory sources — specified as the marked roadmap section below (§v2/v3), not v1.
- **Multiple or federated intel sources, source failover, and provider fan-out** — a single optional source for v1.
- **Automatic corpus refresh, versioning, or scheduled re-seed** — the v1 corpus is a manually-curated static snapshot.
- **Detection of novel/zero-day threats** — knowledge improves *response* reasoning, not detection (a detection-layer roadmap concern).

## Roadmap — §v2/v3 Live Knowledge Ingestion *(roadmap, not v1 core)*

Per the build plan and brief, live ingestion is recorded here so the knowledge layer is not half-specified, but it is **out of v1 scope**.

The v1 corpus is a static snapshot; v2/v3 adds **standing scheduled connections to *volatile* sources whose value is freshness** — IP/domain reputation (e.g. AbuseIPDB / OTX / GreyNoise) and emerging-threat advisories (e.g. CISA) — normalized and written into the temporal memory store as time-stamped episodes. The tier distinction is about **consumption mode** (static snapshot vs. live stream), not the source: the *same* source may seed v1 as a snapshot and feed v2 as a stream. This is where the temporal-validity model (#6) earns its keep — when a live feed contradicts the seeded snapshot, it invalidates the old edge and time-stamps the new one, preserving "benign as of the seed, malicious as of the feed update." Like all externally-sourced content, streamed feed text is treated as **untrusted input under the guardrail boundary**. The v1 write contract (FR-006/FR-007: redacted, guardrail-checked, written as a temporal episode) is exactly the seam this later consumes; no v1 behavior depends on it.
