# Quickstart — Incident Memory (Temporal)

**Component**: #6 `SPEC-memory` (branch `007-incident-memory`)

How to bring up the memory layer, run the day-1 spike, and verify each milestone. Assumes the existing
turnkey stack (`docker compose up` brings up postgres/vault/minio/redis/ollama/api/worker).

## 0. Day-1 Graphiti spike (Milestone 0 — go/no-go)

```bash
# Bring up just Neo4j alongside the stack and confirm it's healthy
docker compose up -d neo4j
docker compose ps neo4j           # expect healthy (bolt on 7687, browser on 7474)

# Run the spike script against sample incidents (writes a few episodes, retrieves a similar one,
# forces a fact conflict to observe native invalidation) and prints latency + token cost.
uv run python -m scripts.memory_spike      # records numbers; decide go/no-go vs pgvector in DECISIONS.md
```

**Go/no-go**: if write/retrieval latency and per-episode token cost are acceptable → keep
`SENTINEL__MEMORY__BACKEND=graphiti`. If not → flip to `pgvector` and build the `0005` fallback (research
MD9). **Record the decision in `DECISIONS.md` before proceeding.**

## 1. Configure (Vault-seeded creds, typed settings)

```bash
# vault-seed writes secret/memory (username/password/uri) on `docker compose up`.
# Verify the Neo4j credentials are present:
docker compose exec vault vault kv get secret/memory

# Memory settings (env SENTINEL__MEMORY__*), all with sane defaults:
#   SENTINEL__MEMORY__ENABLED=true
#   SENTINEL__MEMORY__BACKEND=graphiti
#   SENTINEL__MEMORY__NEO4J_URI=bolt://neo4j:7687
#   SENTINEL__MEMORY__RETRIEVAL_K=5
```

## 2. Milestone (a) — write path green

```bash
# Drive an incident through the pipeline to a disposition; the worker writes one redacted episode.
# (reuse the ingestion quickstart to POST a Wazuh alert, or replay a fixture)
uv run pytest tests/integration/test_graphiti_memory.py -k write -q   # write_episode against real Neo4j
docker compose exec neo4j cypher-shell -u neo4j -p "$NEO4J_PW" "MATCH (n) RETURN count(n);"  # nodes exist
```

## 3. Milestone (b) — retrieve path green

```bash
uv run pytest tests/integration/test_graphiti_memory.py -k retrieve -q   # search_similar finds a prior
uv run pytest tests/eval/test_retrieval_gate.py -q                       # hit@k / MRR gate
```

## 4. Milestone (c) — temporal-validity green

```bash
uv run pytest tests/integration/test_graphiti_memory.py -k temporal -q   # invalidate, not delete; as_of
uv run pytest tests/eval/test_temporal_gate.py -q                        # current vs superseded, 100%
```

## 5. Graceful degradation (Constitution VI / FR-006)

```bash
# Stop Neo4j and confirm the worker still disposes incidents (memory degrades to NullMemory, returns []).
docker compose stop neo4j
uv run pytest tests/unit/test_memory_degrade.py -q          # NullMemory: no-op write, empty read
# (e2e) an incident still reaches a terminal disposition with Neo4j down — no worker crash.
```

## 6. Full local verification (the per-spec "done" bar)

```bash
uv run ruff check . && uv run ruff format --check .
uv run lint-imports                                   # import-linter contracts hold
uv run pytest tests/unit -q                           # schemas, redaction-before-write, factstate, degrade
uv run pytest tests/integration -q                    # GraphitiMemory vs real Neo4j (testcontainers)
uv run pytest tests/e2e -q                             # spine: disposition → episode → retrievable
uv run pytest tests/eval -q                            # retrieval + temporal gates
docker compose up --build                             # fresh-clone smoke: stack (now incl. neo4j) healthy
```

## What "done" means (Constitution I)

Unit + integration + e2e green in CI **and** pushed; the `retrieval` and `temporal_memory` gates green; the
`redaction` gate's `memory_write` boundary green; the day-1 spike decision recorded in `DECISIONS.md`. Big
spec → commit at each milestone (0 → a → b → c), never go dark.
