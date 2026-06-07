---
description: "Task list for SPEC-observability (Component #2) implementation"
---

# Tasks: Observability & Redaction (Cross-Cutting Foundation)

**Input**: Design documents from `specs/002-observability-redaction/`

**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md),
[data-model.md](./data-model.md), [contracts/](./contracts/)

**Tests**: REQUIRED (constitution Principle II — Test-First, Three-Tier, Eval-Gated). Every story
carries unit/integration/e2e tasks written **before** implementation and green in CI before the spec
is "done". ≥80% coverage on new code, **higher on the redaction safety boundary** (Principle III).

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different files, no dependency on an incomplete task)
- **[Story]**: US1–US4 (story phases only; Setup/Foundational/Polish carry no story label)
- All paths are repo-root-relative.

## ⚠️ Story sequencing note (read first)

This is a **cross-cutting** component: the four user stories are co-prioritized but not fully
independent — logging redaction (**US2**) and span-attribute redaction (**US3**) both consume the
**US1** `Redactor`, and the off-path/overhead guarantee (**US4**) wraps the US1–US3 seam into one
provider. Phases below follow **priority *and* build dependency** (they coincide here):

`Setup → Foundational → US1 (P1, redaction = MVP) → US2 (P1, logging) → US3 (P2, tracing) → US4 (P2, off-path + unified seam) → Polish`

Each story stays independently **testable** via its Independent Test (spec.md). The **MVP** is through
**US1**: the `Redactor` proven against seeded secrets/PII at every boundary. This component fills the
#1-reserved seams `backend/infra/redaction.py` + `logging.py`; it adds **no new compose service**.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Dependencies and build/coverage config — no runtime behavior yet.

- [X] T001 Add observability deps via `uv add`: `opentelemetry-sdk`, `opentelemetry-api`, `presidio-analyzer`, `presidio-anonymizer`, `spacy`; regenerate committed `uv.lock`
- [X] T002 [P] Add a spaCy model build step `RUN python -m spacy download en_core_web_sm` to `deploy/api/Dockerfile` (after deps install)
- [X] T003 [P] Update `pyproject.toml` coverage: remove `backend/infra/redaction.py` from `[tool.coverage.run] omit`; confirm `backend/infra/tracing.py` and `backend/infra/observability.py` are measured

**Checkpoint**: `uv sync` resolves; image build downloads the spaCy model; coverage targets the new modules. 

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Shared settings + shared pure types every story imports. **No user story can begin until this phase is complete.**

- [X] T004 Add `ObservabilitySettings` (fields per [data-model.md](./data-model.md) §Settings: `presidio_enabled`, `spacy_model`, `entropy_threshold`, `span_attr_max_bytes`, `export_batch_size`, `export_interval_ms`, `trace_to_stdout`) to `backend/infra/config.py`, register it on `Settings`, and add `"observability"` to `_KNOWN_SENTINEL_SECTIONS`; document the section (no values) in `.env.example`
- [X] T005 [P] Define redaction domain types `SensitiveClass`, `Boundary`, `RedactionPolicy` (with the default class×boundary matrix + invariants: `CREDENTIAL` covers every `Boundary`; internal boundaries excluded for PII/identifiers) in `backend/domain/redaction.py` per [contracts/redaction-policy.md](./contracts/redaction-policy.md)

**Checkpoint**: `Settings` validates with the new section; domain enums/policy import with no outward deps; `import-linter` contracts still pass.

---

## Phase 3: User Story 1 — Nothing sensitive leaves the service in the clear (Priority: P1) 🎯 MVP

**Goal**: One `Redactor` (Presidio PII + deterministic secret scrubber) behind the reserved Protocol, fail-closed, enforcing the class×boundary policy at every boundary.

**Independent Test**: Feed payloads seeded with fake secrets + PII through each boundary; every seeded value is redacted at every output boundary, credentials are redacted internally too, and raw identifiers survive only at `OPERATIONAL`/`MEMORY_WRITE`.

