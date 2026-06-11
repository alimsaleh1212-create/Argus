---
description: "Task list for SPEC-llm-provider (Component #3) implementation"
---

# Tasks: Provider-Agnostic LLM Adapter (Cross-Cutting Foundation)

**Input**: Design documents from `specs/003-llm-provider/`

**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md),
[data-model.md](./data-model.md), [contracts/](./contracts/)

**Tests**: REQUIRED (constitution Principle II — Test-First, Three-Tier, Eval-Gated). Every story
carries unit/integration/e2e tasks written **before** implementation and green in CI before the spec
is "done". ≥80% coverage on new code, **higher on the fail-closed contract-validation path** (the
safety-relevant boundary here).

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different files, no dependency on an incomplete task)
- **[Story]**: US1–US4 (story phases only; Setup/Foundational/Polish carry no story label)
- All paths are repo-root-relative.

## ⚠️ Story sequencing note (read first)

This is a **cross-cutting** component: the four user stories form a single-author dependency chain, not
a staffing fan-out. The fallback logic (**US2**) wraps the **US1** seam; telemetry/redaction (**US3**)
wraps the US1 call site; substitutability + readiness + both-providers (**US4**) prove the US1–US3 whole.
Phases follow **priority *and* build dependency** (they coincide):

`Setup → Foundational → US1 (P1, the seam = MVP) → US2 (P1, fallback) → US3 (P2, telemetry/redaction) → US4 (P2, provable + readiness) → Polish`

Each story stays independently **testable** via its Independent Test (spec.md). The **MVP** is through
**US1**: one provider-agnostic seam, obtained via DI, a real call working on the primary. This component
fills the #1-reserved seam `backend/infra/llm.py`; it adds **one** new compose service (`ollama`).

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Dependencies, the local-fallback runtime, and build/coverage config — no adapter behavior yet.

- [x] T001 Add LLM deps via `uv add`: `google-genai`, `ollama`; regenerate the committed `uv.lock` (in `pyproject.toml`)
- [x] T002 [P] Add an `ollama` service (official `ollama/ollama` image, port `11434`, named volume) plus a one-shot tiny-model pull, reachable by api/worker at `ARGUS__LLM__OLLAMA_BASE_URL`, in `compose.yaml`
- [x] T003 [P] Update `pyproject.toml` coverage: remove `backend/infra/llm.py` from `[tool.coverage.run] omit`; confirm `backend/infra/llm_drivers.py` and `backend/domain/llm.py` are measured
- [x] T004 [P] Update `.env.example`: add a `GEMINI_API_KEY` placeholder (seeded into Vault at `secret/llm`, replacing the legacy `OPENAI_API_KEY` line) and document the `ARGUS__LLM__*` section (no secret values)

**Checkpoint**: `uv sync` resolves; `docker compose up` brings up `ollama` and pulls the tiny model; coverage targets the new modules.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Shared pure types + the typed settings section every story imports. **No user story can begin until this phase is complete.**

- [x] T005 Define LLM domain types (`ProviderId`, `StopReason`, `LlmErrorKind`, `LlmMessage`, `ToolSpec`, `LlmRequest`, `ToolCall`, `TokenUsage`, `LlmResponse`, `ProviderCapability`, `LlmError`) in `backend/domain/llm.py` per [data-model.md](./data-model.md) — pure types, no outward deps; `TokenUsage` exposes `prompt_tokens`/`completion_tokens` to match #2's `record_llm_usage`
- [x] T006 Add `LlmSettings` (fields per [data-model.md](./data-model.md) §Configuration: `primary`, `fallback_order`, `request_timeout_s`, `max_retries`, `gemini_model`, `gemini_vault_path`, `ollama_base_url`, `ollama_model`; validator: `fallback_order[0] == primary`) to `backend/infra/config.py`, register it on `Settings`, add `"llm"` to `_KNOWN_ARGUS_SECTIONS`, and ensure the Gemini Vault path joins `vault.required_paths` (fail-boot if absent)

**Checkpoint**: `Settings` validates with the new `llm` section; domain types import with no outward deps; `import-linter` contracts still pass.

---

## Phase 3: User Story 1 — One seam every component calls the model through (Priority: P1) 🎯 MVP

**Goal**: One provider-agnostic `LlmClient` obtained via DI, presenting a uniform request/response over either backend, with tool-scoping and structured-output support; a real call works on the primary.

