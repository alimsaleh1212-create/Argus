---
description: "Task list — Alert Ingestion Pipeline (#4)"
---

# Tasks: Alert Ingestion Pipeline

**Input**: Design documents from `specs/004-ingestion-pipeline/`
**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md),
[data-model.md](./data-model.md), [contracts/](./contracts/)

**Tests**: REQUIRED (Sentinel constitution Principle II — Test-First, Three-Tier). Each story is
**test-first**: write the listed tests, watch them fail, then implement until green. No LLM in this
component ⇒ the both-providers eval gate is **N/A**; the existing **smoke** + **redaction** gates cover it.

**Organization**: by user story (US1 → US2 → US3), matching the plan's internal milestones (a → b → c).

## Format: `[ID] [P?] [Story] Description`

- **[P]**: parallelizable (different files, no incomplete dependency)
- **[Story]**: US1 / US2 / US3 (Setup, Foundational, Polish carry no story label)
- Exact file paths are given in every task.

## Path summary (from plan.md)

Fills reserved #1 seams; new files marked `+`. `backend/domain/incident.py +`, `services/wazuh.py +`,
`services/intake.py +`, `services/grounding.py +`, `services/pipeline.py +`, `repositories/incidents.py +`,
`db/migrations/versions/0003_incidents.py +`; FILL `routers/ingest.py`, `infra/cache.py`, `infra/queue.py`,
`worker.py`; EDIT `infra/config.py`, `infra/health.py`, `routers/health.py`, `dependencies.py`, `main.py`,
`routers/__init__.py`, `compose.yaml`, `pyproject.toml`, `.env.example`, `config/eval_thresholds.yaml`.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: dependency + fixtures plumbing every later phase needs.

- [X] T001 Add `redis>=5` to `[project.dependencies]` and remove `backend/worker.py`, `backend/routers/ingest.py`, `backend/infra/cache.py`, `backend/infra/queue.py` from `[tool.coverage.run] omit` in `pyproject.toml`; run `uv lock`.
- [X] T002 [P] Create representative Wazuh sample fixtures in `tests/fixtures/wazuh_alerts/` — `ssh_bruteforce.json` (valid, rule.level≈10), `high_severity.json` (level≥12), `with_secret.json` (full_log carries a fake `AKIA…` key + `Bearer eyJ…` token + an email), `malformed.json` (not a Wazuh alert).
- [X] T003 [P] Update `.env.example` with `INGEST_WEBHOOK_TOKEN` (seeded to `secret/ingest`) and `SENTINEL__REDIS__URL` / `SENTINEL__INGEST__MAX_ALERT_BYTES` / `SENTINEL__INGEST__DEDUP_WINDOW_S` / `SENTINEL__INGEST__MAX_ATTEMPTS` placeholders.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: the Incident schema, persistence, settings, and Redis substrate that **all** stories import.

**⚠️ CRITICAL**: No user story can begin until this phase is complete.

