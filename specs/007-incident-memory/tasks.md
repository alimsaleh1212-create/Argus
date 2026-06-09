---
description: "Task list — Incident Memory (Temporal) (#6)"
---

# Tasks: Incident Memory (Temporal)

**Input**: Design documents from `specs/007-incident-memory/`
**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md),
[data-model.md](./data-model.md), [contracts/](./contracts/)

**Tests**: REQUIRED (Constitution II — three-tier + eval-gated, NON-NEGOTIABLE). Unit/integration/e2e plus the
**retrieval** (hit@k/MRR) and **temporal_memory** eval gates land with this component.

**Organization**: Tasks are grouped by user story (US1 P1, US2 P2, US3 P3) for independent implementation and
testing. This is a **big spec** → it commits at each internal milestone: **0 spike → a write → b retrieve →
c temporal**. The supervisor stays a pure deterministic state machine throughout (no memory dependency);
**redaction runs before every memory write**; a memory outage never blocks a disposition or crashes the worker.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different files, no dependency on an incomplete task)
- **[Story]**: US1 / US2 / US3 (setup, foundational, polish carry no story label)
- Exact file paths are in each description.

## Path Conventions

Modular monolith `backend/` (per [plan.md](./plan.md)); tests under `tests/{unit,integration,e2e,eval,fixtures}`.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: bring in the new dependency + service and confirm a green baseline.

- [X] T001 [P] Add deps in `pyproject.toml`: runtime `graphiti-core[google-genai]` (rides on the existing `google-genai>=2.8.0`; bundles the `neo4j` async driver + `GeminiClient`/`GeminiEmbedder`), dev `testcontainers[neo4j]`; then `uv lock` + `uv sync`. (plan Technical Context; research MD1)
- [X] T002 [P] Remove `backend/infra/memory.py` from `[tool.coverage.run] omit` in `pyproject.toml` so it is measured (the new `backend/domain/memory.py` and `backend/services/memory.py` are measured automatically).
- [X] T003 Configure `compose.yaml`: replace the reserved `neo4j:` block with `image: neo4j:5.26` — `NEO4J_AUTH=neo4j/<dev-pw>`, ports `7474`/`7687`, a named volume, a bolt healthcheck; have `vault-seed` write `secret/memory` (`username`/`password`/`uri`); give `worker` `depends_on: neo4j: {condition: service_healthy}` and add `secret/memory` to its `SENTINEL__VAULT__REQUIRED_PATHS`. (research MD8)
- [X] T004 Establish a green baseline: `uv sync` then `uv run pytest -q -m "not integration and not e2e"` passes before any change.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: the day-1 spike (the go/no-go gate) plus the pure types, Protocol, config, provider, episode-build
service, and worker wiring that **all** stories need.

**⚠️ CRITICAL**: No user-story work begins until this phase is complete and the existing suite stays green.

