---
description: "Task list — Knowledge Corpus (Reference + On-Demand Intel) (#5)"
---

# Tasks: Knowledge Corpus (Reference + On-Demand Intel)

**Input**: Design documents from `specs/008-knowledge-corpus/`
**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md),
[data-model.md](./data-model.md), [contracts/](./contracts/)

**Tests**: REQUIRED (Constitution II — three-tier + eval-gated, NON-NEGOTIABLE). Unit/integration/e2e plus a
corpus fixture set added to the existing **retrieval** (hit@k/MRR) gate land with this component.

**Organization**: Tasks are grouped by user story (US1 P1, US2 P2, US3 P3) for independent implementation and
testing. Commit at each internal milestone: **(a) seed→retrieve** (US1) → **(b) intel→temporal-fact** (US2).
**Keep-it-simple posture**: two stores, each for what it fits — static reference docs → Postgres
`reference_corpus` (deterministic keyed/lexical, **no LLM, no embeddings**); temporal reputation → the #6
store via a minimal `write_fact`. On-demand intel is **optional / config-gated / fail-closed / off-path**.
**Redaction + the (graceful-no-op) guardrail seam run before every write**; missing intel creds **disable**,
they do not fail boot; a knowledge outage never blocks an incident.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different files, no dependency on an incomplete task)
- **[Story]**: US1 / US2 / US3 (setup, foundational, polish carry no story label)
- Exact file paths are in each description.

## Path Conventions

Modular monolith `backend/` (per [plan.md](./plan.md)); tests under `tests/{unit,integration,e2e,eval,fixtures}`.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: confirm a green baseline and reserve the compose + coverage seams. No new third-party dependency
(`httpx`, async SQLAlchemy, pgvector image, Neo4j are all already in the stack).

- [X] T001 [P] In `pyproject.toml`, remove the new modules from `[tool.coverage.run] omit` so they are measured: `backend/infra/intel.py`, `backend/repositories/corpus.py`, `backend/services/corpus.py`, `backend/seed_corpus.py` (the new `backend/domain/corpus.py` is measured automatically). Confirm no new runtime dep is needed; `uv sync`. (plan Technical Context)
- [X] T002 Configure `compose.yaml`: add a one-shot **`seed-corpus`** service (same backend image, `command: ["python","-m","backend.seed_corpus"]`) with `depends_on` `migrate: {condition: service_completed_successfully}`, `neo4j: {condition: service_healthy}`, `vault-seed: {condition: service_completed_successfully}`; do **not** make `api`/`worker` depend on it. Have `vault-seed` write `secret/intel` **only when** an intel key is present in `.env` (optional). (research CD4; contracts/corpus-data-schema.md)
- [X] T003 Establish a green baseline: `uv sync` then `uv run pytest -q -m "not integration and not e2e"` passes before any change.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: the pure types, the read Protocol, config, and the minimal `write_fact` contract addition that
**all** stories need.

**⚠️ CRITICAL**: No user-story work begins until this phase is complete and the existing suite stays green.

