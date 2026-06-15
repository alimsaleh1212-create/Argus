# Corpus & Memory Seeding Architecture

## Two stores, two purposes, different retrieval strategies

### Postgres → `reference_corpus` table (MITRE + runbooks)

The `seed-corpus` service writes **two JSON files** from `backend/data/corpus/`:

| File | What | Postgres columns |
|---|---|---|
| `techniques.json` | MITRE ATT&CK techniques + mitigations | `kind=technique, key=T1110, title, content, tags` |
| `runbooks.json` | SOC runbook steps | `kind=runbook, key=..., title, content, tags` |

Retrieval is **purely deterministic — no LLM, no embeddings** (v1 design decision CD1).
`CorpusRepository.search_reference` does three passes:

1. Exact key match → relevance 1.0
2. Tag-array overlap → relevance proportional to overlap (max 0.9)
3. ILIKE lexical scan → relevance 0.3

No vectors involved at any step.

---

### Neo4j → via Graphiti (IOC reputation facts + runtime incidents)

The `seed-corpus` service also writes one JSON file here:

| File | What | Where |
|---|---|---|
| `ioc_reputation.json` | Known-bad IPs/hosts with reputation verdict | Neo4j via `write_fact` → Graphiti episode nodes + `RELATES_TO` edges |

Neo4j **is** semantic/LLM-assisted. `write_fact` calls `graphiti.add_episode()` which:

1. Uses the configured **LLM** (`GeminiClient` or Ollama OpenAI-compatible) to do entity extraction and build graph structure
2. Uses the **embedder** (`GeminiEmbedder` or `nomic-embed-text` via Ollama) to create vector embeddings on each episode/edge
3. Stores entity nodes, episode nodes, and `RELATES_TO` edges with temporal bounds (`valid_at`, `invalid_at`) in Neo4j

At runtime the **worker** also calls `write_episode` after each terminal incident — same Graphiti path.
`search_similar` retrieves via `graphiti.search(text)` — graph traversal + cosine similarity on stored embeddings.

The embedder provider is controlled by `ARGUS__MEMORY__EMBEDDER_PROVIDER` (default: `gemini`; set to `ollama` for local-only stacks). **Do not change the provider after data has been written** — vectors from different models are incompatible and corrupt search.

---

### `seed-corpus` compose service

```yaml
seed-corpus:
  command: ["python", "-m", "backend.seed_corpus"]
  depends_on:
    neo4j: { condition: service_healthy }
    vault-seed: { condition: service_completed_successfully }
    migrate: { condition: service_completed_successfully }
  restart: on-failure
```

It runs automatically on `docker compose up` — **no manual trigger needed**.
Startup order: Postgres migrations (`migrate`) → `seed-corpus` seeds both stores → API starts.

If it fails, look for `seed_corpus_failed` in logs and re-run with:

```bash
docker compose run --rm seed-corpus
```

---

### Which demo acts depend on seeding

| Act | Depends on Postgres corpus | Depends on Neo4j seed |
|---|---|---|
| Act 1 — Noise (LOW/MEDIUM fast-path) | No | No |
| Act 2.1/2.2 — SSH/web brute force | Yes (MITRE T1110) | No |
| Act 2.3/2.4 — C2 beacon / PowerShell | Yes (MITRE T1195, T1059) | No |
| Act 3 — HITL approval flow | No | No |
| Act 4 — Escalation | No | No |
| **Act 5.1 — Insider threat** | Yes | **Yes — memory cross-correlation** |
| **Act 5.2 — Supply chain** | **Yes (T1195.002)** | No |
| **Act 5.3 — Web shell** | **Yes (T1505.003)** | No |