- [X] T005 **Milestone 0 — Graphiti spike (go/no-go)**: add a throwaway `scripts/memory_spike.py`; `docker compose up -d neo4j`; write a few sample-incident episodes, retrieve a similar one, and force a fact conflict to observe native invalidation; **measure** write + retrieval latency and per-episode graph-construction token cost; record a **go/no-go vs. the pgvector fallback in `DECISIONS.md`**. On "no-go", switch to the Contingency tasks (TC01–TC03) before proceeding. (research MD0; Constitution VI)
- [X] T006 [P] Create `backend/domain/memory.py` — pure types (`extra="forbid"`): `EntityKind` (`address`/`host`/`user`/`indicator`), `EntityRef`, `IncidentEpisode` (incident_id, observed_at, summary, verdict, severity, disposition, entities, fields), `TemporalFact` (entity, fact_type, value, valid_from, valid_until|None), `FactState` (fact|None, is_current, has_superseded), `MemoryHit` (incident_id, summary, disposition, observed_at, relevance∈[0,1]), `EpisodeQuery`, and the `MemoryStore` **Protocol**. Import `Severity` from `backend/domain/incident.py` (domain→domain allowed); no outward imports. (data-model §domain types; contracts/memory-store-contract.md)
- [X] T007 [P] Unit test `tests/unit/test_memory_types.py` — `IncidentEpisode` requires `incident_id`/`observed_at`; `EntityRef.kind` must be in the enum; `MemoryHit.relevance` ∈ [0,1]; `extra="forbid"` rejects unknown keys. (data-model)
- [X] T008 [P] Extend `backend/infra/config.py` — add `MemorySettings` (`enabled=True`, `backend: Literal["graphiti","pgvector"]="graphiti"`, `neo4j_uri="bolt://neo4j:7687"`, `neo4j_vault_path="secret/memory"`, `retrieval_k=5` `gt=0`, `retrieval_timeout_s=5.0` `gt=0`, `embedding_model="text-embedding-004"`); add `"memory"` to `_KNOWN_SENTINEL_SECTIONS`; add `memory: MemorySettings` to `Settings`; add a `model_validator` appending `neo4j_vault_path` to `vault.required_paths` (fail-boot if unseeded — mirror `_ensure_llm_vault_path_required`). (data-model §config; research MD5)
- [X] T009 [P] Unit test `tests/unit/test_memory_config.py` — defaults; `extra="forbid"` rejects unknown keys; `backend` accepts `graphiti`/`pgvector` only; `neo4j_vault_path` is forced into `vault.required_paths`.
- [X] T010 Replace the `backend/infra/memory.py` stub — implement `NullMemory(MemoryStore)` (no-op `write_episode`, `search_similar`→`[]`, `query_fact`→empty `FactState`); a `GraphitiMemory(MemoryStore)` **class shell** (holds the Graphiti client + `MemorySettings`; the three methods filled in US1/US2); and `MemoryProvider.build` as an async context manager — resolve Neo4j creds from Vault, build the async Graphiti client + run one-time index setup, `yield GraphitiMemory`, dispose the driver on exit; on `enabled=False` **or** a startup connection failure, **log and `yield NullMemory`** (never crash the worker). `graphiti_core`/`neo4j` are imported **only here**. (research MD1/MD6; contracts/memory-store-contract.md)
- [X] T011 [P] Create `backend/services/memory.py` — `record_episode(incident, store, redactor) -> None`: a pure `_extract_entities(normalized_event)` helper (src/dst address, host, user, indicators — absent fields yield no entity, never an error); **redact** summary/fields/entity values via the #2 `Redactor` (stored-snapshot boundary) **before** building the `IncidentEpisode`; `await store.write_episode(episode)`. Idempotent by `incident_id`. (data-model §redaction; contracts/memory-episode-schema.md; FR-001/005/007)
- [X] T012 [P] Unit test `tests/unit/test_memory_record.py` — `record_episode` with a **mock store + real `Redactor`**: a planted secret + PII in the evidence never appears in the `IncidentEpisode` handed to `write_episode`; entities are extracted from `normalized_event`; missing fields produce no entity (no error). (FR-005, FR-006a, SC-004)
- [X] T013 Wire the worker — `backend/worker.py`: register `MemoryProvider()`; add a `memory=None` param to `_run` (safe no-op when absent, mirroring `supervisor=None`); after `dispatch_to_pipeline` returns, reload the incident and, if it is terminal and `memory` is set, call `record_episode(...)` inside a `try/except` that **logs and swallows** any error (best-effort, off the disposition/ack path — never re-raised). (research MD3; FR-006)
- [X] T014 Unit test `tests/unit/test_memory_degrade.py` — `NullMemory`: write no-ops, `search_similar`→`[]`, `query_fact`→empty `FactState`; and `record_episode` swallows a store that raises (best-effort). (FR-006; US3 seed)

**Checkpoint**: types, config, provider, `NullMemory`, episode-build (with redaction), and worker wiring exist; the full existing suite stays green; `GraphitiMemory.write_episode`/`search_similar`/`query_fact` are still unfilled.

---

## Phase 3: User Story 1 — The system remembers a similar prior incident (Priority: P1) 🎯 MVP

**Goal** (Milestones **a** + **b**): write each disposed incident as a redacted, time-stamped episode, and
retrieve the closest prior incidents and their dispositions for a new one — the minimal "remember" loop.

**Independent Test**: process+dispose incident A, then a similar incident B; `search_similar(B)` returns A in
top-k with its disposition and observed time; an empty store returns `[]` (not an error).

### Implementation for User Story 1

