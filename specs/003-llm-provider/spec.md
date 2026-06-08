# Feature Specification: Provider-Agnostic LLM Adapter (Cross-Cutting Foundation)

**Feature Branch**: `003-llm-provider`

**Created**: 2026-06-08

**Status**: Draft

**Input**: User description: "depending on @docs/resources/SOAR_brief.md and @docs/resources/SOAR_Plan.md — Component #3, SPEC-llm-provider: a provider-agnostic async LLM adapter behind the #1-reserved llm.py seam; env-selected primary provider with automatic fallback to a secondary; token-usage reporting wired into #2's span telemetry; evals must pass on both providers."

## Overview

Sentinel is an AI-driven SOAR platform built spec-by-spec in dependency order. This component delivers
the **one seam through which every model call in the system is made**. The three reasoning agents
(triage → enrichment → response), the memory layer's reference reasoning, and the eval harness all
talk to a large language model; this component is the single, provider-agnostic adapter they call so
that **no component is coupled to a specific model vendor**, the operator can **choose the primary
provider by configuration**, the system **automatically fails over to a secondary** when the primary
is unavailable, and **every model call is observable and costed** through the spine built in #2.

When this component is done: any component obtains an LLM client through the platform's dependency
injection and calls it with a **uniform request/response shape** regardless of which vendor serves it;
the **primary provider is selected by configuration** with **no code change** required to switch it;
a **transient failure of the active provider transparently fails over** to the configured secondary so
that an incident still reaches a disposition; **every call records its provider, model, token counts,
and latency** through the observability seam, and its prompts and completions pass through redaction at
the model-prompt boundary before anything is logged, traced, or stored; and the capability is
**exercisable against each configured provider independently**, so the committed "evals pass on both
providers" gate can be enforced from day one.

This component contains **no incident/business logic and defines no prompts or agent behavior** — it is
the cross-cutting layer the reasoning components are wired through. It fills the **`llm.py` seam
reserved as a stub in #1** and builds on the dependency-injection, lifespan-singleton, typed-config,
and secrets seams from #1 and the **observability/redaction seam from #2**.

## Clarifications

### Session 2026-06-08

- Q: Under a sustained primary-provider outage, how should fallback routing recover? → A: Stateless per-call — every call begins at the configured primary and fails over to the secondary on transient failure; v1 keeps no cross-call provider-health state (no circuit-breaker; that is a later optimization).
- Q: When a call needing structured output / tool-calling fails over to the weaker local model, what is the result contract? → A: Same contract, fail-closed — the failover result MUST validate against the caller's required output shape/tools or surface a structured error; never a silently degraded result (a capability-insufficient fallback becomes a step failure the supervisor escalates).
- Q: Should the service block on provider availability at startup? → A: At-least-one-reachable gate — configuration/credential errors fail boot (FR-015), while the service reports **not-ready** via `/ready` unless ≥1 configured provider is reachable (becoming ready once one is); runtime outages are absorbed by per-call fallback.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - One seam every component calls the model through (Priority: P1) 🎯 MVP

Every component that needs a model call obtains an LLM client through the platform's dependency
injection and calls it with a single, uniform request shape (the conversation, an optional system
instruction, generation parameters, and any tools the caller is permitted) and receives a single,
uniform response shape (the produced content, the usage counts, the model and provider that served it,
and why generation stopped). No component constructs a vendor client or imports a vendor SDK itself.