**Independent Test**: With the model provider mocked, a consumer obtains the client via `Depends(get_llm)`, issues a uniform request, and receives a uniform response carrying content, usage, and the serving model/provider; a check confirms no vendor SDK is referenced outside the seam.

### Tests for User Story 1 ⚠️ (write first, must fail)

- [x] T007 [P] [US1] Unit tests in `tests/unit/test_llm_seam.py`: uniform request→response independent of provider (driver faked); client obtained via `Depends(get_llm)`; tool-scoping passthrough (a client handed no tools cannot emit a tool call, FR-003); structured-output request + single-result validation (FR-004); assert no `google-genai`/`ollama` import outside `backend/infra/llm_drivers.py` (FR-001/SC-001)
- [x] T008 [P] [US1] Integration test in `tests/integration/test_llm_ollama.py` (real Ollama via compose, tiny model): a real generate returns the uniform response; usage normalized to `prompt_tokens`/`completion_tokens` (or `None`); a structured-output request is honored or flagged by validation
- [x] T009 [P] [US1] Integration test in `tests/integration/test_llm_gemini_mapping.py`: Gemini request/response + usage mapping via **mocked HTTP** (always runs); a **live** Gemini smoke test **gated on `GEMINI_API_KEY` presence** (skipped in keyless CI)

### Implementation for User Story 1

- [x] T010 [US1] Define the internal `Driver` protocol (`async generate(request) -> LlmResponse`, `capability: ProviderCapability`) and implement `GeminiDriver` (maps uniform request↔`google-genai`, normalizes usage, shapes structured-output/tools) in `backend/infra/llm_drivers.py` (depends T005)
- [x] T011 [US1] Implement `OllamaDriver` (maps uniform request↔`ollama`, normalizes usage incl. frequent `None`, shapes `format`/`tools`) in `backend/infra/llm_drivers.py` (depends T005, T010)
- [x] T012 [US1] Implement the `LlmClient` adapter happy path — primary-only `generate(request, *, correlation_id, parent_span_id=None)`: shape request → call driver → return uniform `LlmResponse`; structured-output request shaping + single-result validation — in `backend/infra/llm.py` (depends T010, T011) per [contracts/llm-seam.md](./contracts/llm-seam.md)
- [x] T013 [US1] Implement `LlmProvider` (lifespan singleton — build both drivers **once**, resolve the Gemini key from Vault, dispose on shutdown) and `register_llm_provider()` in `backend/infra/llm.py` (depends T012)
- [x] T014 [US1] Add `get_llm()` `Depends()` provider reading `app.state.container.llm` in `backend/dependencies.py` (depends T013)
- [x] T015 [US1] Wire the provider-registration sequence in `backend/main.py` `create_app()` to invoke the registration helpers in order (vault → db → blob → observability → llm), registering `LlmProvider` **after** observability — closing the latent registration-wiring gap so the container exposes `.llm` (depends T013)

**Checkpoint**: a real call works through the seam on the primary; DI + lifespan singleton in place; no vendor import outside the driver. **MVP reached.**

---

## Phase 4: User Story 2 — Env-selected primary with automatic fallback (Priority: P1)

**Goal**: Config-selected primary; transient failure of the active provider transparently fails over per call to the secondary; non-retryable conditions surface; a failover that can't meet the output contract fails closed.

**Independent Test**: Configure primary + secondary; force the primary transient and confirm the call succeeds via the secondary (`served_by_fallback`); change the configured primary and confirm order flips with no code change; force a non-retryable error and confirm it surfaces, not silently fails over.

**Depends on**: US1 (the uniform seam the selection/fallback logic wraps).

### Tests for User Story 2 ⚠️ (write first, must fail)

- [x] T016 [P] [US2] Unit tests in `tests/unit/test_llm_fallback.py`: stateless per-call order (every call starts at primary, FR-007); transient error → fail over, response records serving provider + `served_by_fallback`; non-retryable (AUTH/INVALID_REQUEST/CONTENT_REFUSAL) → surfaced, no failover (FR-008); per-call timeout → transient → failover (FR-009); both providers transient → single `LlmError(EXHAUSTED)` (FR-010); fail-closed: a failover result failing the required schema/tools → `CONTRACT_UNSATISFIED`, never a degraded result (FR-004/SC-009); switching `primary`/`fallback_order` flips order with no code change (SC-002)

### Implementation for User Story 2

