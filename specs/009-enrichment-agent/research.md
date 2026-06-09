# Phase 0 — Research & Decisions: Enrichment Agent (#9)

All Technical-Context unknowns are resolved here. Decisions are numbered `ED<n>` (Enrichment Decision) and
carry forward into `DECISIONS.md`. The throughline is the user's standing steer: **make it simple, don't
overengineer** — reuse the contracts #5/#6/#3/#7 already shipped, add no new service, and mirror the triage
stage's proven shape.

---

## ED1 — Mirror triage: a bounded retrieval fan-out + exactly ONE structured LLM call (no agentic loop)

**Decision**: Enrichment is **not** a tool-calling/multi-step agent. It is a deterministic retrieval
**fan-out** followed by **one** structured-output `LlmClient.generate` call that returns a validated
`EnrichmentReport`, mapped to a `StageOutcome` by a pure config-threshold function — the exact shape of the
triage stage (`make_triage_handler` → one call → `TriageJudgment` → `decide_outcome`).

**Rationale**: The brief's "fixed flow with one bounded agentic step" lesson; the supervisor already owns the
loop and the step/token cap, so a second internal loop inside enrichment would duplicate that and threaten
the cap. One call keeps cost bounded (SC-006), keeps `tokens_consumed` reportable in one place, and reuses
the triage error-handling and validation patterns verbatim. Cross-correlation over a *pre-assembled* evidence
bundle is well within a single structured call.

**Rejected**: A LangGraph/tool-calling enrichment agent that decides which retrievals to run iteratively —
overengineering for v1, harder to bound and to eval, and a Principle IV/VII regression versus the proven
one-call pattern. (Adaptive *retrieval depth* is unnecessary: the fan-out is cheap and deterministic.)

---

## ED2 — Reuse existing contracts via closure-factory DI; retrievers may be `None` (best-effort)