- [X] T015 [US1] Implement `GraphitiMemory.write_episode` in `backend/infra/memory.py` — map `IncidentEpisode` → Graphiti `add_episode` (redacted JSON body of summary+fields+verdict+severity+disposition+entities, `reference_time=observed_at`); idempotent on `incident_id`. **Milestone a.** (data-model §Graphiti mapping; FR-001/007)
- [X] T016 [US1] Implement `GraphitiMemory.search_similar(query, *, k)` — Graphiti hybrid search → `list[MemoryHit]` (incident_id, summary, disposition, observed_at, relevance), top-k ordered by relevance desc; a read miss or timeout returns `[]`. **Milestone b.** (FR-002/009; contracts/memory-store-contract.md)
- [X] T017 [P] [US1] Integration test `tests/integration/test_graphiti_memory.py::test_write_then_retrieve` (`-m integration`) — against a **real Neo4j** (`testcontainers[neo4j]`): write 2–3 episodes, `search_similar` for one resembling a prior returns it in top-k with its disposition; an empty store returns `[]`. (US1 acceptance; SC-001)
- [X] T018 [US1] E2E test `tests/e2e/test_memory_e2e.py` — drive an incident to a terminal disposition through worker→supervisor (LLM faked at the driver boundary), then assert an episode was written and is retrievable via `search_similar`; the supervisor transition path is unchanged. (US1 independent test)
- [X] T019 [P] [US1] Create committed retrieval fixtures `tests/fixtures/memory_retrieval/*.json` — a labeled set: seed priors + held-out "new" incidents, each labeled with its expected prior, already-redacted. (contracts/memory-eval.md)
- [X] T020 [US1] Add the `retrieval` gate to `config/eval_thresholds.yaml` (`required: true`, `min_hit_at_k`, `k`, `min_mrr`; **no** `check_per_provider` — deterministic store-logic, provider-independent like `smoke`/`supervisor_routing`). (contracts/memory-eval.md; research MD7)
- [X] T021 [US1] Eval test `tests/eval/test_retrieval_gate.py` — seed priors, issue each new incident as an `EpisodeQuery`, compute hit@k + MRR, assert the committed thresholds; cold-start (empty store) queries are excluded, not counted as misses. (SC-001)

**Checkpoint**: incidents are remembered and similar priors + dispositions retrieved; the `retrieval` gate is green. **MVP complete (Milestones a + b).**

---

## Phase 4: User Story 2 — The system answers "what was true when" (Priority: P2)

**Goal** (Milestone **c**): facts are time-bounded; a conflicting update **invalidates** the prior fact (kept,
not deleted); a time-scoped query returns the correct current-vs-superseded state — the temporal differentiator.

**Independent Test**: record a fact benign@t1, malicious@t2; `query_fact(as_of=t1)`=benign (superseded),
`query_fact(now)`=malicious (current); the benign fact still exists (invalidated, not deleted).

### Implementation for User Story 2

- [X] T022 [US2] Implement `GraphitiMemory.query_fact(entity, fact_type, *, as_of=None)` in `backend/infra/memory.py` — read the entity's edges, select the one whose `[valid_at, invalid_at)` window contains `as_of` (or the open/current one when `as_of is None`) → `FactState` (`is_current = invalid_at is None`; `has_superseded =` any edge with non-null `invalid_at`); no match → empty `FactState`. (FR-004; data-model §Graphiti mapping)
- [X] T023 [US2] Confirm/enable conflicting-fact invalidation in `write_episode` — verify Graphiti's native **invalidate-not-delete** fires on a contradicting episode; add only the episode shaping needed to trigger it. **No deletion path is ever introduced.** (FR-003; US2)
- [X] T024 [P] [US2] Integration test `tests/integration/test_graphiti_memory.py::test_temporal_validity` — benign@t1 then malicious@t2: `query_fact(as_of=t1)`=benign (`is_current=False`), `query_fact(now)`=malicious (`is_current=True`), benign still present (`has_superseded=True`) — zero deletes. (SC-002, SC-005)
- [X] T025 [P] [US2] Unit test `tests/unit/test_memory_factstate.py` — the pure window-selection logic over a set of `TemporalFact`s: `as_of` picks the right window; current vs. superseded flags are correct — independent of the store. (FR-004)
- [X] T026 [P] [US2] Create temporal fixtures `tests/fixtures/memory_temporal/*.json` — changed-fact scenarios (`reputation_flip`, `host_role_change`, `no_destructive_delete`). (contracts/memory-eval.md)
- [X] T027 [US2] Add the `temporal_memory` gate to `config/eval_thresholds.yaml` (`required: true`, `pass_rate: 1.0`, `cases:`; no `check_per_provider`) and add eval test `tests/eval/test_temporal_gate.py` asserting current-vs-superseded over the fixtures. (SC-002; contracts/memory-eval.md)

**Checkpoint**: time-valid queries distinguish current vs. superseded; conflicts invalidate (not delete); the `temporal_memory` gate is green. **Milestone c complete.**

