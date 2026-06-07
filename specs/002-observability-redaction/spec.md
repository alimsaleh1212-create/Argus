# Feature Specification: Observability & Redaction (Cross-Cutting Foundation)

**Feature Branch**: `002-observability-redaction`

**Created**: 2026-06-07

**Status**: Draft

**Input**: User description: "depending on @docs/resources/SOAR_brief.md and @docs/resources/SOAR_Plan.md — the next spec" — Component #2 of the Sentinel build plan: `SPEC-observability` (the first cross-cutting concern, depends only on the platform foundation #1). Scope: tracing, structured logging, and redaction delivered as **one** cross-cutting capability so that every later component emits observable, correlated, secret-free output through a single shared seam.

## Overview

Sentinel is an AI-driven SOAR platform built spec-by-spec in dependency order. This component delivers the **observability spine and the redaction boundary** that every later component (ingestion, memory, the three agents, response/remediation, dashboard, evals) is required to route through. It exists because Sentinel reasons over **untrusted security payloads that carry both PII and live credentials**, and because the system takes consequential actions whose decisions must be reconstructable and auditable.

When this component is done: every log line the system emits is structured and tagged with the incident it belongs to; a single incident's journey through triage → enrichment → response is reconstructable as one trace tree where each agent step, tool call, and retrieval is a span carrying its token counts, model, latency, and the (redacted) evidence it considered; and **no sensitive value ever leaves the service in the clear** — not in a log, not in a trace, not in a prompt sent to a model, not in a stored snapshot, and not in any view the dashboard will later render. All of this is added **without meaningfully slowing the incident path**: the work on the critical path is cheap, and span/eval export happens off it.