**Why this priority**: Nothing downstream can be built until the model seam exists — the supervisor and
all three agents (#7–#10) and the eval harness (#13) reason through it. Routing every call through one
adapter is also what makes provider-agnosticism, fallback, token accounting, and the structural
tool-gating boundary possible at all: they are properties of the single seam, so the seam is the
precondition for every later model-using component. This is the MVP.

**Independent Test**: With the model provider mocked, a consumer obtains the client through dependency
injection, issues a uniform request, and receives a uniform response carrying content, usage, and the
serving model/provider; a check confirms no component outside the seam references a vendor SDK.

**Acceptance Scenarios**:

1. **Given** a component that needs a model call, **When** it obtains the LLM client through dependency
   injection and issues a uniform request, **Then** it receives a uniform response independent of which
   vendor served it, and it never references a vendor client directly.
2. **Given** a caller that must be denied action capabilities (e.g., the triage role), **When** it is
   handed its LLM client, **Then** the client exposes only the tools that caller is permitted, so the
   capability boundary is structural rather than a matter of prompt wording.
3. **Given** a caller that needs a typed/structured result, **When** it requests one, **Then** the
   adapter returns content the caller can validate against its expected shape.

---

### User Story 2 - Env-selected primary with automatic fallback (Priority: P1)

The operator selects which provider is primary through configuration alone. When the active provider is
unavailable or failing transiently (a timeout, a rate limit, a connection error, a server-side error),
the adapter automatically retries the call against the configured secondary within the same request, so
the caller still gets a result and the incident still reaches a disposition. A non-retryable condition
(a malformed request, an authentication or permission failure, or a content/safety refusal) is surfaced
as a structured error rather than masked by a silent failover.

**Why this priority**: A SOAR is judged on time-to-disposition and must keep working when one vendor has
an outage or rate-limits the workload; provider-agnosticism is only valuable if switching and failing
over actually work. The brief and plan name "env-selected primary; automatic fallback" as the defining
scope of this component, and the day-9 freeze explicitly exercises LLM-fallback failure-path recovery.

**Depends on**: US1 (the uniform seam the selection/fallback logic wraps).

**Independent Test**: Configure a primary and a secondary; force the primary to fail transiently and
confirm the call succeeds via the secondary and the response records the secondary as the serving
provider; change the configured primary and confirm the other provider now serves first — both with no
code change. Force a non-retryable error and confirm it surfaces rather than failing over silently.

**Acceptance Scenarios**:

1. **Given** a configured primary and secondary, **When** the primary returns a transient/availability
   error, **Then** the adapter automatically completes the call against the secondary and the response
   indicates which provider served it.
2. **Given** the primary is selected by configuration, **When** the configuration names the other
   provider as primary, **Then** that provider is tried first on the next run with no code change.
3. **Given** a non-retryable error (malformed request, authentication failure, or content refusal),
   **When** it occurs, **Then** the adapter surfaces a structured error and does not silently fall back
   in a way that hides the condition.
4. **Given** any provider call, **When** it is made, **Then** it is bounded by a timeout and a limited,
   transient-only retry policy so it never hangs the incident path unboundedly.

---

### User Story 3 - Every model call is costed and observable (Priority: P2)

Each model call records, through the observability seam from #2, the provider and model that served it,
the tokens consumed in and out, and the call latency — and its prompts and completions pass through
redaction at the model-prompt boundary before they are logged, traced, or stored. Counts a provider
does not return are marked "unknown" rather than fabricated.

**Why this priority**: Per-call token and latency telemetry is what the dashboard's per-agent cost view,
the KPI views, and cost accounting consume, and redaction-before-emit at the prompt boundary is the
safety guarantee #2 established — both are essential to Tier-1, but the seam (US1) and its
resilience (US2) must exist first.

**Depends on**: US1 (the call site that emits telemetry); #2 (the span telemetry and redaction seam).

**Independent Test**: With the provider mocked to return a known usage figure, drive a call through the
seam and confirm its span carries provider, model, tokens-in, tokens-out, and latency; with usage
omitted, confirm the counts read "unknown"; confirm a seeded secret in a prompt never appears raw in
the resulting log/trace.

**Acceptance Scenarios**:

1. **Given** any completed model call, **When** its telemetry is inspected, **Then** it reports the
   serving provider and model, tokens-in, tokens-out, and latency.
2. **Given** a provider that does not return usage, **When** the call completes, **Then** the missing
   counts are recorded as "unknown" rather than guessed.
3. **Given** a prompt or completion containing a secret or PII, **When** the call is logged, traced, or
   its content is stored, **Then** the sensitive value is redacted and the raw value does not appear.

---

### User Story 4 - Substitutable in tests and provable on both providers (Priority: P2)

The seam is replaceable with a test double through the injection mechanism without changing the
components that consume it, so unit tests run with the model mocked and make no real provider calls;
and the capability is exercisable against each configured provider independently, so the committed
"every eval passes on both configured providers" gate can be enforced.

**Why this priority**: Mocking the LLM in unit tests and proving behavior on both providers are
non-negotiable engineering standards (Constitution II), but they refine the seam introduced by the
P1 stories, so they sit just behind them. The full eval suite is owned by #13; this component provides
the substitutable seam those gates exercise and seeds the both-providers check.

**Depends on**: US1, US2, US3.

**Independent Test**: Substitute a test double for the seam through injection and confirm consumers run
unchanged with no real provider call; run the seeded provider check against each configured provider in
turn and confirm each completes independently.

**Acceptance Scenarios**:

1. **Given** a unit test, **When** it substitutes a test double for the LLM seam through injection,
   **Then** the consuming component runs unchanged and makes no real provider call.
2. **Given** two configured providers, **When** the seeded provider check is run, **Then** it executes
   to completion against each provider independently, so a regression on either fails the gate.

---

### Edge Cases

- **Primary down, secondary healthy**: the call transparently completes on the secondary; the response
  records the secondary as the serving provider and the failover is observable (a counter / event), not
  silent.
- **Both providers unavailable**: the adapter surfaces a single structured, retryable error after
  exhausting the configured order; it never hangs and never fabricates a result.
- **Fallback cannot meet the output contract**: a failover whose result fails to validate against the
  caller's required output shape/tools is treated as a failed call (structured error), **not** a
  degraded success — the supervisor escalates (HITL) rather than acting on lower-fidelity output.
- **Authentication / permission failure**: treated as non-retryable — surfaced immediately, not
  failed-over (a bad credential on the primary must not silently shift all load to the secondary). A
  missing required credential fails startup, not the first incident.
- **No provider reachable at startup**: the service reports not-ready (it does not accept incidents it
  cannot process) and becomes ready once ≥1 provider is reachable; transient unreachability does not
  crash boot.
- **Content / safety refusal from the model**: surfaced as a distinct, non-retryable outcome the caller
  can branch on; not treated as a transient error to retry or fail over.
- **Provider returns no token usage**: recorded as "unknown"; never fabricated (consistent with #2).
- **Provider-specific usage shapes differ**: the adapter normalizes each vendor's usage into the one
  telemetry shape #2 consumes, so downstream views are provider-independent.
- **Oversized prompt / context-window exceeded**: surfaced as a clear, non-retryable error rather than
  an opaque failure; truncation policy for what is *recorded* about the call is owned by #2.
- **Slow provider**: bounded by a per-call timeout; a timeout is a transient failure eligible for
  fallback, never an unbounded wait on the incident path.
- **Switching the primary**: a configuration-only change; no code edit and no change to any consumer.

## Requirements *(mandatory)*

### Functional Requirements

**The provider-agnostic seam**

- **FR-001**: The system MUST expose a single LLM interface obtained through the platform's
  dependency-injection mechanism; every component that calls a model MUST use this seam and MUST NOT
  import or call a model-vendor SDK through any path that bypasses it.
- **FR-002**: The seam MUST present a uniform request shape (conversation messages, optional system
  instruction, generation parameters, and permitted tools) and a uniform response shape (produced
  content, token usage, the serving model and provider identity, and a stop/finish reason) that is
  independent of which provider served the call.
- **FR-003**: The seam MUST support handing a caller a client scoped to only the tools that caller is
  permitted, so capability boundaries (e.g., a read-only role that holds no action tools) are
  enforceable structurally rather than by prompt wording. (The per-role tool sets themselves are owned
  by later specs; this component provides the mechanism.)
- **FR-004**: The seam MUST support requesting a typed/structured result (and/or required tool use) that
  the caller can validate against an expected shape. This output contract MUST hold **regardless of which
  provider serves the call**: a failover result MUST satisfy the caller's required shape/tools, or the
  adapter MUST surface a structured error (**fail-closed**) — it MUST NOT return a silently degraded
  result. A capability-insufficient fallback thus becomes a step failure the supervisor can escalate,
  never an auto-acted lower-fidelity answer.

**Provider selection & fallback**

- **FR-005**: The primary provider MUST be selectable through configuration; switching the primary MUST
  require no code change and MUST take effect on restart.
- **FR-006**: A secondary provider MUST be configurable as an automatic fallback, with a defined,
  configuration-driven provider order.
- **FR-007**: On a transient/availability failure of the active provider (timeout, rate limit,
  connection error, or server-side error), the adapter MUST automatically attempt the configured
  fallback within the same call, transparently to the caller, and the response MUST record which
  provider ultimately served it. Failover MUST be evaluated **per call (stateless)** — each call begins
  at the configured primary and falls over in the configured order; v1 maintains no cross-call
  provider-health state (no circuit-breaker).
- **FR-008**: On a non-retryable condition (malformed request, authentication/permission failure, or a
  content/safety refusal), the adapter MUST surface a structured error distinguishable by the caller and
  MUST NOT silently fall back in a way that masks the condition.
- **FR-009**: Every provider call MUST be bounded by a timeout and a limited, transient-only retry/backoff
  policy, so a model call never hangs or retries non-transient errors on the incident path.
- **FR-010**: When the configured provider order is exhausted without success, the adapter MUST surface a
  single structured error rather than hang or fabricate a result.

**Telemetry & redaction (consumes #2)**

- **FR-011**: Every model call MUST record, through the observability seam (#2), the serving provider and
  model, tokens-in, tokens-out, and call latency; counts a provider does not supply MUST be marked
  "unknown" rather than fabricated.
- **FR-012**: Prompts/inputs and completions/outputs MUST pass through redaction at the model-prompt
  boundary (#2) before they are logged, traced, or stored; no raw secret or PII may leave the service via
  an LLM-call log, trace, or stored snapshot.
- **FR-013**: Provider-specific usage and metadata MUST be normalized into the single telemetry shape #2
  defines, so downstream cost/KPI views are provider-independent.

**Lifecycle, secrets & testability (builds on #1)**

- **FR-014**: The LLM client(s) MUST be initialized once as startup singletons via the platform's
  registration seam and disposed cleanly on shutdown; the adapter MUST NOT construct a client per call.
- **FR-015**: Provider credentials MUST be resolved from the platform's secrets store at startup, MUST
  never be hardcoded or emitted in logs or errors, and a missing or invalid required credential MUST fail
  startup fast (not at the first incident).
- **FR-019**: The service MUST report **not-ready** (via the platform readiness signal, #1's `/ready`)
  unless **at least one** configured provider is reachable, and MUST become ready once one is reachable.
  Provider network reachability MUST NOT crash boot (liveness) — only configuration/credential errors do
  (FR-015) — so the service stays up and absorbs runtime provider outages via per-call fallback (FR-007).
- **FR-016**: The seam MUST be substitutable with a test double through the injection mechanism without
  modifying consuming components, so the model can be mocked in unit tests.
- **FR-017**: The adapter MUST allow callers to request low-variance / deterministic-leaning generation
  where the provider supports it, consistent with Determinism-First (Constitution IV), and MUST introduce
  no nondeterminism of its own beyond the model's.

**Evaluation (both providers)**

- **FR-018**: The capability MUST be exercisable by the eval harness against each configured provider
  independently, so the committed "every eval passes on both configured providers" gate (Constitution II)
  can be enforced; an initial provider check is seeded now (the full harness is owned by #13).

### Key Entities *(include if feature involves data)*

- **LLM provider adapter (the seam)**: the single interface every component calls a model through;
  provider-agnostic request in, provider-agnostic response out; obtained via dependency injection.
- **Provider**: a concrete model backend the adapter can call; each is interchangeable behind the seam
  and identified in responses and telemetry.
- **Provider selection policy**: the configuration-driven choice of primary provider and the ordered
  fallback list.
- **LLM request**: the uniform input — conversation messages, optional system instruction, generation
  parameters, permitted tools, and an optional expected output shape.
- **LLM response**: the uniform output — produced content, token usage, the serving model and provider
  identity, and a stop/finish reason.
- **Token usage record**: tokens-in, tokens-out, model, provider, and latency for one call, in the shape
  the observability telemetry (#2) consumes; missing counts marked "unknown".
- **Provider credential**: the per-provider secret resolved from the secrets store at startup; never
  logged, required ones fail boot if absent.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of model calls in the system go through the single seam — **0** direct model-vendor
  calls exist outside it (verified structurally).
- **SC-002**: Switching the primary provider is a **configuration-only** change — **0** code edits — and
  takes effect on restart.
- **SC-003**: Under an induced transient failure of the active provider, **100%** of calls (in the
  failover test) still return a successful result via the configured fallback, and **0** incidents fail
  solely because one provider was unavailable.
- **SC-004**: 100% of completed model calls record the serving provider, model, tokens-in, tokens-out,
  and latency in their telemetry, with "unknown" recorded only when the provider omits usage.
- **SC-005**: Across the redaction check, **0** seeded secrets or PII values appear unredacted in any
  LLM-call log, trace, or stored prompt/response snapshot (inherits the #2 boundary).
- **SC-006**: The seeded provider check runs to completion against **each** configured provider
  independently, so a regression on either provider fails the gate.
- **SC-007**: A missing or invalid required provider credential prevents startup with a clear,
  secret-free error — **0** such failures surface for the first time at incident-processing time.
- **SC-008**: In the unit test tier, the LLM is fully replaceable with a test double via injection —
  **0** real provider calls occur in unit tests.
- **SC-009**: 100% of failover results either validate against the caller's required output contract or
  surface as a structured error — **0** silently degraded results reach a caller.
- **SC-010**: When no configured provider is reachable the service reports **not-ready**, and reports
  ready once ≥1 provider is reachable — **0** incidents are accepted while no provider is reachable.

## Assumptions

- **One cross-cutting component, by design**: the model adapter is specified once and consumed by every
  reasoning component, so provider-agnosticism, fallback, token accounting, and the tool-gating
  mechanism live in one seam rather than being re-implemented per agent.
- **Builds on #1 and #2**: this component consumes the dependency-injection mechanism, lifespan-singleton
  lifecycle, the registration seam, typed configuration, and the secrets store from #1, and the
  observability span telemetry + redaction boundary from #2. It fills the `llm.py` seam reserved as a
  stub in #1.
- **Provider pair (decided)**: the configured providers are **Google Gemini (cloud) as the primary**
  and a **local Ollama runtime as the secondary/fallback**, following the brief's named candidates. This
  deliberately uses **no Anthropic provider** for this component, overriding the repository's general
  Anthropic-first guidance within the LLM-adapter scope. Implications carried into planning: the Gemini
  credential is resolved from the secrets store at startup (FR-015), while Ollama needs no API key but
  requires its model to be available locally; local-runtime calls often omit token usage, so "unknown"
  counts (FR-011) are expected more often on the fallback path; and structured-output / tool-calling
  parity differs between a cloud API and a local model, so the both-providers eval (FR-018 / SC-006)
  verifies each provider against its own bar rather than assuming identical capability. The spec stays
  provider-agnostic; the concrete choice and exact model identifiers are recorded in
  planning / `DECISIONS.md`, not as requirements.
- **Fallback triggers (default)**: failover fires only on transient/availability failures (timeouts,
  rate limits, connection errors, server-side errors); configuration/authentication/validation errors
  and content/safety refusals are non-retryable and surfaced, not failed-over.
- **Streaming (out of scope for v1)**: the adapter provides request/response with usage accounting,
  which is what the agents need; token-by-token streaming (e.g., for a live dashboard view) is a later
  addition and not required for v1.
- **Embeddings out of scope here**: model *embeddings* needed for the vector store are owned by
  `SPEC-memory` (#6); if #6 chooses to reuse this adapter's provider plumbing, that is a #6 decision.
  This component covers chat/completion calls.
- **100% call observability (no sampling)**: consistent with #2, every model call is traced and costed;
  sampling is a later optimization, not v1 scope.
- **Eval gates owned by #13**: the committed golden sets and CI gates live in `SPEC-eval`; this component
  provides the substitutable, dual-provider-exercisable seam and seeds an initial provider check so the
  gate can be wired from day one.
- **Single organization / local demo deployment**: consistent with #1/#2 — no multi-region provider
  routing, no per-tenant provider isolation in scope for v1.

## Dependencies

- **Depends on #1 (`SPEC-platform-infra`)**: dependency injection, lifespan singletons, the
  startup-singleton registration seam, typed configuration, and the secrets store. This component fills
  the `llm.py` seam #1 reserved as a stub.
- **Depends on #2 (`SPEC-observability-redaction`)**: the observability seam (per-call span telemetry —
  tokens/model/latency — via the reserved token-accounting hook) and the redaction boundary applied to
  model prompts and completions.
- **Consumed by every reasoning component**: the supervisor/state machine (#7), the triage/enrichment/
  response agents (#8/#9/#10), memory reasoning (#6 where it calls a model), and eval (#13) all call the
  model **only** through this seam — the "agents → LLM provider" contract: no agent talks to a vendor
  directly.