- [x] T017 [US2] Implement selection + **stateless per-call** fallback loop + per-call timeout + transient-only retry/backoff (`tenacity`) around driver calls in `backend/infra/llm.py` (depends T012) per [contracts/provider-selection-and-fallback.md](./contracts/provider-selection-and-fallback.md)
- [x] T018 [US2] Implement the error taxonomy (map each driver's vendor errors → `LlmErrorKind`: transient vs AUTH/INVALID_REQUEST vs CONTENT_REFUSAL) in `backend/infra/llm_drivers.py` and consume it in the fallback loop in `backend/infra/llm.py` (depends T010, T011, T017)
- [x] T019 [US2] Implement fail-closed failover contract enforcement (validate the failover result against the required schema/tools; raise `CONTRACT_UNSATISFIED`; set `served_by_fallback`; increment a dropped-/failover-counter) in `backend/infra/llm.py` (depends T017)

**Checkpoint**: fallback proven; non-retryable surfaced; fail-closed on failover; primary switch is config-only.

---

## Phase 5: User Story 3 — Every model call is costed and observable (Priority: P2)

**Goal**: Each call records provider/model/tokens/latency through #2's span seam and passes prompts/completions through redaction before they are logged/traced/stored; credentials never leave in the outbound prompt.

**Independent Test**: With the provider mocked to a known usage figure, drive a call and confirm its `llm_call` span carries provider, model, tokens-in/out, and latency; with usage omitted, confirm counts read "unknown"; confirm a seeded secret in a prompt never appears raw in the resulting span/log.

**Depends on**: US1 (the call site that emits telemetry); #2 (the span telemetry + redaction seam).

### Tests for User Story 3 ⚠️ (write first, must fail)

- [x] T020 [P] [US3] Unit tests in `tests/unit/test_llm_telemetry.py` (provider mocked): `generate()` opens an `LLM_CALL` span carrying provider, model, tokens-in/out, latency via `record_llm_usage` (FR-011/SC-004); usage omitted → counts "unknown"; a credential in the prompt is scrubbed from the outbound request and never appears raw in the span attributes or a log line (FR-012/SC-005)

### Implementation for User Story 3

- [x] T021 [US3] Wrap `generate()` with the #2 seam — open `span(tracer, name, SpanKind.LLM_CALL, correlation_id, parent_span_id, attrs)`, call `record_llm_usage(span, response.usage, response.model)`, record the (redacted-at-`TRACE`) prompt/completion attributes — in `backend/infra/llm.py` (depends T012) per [contracts/llm-seam.md](./contracts/llm-seam.md)
- [x] T022 [US3] Scrub CREDENTIAL-class content from the **outbound** prompt via the #2 `Redactor` before transmission (defense-in-depth FR-006a; do **not** strip operational identifiers the agent needs) in `backend/infra/llm.py` (depends T021)

**Checkpoint**: every call costed + observable; no seeded secret leaks; credentials never transmitted to a provider.

---

## Phase 6: User Story 4 — Substitutable in tests and provable on both providers (Priority: P2)

**Goal**: The seam is replaceable with a test double via DI (LLM mocked in unit tests); the service is operationally ready only when ≥1 provider is reachable; the capability is exercisable against each provider independently (the seeded both-providers gate).

**Independent Test**: Substitute a test double via DI and confirm consumers run unchanged with no real provider call; toggle provider reachability and confirm `/ready` reflects the at-least-one-reachable gate; run the seeded check against each configured provider.

**Depends on**: US1, US2, US3.

### Tests for User Story 4 ⚠️ (write first, must fail)

- [x] T023 [P] [US4] Unit test in `tests/unit/test_llm_di_substitution.py`: `app.dependency_overrides[get_llm] = FakeLlm` → the consuming component runs unchanged and makes **0** real provider calls (FR-016/SC-008)
- [x] T024 [P] [US4] Integration test in `tests/integration/test_llm_readiness.py`: `/ready` reports the `llm` dependency healthy iff ≥1 provider reachable; **503 only when none** reachable; recovers to **200** once one returns (FR-019/SC-010); boot is not crashed by unreachability
- [x] T025 [P] [US4] e2e test in `tests/e2e/test_llm_e2e.py`: a synthetic call through the DI seam with the primary forced down completes via the secondary (`served_by_fallback`); its `llm_call` span is redacted (**0** seeded-secret leaks); the seeded both-providers check runs to completion against **each** provider independently (SC-001/SC-003/SC-005/SC-006)

### Implementation for User Story 4

- [x] T026 [US4] Implement `check_llm(settings, container)` (healthy iff ≥1 configured provider reachable; secret-free `detail`; bounded by `startup.dependency_timeout_s`) in `backend/infra/health.py` (depends T013) per [contracts/llm-config-and-readiness.md](./contracts/llm-config-and-readiness.md)
- [x] T027 [US4] Add the `llm` probe to `run_readiness_probes` so `/ready` aggregates it (the existing `all(d.healthy)` over an any-reachable `llm` dep yields the at-least-one-reachable gate) in `backend/routers/health.py` (depends T026)
- [x] T028 [US4] Seed the `llm_provider` both-providers gate in `config/eval_thresholds.yaml` (a minimal generate-and-validate check that runs per configured provider; wired as a required CI check; full harness owned by #13) (depends T012)

**Checkpoint**: substitutable in tests; readiness gate enforced; both-providers gate seeded; e2e green.

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Defensibility, the no-bypass guard, and coverage.

- [x] T029 [P] Record decisions LP1–LP11 (provider pair, SDK transport, stateless fallback, fail-closed contract, readiness gate, usage normalization, redaction wiring, error taxonomy/timeout/retry, Ollama compose + per-tier test strategy, singleton/registration, structured-output per provider) in `DECISIONS.md`
- [x] T030 [P] Run `quickstart.md` validation end-to-end and fix any drift in `specs/003-llm-provider/quickstart.md`
- [x] T031 Verify the no-bypass guard: `google-genai`/`ollama` imported only in `backend/infra/llm_drivers.py` (grep + `import-linter`); add a ruff banned-import / import-linter note if needed in `pyproject.toml`
- [x] T032 Verify coverage ≥80% on new code (higher on the fail-closed contract-validation path in `backend/infra/llm.py`); run all three tiers + `make lint` green locally and in CI

---

## Dependencies & Execution Order

### Phase dependencies

- **Setup (P1)**: no dependencies — start immediately.
- **Foundational (P2)**: depends on Setup — **blocks all stories**.
- **US1 (P3)**: depends on Foundational. The MVP.
- **US2 (P4)**: depends on US1 (wraps the seam with selection/fallback).
- **US3 (P5)**: depends on US1 (wraps the call site with telemetry/redaction) + #2.
- **US4 (P6)**: depends on US1 + US2 + US3 (proves the whole + readiness).
- **Polish (P7)**: depends on all stories.

### Within each story

- Tests written and **failing** before implementation (Principle II).
- Domain types → settings → drivers → adapter → provider/DI wiring.

### Parallel opportunities

- Setup: T002, T003, T004 in parallel (T001 first — the others assume the deps exist).
- US1 tests T007/T008/T009 in parallel; drivers T010 then T011 (same file — not parallel).
- US4 tests T023/T024/T025 in parallel.
- Polish T029/T030 in parallel.
- Note: US2/US3/US4 are **not** mutually parallel (each builds on US1's `llm.py`) — a single-author dependency chain, not a staffing fan-out.

---

## Parallel Example: User Story 1

```bash
# Write US1 tests together (they must fail first):
Task: "Unit tests for the uniform seam in tests/unit/test_llm_seam.py"
Task: "Integration test for real Ollama in tests/integration/test_llm_ollama.py"
Task: "Gemini mapping (mocked HTTP) + gated live test in tests/integration/test_llm_gemini_mapping.py"
```

---

## Implementation Strategy

### MVP first (US1 only)

1. Setup → Foundational → US1.
2. **STOP and VALIDATE**: a real call works through the one seam on the primary; consumers obtain it via
   `Depends(get_llm)`; no vendor SDK is referenced outside `llm_drivers.py`.
3. This alone unblocks every later model-using component (#6–#10, #13) at the seam level and is committable.

### Incremental delivery (commit per milestone, PR ≤ ~400 lines)

1. US1 (seam) → US2 (fallback) → US3 (telemetry/redaction) → US4 (provable + readiness).
2. Each milestone keeps all three test tiers green; no later story leaves an earlier one broken.

---

## Notes

- [P] = different files, no dependency on an incomplete task.
- Tests precede implementation and must fail first (Principle II).
- Commit after each task or logical group; keep PRs focused (≤ ~400 lines).
- **One** new compose service is introduced (`ollama`, the local fallback runtime) — see Complexity Tracking in [plan.md](./plan.md).
- Vendor SDKs (`google-genai`, `ollama`) live **only** in `backend/infra/llm_drivers.py` (no-bypass FR-001).
- The fail-closed contract-validation path is the safety boundary here: prioritize its coverage above all else.