- [X] T004 [P] Unit test the Incident domain contract (FIRST — must fail) in `tests/unit/test_incident_schema.py`: `IncidentStatus`/`Severity` enums, `WazuhAlert` tolerates extra fields (`extra="ignore"`), `NormalizedEvent`/`Evidence`/`Incident`/`IngestResult` validate per [data-model.md](./data-model.md).
- [X] T005 Implement `backend/domain/incident.py` (pure Pydantic, no outward imports): `IncidentStatus` (received/grounding/grounded/failed), `Severity`, `WazuhAlert`/`WazuhRule`/`WazuhAgent`, `NormalizedEvent`, `Evidence`, `Incident`, `IngestResult` → makes T004 green.
- [X] T006 [P] Unit test settings (FIRST — must fail) in `tests/unit/test_config_ingest.py`: `RedisSettings` + `IngestSettings` defaults & `extra="forbid"`; `"redis"`/`"ingest"` accepted as known sections; `ingest.webhook_vault_path` auto-appended to `vault.required_paths`.
- [X] T007 Edit `backend/infra/config.py`: add `RedisSettings` + `IngestSettings`, register both on `Settings`, extend `_KNOWN_SENTINEL_SECTIONS` with `"redis"`,`"ingest"`, add a `model_validator` appending `ingest.webhook_vault_path` to `vault.required_paths` → makes T006 green.
- [X] T008 Create Alembic migration `backend/db/migrations/versions/0003_incidents.py` (`down_revision = "0002"`): `incidents` table (cols per [data-model.md](./data-model.md), `raw_alert`/`normalized_event`/`evidence` JSONB) + indexes `ix_incidents_status`, `ix_incidents_dedup_fingerprint`, `ix_incidents_correlation_id`; reversible `downgrade()`.
- [X] T009 Integration test the migration round-trip in `tests/integration/test_incidents_migration.py`: upgrade creates `incidents` + indexes; downgrade drops them cleanly.
- [X] T010 Integration test `IncidentRepository` against real Postgres (FIRST — must fail) in `tests/integration/test_incident_repository.py`: `create`/`get`/`get_by_fingerprint`/`claim_for_grounding` (atomic, second call returns `False`)/`set_grounded`/`bump_attempt`/`mark_failed`/`list_non_terminal`.
- [X] T011 Implement `backend/repositories/incidents.py` (`IncidentRepository`, the only module touching `incidents`) → makes T010 green.
- [X] T012 Integration test `RedisTaskQueue` against real Redis (FIRST — must fail) in `tests/integration/test_queue.py`: `enqueue`→`dequeue` returns the id; `ack` removes from processing; `recover()` drains a stranded processing entry back to the main queue.
- [X] T013 Implement `backend/infra/cache.py`: `CacheProvider` (Provider protocol — one `redis.asyncio` pool built on startup, disposed on shutdown). Redis imported **only** here and in `queue.py` (no-bypass).
- [X] T014 Implement `backend/infra/queue.py`: `QueueProvider` + `RedisTaskQueue` (`enqueue` LPUSH / `dequeue` BLMOVE main→processing / `ack` LREM / `recover` drain) per [contracts/queue-and-worker.md](./contracts/queue-and-worker.md) → makes T012 green.
- [X] T015 Register `CacheProvider` + `QueueProvider` in `backend/main.py::_bootstrap_providers` (after `observability`); add `get_cache()`, `get_queue()`, `get_incident_repo()` Depends accessors in `backend/dependencies.py`.
- [X] T016 Add `check_redis(settings)` in `backend/infra/health.py` (PING with per-dep timeout, redaction-safe detail) and include it in `run_readiness_probes` in `backend/routers/health.py` (FR-014).
- [X] T017 Integration test `/ready` returns 503 when Redis is unreachable in `tests/integration/test_ready_redis.py`.
- [X] T018 Activate the reserved `redis` service in `compose.yaml` (`redis:7`, `redis-cli ping` healthcheck) and add `redis: { condition: service_healthy }` to the `api` `depends_on`.

**Checkpoint**: schema, persistence, queue, dedup substrate, and readiness are live — stories can begin.

---

## Phase 3: User Story 1 — Ingest a Wazuh alert (Priority: P1) 🎯 MVP

**Goal**: `POST /ingest/wazuh` does validate → redact → persist → enqueue → `202` (dedup added in US3).

**Independent Test**: POST a sample alert → `202` + one `received` Incident + one queued job; `401` without a token; `422` malformed; `413` oversize; `503` when Redis is down; a planted secret never appears unredacted.

### Tests for User Story 1 (write FIRST — must fail)

- [X] T019 [P] [US1] Unit test the Wazuh adapter in `tests/unit/test_wazuh_adapter.py`: `WazuhAlert`→`NormalizedEvent` mapping; `rule.level`→`Severity` band table; missing level ⇒ `MEDIUM` + `severity_defaulted` flag; `content_signature` ignores the volatile timestamp.
- [X] T020 [P] [US1] Unit test `intake.accept()` with faked repo/queue/cache/redactor in `tests/unit/test_intake.py`: happy path returns `IngestResult(received, deduplicated=False)` and enqueues once; redaction error ⇒ fails closed (nothing persisted/enqueued); enqueue failure ⇒ Incident rolled back (no orphan).
- [X] T021 [P] [US1] e2e test the endpoint in `tests/e2e/test_ingest_e2e.py`: `202` + exactly one Incident + one job; `401` (no token); `422` (malformed.json); `413` (oversize body); `503` (Redis down) with no orphan Incident.
- [X] T022 [P] [US1] e2e test redaction through the ingest path in `tests/e2e/test_ingest_redaction.py`: POST `with_secret.json` → the stored `incident.raw_alert`, the queue message, and emitted log/span output contain only `[REDACTED:*]` forms (SC-005).

### Implementation for User Story 1

