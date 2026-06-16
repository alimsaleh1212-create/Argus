# Contract — Feedback Write-Back (#16, M1)

**Owner**: `services/memory.record_outcome_facts` + `worker` off-path hook. **Store**: existing
`MemoryStore.write_fact` (`domain/memory.py`). **No new table, no migration.**

---

## 1. Trigger & guard

- Runs **off-path, best-effort**, from the worker's existing post-terminal task
  (`_maybe_record_episode` → extended), alongside `record_episode`.
- Guard (already present): incident `status ∈ {RESOLVED, ESCALATED, FAILED}` (terminal only).
- Additional guard: `evidence["response"]["verification"]` is present (a verdict was produced by #15).
  Absent → **no fact written**, no error (e.g. triage/enrichment-terminal incidents).

## 2. What is written

For each **applied** action target (from `evidence["response"]["results"]` where `status == applied`):

```
TemporalFact(
  entity     = EntityRef(kind=<from target>, value=<target>),   # keyed like the reputation fact (D2)
  fact_type  = cfg.feedback.outcome_fact_type,                  # "remediation_outcome"
  value      = evidence["response"]["verification"]["verdict"], # verified|unverified|regressed
  valid_from = incident.updated_at,                             # terminal observed_at
)
→ await store.write_fact(fact)   # invalidate-not-delete; prior outcome on (entity, fact_type) superseded
```

- **Incident-level verdict** is used (the worst-case aggregate #15 already computed); one fact per applied
  target, all carrying that verdict value.
- Targets with no resolvable `EntityRef` kind are skipped (best-effort).

## 3. Redaction (Constitution III)

- The same `Boundary.MEMORY_WRITE` redactor used by `record_episode` runs on any free-text.
- The `value` is a non-sensitive verdict enum. Indicator-class keys follow the established reputation
  convention (D2). **Write-key MUST equal the read-key** the consumers query with (see consumption contract).

## 4. Best-effort / non-blocking (FR-003)

- The entire call is wrapped in the worker's existing try/except: any store outage / error is **logged and
  swallowed**; it **never** blocks the disposition acknowledgement and **never** raises into the pipeline.
- Degrades to `NullMemory` no-op exactly like the episode write.

## 5. Idempotency (FR-004)

- Runs only for terminal incidents (bounded resume re-runs).
- Re-writing an **identical** current `(entity, remediation_outcome, value, valid_from)` is a no-op at the store
  (no spurious supersession). A genuinely changed outcome supersedes (the intended time-valid behavior, FR-002).

## 6. Invariants

- **No new write authority over incident state** — writes only to the memory store (Constitution III).
- Off the synchronous incident path (Constitution VII observability-without-latency).