This component contains **no incident/business logic** — it is the cross-cutting layer the rest of the system is wired through. It builds directly on the dependency-injection, lifespan-singleton, and registration seams established by the platform foundation (#1).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Nothing sensitive leaves the service in the clear (Priority: P1)

Every place where data exits the running service or is persisted/displayed — a log line, a trace span, a prompt sent to a model, a stored incident or report snapshot, and (later) a dashboard view — first passes through a single redaction boundary. A secret (API key, token, JWT, password, private key) or piece of PII present in the incoming alert text, in an agent's reasoning, or in a tool result never appears unredacted at any of those exits. Redaction is the composition of two strategies behind one interface: detection of PII and a deterministic scrubber for credentials/secrets.

**Why this priority**: Sentinel ingests Wazuh/packet-derived payloads that routinely carry **both** PII and live credentials, and it is attacker-influenced input. A single leaked credential in a log, trace, model prompt, or memory store is a security incident in itself and breaks the trust the whole project depends on. The project's redaction eval gate is committed from day one, so this capability is the precondition for safely emitting anything at all — every other observability output depends on it being correct first.

**Independent Test**: Feed payloads seeded with fake secrets and fake PII through each exit boundary (a log emit, a span emit, a model-prompt assembly, and a stored snapshot write); confirm every seeded sensitive value is redacted at every boundary and that the original raw value appears nowhere in the captured output.

**Acceptance Scenarios**:

1. **Given** an alert payload containing a credential-shaped value and a PII value, **When** any component logs, traces, builds a model prompt, or stores a snapshot derived from it, **Then** the credential and PII are redacted in that output and the raw values do not appear.
2. **Given** content nested inside a structured payload (values within nested objects/arrays), **When** it is redacted, **Then** sensitive values at any depth are redacted, the surrounding structure remains intact and parseable, and re-redacting already-redacted content changes nothing (idempotent).
3. **Given** the redactor cannot complete its work for some input, **When** that input would otherwise be emitted or stored, **Then** the system fails closed — the raw content is withheld rather than emitted unredacted.
4. **Given** a credential-shaped value the explicit pattern set does not recognize, **When** it is processed, **Then** a secondary high-entropy heuristic still flags it for redaction (defense in depth), and any residual risk is covered by the redaction eval.

---

### User Story 2 - Structured, incident-correlated logging (Priority: P1)

Every log line the system emits is structured (machine-parseable key/value fields rather than free-form text), carries a correlation identifier that ties it to the specific incident it belongs to, and has already passed through redaction. There is no code path that writes a raw, uncorrelated, or unstructured log line.

**Why this priority**: A supervisor-coordinated, multi-agent, asynchronous pipeline is undebuggable without structured, correlated logs — from day one a developer must be able to follow a single incident across the worker, three agents, and their tool calls. Correlation is what stitches those lines into one story; structure is what makes them queryable; redaction-before-emit is what keeps that safe. This is the minimum observability the pipeline needs to be operable, so it is co-P1 with redaction.

**Independent Test**: Drive one incident through the system and confirm that every log line it produced is structured, shares a single correlation identifier resolving to exactly that incident, and contains no raw sensitive values; confirm a log line emitted outside any incident context is still structured and does not crash for lack of a correlation id.

**Acceptance Scenarios**:

1. **Given** an incident is being processed, **When** any component emits a log line about it, **Then** the line is structured and carries the incident's correlation identifier.
2. **Given** the log output for one incident, **When** it is filtered by the correlation identifier, **Then** the complete set of that incident's log lines is returned and no other incident's lines are included.
3. **Given** a log line is emitted outside any incident context (e.g., startup), **When** it is written, **Then** it is still structured, is clearly marked as having no incident context, and is emitted without error.
4. **Given** any emitted log line, **When** it is inspected, **Then** it contains no raw secret or PII value.

---

### User Story 3 - An incident is a trace tree with per-step telemetry (Priority: P2)

Each unit of work inside an incident — every agent step, every tool call, every retrieval — is recorded as a span, and all the spans for one incident form a single trace tree rooted at that incident. Each span carries the telemetry needed to understand and cost the work: for model calls, the tokens consumed in and out, the model identifier, and the latency; for every step, its status and the (redacted) inputs, outputs, and evidence it considered. This is the structured record the dashboard's trace inspector and the project's KPI and eval views will later consume.

**Why this priority**: This is the observability backbone that makes the system's intelligence visible and auditable — the trace tree is exactly what the dashboard drill-down and the per-agent token/latency telemetry render, and what eval and cost accounting read. It is essential to Tier-1, but the pipeline can run and be debugged from structured logs (Story 2) before the full trace tree exists, so it sits just behind the two P1 safety/operability stories.

**Independent Test**: Process one incident end to end and confirm a single trace tree is produced whose spans correspond to each agent step, tool call, and retrieval, with no orphaned spans; confirm each model-call span reports tokens-in, tokens-out, model, and latency, and that every span's recorded inputs/outputs are redacted.

**Acceptance Scenarios**:

1. **Given** an incident flows through triage → enrichment → response, **When** its trace is inspected, **Then** there is one trace tree for the incident containing a span per agent step / tool call / retrieval, correctly nested, with no orphaned or duplicated spans.
2. **Given** any model call within the incident, **When** its span is inspected, **Then** it reports tokens-in, tokens-out, the model identifier, and the call latency (and is marked "unknown" only when the provider does not return usage).
3. **Given** any span that recorded inputs, outputs, or evidence, **When** it is inspected, **Then** those values are redacted and oversized values are bounded by a documented truncation policy.
4. **Given** the incident's spans, **When** per-incident telemetry is aggregated, **Then** total tokens and end-to-end latency are derivable from the trace.

---

### User Story 4 - Observability that does not slow the incident path (Priority: P2)

Adding observability does not meaningfully slow how fast Sentinel dispositions an incident. The work that must happen on the synchronous incident path — creating spans, accounting tokens, redacting, emitting logs — is cheap, and the heavier work of exporting spans and writing eval/telemetry records happens asynchronously, off that path. The overhead is measured against the incident disposition-time budget and re-verified before the Tier-1 freeze.

**Why this priority**: A SOAR is judged on time-to-disposition; observability that taxes every incident would undermine the product it is meant to make visible. Decoupling export from the critical path is the design guarantee. It is P2 because it refines behaviour the P1/early-P2 stories introduce, but it is a committed non-functional standard, not optional polish.

**Independent Test**: Measure per-incident disposition time with observability fully enabled versus a baseline with it minimized, and confirm the synchronous overhead stays within the documented budget; confirm that when the span/telemetry export destination is slow or unreachable, incident processing latency and success are unaffected.

**Acceptance Scenarios**:

1. **Given** observability is fully enabled, **When** an incident is processed, **Then** the added synchronous overhead stays within the documented share of the disposition-time budget.
2. **Given** the span/telemetry export destination is unreachable or slow, **When** incidents are processed, **Then** they continue to complete successfully and on time; export is retried or buffered off the critical path and its failure never fails an incident.
3. **Given** the Tier-1 freeze checkpoint, **When** the overhead is re-measured, **Then** it remains within the documented budget.

---

### Edge Cases

- **Redactor unavailable or erroring**: the system fails closed at the affected exit boundary (withholds raw content); it never falls back to emitting unredacted data. (Distinct from export failure below, which is best-effort.)
- **Export destination down/slow**: span and eval/telemetry export is best-effort and off the critical path — buffered or dropped with a counter, never blocking or failing an incident; redaction, by contrast, is mandatory and blocking.
- **Value that is both PII and an operational signal** (e.g., an IP address or hostname the enrichment agent must correlate on): retained raw in the operational object and memory store for correlation (FR-006b) but redacted whenever it crosses an output boundary — the correlation need never forces raw exposure in a log, trace, prompt, snapshot, or dashboard view.
- **Oversized payload**: span attributes and log fields are bounded by a documented truncation policy so a single large alert cannot exhaust trace/log storage; truncation never re-exposes redacted content.
- **Nested / encoded sensitive data**: redaction traverses nested structures; a documented stance covers commonly-encoded forms (the residual risk for arbitrary encodings is owned by the redaction eval).
- **Missing token usage from the model provider**: the span records what is available and marks the rest "unknown" rather than fabricating a count.
- **Log/span emitted with no incident context** (startup, background task): still structured, clearly marked as having no correlation, emitted without error.
- **Shutdown with buffered spans/logs pending**: the observability seam flushes buffered telemetry on clean shutdown so in-flight records are not silently lost.
- **Double redaction**: applying redaction to already-redacted content is idempotent and does not corrupt the redaction markers.

## Requirements *(mandatory)*

### Functional Requirements

**Redaction (the safety boundary)**

- **FR-001**: The system MUST expose a single redaction interface that composes two strategies — detection of PII and a deterministic scrubber for credentials/secrets (e.g., API keys, tokens, JWTs, passwords, private keys) — so that callers redact through one seam rather than ad hoc.
- **FR-002**: Redaction MUST be applied at every boundary where data leaves the service or is persisted/displayed: log emission, trace/span emission, model-prompt assembly, stored incident/report snapshots, and dashboard-facing views.
- **FR-003**: Redaction MUST fail closed — if redaction cannot complete for a given input, the raw content MUST NOT be emitted or stored.
- **FR-004**: Redaction MUST traverse nested/structured content (values at any depth), preserve the surrounding structure so output stays parseable, and be idempotent (re-redacting redacted content is a no-op).
- **FR-005**: The credential scrubber MUST combine an explicit pattern set with a high-entropy heuristic so that credential-shaped values not matched by an explicit pattern are still flagged (defense in depth).
- **FR-006**: The set of sensitive value classes, the boundaries at which each class is redacted, and the handling of values that are simultaneously sensitive and operationally needed for correlation MUST be governed by a centralized, declarative redaction policy rather than scattered across call sites.
- **FR-006a**: **Credentials/secrets MUST be scrubbed everywhere** — at every output boundary **and** in the operational incident object and the temporal memory store — because no downstream stage has a legitimate use for a raw credential.
- **FR-006b**: **PII MUST be redacted at the output boundaries** (logs, traces, model prompts, stored snapshots, dashboard views), while the operational incident object and the temporal memory store **MAY retain raw operational identifiers** (e.g., IP addresses, hostnames, usernames) so that correlation and enrichment continue to function; such identifiers MUST still be redacted whenever they cross an output boundary.
- **FR-007**: The redaction capability MUST be inspectable in tests such that a test can assert a given sensitive value never appears in captured output at any boundary.

**Structured, correlated logging**

- **FR-008**: All logs MUST be emitted as structured, machine-parseable records (key/value fields), not free-form text.
- **FR-009**: Every log record produced in the context of an incident MUST carry a correlation identifier that resolves to exactly that incident; the correlation identifier MUST be consistent across the worker and all three agents for a single incident.
- **FR-010**: Every log record MUST pass through redaction before emission; there MUST be no logging path that bypasses redaction.
- **FR-011**: Logs emitted outside any incident context MUST still be structured and MUST clearly indicate the absence of a correlation context without erroring.

**Tracing & telemetry**

- **FR-012**: Each agent step, tool call, and retrieval MUST be recorded as a span, and all spans for a single incident MUST form one correctly-nested trace tree rooted at that incident, with no orphaned spans.
- **FR-013**: Each model-call span MUST record tokens-in, tokens-out, the model identifier, and the call latency; counts that the provider does not supply MUST be marked "unknown" rather than fabricated.
- **FR-014**: Spans MUST record the inputs, outputs, and evidence considered at each step in **redacted** form.
- **FR-015**: Span/trace export and any eval/telemetry persistence MUST occur asynchronously, off the synchronous incident-processing path.
- **FR-016**: Per-incident telemetry (total tokens, end-to-end latency, per-step status) MUST be derivable from the trace in a stable shape that later components (dashboard trace inspector, KPI views, eval) can consume.
- **FR-017**: Span attribute and log field sizes MUST be bounded by a documented truncation policy; truncation MUST NOT re-expose redacted content.

**The shared seam (how the rest of the system is wired through this)**

- **FR-018**: The system MUST provide a single observability seam (logging, tracing, and redaction together) obtained through the platform's dependency-injection mechanism; components MUST use this seam and MUST NOT emit logs, spans, or stored/displayed content through any path that bypasses it.
- **FR-019**: The observability seam MUST be initialized once as a startup singleton via the platform's registration seam and MUST flush buffered telemetry on clean shutdown.
- **FR-020**: The observability seam MUST be substitutable with test doubles through the injection mechanism without modifying consuming components.

**Performance / non-functional**

- **FR-021**: The observability work performed on the synchronous incident path (span creation, token accounting, redaction, log emission) MUST add only negligible overhead relative to the incident disposition-time budget, with the heavier export/persistence work kept off that path.
- **FR-022**: The synchronous observability overhead MUST be measurable, and MUST be re-verified at the Tier-1 freeze checkpoint to confirm it remains within budget.

### Key Entities *(include if feature involves data)*

- **Redactor**: the single interface that redacts content; composes a PII-detection strategy and a deterministic credential/secret scrubber; applied at every exit boundary; fails closed.
- **Redaction policy**: the centralized, declarative definition of sensitive value classes (credential/secret, PII, operational identifier), which boundaries redact which classes, and how dual-purpose identifiers are handled.
- **Log record**: a structured key/value event carrying a severity, a correlation identifier (or an explicit no-context marker), and only redacted content.
- **Span**: a recorded unit of work (an agent step, tool call, or retrieval) with a status, redacted inputs/outputs/evidence, and — for model calls — tokens-in, tokens-out, model identifier, and latency.
- **Trace tree**: the set of all spans for one incident, correctly nested and rooted at the incident; the per-incident record consumed by the dashboard and eval.
- **Correlation identifier**: the value that ties every log line and span of one incident together and resolves to exactly that incident.
- **Telemetry record**: the per-step and per-incident token and latency metrics derived from spans, in a stable, consumable shape.
- **Observability seam**: the injected, startup-singleton bundle of logger + tracer + redactor that every component uses and that no component bypasses.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Across the redaction check, **0** seeded fake secrets and **0** seeded fake PII values ever appear unredacted in any log line, trace span, model prompt, stored snapshot, or dashboard-facing view (100% redaction at every boundary).
- **SC-002**: 100% of log lines emitted for an incident are structured and carry a correlation identifier that resolves to exactly one incident; filtering by that identifier returns that incident's complete log set and no other incident's lines.
- **SC-003**: For 100% of processed incidents, the full triage → enrichment → response journey is reconstructable as a single trace tree with no orphaned spans.
- **SC-004**: 100% of model-call spans report tokens-in, tokens-out, model identifier, and latency, with "unknown" recorded only when the provider omits usage.
- **SC-005**: Synchronous observability overhead adds no more than 5% (p95) to per-incident disposition time, and 100% of span/eval export happens off the synchronous path. *(Default target; may be tightened during planning.)*
- **SC-006**: Under an induced redaction failure, **0** incidents emit raw sensitive content; under an unreachable/slow export destination, **0** incidents fail or exceed their time budget because of it (fail-closed on redaction, best-effort on export).
- **SC-007**: A developer can localize a failed incident to the specific step that failed using only its structured logs and trace (correlation id + per-step status + redacted I/O are present), without reproducing the incident.
- **SC-008**: At the Tier-1 freeze checkpoint, the re-measured synchronous observability overhead is still within the documented budget (SC-005).

## Assumptions

- **One cross-cutting component, by design**: tracing, structured logging, and redaction are specified together because they change for the same reasons (every exit boundary) and are consumed by every other component; splitting them would fragment the "nothing leaves raw / everything is correlated" guarantee.
- **Builds on the platform foundation (#1)**: this component consumes the dependency-injection mechanism, lifespan-singleton lifecycle, the registration seam for startup singletons, typed configuration, and the object store (for stored snapshots / eval reports) delivered by `SPEC-platform-infra`. The redaction, logging, and tracing seams reserved as stubs in #1 are the seams filled here.
- **Mandated stack (pre-decided project constraints, recorded here rather than as requirements)**: the brief and `DECISIONS.md` fix the implementing technologies — structured logging via `structlog`; tracing where each agent step/tool/retrieval is a span and an incident is a trace tree, with export off the synchronous path; redaction as **Microsoft Presidio (PII) + a deterministic secret/credential scrubber (regex + entropy)** behind one `Redactor` interface, in-process by default, applied at the logs / model-prompts / stored-snapshots boundaries. These are honored by the implementation, but the requirements above are stated as capability outcomes so they remain verifiable independently of any specific tool.
- **Eval gates are owned by `SPEC-eval` (#13)**: the committed redaction and red-team golden sets and the CI gates that enforce them live in the eval spec; this component provides the redaction/observability capability those gates verify and seeds an initial redaction check so the gate can be wired from day one.
- **Dashboard consumes, does not define here**: the trace inspector, per-agent token/latency telemetry, and KPI views (`SPEC-dashboard` #12) consume the trace-tree and telemetry shapes defined here; building those views is out of scope.
- **Memory-write interaction (#6, decided)**: per FR-006a/FR-006b, the temporal memory store **never** holds raw credentials/secrets (scrubbed before write) but **may** hold raw operational identifiers (IPs, hostnames, usernames) so `SPEC-enrichment-agent` (#9) can correlate; those identifiers are redacted at any output boundary. This spec defines the policy; `SPEC-memory` and `SPEC-enrichment-agent` honor it.
- **100% trace capture for v1**: given replayed/demo alert volume and the audit/explainability goal, every incident is traced (no sampling); sampling is a later optimization, not v1 scope.
- **Single organization / local demo deployment**: consistent with #1 — no multi-tenant log/trace isolation and no remote, clustered, or multi-host telemetry backend is in scope for v1.
- **Default targets where the brief gave none**: the ≤5% p95 synchronous-overhead budget (SC-005) is a reasonable default derived from "negligible, measured against the disposition-time budget" and can be tightened during planning without changing the requirements.

## Dependencies

- **Depends on #1 (`SPEC-platform-infra`)**: dependency injection, lifespan singletons, the startup-singleton registration seam, typed configuration, and the object store. This component fills the redaction/logging/tracing seams that #1 reserved as stubs.
- **Consumed by every later component**: ingestion (#4), memory (#6), the incident state machine (#7), the triage/enrichment/response agents (#8/#9/#10), safety (#11), the dashboard (#12), and eval (#13) all emit through this seam — the "everything → observability" contract: no component logs, traces, prompts, or stores raw, and the redaction boundary is honored everywhere.
