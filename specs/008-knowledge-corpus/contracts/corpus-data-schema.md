# Contract — Bundled corpus data files & seeding

The curated v1 snapshot ships as committed repo files under `backend/data/corpus/`. The seeder
(`backend/seed_corpus.py`, CD4) loads them, redacts text at the write boundary, and writes them to the two
stores. **Small and curated** — tens of techniques, a few runbooks, a small IOC seed set.

## `techniques.json` → `ReferenceCorpusEntry(kind=TECHNIQUE)` rows
```json
[
  {
    "id": "T1110",
    "title": "Brute Force",
    "tactic": "credential-access",
    "mitigations": "Enforce account lockout; require MFA; monitor failed-auth spikes."
  }
]
```
Mapping: `key=id`, `title=title`, `content=mitigations`,
`tags=[lower(id), lower(tactic)] + keyword tokens`.

## `runbooks.json` → `ReferenceCorpusEntry(kind=RUNBOOK)` rows
```json
[
  {
    "key": "rb-brute-force",
    "title": "Brute-force response runbook",
    "techniques": ["T1110"],
    "steps": "1) Confirm source. 2) Lock affected account. 3) Block source IP. 4) Notify owner."
  }
]
```
Mapping: `key=key`, `title=title`, `content=steps`,
`tags=[lower(t) for t in techniques] + keyword tokens`.

## `ioc_reputation.json` → `TemporalFact(fact_type="reputation")` via `store.write_fact`
```json
[
  {
    "indicator": "203.0.113.10",
    "kind": "address",
    "reputation": "malicious",
    "as_of": "2026-01-01T00:00:00Z"
  }
]
```
Mapping: `entity=EntityRef(kind=<kind>, value=redact(indicator))`, `fact_type="reputation"`,
`value=reputation`, `valid_from=as_of`, `valid_until=None` (current until an on-demand intel verdict
supersedes it).

## Seeding behavior (`seed_corpus.py`)
1. Load + validate each file (Pydantic). A malformed entry is **skipped with a logged warning**, not fatal
   — a partial corpus is better than no boot.
2. **Redact** `title`/`content`/indicator (`Boundary.MEMORY_WRITE`) before writing (FR-007).
3. **Upsert** reference rows: `INSERT … ON CONFLICT (kind, key) DO UPDATE` → **idempotent** (FR-002,
   SC-002): re-running changes nothing for unchanged data and updates content for changed data, never
   duplicating.
4. **Write reputation facts** via `store.write_fact`; re-seeding an unchanged current fact is a no-op
   supersession (idempotent). If memory is `NullMemory` (Neo4j down), fact writes no-op and seeding still
   succeeds for the reference table (graceful degradation, FR-008).
5. Exit non-zero only on an unrecoverable error (e.g., Postgres unreachable) so the compose dependency
   surfaces it; individual bad entries never fail the run.

## Compose wiring (CD4)
A `seed-corpus` one-shot (same backend image, `command: ["python","-m","backend.seed_corpus"]`)
`depends_on`: `migrate` completed + `neo4j` healthy + `vault-seed` completed. `api`/`worker` do **not**
depend on it (knowledge is additive; the spine boots without a seeded corpus). The **optional** intel key,
when present in `.env`, is written by `vault-seed` to `secret/intel`.