- [X] T004 [P] Create `backend/domain/corpus.py` — pure types (`extra="forbid"`, `frozen=True`): `ReferenceKind` (`TECHNIQUE`/`RUNBOOK`), `ReferenceCorpusEntry` (kind, key, title, content, tags), `ReferenceQuery` (technique_ids, terms), `ReferenceHit` (entry, relevance∈[0,1], matched_on∈{technique,tag,term}), `IntelVerdict` (indicator, verdict∈{benign,malicious,suspicious,unknown}, source, observed_at), and the `CorpusRetriever` **Protocol** (`search_reference(query, *, k) -> list[ReferenceHit]`). Reuse `EntityKind`/`EntityRef`/`TemporalFact` from `backend/domain/memory.py` (domain→domain allowed); no outward imports. (data-model §domain types; contracts/corpus-retrieval-contract.md)
- [X] T005 [P] Unit test `tests/unit/test_corpus_types.py` — `ReferenceHit.relevance` ∈ [0,1]; `tags` lowercased + de-duplicated; `key` non-empty; `IntelVerdict.verdict` enum-bounded; `extra="forbid"` rejects unknown keys. (data-model)
- [X] T006 [P] Extend `backend/infra/config.py` — add `CorpusSettings` (`enabled=True`, `data_dir="backend/data/corpus"`, `retrieval_k=5` `gt=0`) and `IntelSettings` (`enabled=False`, `source_name="demo-intel"`, `base_url=""`, `api_key_vault_path="secret/intel"`, `timeout_s=5.0` `gt=0`, `cache_ttl_s=3600` `gt=0`); add `"corpus"`/`"intel"` to `_KNOWN_ARGUS_SECTIONS`; add `corpus`/`intel` to `Settings`. **Do NOT** force `intel.api_key_vault_path` into `vault.required_paths` — it is optional (absent → disabled, not fail-boot). (data-model §settings; research CD3)
- [X] T007 [P] Unit test `tests/unit/test_corpus_config.py` — defaults (`intel.enabled is False`); `extra="forbid"` rejects unknown keys; the intel key path is **not** appended to `vault.required_paths` (contrast with `memory.neo4j_vault_path`). (research CD3)
- [X] T008 Extend the memory contract for #5: add `write_fact(self, fact: TemporalFact) -> None` to the `MemoryStore` **Protocol** in `backend/domain/memory.py`, and implement `NullMemory.write_fact` (no-op) in `backend/infra/memory.py`. Leave `GraphitiMemory.write_fact` as a `NotImplementedError`/`...` shell — its body lands in US2 (T021). (research CD2; contracts/intel-lookup-contract.md)
- [X] T009 [P] Unit test `tests/unit/test_write_fact_null.py` — `NullMemory.write_fact` no-ops (degradation preserved) and the `MemoryStore` Protocol still recognises `GraphitiMemory`/`NullMemory` via `isinstance` (`runtime_checkable`). (FR-008)

**Checkpoint**: types, read Protocol, config, and the `write_fact` contract (Null impl) exist; the full existing suite stays green; no reference table or retrieval logic yet.

---

## Phase 3: User Story 1 — Competent on the very first incident (Priority: P1) 🎯 MVP — Milestone (a)

**Goal**: seed a curated reference corpus (MITRE technique→mitigation + runbooks) and retrieve relevant
reference knowledge for an incident's technique/indicators — closing cold-start.

**Independent Test**: on a freshly seeded store, `search_reference(technique_ids=["T1110"], terms=[...])`
returns the T1110 technique entry + its tagged runbook ranked by relevance; an unmatched query returns `[]`;
re-seeding duplicates nothing.