- [X] T023 [US1] Implement `backend/services/wazuh.py`: parse `WazuhAlert`→`NormalizedEvent`, severity band, and a `content_signature()` helper → makes T019 green.
- [X] T024 [US1] Implement `backend/services/intake.py::accept(session, queue, redactor, settings, alert)`: redact (`SNAPSHOT`/`LOG`) → persist `Incident(received)` → `enqueue`; enqueue failure rolls back the insert and raises (→ `503`); **no dedup branch yet** → makes T020 green.
- [X] T025 [US1] Implement the webhook auth guard in `backend/routers/ingest.py` (or a small `infra` helper): constant-time compare `Authorization: Bearer` against the Vault `secret/ingest` token resolved at startup; missing/invalid ⇒ `401`.
- [X] T026 [US1] Implement `POST /ingest/wazuh` in `backend/routers/ingest.py` (auth → size guard `413` → parse `WazuhAlert` `422` → `intake.accept` → `202`) and wire `ingest.router` into `backend/routers/__init__.py::api_router`.
- [X] T027 [US1] Update `compose.yaml`: `vault-seed` writes `secret/ingest` (from `INGEST_WEBHOOK_TOKEN`) and add `secret/ingest` to the `api` `SENTINEL__VAULT__REQUIRED_PATHS` → makes T021/T022 green.

**Checkpoint**: MVP — alerts are authenticated, validated, redacted, durably recorded, and queued.

---

## Phase 4: User Story 2 — Worker grounds the incident (Priority: P2)

**Goal**: the async worker consumes the queue, grounds the Incident deterministically, and hands off to the (stub) pipeline seam.

**Independent Test**: enqueue a job → run the worker → Incident reaches `grounded` with evidence populated and the handoff invoked once; a retried/duplicate delivery doesn't double-process; a crash mid-job is recovered to a terminal state.

### Tests for User Story 2 (write FIRST — must fail)

- [X] T028 [P] [US2] Unit test `grounding.ground()` in `tests/unit/test_grounding.py`: deterministic `Evidence` (verdict=`rule_match`, severity from band, one-line `summary`, empty `retrieved_context`, flags) with no I/O.
- [X] T029 [P] [US2] Integration test the worker in `tests/integration/test_worker.py` (real Redis + Postgres): claim→ground→`set_grounded`→ack; re-delivery of an already-`grounded` Incident is skipped (idempotent); forced exception bumps attempts and at the budget marks `failed`; a simulated crash (no ack) is reclaimed by `recover()` on restart.
- [X] T030 [P] [US2] e2e test in `tests/e2e/test_pipeline_e2e.py`: POST a sample alert → run the worker → Incident `grounded` with `evidence`/`normalized_event` populated (SC-007).

### Implementation for User Story 2

