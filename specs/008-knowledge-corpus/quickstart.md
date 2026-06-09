# Quickstart — Knowledge Corpus (#5)

Bring up the stack, seed the corpus, retrieve reference knowledge for an incident, run an optional intel
lookup, and verify temporal supersession + the eval gate. Assumes #1–#6 are in place (Postgres+pgvector,
Neo4j, Redis, Vault, the memory store).

## 0. Bring up & seed (turnkey)
```bash
docker compose up -d           # vault-seed → migrate → seed-corpus run as one-shots before api/worker
docker compose logs seed-corpus    # idempotent: reference rows upserted, seed reputation facts written
```
`seed-corpus` is safe to re-run — `docker compose up` again duplicates nothing (FR-002 / SC-002).

## 1. Milestone (a) — seed → retrieve (the MVP, cold-start closed)
```python
from backend.domain.corpus import ReferenceQuery
from backend.infra.container import get_corpus_retriever   # DI provider

retriever = await get_corpus_retriever()
hits = await retriever.search_reference(
    ReferenceQuery(technique_ids=["T1110"], terms=["credential-access"]), k=5
)
assert hits                       # non-empty on a freshly seeded store (SC-001)
assert hits[0].matched_on == "technique"
# returns the T1110 technique→mitigation entry and the brute-force runbook tagged T1110
```
Empty/cold path:
```python
assert await retriever.search_reference(ReferenceQuery(technique_ids=["T9999"], terms=[]), k=5) == []
# no match → [], never an error (FR-003)
```

## 2. Milestone (b) — on-demand intel → temporal fact (optional)
Enable intel (off by default):
```bash
# .env: provide the intel API key → vault-seed writes secret/intel; set enabled
export SENTINEL__INTEL__ENABLED=true
```
```python
from backend.domain.memory import EntityKind, EntityRef
from backend.infra.container import get_intel_client, get_memory_store

intel = await get_intel_client()
v = await intel.lookup("203.0.113.10", EntityKind.ADDRESS)   # seeded benign-ish? now look it up
assert v.verdict in {"benign", "malicious", "suspicious", "unknown"}

# repeat within TTL → served from cache, no second external call (FR-005)
v2 = await intel.lookup("203.0.113.10", EntityKind.ADDRESS)

# the verdict was written as a temporal fact and supersedes the seeded reputation (SC-004)
store = await get_memory_store()
now = await store.query_fact(EntityRef(kind=EntityKind.ADDRESS, value="203.0.113.10"), "reputation")
assert now.is_current and now.has_superseded     # new fact current; seed retained as superseded
```
Disabled path (no creds / `enabled=false`):
```python
assert (await intel.lookup("203.0.113.10", EntityKind.ADDRESS)).verdict == "unknown"   # no call, no error
```

## 3. Failure & safety paths
```python
# intel source down/timeout → unknown, never blocks (FR-008)
# (point base_url at a dead host) → lookup returns verdict="unknown"

# Neo4j down → seeding still writes reference rows; fact writes no-op via NullMemory (graceful degradation)

# redaction: a secret planted in intel response text never appears in the memory store or logs
#   (verified by the redaction eval's memory_write boundary)
```

## 4. Tests & eval
```bash
uv run pytest tests/unit/test_corpus_retrieval.py tests/unit/test_corpus_seed_idempotent.py \
              tests/unit/test_intel_client.py tests/unit/test_write_fact_supersede.py
uv run pytest tests/integration/test_corpus_repo.py tests/integration/test_write_fact.py   # real PG + Neo4j
uv run pytest tests/e2e/test_corpus_e2e.py
uv run pytest tests/eval/test_retrieval_gate.py        # now includes the corpus fixture set (cold-start)
```

## Done when
- Unit + integration + e2e green; ≥80% coverage on new code.
- The `retrieval` gate passes with the added corpus fixtures (hit@k ≥ 0.80, MRR ≥ 0.60) — provider-independent.
- Fresh `docker compose up` seeds idempotently and the spine still boots if the corpus/intel is absent.
- Two milestone commits pushed: **(a) seed→retrieve**, **(b) intel→temporal-fact**.
