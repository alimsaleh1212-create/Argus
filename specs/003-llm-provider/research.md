# Phase 0 Research — Provider-Agnostic LLM Adapter

**Feature**: `003-llm-provider` | **Date**: 2026-06-08

Decisions LD1–LD11 below resolve the Technical Context unknowns and the spec's deferred items. Each is
carried into `DECISIONS.md` (proposed IDs **LP1–LP11**). No `NEEDS CLARIFICATION` remain (the three spec
clarifications — fallback recovery, fail-closed contract, readiness gate — are reflected in LD3/LD4/LD5).

---

## LD1 — Provider pair: Gemini (primary, cloud) + Ollama (secondary, local)

**Decision**: Configure exactly two providers — **Google Gemini** as the env-selected primary and a
**local Ollama** runtime as the automatic fallback. No Anthropic provider in this component.

**Rationale**: This is the brief-named candidate pair (`Covers: Ollama, Gemini`) and the explicit choice
recorded in the spec's Assumptions and clarifications. It satisfies the constitution's "evals on **both**
providers" with one cloud + one local backend. The capability gap between them (below) is exactly what the
fail-closed contract (LD4) and the per-provider eval (LD5/FR-018) are designed to handle.

**Alternatives considered**: Anthropic-first (Claude primary) — rejected by the user for this component
despite the repo's general Anthropic-first lean; Gemini+Anthropic (two cloud) — rejected (the user wanted
the local-runtime path). The spec stays provider-agnostic so a third provider can be added behind the
seam later without touching consumers.

---

## LD2 — Transport: official async vendor SDKs, confined to one driver module

**Decision**: Use the official async SDKs **`google-genai`** (Gemini) and **`ollama`** (Ollama),
imported **only** inside `backend/infra/llm_drivers.py`. Each provider is a small `Driver` that maps the
uniform `LlmRequest` to the vendor call and the vendor result back to the uniform `LlmResponse`
(content, normalized usage, model, stop reason).

**Rationale**: the SDKs handle vendor auth, tool/function-calling, structured-output, and usage shapes
correctly, so the adapter owns only the parts that are genuinely ours — normalization, selection,
fallback, validation, telemetry. Confining vendor imports to one module mirrors #2's confinement of
`presidio`/`opentelemetry` to `infra/` and keeps the **no-bypass** guard (FR-001) targeting a single file.

**Alternatives considered**: hand-rolled `httpx` REST for both — rejected: re-implements tool/schema/usage
mapping the SDKs already get right, more surface to get subtly wrong, marginal dependency savings (httpx
is already present). Mixed (SDK for one, httpx for the other) — rejected for inconsistency.

---

## LD3 — Selection & fallback: env-selected primary, stateless per-call order (clarify Q1)

**Decision**: `LlmSettings.primary` + `LlmSettings.fallback_order` define a config-driven provider order.
Every call begins at the primary and walks the order on transient failure. **No cross-call health state /
no circuit-breaker** in v1 — each call is evaluated independently (stateless).

**Rationale**: simplest correct behavior at demo scale; deterministic and trivially testable (no shared
mutable state across async workers); each incident independently gets the best available provider. The
only cost — re-paying the primary timeout during a sustained outage — is bounded by the per-call timeout
(LD8) and acceptable for v1. Switching the primary is configuration-only (SC-002).

**Alternatives considered**: circuit-breaker / sticky-secondary with a cooldown — deferred as a later
optimization (lower latency under sustained outage, but adds shared state + cooldown config); sticky-to-
success — rejected (least predictable).

---

## LD4 — Output contract: capability-aware request shaping + fail-closed post-call validation (clarify Q2)

**Decision**: When a caller requires structured output (`response_schema`) and/or tool use, the driver
shapes the request using each provider's native mechanism (Gemini: `response_schema` / function
declarations; Ollama: `format`=json-schema / `tools`). After the call, the adapter **validates** the
result against the caller's required shape/tools. If it does not validate, the adapter raises a structured
`LlmError(kind=CONTRACT_UNSATISFIED)` — it **never** returns a silently degraded result. A
`ProviderCapability` record per provider is used for **request shaping and telemetry only**, not for
routing.

