# Feature Specification: Enrichment Agent

**Feature Branch**: `009-enrichment-agent`

**Created**: 2026-06-10

**Status**: Draft

**Input**: User description: "Enrichment agent — the second pipeline stage, reached only for incidents triage advanced as real-and-actionable. Retrieval-only: it assembles the evidence picture from both directions — external threat intel and the seeded reference corpus, plus internal history from temporal memory — and its core deliverable is the cross-correlation between them. No action tools. Reuses the corpus retriever (#5), the memory store (#6), and the LLM adapter (#3); keep it simple, don't overengineer."

## Context & Boundary *(why this component, where it sits)*

The deterministic supervisor (Component #7) drives an incident through a fixed state machine. Triage (Component #8) is now real; the **enrichment** stage handler is still a stub that blindly advances every incident to response. This component replaces that stub with a real, retrieval-backed cross-correlation step. It is the **second point where reasoning enters the pipeline**, and the first that **retrieves** context from outside the incident itself.

Enrichment runs **only on incidents triage marked `advance`** (real-and-actionable). It does not re-litigate the real/noise call triage already made over the supplied evidence. Instead it answers the question triage could not, because triage reasons only over evidence already on the incident: **"now that we believe this is real, what is the full picture — what does the outside world say about these indicators, what have we seen about these entities before, and do those two directions, correlated, change the assessment?"**

The single capability that justifies an agent here is **cross-correlation**: neither an external signal ("this IP is on a threat list") nor an internal signal ("this host moved 24× its normal volume last night") is independently actionable, but **together** they are an incident. Assembling each side is retrieval; fusing them into one assessment with a plain-language rationale is the judgment. Enrichment holds **retrieval tools only** — it can read intel, the reference corpus, and temporal memory, but it has **no action tools** and writes **no** incident state (the supervisor remains the single writer).

This component **consumes existing contracts and adds no new service**: the `CorpusRetriever` and on-demand intel from the knowledge corpus (Component #5), the `MemoryStore` (`search_similar` / `query_fact`) from temporal memory (Component #6), and the LLM adapter (Component #3). It mirrors the triage stage's shape — a bounded retrieval fan-out followed by **one** structured-output reasoning call, fail-closed.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - A real incident is enriched with cross-correlated context and advances (Priority: P1)

A real-and-actionable incident reaches enrichment. The system fans out retrieval in both directions — external (reference corpus mappings for the technique, and an intel verdict on the incident's indicators when available) and internal (similar prior incidents and the time-valid reputation/role of the incident's entities from temporal memory) — then makes one reasoning call that **correlates the two directions** into a structured enrichment report: a headline cross-correlation, the external and internal findings it rests on, and an assessment with a plain-language, evidence-citing rationale. The incident then **advances to response** carrying that report. This is the MVP — it turns the enrichment stub into the system's core "assemble both sides and correlate" capability.

**Why this priority**: Without this, every advanced incident reaches the response stage with only triage's narrow, evidence-only view. This story delivers the distinguishing value of the pipeline: a fused external+internal picture that the response stage and the dashboard can act on and display.

**Independent Test**: Feed a real-and-actionable incident whose indicators have a corpus mapping and a prior memory episode through the supervisor with enrichment enabled. Verify the incident transitions to `responding` with an "advance" outcome, and that the recorded enrichment report contains at least one external finding, at least one internal finding, and a correlation summary that references both. No action is executed; enrichment writes no state itself.

**Acceptance Scenarios**:

1. **Given** a real-and-actionable incident whose entity has a malicious-reputation fact in temporal memory **and** a matching technique entry in the reference corpus, **When** the supervisor runs enrichment, **Then** enrichment returns an "advance" outcome, the incident moves to `responding`, and the recorded report includes the external finding, the internal finding, and a correlation summary citing both.
2. **Given** an incident with no corpus match, no prior memory, and no intel verdict available, **When** enrichment runs, **Then** an empty retrieval result is treated as normal (not an error), and enrichment still produces a report (its correlation summary may state "no corroborating external/internal context found") and advances or escalates per its assessment.
3. **Given** a completed enrichment report, **When** the supervisor persists the transition, **Then** the report is written into the incident's evidence by the supervisor (single writer) so the response stage and dashboard can read it.

---

### User Story 2 - Cross-correlation can downgrade or escalate, not just advance (Priority: P2)

When the assembled, correlated picture clearly **exonerates** the incident (e.g., the indicator is now confirmed benign by current intel and the host's history shows a known-good pattern), enrichment **auto-resolves** it as noise rather than passing a non-threat to the response stage. When the two directions **conflict** in a way enrichment cannot confidently settle (e.g., current intel says benign but memory shows the entity was repeatedly malicious), enrichment **escalates to a human** rather than guessing. The configured confidence threshold governs which of advance / resolve / escalate is chosen, exactly as in triage.

**Why this priority**: This is the judgment that makes enrichment more than a context-fetcher. The brief's motivating case for an agent at this stage — conflicting evidence that needs a judgment call, not a keyed lookup — lives here. It also protects the response stage from wasting an action (or an approval interrupt) on something the fused picture shows is benign.

**Independent Test**: Run one incident whose correlated evidence clearly exonerates it and verify it transitions to `resolved` with the enrichment auto-resolved disposition; run one incident with directly conflicting external/internal signals and a forced sub-threshold confidence and verify it transitions to `escalated` with the enrichment escalated disposition. Neither reaches `responding`.

**Acceptance Scenarios**:

1. **Given** an incident whose correlated evidence confidently indicates benign activity, **When** enrichment runs, **Then** it returns a "resolved" outcome with the auto-resolved-by-enrichment disposition and the incident does **not** proceed to response.
2. **Given** an incident with conflicting external and internal signals and an assessment confidence below the configured threshold, **When** enrichment runs, **Then** it returns an "escalate" outcome, the incident moves to `escalated`, and the recorded rationale states the conflict that prevented a confident call.
3. **Given** the confidence threshold is changed in configuration, **When** enrichment runs on the same incident, **Then** the advance/resolve/escalate decision shifts accordingly — the threshold is configuration-backed, never hardcoded in reasoning logic.

---

### User Story 3 - Enrichment degrades gracefully and stays bounded (Priority: P3)

Retrieval backends are best-effort: when temporal memory is unavailable, the corpus is empty, or the optional intel lookup times out or is disabled, enrichment **proceeds with whatever context it could gather** rather than failing the incident — a memory or intel outage never blocks disposition. When the reasoning provider itself fails or returns malformed output, enrichment **fails closed**: the incident is retried within policy and otherwise escalated, and the worker keeps processing. Enrichment makes **at most one** reasoning call per incident, fans out its retrieval concurrently, and reports the tokens it consumed so the supervisor's per-incident cap holds.

**Why this priority**: Robustness and cost-control. It protects the "worker never crashes," "hard step/token cap," and "memory is never a single point of failure" guarantees, but the system is demonstrable without the failure paths, so it ranks below the core correlation and judgment behaviors.

**Independent Test**: In separate runs, (a) point enrichment at an unavailable memory store and a disabled intel client and verify it still produces a report from corpus-only context and advances/escalates; (b) inject a reasoning-provider timeout and a malformed reasoning response and verify each escalates the incident (after policy retries for the transient case) without crashing the worker. Verify the reported token count is non-zero and feeds the supervisor's cap.

**Acceptance Scenarios**:

1. **Given** the temporal memory store is unavailable and the intel client is disabled, **When** enrichment runs, **Then** it completes using the remaining context (e.g. reference corpus only), the incident is not failed by the outage, and the report notes the missing internal/external context.
2. **Given** a transient reasoning-provider failure, **When** enrichment runs, **Then** it surfaces a retryable structured error, the supervisor retries within its policy, and on continued failure the incident is escalated — the worker does not crash.
3. **Given** the reasoning provider returns output that fails validation, **When** enrichment runs, **Then** the incident is escalated (fail-closed), never advanced or auto-resolved on unvalidated output; and across a normal run enrichment makes exactly one reasoning call and reports a non-zero token count.

---

### Edge Cases

- **No extractable entities** (the normalized event carries no IP/host/user/indicator): enrichment skips entity-keyed memory/intel lookups, still performs corpus/term retrieval, and reasons over whatever was assembled — an empty entity set is normal, not an error.
- **Intel returns `unknown`** (source outage, timeout, or no record): `unknown` is a valid verdict that enrichment reasons over as "no external signal," never a failure that blocks the incident (the intel client is fail-closed and off the critical path).
- **Memory returns a superseded fact vs. a current one**: enrichment must use the **time-valid** state (e.g. "benign as of the seed, malicious as of a later feed update") rather than collapsing to "what is true now," so correlation reflects how the fact changed over time.
- **Retrieved context (corpus / intel / memory text) contains an injection attempt**: enrichment has no action tools and writes no state, so the worst case is a wrong assessment, not an action — all retrieved and feed-derived text is treated as untrusted data. Dedicated injection rails are out of scope here (Component #11).
- **A reasoning call whose usage would exceed the per-incident token cap**: the supervisor's cap check catches it and escalates; enrichment does not attempt to self-enforce the global cap.
- **Enrichment's assessment disagrees with triage's verdict**: enrichment records its own assessment and may resolve/escalate accordingly, but it does not rewrite triage's recorded judgment or the canonical ingested severity (provenance preserved; supervisor remains single writer).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST accept a real-and-actionable incident (the only incidents the supervisor routes to enrichment) and produce an enrichment report by retrieving context in **both directions** and correlating it — it MUST NOT re-decide the real/noise question triage already settled over the supplied evidence.
- **FR-002**: Enrichment MUST retrieve **external** context: the seeded reference corpus (technique→mitigation mappings and runbooks, via technique IDs and/or terms drawn from the incident) through the `CorpusRetriever`, and, when enabled and applicable, an on-demand intel **verdict** for the incident's indicators.
- **FR-003**: Enrichment MUST retrieve **internal** context from temporal memory: similar prior incidents (`search_similar`) and the **time-valid** reputation/role/state of the incident's entities (`query_fact`), using the time dimension rather than only the currently-true value.
- **FR-004**: Enrichment's core output MUST be the **cross-correlation** between the external and internal context — a structured report whose headline is how the two directions relate, supported by the specific external and internal findings it rests on and a plain-language rationale that cites at least one item from each direction it actually used.
- **FR-005**: Enrichment MUST map its report to exactly one supervisor stage outcome: **advance** (correlated real incident → response), **resolved** (correlation confidently exonerates → auto-close as noise), or **escalate** (conflicting or low-confidence → human). It MUST NOT emit any other outcome.
- **FR-006**: Enrichment MUST escalate (abstain) rather than advance or auto-resolve whenever its assessment confidence is below the configured threshold. The threshold(s) MUST be configuration-backed and changeable without modifying reasoning logic.
- **FR-007**: Enrichment MUST be **retrieval-only**: it MUST hold no action tools, MUST NOT execute or propose any remediation, and MUST NOT write incident status, disposition, or any persistent incident state. The supervisor remains the single writer of all incident state.
- **FR-008**: Enrichment MUST treat retrieval backends as **best-effort**: an unavailable memory store, an empty corpus, a disabled intel client, or an intel `unknown`/timeout MUST degrade to "context not available" and MUST NOT fail the incident or block disposition. Empty retrieval results are normal input, not errors.
- **FR-009**: Enrichment's structured reasoning output MUST be validated. Any malformed, missing, or out-of-vocabulary output MUST be treated as a failure that escalates the incident (fail-closed) — never silently resolved or advanced on unvalidated output.
- **FR-010**: A transient reasoning-provider failure MUST surface as a structured, retryable error so the supervisor retries within its policy and otherwise escalates. Enrichment MUST NOT crash the worker under any failure (retrieval or reasoning).
- **FR-011**: Enrichment MUST make **at most one** reasoning call per incident (no internal multi-step agent loop), MUST fan out its independent retrievals concurrently, and MUST report the reasoning tokens it consumed so the supervisor's per-incident step/token cap is enforced.
- **FR-012**: The enrichment report MUST be persisted into the incident's evidence by the supervisor (single writer) as part of the same transition, so the response stage and dashboard can read and display the correlated picture, the cited external/internal findings, and the assessment.
- **FR-013**: Enrichment MUST invoke the reasoning provider only through the shared LLM adapter (Component #3), and reach intel/corpus/memory only through their existing contracts (Components #5/#6) — never a provider or backend SDK directly. All retrieved and feed-derived text is treated as untrusted data, and any spans/logs it produces MUST contain no unredacted sensitive values (Component #2 redaction boundary).
- **FR-014**: Enrichment MUST honor the adaptive-depth contract: only incidents it marks **advance** proceed to the response stage; an incident it marks **resolved** or **escalate** stops here with no response-stage call.
- **FR-015**: Enrichment's retrieval quality MUST be evaluated against the committed **retrieval** eval gate (hit@k / MRR) — extended with an enrichment fixture set covering "does memory surface the right prior incident and does the corpus return the right mapping for this incident" — runnable identically on both supported reasoning providers. (Extends the existing retrieval gate; no new gate.)

### Key Entities *(include if feature involves data)*

- **Enrichment Report**: The structured cross-correlation enrichment produces for one incident — the headline correlation summary, the supporting external findings (corpus mappings, intel verdict) and internal findings (similar prior incidents, time-valid entity facts), an assessment (e.g. confirmed-threat / benign / inconclusive) with a confidence value, and a plain-language rationale citing the evidence used from each direction. It maps to a stage outcome and is recorded into the incident's evidence.
- **Stage Result** *(contract owned by Component #7)*: What enrichment returns to the supervisor — the chosen outcome (advance / resolved / escalate), the tokens consumed, and the evidence patch carrying the Enrichment Report. Enrichment produces it; the supervisor acts on it and persists it.
- **Retrieval Inputs** *(contracts owned by Components #5/#6)*: The read-only retrieval surfaces enrichment consumes — `CorpusRetriever` (reference corpus), the on-demand intel verdict (`IntelVerdict`), and the `MemoryStore` (`search_similar` → prior incidents, `query_fact` → time-valid entity facts). Enrichment reads them; it does not own or write them.
- **Enrichment Configuration**: The configuration-backed knobs governing enrichment — the confidence threshold(s) for advance/resolve vs. escalate, retrieval breadth (k for corpus and memory), whether the optional intel lookup is consulted, and reasoning-provider/prompt selection. Required values fail at startup if absent.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of incidents enrichment advances carry a recorded enrichment report containing a correlation summary plus at least one cited finding from each direction it used (external and internal) when such context exists — the cross-correlation is always present and inspectable, never an empty pass-through.
- **SC-002**: On the committed enrichment fixture set, retrieval meets or exceeds the committed hit@k / MRR thresholds (the right prior incident and the right corpus mapping are surfaced), measured identically on both supported reasoning providers, gating CI.
- **SC-003**: 100% of incidents whose correlated assessment confidence is below the configured threshold are escalated for human review rather than advanced or auto-resolved — the system never emits a confident disposition it does not hold; and incidents the correlation confidently exonerates are auto-resolved rather than passed to the response stage.
- **SC-004**: Enrichment never executes or proposes a remediation action and never writes incident state directly; an incident whose retrieved context carries injected instructions still produces only one of the three allowed outcomes — verified structurally (no action tools injected) and in tests.
- **SC-005**: Across the degradation suite (memory unavailable, corpus empty, intel disabled / `unknown` / timeout, reasoning-provider timeout, malformed reasoning output), every affected incident either completes from partial context or is escalated (after policy retries where applicable), with zero worker crashes and zero failure-driven auto-resolves.
- **SC-006**: Enrichment consumes at most one reasoning call per incident, fans out its retrievals concurrently, and reports its token usage, so the supervisor's per-incident token/step cap holds — no incident exceeds the configured cap because of enrichment.

## Assumptions

- **Only real-and-actionable incidents reach enrichment.** The supervisor routes only triage-`advance` incidents to enrichment; the deterministic fast-path and triage already disposed of obvious criticals, obvious noise, and uncertain cases. Enrichment assumes this routing rather than re-implementing real/noise classification.
- **The contracts it consumes already exist.** The `CorpusRetriever` and on-demand intel (Component #5) and the `MemoryStore` (Component #6) are built and wired; enrichment adds **no new external service**, only a new stage handler and the pure report type, injected the same closure-factory way as triage. When a backend is degraded (e.g. `NullMemory`, empty corpus, disabled intel), enrichment still runs on partial context.
- **One reasoning call per incident is sufficient** for cross-correlation in v1 — a bounded retrieval fan-out followed by a single structured-output reasoning call, mirroring triage. No agentic multi-step / tool-calling loop is needed; structured output is reliable enough that malformed responses are the fail-closed exception.
- **Redaction (Component #2) is applied** to stored evidence and to anything enrichment logs or traces; enrichment builds its reasoning input from already-redacted evidence plus retrieved context and emits no raw sensitive values.
- **Injection/jailbreak rails are deferred to Component #11.** v1 enrichment relies on its structural boundary (no action tools, no write capability) as the safety net and treats all retrieved, intel-, and feed-derived text as untrusted data — the same posture as alert text.
- **Entities for keyed memory/intel lookups are extracted deterministically** from the incident's normalized event fields (addresses, hosts, users, indicators); enrichment does not rely on the LLM to discover entities before retrieving.
- **Enrichment reads the incident slice and returns a result; the supervisor persists everything**, including merging enrichment's evidence patch into the incident — the same single-writer extension already used for triage.
- **The intel result that informs enrichment is also written back to temporal memory as a time-stamped fact by Component #5's intel path**, so an indicator's history accrues over time; enrichment is a *reader* of that history, not the writer of the episode (terminal-state episode writing is owned by Component #6's worker step).

## Out of Scope

- Building or owning the reference corpus, the intel client, or the temporal memory store (Components #5 and #6) — enrichment only consumes their existing contracts.
- Live / streaming knowledge ingestion (standing scheduled feeds) — roadmap v2/v3, out of v1.
- Any remediation, playbook selection, action execution, or approval interrupt (response, Component #10).
- The §v2c feedback loop that lets memory tune future triage/severity scoring — that write-back lives in Components #6 and #7/#10, not in this retrieval-only stage.
- Injection/jailbreak guardrails and the red-team probe set (safety, Component #11).
- Mutating the canonical incident severity or triage's recorded judgment, and any change to the supervisor's routing/transition table beyond persisting enrichment's evidence patch.
- Multi-step or tool-calling agent behavior inside enrichment; v1 is a bounded retrieval fan-out plus a single reasoning call.
- Dashboard rendering of the enrichment report (Component #12) — enrichment only produces the data the dashboard later displays.