**Decision**: `make_enrichment_handler(llm, corpus, memory, intel, cfg) -> StageHandler` closes over the
**already-built DI singletons**: the `LlmClient` (#3), the `CorpusRetriever` (#5, `container.corpus`), the
`MemoryStore` (#6, `container.memory`), and the optional `ThreatIntelClient` (#5, `container.intel`, which is
`None` when intel is disabled). Each of `corpus`/`memory`/`intel` may be `None`; the handler skips that source
and proceeds. No new service, dependency, or migration is introduced.

**Rationale**: The closure factory preserves the frozen `StageHandler` signature (`Incident` → `StageResult`)
— which is exactly what enforces the Principle III boundary (only read-only retrievers are ever injected; no
session, no action client) and lets every dependency be mocked in unit tests. `None`-tolerance is what makes
graceful degradation (FR-008) fall out for free: an e2e without a memory/corpus provider still runs the stage.

**Rejected**: Construct retrievers inside the handler (would require `agents/` to import `infra`/`services`,
violating the inward-only layering contract) or pass a service-locator/container into the agent (couples the
agent to the container; harder to test). FastAPI `Depends` is for the request path, not the worker's stage
loop — the closure factory is the established worker-side DI (triage precedent).

---

## ED3 — Output is the cross-correlation: `EnrichmentReport`; ADVANCE is primary, RESOLVED/ESCALATE supported

**Decision**: The one reasoning call produces a validated `EnrichmentReport`:
`assessment` ∈ {`confirmed`, `benign`, `inconclusive`}, `confidence` ∈ [0,1], a one-line
`correlation_summary` (the headline cross-correlation), `external_findings[]` and `internal_findings[]` (the
specific items it rests on), and `cited_evidence[]` (≥1). A pure `decide_outcome(report, cfg)` maps it to a
`StageOutcome`, mirroring triage's precedence:

1. `inconclusive` → **ESCALATE**;
2. `confidence < advance_min_confidence` → **ESCALATE**;
3. `confirmed` → **ADVANCE** (→ response — the common path);
4. `benign` with `confidence ≥ resolve_min_confidence` → **RESOLVED** (auto-close as noise);
5. `benign` below `resolve_min_confidence` → **ESCALATE** (not confident enough to auto-close).

**Rationale**: ADVANCE is the expected path (triage already judged these real); the brief explicitly motivates
the other two — *conflicting evidence that needs a judgment call* (→ escalate) and a correlation that
*exonerates* (→ resolve, sparing the response stage a wasted action). The supervisor **already** defines all
three `ENRICHING → {responding, resolved, escalated}` edges with their dispositions
(`DISP_AUTO_RESOLVED_ENRICHMENT` / `DISP_ESCALATED_ENRICHMENT`), so supporting them costs **zero** extra
transition wiring and the same single call — it is not overengineering. Reusing triage's threshold semantics
keeps `EnrichmentSettings` and the eval intuition consistent.

**Rejected**: Enrichment always ADVANCEs (a pure context-fetcher) — rejected: throws away the one judgment
that makes it an agent and the transitions #7 already built, and passes known-benign incidents to the response
stage. A 4th/5th outcome verb — rejected: the `StageOutcome` enum + transition table are closed; three
outcomes suffice.

---

## ED4 — Concurrent, individually-guarded retrieval fan-out; deterministic entity/query extraction

**Decision**: Assemble the evidence bundle with `asyncio.gather` over four independent, **individually
guarded** retrievals (a failure in any one → empty for that source, logged, never fails the stage — FR-008):
- **corpus** → `corpus.search_reference(build_reference_query(evidence), k=cfg.corpus_k)`;
- **memory priors** → `memory.search_similar(EpisodeQuery(text=summary, entities=…), k=cfg.memory_k)`;
- **memory facts** → `memory.query_fact(entity, "reputation", as_of=None)` for a bounded set of entities;
- **intel** (only if `intel is not None` and `cfg.consult_intel`) → `intel.lookup(indicator, kind)` for a
  bounded set of indicator entities.

`build_reference_query` and `extract_entities` are **pure, deterministic** functions over the incident's
**already-redacted** `evidence.normalized_event` (technique ids/terms from rule fields + groups; entities
from ip/host/user/indicator fields) — mirroring `services/memory.py::_extract_entities`, but read-only and
**without a redactor** (the evidence is already redacted upstream).

**Rationale**: `asyncio.gather` is precisely the "where enrichment fans out" called out in Principle VII.
Per-source guards localize failure so partial context still yields a report. Deterministic extraction keeps
the entity set out of the LLM's hands (the LLM correlates; it does not discover entities), which keeps
retrieval reproducible and the `retrieval` eval meaningful. The entity set is naturally small (a handful of
fields), so the intel cap and `query_fact` count are bounded without extra config.

**Rejected**: LLM-driven entity extraction before retrieval (nondeterministic, an extra call, Principle IV
regression). Re-redacting in-stage (redundant — grounding already redacted the evidence; #5/#6 redacted the
retrieved content at write time). Importing `services/memory.py::_extract_entities` (would break the
agents→services layering rule; a small pure copy over the redacted dict is cleaner).

---

## ED5 — Temporal memory is read time-validly; enrichment needs no redactor and writes no incident state

**Decision**: Enrichment calls **only the read** methods of the `MemoryStore` — `search_similar` and
`query_fact(as_of=…)` — and uses the returned `FactState` **time-valid** flags (`is_current` /
`has_superseded`) in its correlation, so it reasons about *how a fact changed over time*, not merely "what is
true now." It calls **no** `write_episode` and performs **no** redaction in-stage: every text it sees is
already redacted (grounding redacted the evidence; the seed/intel/episode paths redacted the corpus content,
intel verdicts, and prior-incident summaries at write time). The only incidental write is internal to
`ThreatIntelClient.lookup` (its designed reputation accretion), which is off the incident-state path.

**Rationale**: Realizes Constitution VI at the *reasoning* layer (the Graphiti differentiator is only valuable
if a reader uses the time dimension — enrichment is that reader). Not injecting a redactor and not calling any
write method keeps the Principle III boundary crisp (enrichment cannot mutate incident state or knowledge
directly) and keeps the stage trivially mockable.

**Rejected**: Inject a redactor "to be safe" (redundant; the evidence/retrieved text is already redacted, and
an unused dependency muddies the boundary). Have enrichment write the incident episode (that is the worker's
terminal-state job, #6 — writing here would double-write and break single-writer discipline).

---

## ED6 — Worker DI wiring: register corpus/intel in the worker, order them before the supervisor

**Decision**: In `backend/worker.py`, register the (currently API-only) `CorpusProvider` and `IntelProvider`,
and order provider registration so `MemoryProvider`, `CorpusProvider`, `IntelProvider` all build **before**
`SupervisorProvider`. `SupervisorProvider.build` then reads `container.{llm, corpus, intel, memory}` and
builds the real enrichment handler **eagerly** — the same pattern it already uses for triage's `llm` — falling
back to the existing ADVANCE stub when no `LlmClient` is registered (preserving the LLM-less e2e path).
**`services/supervisor.py` and `repositories/incidents.py` are untouched.**

**Rationale**: The worker (not the API) runs the pipeline, so the enrichment retrievers must exist in the
worker container; the API already registers `CorpusProvider`. `IntelProvider.build` reads
`container.{cache, memory, observability}`, so memory/cache/observability must precede it — the chosen order
satisfies that. Mirroring triage's eager build keeps one wiring idiom. #7 already defined every `ENRICHING`
edge and already merges `evidence_patch`, so there is genuinely nothing to change in the supervisor or repo —
the biggest simplicity win of this component.

**Rejected**: Lazy/at-call-time container lookups inside the handler (order-independent but a second DI idiom
for no real benefit — by the time the worker's run-loop fires, all providers are built anyway; eager is
consistent with triage). Reorder via a new "stage-deps" provider (overengineering for a four-line registration
change).

---

## ED7 — Eval: extend the existing `retrieval` gate with an enrichment fixture set (no new gate)

**Decision**: Add a small **enrichment fixture set** (incident → expected prior incident + expected corpus
mapping) under `tests/fixtures/` and score enrichment's *assembled retrieval* through the existing
provider-independent **`retrieval`** gate (hit@k/MRR). No new gate is invented. The cross-correlation
*quality* judge (LLM-judge agreement on the correlation, per the brief's "hand-label a few, report judge
agreement") is left to **SPEC-eval (#13)**, which owns the full harness; the handler's correlation call is
validated functionally on **both** providers in the integration tier.

**Rationale**: The plan's enrichment eval *is* "does memory surface the right prior incidents? hit@k/MRR" —
deterministic store logic, provider-independent, already the `retrieval` gate's job (which #6 seeded and #5
extended with corpus fixtures). Reusing it keeps the eval surface coherent and honors the eval file's standing
"no new gate" note. Deferring the correlation LLM-judge to #13 avoids building a second judged gate here for a
v1 stage — the keep-it-simple steer.

**Rejected**: A brand-new `enrichment` gate (redundant with `retrieval` for the retrieval half; the judged
half belongs to #13's harness). Scoring the correlation judgment now with an ad-hoc LLM-judge (premature; #13
owns judge validation against hand-labels).

---

## Resolved unknowns

| Unknown | Resolution |
|---------|-----------|
| Agentic loop or one call? | One structured call after a deterministic fan-out — mirror triage (ED1). |
| How are the retrievers injected? | Closure factory `make_enrichment_handler(llm, corpus, memory, intel, cfg)`; each may be `None` (ED2). |
| What is the stage's output? | A validated `EnrichmentReport` (cross-correlation) → `decide_outcome` → ADVANCE/RESOLVED/ESCALATE (ED3). |
| How is the evidence bundle built? | `asyncio.gather` over corpus + memory(priors) + memory(facts) + optional intel, each guarded; deterministic entity/query extraction over redacted evidence (ED4). |
| Does enrichment need a redactor / does it write? | No redactor (text already redacted); read-only memory methods; no incident-state write (ED5). |
| How does it reach the corpus/intel/memory in the worker? | Register `CorpusProvider`/`IntelProvider` in the worker, order before `SupervisorProvider`; eager handler build (ED6). |
| Any change to the supervisor / repo / schema? | **None** — #7 already wired the `ENRICHING` transitions + `evidence_patch` merge; no migration (ED6). |
| New eval gate? | No — extend the existing `retrieval` gate with an enrichment fixture set; correlation LLM-judge is #13 (ED7). |