**Rationale**: the Gemini→Ollama capability gap means a local-model failover may not honor a strict schema
or tool call; for a security SOAR, a malformed/degraded disposition is worse than a surfaced error — it
becomes a step the supervisor (#7) escalates to a human (HITL-consistent). Post-call validation keeps
acceptance tests crisp (SC-009): validate or error.

**Alternatives considered**: best-effort degraded result flagged "degraded" (clarify option B) — rejected
(pushes handling to every caller, weakens tests, risks acting on weak output); capability-matched routing
that pre-skips a provider lacking a capability (clarify option C) — rejected (the user chose fail-closed;
capabilities still inform shaping/telemetry, just not routing).

---

## LD5 — Readiness: at-least-one-reachable gate via the existing `/ready` (clarify Q3)

**Decision**: Add `check_llm(settings, container)` to `backend/infra/health.py` returning a single
`DependencyStatus(name="llm", healthy = any(provider reachable))`, and include it in
`run_readiness_probes`. Because `/ready` already aggregates with `all(d.healthy)`, an `llm` dependency
that is healthy iff **≥1** provider is reachable yields exactly the at-least-one-reachable semantics.
Configuration/credential errors still **fail boot** (LD10/FR-015); reachability only affects readiness.

**Rationale**: blocking *boot* on provider reachability would defeat fallback (a Gemini blip at startup
should still bring the worker up to serve via Ollama). Reporting reachability through `/ready` matches
#1's liveness/readiness split (`/health` always-200, `/ready` 200/503) and lets the service recover to
ready when a provider returns. Probes are cheap and time-bounded by `startup.dependency_timeout_s`.

**Alternatives considered**: all-providers-verified-at-boot (mirror Vault strictness) — rejected (brittle:
local model pull, cloud blips; partially defeats fallback); never gate on reachability — rejected (the
user chose the at-least-one gate; silently accepting incidents with no usable provider is worse).

---

## LD6 — Token-usage normalization onto #2's `record_llm_usage` shape

**Decision**: Define `TokenUsage(prompt_tokens: int | None, completion_tokens: int | None)` and have each
driver normalize its vendor usage into it: Gemini `usage_metadata.prompt_token_count` /
`candidates_token_count`; Ollama `prompt_eval_count` / `eval_count`. Missing counts stay `None`. The
adapter passes this object straight to #2's `record_llm_usage(span, usage, model)`, which reads
`prompt_tokens` / `completion_tokens` via `getattr` and renders `None` as "unknown".

**Rationale**: #2 already reserved exactly this seam (its OD9 token-accounting hook reads
`prompt_tokens`/`completion_tokens`); matching the attribute names means **no change to #2** and a
provider-independent telemetry shape (FR-013, SC-004). Local Ollama may omit usage → "unknown" is expected
more often on the fallback path (recorded honestly, never fabricated).

**Alternatives considered**: a new bespoke usage type — rejected (would force a change to #2's hook);
fabricating estimates when usage is absent — rejected (constitution: never fabricate; record "unknown").

---

## LD7 — Redaction wiring at the model boundary

**Decision**: For each call the adapter (a) **scrubs CREDENTIAL-class content from the outbound prompt**
via the #2 `Redactor` before transmission (defense-in-depth, FR-006a — a raw API key in alert text is
never sent to a provider), and (b) records the prompt/completion **into the `LLM_CALL` span attributes**,
which #2's `span()` already redacts at the `TRACE` boundary; any log line the adapter emits passes the #2
log redaction processor. The adapter does **not** strip operational identifiers (IP/host/user) the agent
deliberately included for reasoning — those are #2's policy for output boundaries and the agent's to
manage.

**Rationale**: FR-012 requires redaction "before they are logged, traced, or stored" — i.e., the
**recorded** copy — which #2's span/log redaction already provides; the adapter just routes prompt/
completion through it rather than re-implementing redaction. Outbound credential scrubbing satisfies the
"credentials scrubbed everywhere" clause without crippling the agent's ability to reason over operational
identifiers.