---

## Phase 5: User Story 3 — Memory never blocks or breaks the pipeline (Priority: P3)

**Goal**: memory is an enhancement, never a single point of failure — an outage degrades to empty results and
the disposition still completes; retrieval is bounded; writes are idempotent.

**Independent Test**: with Neo4j down (or `NullMemory`), incidents still reach a terminal disposition and the
worker keeps consuming; a read during the outage returns `[]`; a repeat write does not duplicate.

### Implementation for User Story 3

- [X] T028 [US3] Harden degradation in `backend/infra/memory.py` — `GraphitiMemory.search_similar`/`query_fact` are bounded by `retrieval_timeout_s` and on timeout/outage return `[]`/empty `FactState` (logged), never raise; confirm `MemoryProvider` already degrades to `NullMemory` on `enabled=False` or a startup connection failure (T010). (FR-006; research MD6)
- [X] T029 [P] [US3] Unit test `tests/unit/test_memory_degrade.py` (extend) — the provider degrades to `NullMemory` on a simulated connection failure; a `search_similar` timeout returns `[]`, not an exception. (FR-006)
- [X] T030 [US3] E2E test `tests/e2e/test_memory_e2e.py` (extend) — with memory unavailable (`NullMemory` / Neo4j stopped), an incident still reaches a terminal disposition and the worker continues to the next incident — zero crashes, no disposition blocked. (SC-003; US3 acceptance)
- [X] T031 [P] [US3] Integration test `tests/integration/test_graphiti_memory.py::test_idempotent_write` — writing the same `incident_id` twice does not duplicate the episode or double-apply facts. (FR-007)

**Checkpoint**: graceful degradation, bounded retrieval, and idempotency proven; memory is never a SPOF.

---

## Phase 6: Polish & Cross-Cutting Concerns

- [X] T032 [P] Record decisions in `DECISIONS.md` — the spike go/no-go (MD0), the Graphiti native-Gemini **Constitution VII deviation** + its mitigations (MD2), the provider-independent memory-eval justification (MD7), the worker best-effort off-path write (MD3), and the decided pgvector fallback (MD9).
- [X] T033 Verify the existing `redaction` gate's **`memory_write`** boundary is green — a planted secret/PII never appears unredacted in the Neo4j store or in any returned `MemoryHit`/`FactState`. (FR-006a, SC-004)
- [X] T034 [P] Run `uv run ruff check` + `uv run lint-imports` — confirm `backend/domain/memory.py` imports nothing outward, `services/memory.py`/`infra/memory.py` respect the layered contract, and `graphiti_core`/`neo4j` are imported **only** from `backend/infra/memory.py`.
- [X] T035 Verify coverage ≥80% on new code (higher on the degradation + redaction paths) via `uv run pytest --cov=backend`.
- [X] T036 Run [quickstart.md](./quickstart.md) verification — spike numbers recorded; milestones a/b/c behaviors; the degradation check; fresh-clone `docker compose up` (now including `neo4j`) comes up healthy (smoke set +1).
- [X] T037 Confirm focused PRs (commit per milestone) and that all CI gates are green before marking the spec done: `smoke` (incl. neo4j), `redaction` (incl. `memory_write`), `supervisor_routing`, `llm_provider`, `triage`, **`retrieval`**, **`temporal_memory`**. (Constitution I/II)

---

## Contingency — pgvector fallback (ONLY if Milestone 0 / T005 returns "no-go")

> Not part of the default path. Flip `SENTINEL__MEMORY__BACKEND=pgvector` and build the decided fallback
> (research MD9, data-model §fallback). The `MemoryStore` Protocol makes this a drop-in; the evals (T020/T021,
> T027) run **unchanged** against it.

