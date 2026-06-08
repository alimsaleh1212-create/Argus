# Implementation Plan: Provider-Agnostic LLM Adapter (Cross-Cutting Foundation)

**Branch**: `003-llm-provider` | **Date**: 2026-06-08 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/003-llm-provider/spec.md`

## Summary

Fill the `llm.py` seam that the platform foundation (#1) reserved as a stub, delivering **one
provider-agnostic LLM adapter** every reasoning component (the supervisor #7, the three agents #8/#9/#10,
memory reasoning #6, and eval #13) calls a model through. The adapter presents a **uniform
request/response shape** over two configured backends — **Google Gemini (cloud) as the primary** and a
**local Ollama runtime as the secondary/fallback** (the brief-named pair; decided in the spec) — selects
the primary by configuration, and **fails over per call (stateless)** to the secondary on a transient
failure so an incident still reaches a disposition. The output contract is **fail-closed**: a failover
result must validate against the caller's required structured-output/tool shape or the adapter surfaces a
structured error (a capability-insufficient local fallback becomes a step the supervisor escalates, never
a silently degraded answer). Every call is **costed and observable** through #2 — provider, model,
tokens-in/out, and latency recorded via the reserved `record_llm_usage` hook, prompts/completions
redacted at the recording boundary, credentials scrubbed from the outbound prompt — and the seam is
**substitutable via DI** (LLM mocked in unit tests) and **exercisable against each provider
independently**, seeding the committed "evals pass on both providers" gate.

The component is "done" when unit + integration + e2e are green, the `llm_provider` both-providers check
is seeded in `eval_thresholds.yaml`, and a synthetic call demonstrably fails over from primary to
secondary with redacted per-call telemetry. It adds **no incident/business logic and no prompts**; it
adds **one new compose service** (`ollama`, the local fallback runtime) — the only infra addition, a
direct consequence of the Gemini+Ollama choice and the fresh-clone reproducibility requirement.

## Technical Context

**Language/Version**: Python 3.12 (pinned `>=3.12,<3.13`); managed with `uv`.

**Primary Dependencies**: the official async vendor SDKs **`google-genai`** (Gemini) and **`ollama`**
(Ollama), imported **only** inside the driver module `backend/infra/llm_drivers.py`; everything else is
reuse — `httpx`/`tenacity` (per-call timeout + transient-only retry wrapping), Pydantic v2 (the uniform
request/response types), FastAPI `Depends()` DI, and the **#2 observability seam** (`span()` +
`record_llm_usage()` + the `Redactor`) for telemetry and prompt-boundary redaction. The selection,
fallback, contract-validation, normalization, and telemetry wrapping are **hand-owned** in the adapter.

**Storage**: **None** — the adapter is stateless (no DB table, no Alembic migration, no MinIO use). The
only persisted artifact is the seeded eval threshold in `config/eval_thresholds.yaml`. The Gemini API key
is resolved from **Vault** (#1) at startup; Ollama needs no credential.

**Testing**: `pytest` + `pytest-asyncio` (`asyncio_mode=auto`). **Unit** = selection + stateless
per-call fallback, the error taxonomy (transient vs non-retryable vs content-refusal), fail-closed
contract validation, token-usage normalization, and the telemetry/redaction wiring — both drivers
**faked** (no real provider call, SC-008). **Integration** = a real **Ollama** compose service with a
tiny model (generate + usage normalization + structured-output behavior), the Gemini request/response
mapping via **mocked HTTP** (always runs) plus a **live Gemini** test **gated on credential presence**
(skipped in keyless CI), and the at-least-one-reachable readiness probe. **e2e** = a synthetic call
through the DI seam where the primary is forced down and the secondary serves, asserting redacted per-call
telemetry and zero seeded-secret leakage, plus the seeded both-providers check.

**Target Platform**: Linux containers under Docker Compose v2 on a single host (dev/CI). Adds an
**`ollama` compose service** (official `ollama/ollama` image) with a one-shot tiny-model pull; Gemini is
an external cloud API reached over `httpx`/SDK with its key seeded into Vault by `vault-seed`.

**Project Type**: Cross-cutting infrastructure layer inside the existing modular-monolith `backend/`
package — it *fills* the reserved `infra/llm.py` seam, adds pure domain types and one vendor-isolated
driver module, and extends config/health/DI. No restructuring.

**Performance Goals**: the unavoidable on-path cost is the model call itself; the adapter's *own* overhead
(selection, redaction call, span open/close, usage normalization) is cheap and bounded, and span export
stays off the synchronous path via #2's `BatchSpanProcessor`. Each provider call is bounded by a
configurable **timeout** (default 30 s) with **transient-only** bounded retry; a single failover adds at
most one extra attempt. Honors #2's ≤5% observability-overhead budget for the wrapping (the model latency
is the work, not the overhead).

**Constraints**: **stateless per-call** failover (no circuit-breaker in v1); **fail-closed** output
contract (validate-or-error, never degrade); **at-least-one-reachable** readiness gate; **credentials
scrubbed from the outbound prompt** (FR-006a defense-in-depth) while operational identifiers the agent
needs are preserved; **no-bypass** (vendor SDKs only in `llm_drivers.py`); async all the way down; one
typed `llm` settings section (`extra="forbid"`); required Gemini credential fails boot.

**Scale/Scope**: demo-scale single-SOC workload, replayed alerts; request/response only (**no streaming**
in v1); **no embeddings** (owned by #6); no multi-region/multi-tenant provider routing.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design — still passing.*

Derived from `.specify/memory/constitution.md` (v1.0.0).

- [x] **I. Spec-Driven Delivery**: `spec.md` precedes code; "done" = unit + integration + e2e green and
      pushed. Internal milestones keep PRs ≤ ~400 lines: **(a)** uniform seam + domain types + `llm`
      settings + DI accessor + unit (the MVP — US1); **(b)** selection + stateless per-call fallback +
      error taxonomy + timeout/retry (US2); **(c)** telemetry + prompt-boundary redaction wiring +
      at-least-one-reachable readiness (US3); **(d)** Gemini + Ollama drivers + `ollama` compose service
      + integration/e2e + seeded `llm_provider` both-providers check (US4).
- [x] **II. Test-First, Three-Tier, Eval-Gated (NON-NEGOTIABLE)**: three tiers planned, green daily,
      **≥80% on new code and higher on the fail-closed contract-validation path** (the safety-relevant
      boundary here). The LLM is **mocked in unit** (SC-008). This component is precisely what makes
      "every eval passes on **both** configured providers" enforceable; it seeds the `llm_provider`
      check now (full harness is #13) and is exercisable per-provider (FR-018).
- [x] **III. Security Boundaries Are Structural, Not Prompted**: the seam **provides the tool-gating
      mechanism** (FR-003) — a caller is handed a client scoped to only its permitted tools via DI, so
      "triage holds no action tools" is enforceable structurally and library-independently (the per-role
      tool sets are #8). Prompt-boundary **redaction** is consumed from #2 and **credentials are scrubbed
      from the outbound prompt** so a raw secret in alert text is never transmitted to a provider.
- [x] **IV. Determinism First**: the adapter is **deterministic plumbing** — fixed config-driven provider
      order, deterministic error classification, no LLM call of its own — and supports **low-variance**
      generation (FR-017) for callers that need it. It introduces no nondeterminism beyond the model's.
- [x] **V. Human-in-the-Loop**: N/A — no remediation actions here. The **fail-closed** contract is
      consistent with it: a capability-insufficient fallback becomes a step error the supervisor (#7)
      escalates rather than auto-acting on a weak local-model answer.
- [x] **VI. Temporal Memory & Graceful Degradation**: memory N/A; **graceful degradation is central** —
      a provider outage degrades to the configured fallback and **never fails an incident solely because
      one provider is down** (SC-003); readiness reflects the degraded state (SC-010); export-path
      faults are #2's concern. Embedding provisioning for the memory store stays with #6.
- [x] **VII. Production Engineering Standards**: async vendor SDK clients + `httpx`; **DI** (`Depends`)
      supplies the seam and enables the LLM mock; the adapter is a **lifespan singleton** via the
      provider seam (clients built once); **Pydantic** uniform types at the boundary; structured logging
      + trace-id and off-path export via #2; a typed `llm` settings section (`extra="forbid"`) with the
      required Gemini secret failing boot; `uv`-pinned deps.
- [x] **Scope & Tiers**: strictly v1 / T1 cross-cutting; no ML detector / multi-tenancy / widget / live
      capture / LLM supervisor / 4th agent; respects the inward-only layering. **One deviation**: it adds
      a new compose service (`ollama`) — see Complexity Tracking. (This is #3's scope choice, not a
      constitution rule; #2's "no new service" was #2's own scope line.)

**Result: PASS — one tracked, justified addition (the `ollama` compose service).** The two vendor SDK
deps are a *dependency-weight* trade (official async clients vs. hand-rolled REST mapping), justified in
[research.md](./research.md) and carried into `DECISIONS.md`; they are not a constitution violation.

## Project Structure

### Documentation (this feature)

```text
specs/003-llm-provider/
├── plan.md              # This file (/speckit-plan output)
├── research.md          # Phase 0 — decisions & rationale (LD1–LD11)
├── data-model.md        # Phase 1 — uniform LLM types (domain/llm.py) + LlmSettings shape
├── quickstart.md        # Phase 1 — configure providers; verify fallback / telemetry / readiness / both-providers
├── contracts/           # Phase 1 — the outward contracts later specs consume
│   ├── llm-seam.md                     # the one seam (get_llm + LlmClient.generate) via DI; no-bypass; telemetry/redaction wrapping
│   ├── provider-selection-and-fallback.md  # config-driven order; stateless per-call algorithm; error taxonomy; fail-closed validation
│   └── llm-config-and-readiness.md     # LlmSettings + env/Vault; ollama compose service; at-least-one-reachable /ready; seeded eval
├── checklists/
│   └── requirements.md  # (created by /speckit-specify)
└── tasks.md             # Phase 2 — created by /speckit-tasks (NOT here)
```

### Source Code (repository root)

> Fills the reserved #1 seam and adds the minimum new files; **no restructuring**. New files marked `+`.

```text
backend/
├── infra/
│   ├── llm.py            # FILL: LlmClient adapter (selection, stateless per-call fallback, timeout +
│   │                     #       transient-only retry, fail-closed contract validation, telemetry +
│   │                     #       prompt-redaction wiring); LlmProvider (lifespan singleton);
│   │                     #       register_llm_provider(); get_llm()
│   ├── llm_drivers.py  + # NEW: the ONLY modules importing google-genai / ollama — GeminiDriver +
│   │                     #      OllamaDriver: map uniform request↔vendor, normalize usage, classify errors
│   ├── config.py         # EDIT: add LlmSettings; register on Settings; add "llm" to _KNOWN_SENTINEL_SECTIONS
│   ├── health.py         # EDIT: add check_llm() — healthy iff ≥1 configured provider reachable
│   └── container.py / lifespan.py  # (unchanged) LlmProvider built via the existing provider seam
├── dependencies.py        # EDIT: add get_llm() Depends() reading app.state.container.llm
├── domain/
│   └── llm.py           + # NEW: pure types — LlmMessage, ToolSpec, LlmRequest, LlmResponse, TokenUsage,
│                          #      ProviderId, StopReason, ProviderCapability, LlmError(+LlmErrorKind)
├── main.py                # EDIT: provider-registration sequence invokes register_llm_provider() after observability
└── routers/
    └── health.py          # EDIT: include the llm probe in the /ready aggregation