- [X] T031 [P] [US2] Implement `backend/services/grounding.py::ground(incident) -> Evidence` (pure, deterministic, no LLM) → makes T028 green.
- [X] T032 [P] [US2] Implement `backend/services/pipeline.py::dispatch_to_pipeline(incident)` — logging no-op stub (the supervisor seam #7 fills; signature frozen).
- [X] T033 [US2] Implement `backend/worker.py::main()`: build container/providers, `await queue.recover()`, then the consume loop (`dequeue` → `bind_incident` → `span` → `claim_for_grounding` → `get` → `ground` → `set_grounded` → `dispatch_to_pipeline` → `ack`), with bounded retry → `failed` and idempotent skip → makes T029/T030 green.
- [X] T034 [US2] Activate the reserved `worker` container in `compose.yaml` (`command: ["python","-m","backend.worker"]`; `depends_on` redis healthy + migrate/vault-seed completed; `SENTINEL__VAULT__REQUIRED_PATHS` includes `secret/ingest`).

**Checkpoint**: full spine — an alert flows source → queue → worker → grounded Incident.

---

## Phase 5: User Story 3 — Deduplicate repeat alerts (Priority: P3)

**Goal**: a repeat of a recently-seen alert collapses onto the existing Incident instead of creating a new one.

**Independent Test**: POST the same alert twice within the window → exactly one Incident; the second response is `deduplicated=true` with the existing id; after the window a new Incident is created.

### Tests for User Story 3 (write FIRST — must fail)

- [X] T035 [P] [US3] Unit test fingerprint determinism in `tests/unit/test_dedup_fingerprint.py`: identical alerts → identical fingerprint (timestamp excluded); different rule/agent/content → different fingerprint; computed over redacted content.
- [X] T036 [P] [US3] Integration test the dedup helpers against real Redis in `tests/integration/test_dedup.py`: `claim_fingerprint` returns `True` first then `False` within TTL; the key expires after `dedup_window_s`.
- [X] T037 [P] [US3] e2e test in `tests/e2e/test_dedup_e2e.py`: POST the same alert twice within the window → one Incident, second response `200` `deduplicated=true`; (optionally, with a short window) a later POST creates a second Incident.

### Implementation for User Story 3

- [X] T038 [US3] Implement `fingerprint()` + `claim_fingerprint()`/`lookup_fingerprint()` (`SET NX EX` / `GET`) in `backend/infra/cache.py` → makes T035/T036 green.
- [X] T039 [US3] Add the dedup branch to `backend/services/intake.py::accept()`: compute fingerprint → `claim_fingerprint`; on a hit, `lookup_fingerprint` → `get_by_fingerprint` and return `IngestResult(deduplicated=True)` (no persist, no enqueue) → makes T037 green.

**Checkpoint**: all three stories independently functional; spec spine + dedup complete.

---

## Phase 6: Polish & Cross-Cutting Concerns

- [X] T040 [P] Update `config/eval_thresholds.yaml` notes: the **smoke** gate now brings up `redis` + `worker`; the **redaction** gate is exercised through the `/ingest/wazuh` path. (No new gate.)
- [X] T041 [P] Extend the no-bypass guard so `redis` is importable **only** under `backend/infra/` — update `tests/integration/test_precommit_gates.py` (and/or the `import-linter` config) and run `uv run lint-imports`.
- [X] T042 [P] Record decisions ID1–ID11 and the `redis`+`worker` activation in `DECISIONS.md`.
- [X] T043 Run [quickstart.md](./quickstart.md) end-to-end against a fresh `docker compose up` (POST → grounded → dedup → redaction → Redis-down resilience) and confirm ≥80% coverage on new code.

---

## Dependencies & Execution Order

### Phase dependencies

- **Setup (P1)**: no dependencies — start immediately.
- **Foundational (P2)**: depends on Setup — **blocks all stories**.
- **US1 (P3)** → **US2 (P4)** → **US3 (P5)**: each starts after Foundational. US1 is the MVP. US2 consumes what US1 enqueues (but is independently testable by enqueuing directly). US3 edits `intake.py` (US1's file) — sequence US3 after US1.
- **Polish (P6)**: after the desired stories are green.

### Critical edges

- T005 (schema) ← everything. T007 (settings) ← T013/T014 (Redis), T024 (intake). T008/T011 (migration+repo) ← T024, T033. T013/T014 (cache+queue) ← T024 (enqueue), T033 (consume), T038 (dedup).
- T023 (wazuh) ← T024 (intake) ← T026 (router). T031/T032 ← T033 (worker). T038 (dedup helpers) ← T039 (intake dedup branch).

### Within each story

Tests (FIRST, failing) → domain/services → repository/infra wiring → router/worker → compose. Models before services; services before endpoints; commit after each task or logical group.

---

## Parallel opportunities

- **Setup**: T002, T003 in parallel (T001 edits `pyproject.toml` alone).
- **Foundational**: T004 ∥ T006 (independent test files); after impl, T012 (queue test) is independent of the repo tests.
- **US1 tests**: T019 ∥ T020 ∥ T021 ∥ T022 (four distinct test files).
- **US2**: T028 ∥ T029 ∥ T030 (tests); T031 ∥ T032 (grounding vs pipeline, distinct files).
- **US3 tests**: T035 ∥ T036 ∥ T037.
- **Polish**: T040 ∥ T041 ∥ T042.

### Parallel example — User Story 1

```bash
# Write the four US1 tests together (all must fail before implementing):
Task: "Unit test Wazuh adapter in tests/unit/test_wazuh_adapter.py"
Task: "Unit test intake.accept in tests/unit/test_intake.py"
Task: "e2e test endpoint in tests/e2e/test_ingest_e2e.py"
Task: "e2e test redaction in tests/e2e/test_ingest_redaction.py"
```

---

## Implementation Strategy

### MVP first (US1 only)

1. Phase 1 Setup → 2. Phase 2 Foundational (blocks everything) → 3. Phase 3 US1.
4. **STOP & VALIDATE**: alerts accepted, redacted, persisted (`received`), enqueued; rejection paths clean.
5. This is a coherent, demoable slice on its own (incidents visible; worker simply not yet draining).

### Incremental delivery (matches plan milestones a→b→c)

- Setup + Foundational + **US1** → milestone (a): the `202` front door (PR ≤ ~400 lines).
- **US2** → milestone (b): the worker + grounding spine — alert flows end to end to `grounded`.
- **US3** → milestone (c): dedup + (Foundational already provides) reliable recovery & readiness.
- Then Polish → DECISIONS, no-bypass guard, eval-note, quickstart validation, coverage gate.

### Definition of done (per constitution I/II)

Unit + integration + e2e green in CI; ≥80% coverage on new code; `ruff` + `lint-imports` clean; the
**smoke** gate brings up `redis` + `worker` from a fresh clone; the **redaction** gate passes through the
ingest path; committed and pushed behind focused PRs.

---

## Notes

- `[P]` = different files, no incomplete dependency; `[Story]` maps each task to US1/US2/US3 for traceability.
- No LLM in this component ⇒ the both-providers gate is **N/A** here (it returns with #8's triage F1 gate).
- Verify each test fails before implementing it; commit after each task or logical group.
- Stop at any checkpoint to validate the story independently.