**Alternatives considered**: redact PII/identifiers out of the *outbound* prompt too — rejected (would
blind enrichment correlation on IPs/hosts; #2's policy already governs recorded copies, and stripping the
sent prompt is the agent's decision, not the adapter's); re-implement redaction in the adapter — rejected
(no-bypass: redaction lives in #2).

---

## LD8 — Timeouts, transient-only retry, and the error taxonomy

**Decision**: Wrap each provider call with a per-call **timeout** (`request_timeout_s`, default 30 s) and
a bounded **transient-only** retry (`tenacity`, `max_retries` default 2, exponential backoff). Classify
every failure into `LlmErrorKind`: `TRANSIENT` (timeout / rate-limit / connection / 5xx → retry then fail
over), `AUTH` and `INVALID_REQUEST` (non-retryable → surface, no failover), `CONTENT_REFUSAL` (distinct
non-retryable outcome the caller can branch on), `CONTRACT_UNSATISFIED` (LD4 fail-closed), and `EXHAUSTED`
(whole order failed → one structured error).

**Rationale**: matches the spec's fallback-trigger default and FR-008/FR-009/FR-010, and the project's
existing `tenacity`-on-transient-only convention (mirrors Vault's retry). A timeout is itself a transient
failure eligible for failover, so a slow provider never hangs the incident path.

**Alternatives considered**: retry all errors — rejected (would retry auth/validation pointlessly and mask
refusals); no retry, fail over immediately — rejected (a single transient blip shouldn't always burn the
fallback; one bounded retry is cheap insurance).

---

## LD9 — Ollama as a compose service; provider testing strategy across tiers

**Decision**: Add an **`ollama` compose service** (official image) with a one-shot pull of a **tiny model**
(e.g. a ~0.5–1B model) so the fallback path, the integration tier, and the both-providers eval are real
and the stack still comes up clean from a fresh clone. Test tiers: **unit** fakes both drivers (no real
calls, SC-008); **integration** runs real Ollama (Docker-gated `integration` marker) and tests the Gemini
mapping via **mocked HTTP** always, with a **live Gemini** test **skipped unless a key is present**;
**e2e** drives a synthetic call through the DI seam exercising the fallback path with faked providers.

**Rationale**: the constitution requires three real tiers and "evals on both providers," but live cloud
calls in CI are costly/flaky/keyless and a full Ollama model is heavy. A tiny pinned model keeps Ollama
self-contained and CI deterministic; gating the live Gemini test on credential presence keeps keyless CI
green while still allowing a real check where a key exists. Documented as a CI-weight tradeoff.

**Alternatives considered**: real Gemini calls in CI — rejected (cost, flakiness, secret management);
no Ollama service (host/external) — rejected (breaks fresh-clone reproducibility, non-deterministic CI);
a large Ollama model — rejected (CI RAM/time blowup; the adapter logic, not model quality, is what #3
tests).

---

## LD10 — Lifespan singleton + registration order

**Decision**: `LlmProvider` (Provider protocol, `name="llm"`) builds both driver clients **once** on
startup and disposes them on shutdown; `register_llm_provider()` appends it to the registry. It is
registered **after** the observability provider in the app's provider-registration sequence (so the
adapter can read the `Observability` bundle from `app.state.container` at call time, as
`dependencies.get_obs` does). `get_llm()` in `backend/dependencies.py` returns
`app.state.container.llm`.

**Rationale**: matches #1's provider seam (`build()` async-context, ordered registration, reverse
teardown) and #7's "LLM client is a lifespan singleton" standard — the model client is built once, never
per call (FR-014). Note: the existing `register_*_provider()` helpers (#1) and #2's `ObservabilityProvider`
are **not yet invoked** from a central bootstrap in the committed code; this plan wires
`register_llm_provider()` into `create_app()`'s registration step and ensures observability is registered
before it — closing that latent wiring gap for the providers this adapter depends on.

**Alternatives considered**: build clients per call — rejected (FR-014, wasteful, breaks singleton
standard); read observability via a module global — rejected (no-bypass / DI standard; use the container).

---

## LD11 — Structured-output & tool-calling mechanism per provider (uniform request)

**Decision**: The uniform `LlmRequest` carries optional `tools: list[ToolSpec]` (JSON-schema parameters)
and optional `response_schema: dict`. Each driver translates these to the vendor's native mechanism:
**Gemini** → `tools`/function declarations and `response_schema` (controlled generation); **Ollama** →
`tools` and `format` (JSON schema). Tool *scoping* (FR-003) is enforced by which `ToolSpec`s a caller is
handed — the adapter passes through exactly the permitted set, so a triage client given no action tools
structurally cannot call one.

**Rationale**: keeps callers provider-agnostic (they describe tools/shape once) while each driver uses the
provider's best-supported path; the fail-closed validation (LD4) catches providers (esp. local Ollama)
that don't honor the contract. Tool-scoping-via-DI is the library-independent realization of Constitution
III's structural boundary that #8 will consume.

**Alternatives considered**: a single lowest-common-denominator JSON-in-text prompt for both — rejected
(throws away Gemini's native, more reliable structured output and proper tool-calling); per-caller vendor
branching — rejected (defeats the provider-agnostic seam).

---

## Resolved unknowns summary

| Unknown (Technical Context) | Resolved by |
|---|---|
| Which providers / primary vs fallback | LD1 |
| Transport (SDK vs httpx) and vendor-import isolation | LD2 |
| Fallback recovery semantics | LD3 (clarify Q1) |
| Degraded-vs-fail-closed output contract | LD4 (clarify Q2) |
| Startup vs readiness gate | LD5 (clarify Q3) |
| Token-usage shape vs #2 | LD6 |
| Redaction at the model boundary | LD7 |
| Timeout / retry / error classification | LD8 |
| Ollama runtime + per-tier test strategy | LD9 |
| Lifecycle, singleton, registration order | LD10 |
| Structured-output / tool-calling per provider | LD11 |