config/
└── eval_thresholds.yaml   # EDIT: seed the `llm_provider` both-providers check (enforced by #13's harness)

compose.yaml               # EDIT: add the `ollama` service (local fallback runtime) + one-shot tiny-model pull
.env.example               # EDIT: GEMINI/GOOGLE key placeholder (replaces OPENAI) + llm settings + Vault path + Ollama URL/model
pyproject.toml             # EDIT: add google-genai + ollama deps; drop backend/infra/llm.py from coverage omit; measure llm_drivers.py

tests/
├── unit/                  # selection, stateless fallback, error taxonomy, fail-closed validation, usage normalization, redaction wiring (drivers faked)
├── integration/          # real Ollama (compose, tiny model); Gemini mapping via mocked HTTP (always) + live test gated on key; readiness any-reachable
└── e2e/                   # synthetic call via DI seam → fallback serves, redacted telemetry, seeded both-providers check
```

**Structure Decision**: Stay inside the established modular-monolith `backend/` package and **fill the
reserved `infra/llm.py` seam** rather than restructure. Pure uniform types go in `domain/llm.py` (no
outward deps, satisfying the domain-isolation `import-linter` contract); the two **vendor SDKs are
confined to `infra/llm_drivers.py`** (the no-bypass boundary — the only place `google-genai`/`ollama` are
imported, mirroring how #2 confined `presidio`/`opentelemetry` to `infra/`). The adapter is registered as
a **lifespan singleton via the existing provider seam** (`container.py`) so clients build once; consumers
obtain it **only** through `Depends(get_llm)`. The adapter wraps each call with the **#2 observability
seam** (open an `LLM_CALL` span, `record_llm_usage`, redact recorded prompt/completion) — it does **not**
re-implement tracing or redaction. The single infra addition is the **`ollama` compose service**.

## Complexity Tracking

> One tracked deviation from the "no new service" discipline #2 held; justified below and recorded in `DECISIONS.md`.

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| New `ollama` compose service | The chosen secondary/fallback provider (Ollama, local) must actually run somewhere for the fallback path, the integration tier, and the both-providers eval to be real — and for the fresh-clone `docker-compose up` reproducibility the constitution requires. | "Point at an external/host Ollama" rejected: breaks clean fresh-clone bring-up and makes CI non-deterministic (depends on a host runtime that may be absent). A tiny pinned model in a dedicated service keeps the demo and CI self-contained. |
| Two vendor SDK deps (`google-genai`, `ollama`) | Official async clients handle auth, tool-calling, structured-output, and usage shapes correctly; the adapter owns only normalization + selection + fallback. | Hand-rolled `httpx` REST for both rejected: re-implements vendor tool/schema/usage mapping that the SDKs already get right, more surface to get subtly wrong, for marginal dependency savings. (Dependency-weight trade, not a constitution violation — noted here for visibility.) |