- [X] T010 [P] [US1] Create the migration `backend/db/migrations/versions/0006_reference_corpus.py` — table `reference_corpus` (`id` PK, `kind` text, `key` text, `title` text, `content` text, `tags text[]` default `'{}'`, `embedding vector` **null/reserved**, `created_at`/`updated_at`), a **unique index** on `(kind, key)` and a **GIN index** on `tags`. (data-model §storage)
- [X] T011 [P] [US1] Add the bundled curated snapshot `backend/data/corpus/techniques.json` and `backend/data/corpus/runbooks.json` (small, committed) per the shapes in contracts/corpus-data-schema.md — tens of techniques (id/title/tactic/mitigations) + a handful of runbooks (key/title/techniques/steps). (contracts/corpus-data-schema.md)
- [X] T012 [US1] Create `backend/repositories/corpus.py` — `CorpusRepository(CorpusRetriever)`: `upsert_entries(entries)` (`INSERT … ON CONFLICT (kind, key) DO UPDATE`, idempotent) and `search_reference(query, *, k)` implementing the ranked union — technique-keyed (relevance 1.0) → tag-overlap → lexical `ILIKE`, de-dupe by `(kind,key)`, sort by relevance desc then key asc, truncate to `k`; empty/no-match → `[]`. Async SQLAlchemy session (mirror `repositories/incidents.py`). (contracts/corpus-retrieval-contract.md; FR-003)
- [X] T013 [US1] Create `backend/services/corpus.py` — `seed_reference(entries, redactor, repo)`: map the loaded technique/runbook records → `ReferenceCorpusEntry` (build `tags`), **redact** `title`/`content` (`Boundary.MEMORY_WRITE`) before upsert, call `repo.upsert_entries`. A malformed record is skipped with a logged warning (partial corpus > no boot). Pure-ish + unit-testable. (FR-002/FR-007; contracts/corpus-data-schema.md)
- [X] T014 [US1] Create the one-shot `backend/seed_corpus.py` — `python -m backend.seed_corpus`: load `CorpusSettings.data_dir/*.json` (Pydantic-validated), open an async DB session + resolve the `Redactor`, call `seed_reference(...)`; exit non-zero only on an unrecoverable error (e.g. Postgres unreachable), never on a single bad entry. (research CD4)
- [X] T015 [P] [US1] Add a `CorpusProvider`/`get_corpus_retriever()` to `backend/infra/container.py` (lifespan singleton wrapping `CorpusRepository`), exposing the `CorpusRetriever` for #9 to consume later. (plan Structure; CD6)
- [X] T016 [P] [US1] Unit test `tests/unit/test_corpus_retrieval.py` — ranking is deterministic (technique > tag > term; stable sort), `k` truncation honored, empty query and no-match → `[]`, redaction applied before upsert (planted secret never in the upserted entry). Repo session mocked. (FR-003; contracts/corpus-retrieval-contract.md)
- [X] T017 [P] [US1] Unit test `tests/unit/test_corpus_seed_idempotent.py` — `seed_reference` run twice produces the same set (upsert, no duplicates); a malformed record is skipped, not fatal. (FR-002/SC-002)
- [X] T018 [US1] Integration test `tests/integration/test_corpus_repo.py` — against **real Postgres** (existing harness): migrate → seed → **re-seed idempotent** (row count unchanged) → `search_reference` returns the seeded T1110 entry + tagged runbook ranked; cold/unmatched query → `[]`. (SC-001/SC-002)
- [X] T019 [P] [US1] Create labeled corpus fixtures `tests/fixtures/corpus_retrieval/*.json` (technique/indicator → expected reference entries) and extend the **`retrieval`** gate in `config/eval_thresholds.yaml` to score them (cold-start improvement; provider-independent — no new gate). (research CD7; FR-011/SC-007)
- [X] T020 [US1] Eval test `tests/eval/test_retrieval_gate.py` (extend) — the corpus fixtures meet `min_hit_at_k`/`min_mrr`; assert an *unseeded* store returns nothing for the same queries (demonstrating the seed's value). (SC-007)

**Checkpoint (Milestone a)**: `docker compose up` seeds idempotently; reference knowledge retrieves on a cold store; the `retrieval` gate is green with corpus fixtures. **Commit.** US1 is independently demoable (cold-start closed) without US2/US3.

---

## Phase 4: User Story 2 — On-demand intel, remembered next time (Priority: P2) — Milestone (b)

**Goal**: an optional, config-gated lookup for one indicator that returns a verdict, caches it briefly, and
**writes it into the #6 store as a temporal fact** that supersedes the seeded reputation.

**Independent Test**: with intel enabled, lookup an unseen indicator → verdict within timeout + a temporal
fact written; repeat within TTL → no second external call; a verdict contradicting the seed supersedes it
(both queryable); with intel disabled → `unknown`, no call, pipeline unaffected.

- [X] T021 [US2] Implement `GraphitiMemory.write_fact` in `backend/infra/memory.py` — write the fact as a time-bounded reputation edge with `valid_from`; **end the validity of (invalidate, not delete)** the current fact of the same `(entity, fact_type)`. Read path unchanged (`query_fact(..., as_of=…)` → `FactState` with `is_current`/`has_superseded`). `graphiti_core`/`neo4j` stay confined to this module. (research CD2; contracts/intel-lookup-contract.md; FR-006)
- [X] T022 [US2] Integration test `tests/integration/test_write_fact.py` — against **real Neo4j** (existing `testcontainers[neo4j]`): `write_fact(benign@t1)` then `write_fact(malicious@t2)`; `query_fact(as_of=t1)` → benign (superseded), `query_fact()` (now) → malicious (current) with `has_superseded` true; the benign fact still exists (invalidated, not deleted). (FR-006/SC-004)
- [X] T023 [P] [US2] Add the bundled `backend/data/corpus/ioc_reputation.json` (small seed IOC reputation set) and extend `backend/services/corpus.py` with `seed_reputation(records, redactor, store)`: map → `TemporalFact` (`fact_type="reputation"`, redacted indicator, `valid_from=as_of`), `await store.write_fact(fact)`. Wire it into `backend/seed_corpus.py` after the reference upsert; if `store` is `NullMemory` (Neo4j down) the fact writes no-op and seeding still succeeds. (contracts/corpus-data-schema.md; FR-006/FR-008)
- [X] T024 [US2] Create `backend/infra/intel.py` — `ThreatIntelClient.lookup(indicator, kind) -> IntelVerdict`: disabled/no-key fast-path → `unknown` (no call); Redis cache read (`intel:<redacted-indicator>`) via the existing `CacheProvider`; one async `httpx` GET bounded by `timeout_s` (any error/timeout → `unknown`, fail-closed); **redact + guardrail-check** the response before persistence (CD5, T031 wires the seam); cache write with `cache_ttl_s` (negative caching); for a non-`unknown` verdict build a `TemporalFact` and `await store.write_fact(...)` (best-effort, swallow write errors). Plus an `IntelProvider`/`get_intel_client()` in `backend/infra/container.py`. (contracts/intel-lookup-contract.md; FR-004/005/006/008)
- [X] T025 [P] [US2] Unit test `tests/unit/test_intel_client.py` — disabled (and no-key) → `unknown` with **no httpx call**; cache hit → no second call; timeout/HTTP error → `unknown`; a non-`unknown` verdict calls `store.write_fact`; a planted secret in the response never appears in the written fact or cache (redaction). httpx + Redis + store mocked. (FR-004/005/007/008)
- [X] T026 [P] [US2] Unit test `tests/unit/test_seed_reputation.py` — `seed_reputation` maps records → redacted `TemporalFact`s and calls `write_fact`; with a `NullMemory` store the seed still completes (no raise). (FR-006/FR-008)
- [X] T027 [US2] E2E test `tests/e2e/test_corpus_e2e.py` — fresh seed → `search_reference` non-empty (US1 path) **and** an intel lookup of a seeded indicator writes a fact that supersedes the seed reputation (`query_fact` now-current = looked-up verdict, seed retained as superseded). External intel mocked. (SC-001/SC-003/SC-004)

**Checkpoint (Milestone b)**: optional intel returns verdicts, caches them, and accumulates temporal facts that supersede the seed; disabled → corpus-only. **Commit.**

---

## Phase 5: User Story 3 — Untrusted input & never breaks the pipeline (Priority: P3)

**Goal**: feed/intel text is redacted + guardrail-checked before any write; knowledge is best-effort and
bounded; seeding is idempotent — none of it blocks or crashes an incident.

**Independent Test**: a redaction/injection probe in intel text is caught (nothing unredacted, injection
refused) anywhere downstream; an intel outage/timeout → `unknown` and the incident still completes; a corpus
miss → `[]`; re-seed duplicates nothing; Neo4j-down seeding still writes the reference table.

- [X] T028 [P] [US3] Unit test `tests/unit/test_intel_failclosed.py` — intel `base_url` pointing at a dead host (and a forced timeout) both yield `unknown`, retried only within policy then given up, and never raise into the caller; a `write_fact` raising is swallowed (verdict still returned). (FR-008)
- [X] T029 [P] [US3] Integration test `tests/integration/test_seed_degrade.py` — with the memory store as `NullMemory` (Neo4j unavailable), `seed_corpus` still upserts the reference table successfully and the reputation facts no-op; exit code is success. (FR-008/SC-005)
- [X] T030 [P] [US3] Extend the **redaction** eval driver/fixtures so a fake secret + PII planted in **intel/feed text** is asserted absent from the memory store, the Redis cache, logs, and any returned verdict — exercising the `memory_write` boundary for the intel path. (FR-007/SC-006; config/eval_thresholds.yaml `redaction` gate)
- [X] T031 [US3] Wire the **guardrail seam** at the intel ingestion point in `backend/infra/intel.py` — route response text through the reserved `Guardrail` (`backend/infra/guardrails.py`, #11) **before** any write; because #11 ships later, the call site **catches the un-configured seam and no-ops (debug log) rather than raising**, so #5 is not blocked and #11 drops in with no #5 change. Unit-cover the no-op-until-configured behavior. (research CD5; Constitution III/VI)
- [X] T032 [P] [US3] Unit test `tests/unit/test_corpus_miss.py` — `search_reference` on an empty/unseeded store and on a no-match query returns `[]` (not an error); confirms cold/miss is a normal outcome. (FR-003/US1-3)

**Checkpoint**: knowledge is untrusted-input-safe, fail-closed, idempotent, and never a single point of failure. All three tiers + both eval gates green.

---

## Phase 6: Polish & Cross-Cutting Concerns

- [X] T033 [P] Record the decisions in `DECISIONS.md` — **CD1** (two stores: Postgres reference docs + #6 temporal reputation; FR-009 refined to "no new service"), **CD2** (`write_fact` contract addition; intel is a fact not an episode), **CD3** (optional/fail-closed intel; missing creds disable not fail-boot), **CD4** (idempotent `seed-corpus` one-shot), **CD5** (untrusted-input boundary + #11 graceful-no-op ordering), **CD7** (extend the `retrieval` gate, no new gate). (plan Complexity Tracking)
- [X] T034 [P] `ruff` + formatter clean; `import-linter` contracts kept (`graphiti_core`/`neo4j` stay only in `infra/memory.py`; `httpx` intel call only in `infra/intel.py`; `domain/corpus.py` has no outward imports); ≥80% coverage on new code.
- [X] T035 [P] Verify `specs/008-knowledge-corpus/quickstart.md` end-to-end against the running stack (seed → retrieve → intel lookup → supersession → gate); fix any drift.
- [X] T036 Final: fresh-clone `docker compose up` seeds idempotently and the spine still boots with the corpus/intel **absent** (additive, non-blocking); confirm the two milestone commits (a, b) are focused PRs (≤ ~400 lines each).

---

## Dependencies & Execution Order

- **Setup (P1)** → **Foundational (P2)** → **US1 (P3, MVP)** → **US2 (P4)** → **US3 (P5)** → **Polish (P6)**.
- **US1** depends only on Foundational — it is the independently shippable MVP (cold-start closed).
- **US2** depends on Foundational (T008 `write_fact` Protocol) + US1's seeder/data wiring (T013/T014); its `GraphitiMemory.write_fact` body (T021) is US2-local.
- **US3** hardens US1+US2 (redaction/guardrail/fail-closed/idempotency) — no new capability.
- The **supervisor, worker disposition path, and `incidents` repo are untouched**; enrichment (#9) consumes `CorpusRetriever`/intel later.

## Parallel Example: Foundational

```bash
Task: "Create backend/domain/corpus.py types + CorpusRetriever Protocol"   # T004
Task: "Extend backend/infra/config.py with CorpusSettings + IntelSettings"  # T006
Task: "Unit test tests/unit/test_corpus_types.py"                           # T005
Task: "Unit test tests/unit/test_corpus_config.py"                          # T007
```

## Parallel Example: User Story 1

```bash
# After T012 (repo) + T013 (seed service):
Task: "Unit test tests/unit/test_corpus_retrieval.py"        # T016
Task: "Unit test tests/unit/test_corpus_seed_idempotent.py"  # T017
Task: "Create labeled fixtures tests/fixtures/corpus_retrieval/*.json"  # T019
```

---

## Implementation Strategy

### MVP First (User Story 1 only)

1. Phase 1 (Setup) → Phase 2 (Foundational) → Phase 3 (US1).
2. **STOP and VALIDATE**: a freshly seeded store retrieves reference knowledge for an incident; the
   `retrieval` gate is green with corpus fixtures; re-seed duplicates nothing. (Milestone a.)
3. Demo-ready: the system is competent on the **first** incident (cold-start closed).

### Incremental Delivery

1. Foundation → US1 (MVP: seed + retrieve, Milestone a).
2. + US2 → optional on-demand intel that accumulates temporal facts superseding the seed (Milestone b).
3. + US3 → untrusted-input safety, fail-closed, idempotency (knowledge is never a SPOF).
4. Polish → DECISIONS.md (CD1–CD7), lint/import-linter, coverage, quickstart, focused PRs.

---

## Notes

- `[P]` = different files, no incomplete-task dependency.
- **Two stores, each for what it fits** — static reference docs in Postgres `reference_corpus` (deterministic
  keyed/lexical, **no LLM, no embeddings** in v1); temporal reputation in the #6 store via the minimal
  `write_fact`. No new service, no new external LLM path.
- **Redaction runs before every write**; intel/feed text is **untrusted** and passes the guardrail seam (#11),
  which **no-ops gracefully until #11 lands** so #5 is not blocked.
- On-demand intel is **optional / config-gated / fail-closed / off-path**: missing creds **disable** (not
  fail-boot); outage/timeout → `unknown`; a knowledge problem never blocks a disposition or crashes the worker.
- The corpus contributes a **fixture set to the existing `retrieval` gate** (provider-independent store
  logic) — **no new gate** is invented.
- Commit at each milestone (a → b); stop at any checkpoint to validate the story independently.
