# Contract — Memory eval gates (retrieval + temporal-validity)

**Component**: #6 `SPEC-memory` · **Owner**: `config/eval_thresholds.yaml` (seeded here; harness owned by
`SPEC-eval` #13). Both gates land green with this component (Constitution II; SOAR_Plan milestone note:
"retrieval + temporal gate with #6").

These are **deterministic store-logic gates** — like the existing `smoke` and `supervisor_routing` gates,
they have **no `check_per_provider` dimension** (justification: retrieval ranking is an embedding-similarity
property and temporal validity is store logic; neither is a chat-LLM judgment, and the tiny Ollama fallback
cannot fairly do graph extraction — recorded in `DECISIONS.md`). The embedder is pinned (Gemini
`text-embedding-004`) for reproducibility. Gates run against whichever `MemorySettings.backend` is configured.

## Gate `retrieval` (hit@k / MRR)

```yaml
retrieval:
  description: >
    Similar-incident retrieval gate (SPEC-memory #6). Pre-seeds the memory store with a labeled set of
    prior incidents, then issues each held-out "new" incident as an EpisodeQuery and checks that its
    labeled prior surfaces within top-k. Scores hit@k and MRR. Store-logic gate — provider-independent
    (embedder pinned). Full harness owned by SPEC-eval (#13).
  required: true
  threshold:
    min_hit_at_k: 0.80     # ≥80% of queries surface the correct prior within top-k
    k: 5
    min_mrr: 0.60
```

- **Independent test**: write priors → query each new incident → assert labeled prior ∈ top-k. Cold-start
  (empty store) query returns `[]` and is excluded from scoring, not counted as a miss (SC-001).

## Gate `temporal_memory` (current vs. superseded)

```yaml
temporal_memory:
  description: >
    Temporal-validity gate (SPEC-memory #6) — the Graphiti differentiator. Records a fact as benign at t1,
    a conflicting fact as malicious at t2, then asserts query_fact(as_of=t1)=benign (superseded),
    query_fact(now)=malicious (current), and the benign fact is RETAINED (invalidated, not deleted).
    100% required — this is store logic, not a probabilistic judgment. Provider-independent.
  required: true
  threshold:
    pass_rate: 1.0
    cases:
      - reputation_flip          # benign@t1 → malicious@t2
      - host_role_change         # honeypot@t1 → payroll@t2
      - no_destructive_delete    # superseded fact still queryable as_of its window
```

- **Independent test**: for each case assert (a) `as_of=t1` → old value, `is_current=False`; (b) `as_of=now`
  → new value, `is_current=True`; (c) the old fact still exists (`has_superseded=True`) — zero deletes
  (SC-002, SC-005).

## Redaction (already-seeded gate — this spec makes its `memory_write` boundary live)

The existing `redaction` gate in `eval_thresholds.yaml` already lists the **`memory_write`** boundary and
requires `max_credential_leaks: 0` / `max_pii_leaks: 0` into the memory store. No new gate is added for
redaction; this component is what exercises that boundary (FR-006a, SC-004).