### Tests for User Story 1 ⚠️ (write first, must fail)

- [X] T006 [P] [US1] Unit tests in `tests/unit/test_redaction.py`: credentials redacted at **all** boundaries incl. `MEMORY_WRITE`/`OPERATIONAL`; PII redacted at output boundaries; raw IP/hostname survives at `OPERATIONAL` but is redacted at `LOG`/`PROMPT`; nested-mapping/list traversal at depth; idempotency (re-redact = no-op); high-entropy token caught with no explicit pattern; redactor error → `[REDACTION-FAILED]` (fail-closed)
- [X] T007 [P] [US1] Integration test in `tests/integration/test_redaction_presidio.py`: real in-process Presidio detects email/IP/credit-card/IBAN/phone; deterministic-path toggle (`presidio_enabled=False`) leaves pattern-only detection

### Implementation for User Story 1

- [X] T008 [US1] Implement the deterministic secret scrubber (explicit regex set: AWS-style keys, bearer/JWT, PEM private-key blocks, `secret=`/`token=`/`apikey=` kv; **plus** a Shannon-entropy heuristic) in `backend/infra/redaction.py`
- [X] T009 [US1] Implement Presidio-backed PII redaction and compose it with the scrubber behind `Redactor` — `redact_text(text, boundary)` / `redact_mapping(data, boundary)`: policy-driven by class×boundary, recursive, idempotent, fail-closed, never mutates input — in `backend/infra/redaction.py` (depends T005, T008)
- [X] T010 [US1] Implement `RedactorProvider` (lifespan singleton — build the Presidio engine + scrubber **once**) and `get_redactor()` in `backend/infra/redaction.py` (depends T009)
- [X] T011 [US1] Seed the `redaction` gate in `config/eval_thresholds.yaml` (a seeded fake secret/PII never appears unredacted in any log/trace/snapshot/dashboard view); wire as a required CI check (harness owned by #13)

**Checkpoint**: `Redactor` proven at every boundary; the `redaction` gate is enforced in CI. **MVP reached.**

---

## Phase 4: User Story 2 — Structured, incident-correlated logging (Priority: P1)

**Goal**: Every log line structured, carrying a correlation id, and redacted before emit — with no bypass path.

**Independent Test**: Drive one (synthetic) incident; every line is structured, shares one correlation id resolving to that incident, and contains no raw sensitive value; a no-incident line still renders safely.

**Depends on**: US1 (the redactor the log processor calls).

### Tests for User Story 2 ⚠️ (write first, must fail)

- [X] T012 [P] [US2] Unit tests in `tests/unit/test_logging.py`: line is structured + carries the bound `correlation_id`; filtering by id returns exactly that incident's lines (SC-002); seeded secret/PII never appears raw; no-incident line renders `correlation_id="-"` without error (FR-011); processor error drops the offending field (fail-closed), never the whole line, never raw

### Implementation for User Story 2

- [X] T013 [US2] Insert a redaction processor into the `configure_logging` chain (before `JSONRenderer`, alongside `merge_contextvars`) running the redactor at the `LOG` boundary, fail-closed per field, in `backend/infra/logging.py` (depends T010) per [contracts/logging-contract.md](./contracts/logging-contract.md)
- [X] T014 [US2] Add `bind_incident(correlation_id)` and contextvar binding/clearing helpers (sets `correlation_id` + `trace_id` in `structlog.contextvars`) in `backend/infra/logging.py`

**Checkpoint**: no logging path bypasses redaction; correlation id stitches an incident's lines.

---

## Phase 5: User Story 3 — An incident is a trace tree with per-step telemetry (Priority: P2)

**Goal**: Each step/tool/retrieval is a span; an incident is one trace tree; spans carry tokens/model/latency + redacted I/O; persisted to the Postgres trace store, read off-path.

**Independent Test**: Drive a synthetic incident through nested spans; read `trace_spans` by correlation id → one tree, no orphans; `llm_call` spans carry tokens-in/out + model + latency (or `unknown`); attributes redacted + truncated.

**Depends on**: US1 (redactor for span attributes); Foundational settings.

### Tests for User Story 3 ⚠️ (write first, must fail)

- [X] T015 [P] [US3] Unit tests in `tests/unit/test_tracing.py` (LLM mocked): `record_llm_usage` sets tokens-in/out + model + latency; missing provider usage → `unknown` (SC-004); oversized attribute truncated **after** redaction (no raw substring re-exposed, FR-017); span attributes redacted at `TRACE`
- [X] T016 [P] [US3] Integration test in `tests/integration/test_tracing.py` (Postgres testcontainer): a synthetic incident yields exactly one trace tree with no orphans (SC-003); spans persisted + queryable by correlation id; the `trace_spans` migration applies to an empty DB and rolls back cleanly

### Implementation for User Story 3

- [X] T017 [P] [US3] Define telemetry domain types `Span`, `SpanStatus`, `TraceTree`, `TelemetryRecord`, `LogContext` in `backend/domain/telemetry.py` per [data-model.md](./data-model.md) (pure types)
- [X] T018 [US3] Add the `trace_spans` Alembic migration (columns + indexes on `correlation_id`, `trace_id`, `(trace_id, parent_span_id)`; reversible) in `backend/db/migrations/versions/` per [contracts/span-trace-schema.md](./contracts/span-trace-schema.md)
- [X] T019 [US3] Implement `TraceRepository` (batch-persist spans; query by correlation id; assemble `TraceTree` with no-orphan invariant; derive `TelemetryRecord`) in `backend/repositories/trace_repository.py` (depends T017, T018)
- [X] T020 [US3] Implement the OTel tracer setup + `span(name, kind, **attrs)` helper (redacts attributes at `TRACE`, truncates to `span_attr_max_bytes`) + `record_llm_usage(span, usage)` in `backend/infra/tracing.py` (depends T010, T017)
- [X] T021 [US3] Implement the custom Postgres `SpanExporter` + `BatchSpanProcessor` wiring (export off the synchronous path; failed flush → dropped-batch counter, never an incident failure) in `backend/infra/tracing.py` (depends T019, T020)

**Checkpoint**: an incident reconstructs as one trace tree with per-step telemetry; export is off-path.

---

## Phase 6: User Story 4 — Observability that does not slow the incident path (Priority: P2)

**Goal**: Bundle logger+tracer+redactor into one DI seam built once as a lifespan singleton; prove the synchronous overhead is within budget and export never fails an incident.

**Independent Test**: Measure synthetic disposition time with observability enabled vs minimized (≤5% p95); with the export destination unreachable, incidents still complete on time.

**Depends on**: US1, US2, US3.

### Tests for User Story 4 ⚠️ (write first, must fail)

- [X] T022 [P] [US4] Integration test in `tests/integration/test_export_resilience.py`: exporter pointed at an unreachable DB mid-run → synthetic incident completes; dropped-batch counter increments; no raw content leaks (SC-006)
- [X] T023 [P] [US4] e2e overhead test in `tests/e2e/test_overhead.py`: synchronous observability overhead ≤ **5% p95** of synthetic disposition time and 100% of span export off-path (SC-005)
- [X] T024 [P] [US4] e2e test in `tests/e2e/test_observability_e2e.py`: a synthetic incident through the unified seam produces one trace tree (no orphans) and **zero** seeded-secret leaks across log/trace/prompt/snapshot (SC-001, SC-003)

### Implementation for User Story 4

- [X] T025 [US4] Implement the unified observability seam (`Observability` bundle = logger factory + tracer + redactor; `span()`, `record_llm_usage`, `bind_incident`, `get_logger`, `get_redactor` re-exported) in `backend/infra/observability.py` (depends T010, T013, T020) per [contracts/observability-seam.md](./contracts/observability-seam.md)
- [X] T026 [US4] Implement `ObservabilityProvider` (`name="observability"`, registered after `db_engine`): builds redactor + tracer/exporter once on startup, **force-flushes spans on shutdown** (FR-019) in `backend/infra/observability.py` (depends T021, T025)
- [X] T027 [US4] Register the provider and add `Depends()` providers `get_obs` / `get_redactor` / `get_tracer` reading `app.state.container` in `backend/dependencies.py` (depends T026)
- [X] T028 [US4] Add a CI guard (ruff banned-imports or an `import-linter` contract) blocking direct `logging`/`opentelemetry`/`presidio` imports outside `backend/infra/` (enforces the no-bypass rule FR-018) in `pyproject.toml`

**Checkpoint**: one DI seam; overhead within budget; export-down never fails an incident.

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Defensibility, coverage, and the freeze re-check.

- [X] T029 [P] Record decisions OD1–OD10 (OTel, Postgres trace store, correlation id, Presidio+scrubber, scope policy, fail-closed, singletons/off-path, log processor, token seam, ergonomics) in `DECISIONS.md`
- [X] T030 [P] Run `quickstart.md` validation end-to-end and fix any drift in `specs/002-observability-redaction/quickstart.md`
- [X] T031 Verify coverage ≥80% on new code (higher on `backend/infra/redaction.py`); run all three tiers + `make lint` (incl. the no-bypass guard) green locally and in CI
- [X] T032 Re-verify synchronous overhead within budget at the Tier-1 freeze checkpoint (SC-008) and note the measured figure in `DECISIONS.md`

---

## Dependencies & Execution Order

### Phase dependencies

- **Setup (P1)**: no dependencies — start immediately.
- **Foundational (P2)**: depends on Setup — **blocks all stories**.
- **US1 (P3)**: depends on Foundational. The MVP.
- **US2 (P4)**: depends on US1 (the redactor the log processor calls).
- **US3 (P5)**: depends on US1 (span-attribute redaction) + Foundational.
- **US4 (P6)**: depends on US1 + US2 + US3 (bundles them into one provider).
- **Polish (P7)**: depends on all stories.

### Within each story

- Tests written and **failing** before implementation (Principle II).
- Domain types → repository/migration → infra implementation → provider/DI wiring.

### Parallel opportunities

- Setup: T002, T003 in parallel.
- Foundational: T005 alongside T004.
- US1 tests T006/T007 in parallel; US3 T015/T016 and the pure-types T017 in parallel.
- US4 tests T022/T023/T024 in parallel.
- Polish T029/T030 in parallel.
- Note: US2/US3/US4 are **not** mutually parallel (US2 & US3 both need US1; US4 needs all) — this is a single-author dependency chain, not a staffing fan-out.

---

## Parallel Example: User Story 1

```bash
# Write US1 tests together (they must fail first):
Task: "Unit tests for the Redactor in tests/unit/test_redaction.py"
Task: "Integration test for real Presidio in tests/integration/test_redaction_presidio.py"
```

---

## Implementation Strategy

### MVP first (US1 only)

1. Setup → Foundational → US1.
2. **STOP and VALIDATE**: the `Redactor` holds at every boundary; the `redaction` eval gate is green.
3. This alone satisfies the constitution's redaction clause (Principle III) and is committable.

### Incremental delivery (commit per milestone, PR ≤ ~400 lines)

1. US1 (redaction) → US2 (logging) → US3 (tracing) → US4 (off-path + unified seam).
2. Each milestone keeps all three test tiers green; no later story leaves an earlier one broken.

---

## Notes

- [P] = different files, no dependency on an incomplete task.
- Tests precede implementation and must fail first (Principle II).
- Commit after each task or logical group; keep PRs focused (≤ ~400 lines).
- No new compose service is introduced — Postgres/MinIO from #1 are reused.
- Redaction is the safety boundary: prioritize its coverage and fail-closed behavior above all else.