- [ ] TC01 [contingency] Add `backend/db/migrations/versions/0005_memory_fallback.py` — `incident_episodes` (+`embedding vector(768)`, IVFFlat index) and `entity_facts` (`valid_from`/`valid_until`). (data-model §fallback schema)
- [ ] TC02 [contingency] Implement `PgVectorMemory(MemoryStore)` in `backend/repositories/memory.py` — pgvector cosine similarity for `search_similar`; `UPDATE valid_until` + `INSERT` for invalidation; window query for `query_fact`. Select it in `MemoryProvider.build` when `backend == "pgvector"`.
- [ ] TC03 [contingency] Re-run `tests/integration/test_graphiti_memory.py` (parametrized for the pgvector backend) + the `retrieval` and `temporal_memory` eval gates against pgvector — all green.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: no dependencies. T001/T002 are independent; T003 (compose) is independent; T004 last.
- **Foundational (Phase 2)**: depends on Setup; **blocks all user stories**. **T005 (spike) runs first** (it decides Graphiti vs. the Contingency path). T006/T008/T011 are independent files; T007/T009/T012 are their tests; T010 needs T006+T008; T013 needs T010+T011; T014 needs T010/T011.
- **User Stories (Phase 3–5)**: all depend on Foundational. US1/US2/US3 all evolve `backend/infra/memory.py` (`GraphitiMemory`) and the shared e2e file, so they are sequenced **US1 → US2 → US3** (same-file evolution), though each remains independently *testable*.
- **Polish (Phase 6)**: after the desired stories complete.
- **Contingency (TC01–TC03)**: only on a T005 "no-go".

### User Story Dependencies

- **US1 (P1)**: after Foundational. The MVP — `write_episode` + `search_similar` + retrieval gate (Milestones a+b).
- **US2 (P2)**: after US1 (adds `query_fact` + invalidation to the same class; temporal gate — Milestone c).
- **US3 (P3)**: after US1/US2 (hardens degradation + idempotency on the same class + e2e).

### Within Each Story

- Types/Protocol before the store impl; store impl before integration/e2e; fixtures + gate last.
- Verify each new test **fails before** the implementing change where practical (Constitution II).

### Parallel Opportunities

- Setup: **T001, T002** in parallel; T003 alongside; then T004.
- Foundational: **T006, T008, T011** in parallel (distinct files), with **T007, T009, T012** alongside; T010 then T013/T014. (T005 spike gates the whole phase.)
- US1: after T015→T016, run **T017, T019** in parallel; T018, T020, T021 follow.
- US2: T022→T023, then **T024, T025, T026** in parallel; T027 last.
- US3: T028 first; then **T029, T031** in parallel; T030 follows.
- Polish: **T032, T034** in parallel.

---

## Parallel Example: Foundational

```bash
# Distinct files, no interdependency — run together (after the T005 spike says "go"):
Task: "Create backend/domain/memory.py (types + MemoryStore Protocol)"      # T006
Task: "Extend backend/infra/config.py with MemorySettings"                  # T008
Task: "Create backend/services/memory.py (record_episode + redaction)"      # T011
Task: "Unit test tests/unit/test_memory_types.py"                           # T007
```

## Parallel Example: User Story 1

```bash
# After T015 (write_episode) + T016 (search_similar):
Task: "Integration test tests/integration/test_graphiti_memory.py::test_write_then_retrieve"  # T017
Task: "Create labeled fixtures tests/fixtures/memory_retrieval/*.json"                         # T019
```

---

## Implementation Strategy

### MVP First (User Story 1 only)

1. Phase 1 (Setup) → Phase 2 (Foundational, **starting with the T005 spike**) → Phase 3 (US1).
2. **STOP and VALIDATE**: an incident is written as a redacted episode and a similar prior + its disposition is retrieved; the `retrieval` gate is green. (Milestones a + b.)
3. Demo-ready: the system visibly "remembers" (brief demo moment 3 precursor).

### Incremental Delivery

1. Foundation → US1 (MVP: write + retrieve, Milestones a+b).
2. + US2 → "what was true when" (invalidate-not-delete, temporal gate, Milestone c — the differentiator).
3. + US3 → graceful degradation + idempotency (memory is never a SPOF).
4. Polish → DECISIONS.md, redaction `memory_write` boundary, lint/import-linter, coverage, quickstart, focused PRs.

---

## Notes

- `[P]` = different files, no incomplete-task dependency.
- The **supervisor is never touched** — it stays a pure deterministic state machine; the worker writes episodes **after** disposition, off-path and best-effort. A memory outage degrades to empty results, never a blocked disposition or a crashed worker.
- **Redaction runs before every memory write**; nothing unredacted reaches Graphiti's LLM/embedder, Neo4j, or any returned hit/fact (FR-005, the `redaction` gate's `memory_write` boundary).
- `graphiti_core`/`neo4j` live **only** in `backend/infra/memory.py`; consumers depend on the `MemoryStore` Protocol so the pgvector fallback is a config-toggle swap.
- The two memory eval gates are deterministic **store-logic** gates and are **provider-independent** (like `smoke`/`supervisor_routing`) — justified in `DECISIONS.md`.
- Commit at each milestone (0 → a → b → c); stop at any checkpoint to validate the story independently.
